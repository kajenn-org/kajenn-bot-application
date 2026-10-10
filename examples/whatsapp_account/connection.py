# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Synchronize a paired Tryx account into a local, bounded-query directory.

The server never pairs interactively. A QR event marks pairing_required and
requires the separate local pairing harness. App-state snapshot replay is a
local-owner startup option, not an MCP operation. Snapshot dispatch does not
prove callback completion or complete history coverage.
"""

import asyncio
import base64
import time

from tryx.backend import SqliteStore
from tryx.client import Tryx, CreateGroupOptions, GroupParticipantOptions
from tryx.events import (
    EvArchiveUpdate, EvConnected, EvContactUpdate, EvHistorySync, EvDisconnected, EvReceipt,
    ReceiptType,
    EvMarkChatAsReadUpdate, EvMessage, EvMuteUpdate, EvPairingQrCode, EvPinUpdate,
)
from tryx.types import JID
from tryx.waproto.whatsapp_pb2 import Message

from kajenn.exceptions import HTTPBadRequest, HTTPException

from examples.whatsapp_account.directory import _Directory
from examples.whatsapp_account.session import _Session
from examples.whatsapp_account.events import _Events


class _Connection:
    def __init__(self, directory, resync=False):
        self.path = directory
        self.directory = None
        self.session = _Session(directory, self.create_runtime)
        self.resync_requested = resync
        self.resync_task = None
        self.sync_status = "not_connected"
        self.callback_errors = 0
        self.callback_failures = {}
        self.events = _Events()
        self.history_batches = 0
        self.last_history_at = None

    def create_runtime(self, database):
        self.directory = _Directory(self.path / "directory.db")
        runtime = Tryx(SqliteStore(str(database), 0))
        for method in ("resync_directory", "fetch_message_history"):
            if not callable(getattr(runtime.get_client().advanced, method, None)):
                raise RuntimeError("The Tryx account patches are required")
        for event, handler in (
            (EvDisconnected, self.on_disconnected), (EvReceipt, self.on_receipt),
            (EvConnected, self.on_connected), (EvPairingQrCode, self.on_pairing_required),
            (EvContactUpdate, self.on_contact), (EvHistorySync, self.on_history),
            (EvMessage, self.on_message), (EvArchiveUpdate, self.on_archive),
            (EvMarkChatAsReadUpdate, self.on_read),
            (EvMuteUpdate, self.on_mute), (EvPinUpdate, self.on_chat_state),
        ):
            runtime.on(event)(self.guard(handler))
        return runtime

    def guard(self, handler):
        async def callback(client, event):
            try:
                await handler(client, event)
            except Exception as error:
                self.callback_errors += 1
                self.callback_failures[handler.__name__] = type(error).__name__
        return callback

    @property
    def connected(self):
        return self.session.client is not None and self.session.client.is_connected()

    async def start(self):
        await self.session.start()

    async def stop(self):
        if self.resync_task is not None:
            self.resync_task.cancel()
            await asyncio.gather(self.resync_task, return_exceptions=True)
        await self.session.stop()
        await self.events.stop()
        if self.directory is not None:
            self.directory.close()
            self.directory = None

    async def on_pairing_required(self, client, event):
        self.sync_status = "pairing_required"

    async def on_connected(self, client, event):
        self.sync_status = "connected"
        self.events.emit("connected")
        if self.resync_requested and self.resync_task is None:
            self.resync_task = asyncio.create_task(self.resync(client))

    async def resync(self, client):
        self.sync_status = "replaying"
        try:
            complete = await client.advanced.resync_directory()
            self.sync_status = "replay_dispatched" if complete else "partial_replay"
        except Exception:
            self.sync_status = "replay_failed"

    def peer_id(self, jid):
        return f"{jid.user}@{jid.server}"

    async def on_contact(self, client, event):
        data = event.data
        action = data.action
        self.directory.add_contact(
            self.peer_id(data.jid), action.fullName or action.firstName,
            pn=action.pnJid, lid=action.lidJid,
        )

    async def on_chat_state(self, client, event):
        self.directory.add_chat(self.peer_id(event.data.jid), source="app_state")

    async def on_archive(self, client, event):
        self.directory.add_chat(self.peer_id(event.data.jid), archived=event.data.action.archived,
                                source="app_state")

    async def on_history(self, client, event):
        history = event.proto
        self.history_batches += 1
        self.last_history_at = int(time.time())
        self.directory.set_setting("last_history_at", self.last_history_at)
        self.events.emit("history_received", batch=self.history_batches)
        for mapping in history.phoneNumberToLidMappings:
            self.directory.add_alias(mapping.pnJid, mapping.lidJid)
        for contact in history.inlineContacts:
            self.directory.add_contact(
                contact.pnJid or contact.lidJid, contact.fullName or contact.firstName,
                pn=contact.pnJid, lid=contact.lidJid,
            )
        for contact in history.pushnames:
            self.directory.add_contact(contact.id, contact.pushname, source="profile")
        for chat in history.conversations:
            self.directory.add_chat(
                chat.id, chat.name or chat.displayName,
                archived=chat.archived if chat.HasField("archived") else None,
                timestamp=chat.conversationTimestamp or chat.lastMsgTimestamp,
            )
            if chat.HasField("unreadCount"):
                self.directory.set_chat_state(chat.id, unread=chat.unreadCount > 0)
            for item in chat.messages:
                message = item.message
                body = message.message
                text = self.message_text(body)
                self.directory.add_message(
                    chat.id, message.key.id, message.participant or message.key.participant
                    or ("self" if message.key.fromMe else chat.id),
                    text, message.messageTimestamp, message.key.fromMe,
                )
                self.directory.set_message_data(chat.id, message.key.id,
                    body.SerializeToString(), self.message_kind(body))

    @property
    def event_status(self):
        return {**self.events.status, "history_batches": self.history_batches,
                "last_history_at": self.last_history_at or
                (self.directory.get_setting("last_history_at") if self.directory else None)}

    async def on_disconnected(self, client, event):
        self.sync_status = "disconnected"
        self.events.emit("disconnected")

    async def on_read(self, client, event):
        self.directory.set_chat_state(self.peer_id(event.data.jid),
                                      unread=not event.data.action.read)

    async def on_mute(self, client, event):
        self.directory.set_chat_state(self.peer_id(event.data.jid), muted=event.data.action.muted)

    def message_kind(self, body):
        for field, kind in (("imageMessage", "image"), ("documentMessage", "document"),
                            ("audioMessage", "audio"), ("videoMessage", "video"),
                            ("stickerMessage", "sticker")):
            if body.HasField(field):
                return kind
        return "text" if body.conversation or body.extendedTextMessage.text else "other"

    def message_text(self, body):
        return (body.conversation or body.extendedTextMessage.text or body.imageMessage.caption
                or body.documentMessage.caption or body.videoMessage.caption or None)

    async def on_message(self, client, event):
        data = event.data
        info = data.message_info
        chat_id = self.peer_id(info.source.chat)
        sender = self.peer_id(info.source.sender)
        body = data.raw_proto
        exists = self.directory.get_message(chat_id, info.id) is not None
        self.directory.add_message(chat_id, info.id, sender, self.message_text(body),
                                   int(info.timestamp.timestamp()), info.source.is_from_me)
        self.directory.set_message_data(chat_id, info.id, body.SerializeToString(),
                                        self.message_kind(body))
        pairs = [(info.source.sender, info.source.sender_alt)]
        if info.source.is_from_me and not chat_id.endswith("@g.us"):
            pairs.append((info.source.chat, info.source.recipient_alt))
        for primary, alternate in pairs:
            if alternate is not None:
                identifiers = (self.peer_id(primary), self.peer_id(alternate))
                pn = next((v for v in identifiers if v.endswith("@s.whatsapp.net")), "")
                lid = next((v for v in identifiers if v.endswith("@lid")), "")
                if pn and lid:
                    self.directory.add_alias(pn, lid)
        if not exists and not info.source.is_from_me:
            self.directory.set_chat_state(chat_id, unread=True)
            self.events.emit("message_received", chat_id=chat_id, message_id=info.id)
        if info.push_name:
            self.directory.add_contact(sender, info.push_name, source="profile")

    async def on_receipt(self, client, event):
        statuses = {ReceiptType.Delivered: "delivered", ReceiptType.Read: "read",
                    ReceiptType.Played: "played"}
        status = statuses.get(event.receipt_type)
        if status is None or event.source is None:
            return
        chat_id = self.peer_id(event.source.chat)
        sender = self.peer_id(event.source.sender)
        for message_id in event.message_ids:
            self.directory.add_receipt(chat_id, message_id, sender, status,
                                       int(event.timestamp.timestamp()))
            self.events.emit("message_receipt", chat_id=chat_id, message_id=message_id,
                             sender=sender, status=status)

    def jid(self, value):
        return JID(*value.rsplit("@", 1))

    def record_sent(self, result, chat_id, text=None, proto=None, kind="text"):
        message_id = result.message_id
        self.directory.add_message(chat_id, message_id, "self", text, int(time.time()), True)
        self.directory.set_message_data(chat_id, message_id, proto, kind, "submitted")
        self.events.emit("message_submitted", chat_id=chat_id, message_id=message_id)
        return {"id": message_id, "chat_id": chat_id, "status": "submitted"}

    async def send_text(self, chat_id, text):
        result = await self.session.client.send_text(self.jid(chat_id), text)
        return self.record_sent(result, chat_id, text, Message(conversation=text).SerializeToString())

    def get_stored_message(self, chat_id, message_id):
        row = self.directory.get_message(chat_id, message_id)
        if row is not None and row["id"].startswith("<builtins.SendResult "):
            raise HTTPException(409, detail="The early probe did not retain this message ID")
        if row is None:
            raise HTTPBadRequest("message_id must identify a synchronized message in this chat")
        return row

    def get_message_proto(self, row):
        if not row["proto"]:
            raise HTTPException(409, detail="Message payload was not retained; synchronize it again")
        return Message.FromString(row["proto"])

    async def reply_message(self, chat_id, message_id, text):
        row = self.get_stored_message(chat_id, message_id)
        original = self.get_message_proto(row)
        message = Message()
        message.extendedTextMessage.text = text
        context = message.extendedTextMessage.contextInfo
        context.stanzaId = row["id"]
        context.remoteJid = row["chat_id"]
        if row["from_me"] and row["sender"] == "self":
            own = self.session.client.advanced.get_pn() if chat_id.endswith("@s.whatsapp.net") \
                else self.session.client.advanced.get_lid() or self.session.client.advanced.get_pn()
            if own is None:
                raise HTTPException(409, detail="Own account identity is unavailable")
            context.participant = self.peer_id(own)
        else:
            context.participant = row["sender"]
        if "@" not in context.participant:
            raise HTTPException(409, detail="Original sender identity was not retained")
        context.quotedMessage.CopyFrom(original)
        result = await self.session.client.send_message(self.jid(row["chat_id"]), message)
        return self.record_sent(result, row["chat_id"], text, message.SerializeToString())

    async def react_message(self, chat_id, message_id, reaction):
        row = self.get_stored_message(chat_id, message_id)
        participant = self.jid(row["sender"]) if row["chat_id"].endswith("@g.us") \
            and not row["from_me"] else None
        message_id = await self.session.client.chat_actions.react_message(
            self.jid(row["chat_id"]), row["id"], reaction, bool(row["from_me"]), participant)
        return {"id": message_id, "chat_id": row["chat_id"], "status": "submitted"}

    async def send_media(self, chat_id, kind, content, mimetype, filename, caption):
        client = self.session.client
        if kind == "image":
            result = await client.send_photo(self.jid(chat_id), content,
                                             mimetype=mimetype, caption=caption)
        elif kind == "document":
            result = await client.send_document(self.jid(chat_id), content, mimetype=mimetype,
                                                file_name=filename or "attachment", caption=caption)
        else:
            result = await client.send_audio(self.jid(chat_id), content, mimetype=mimetype)
        return self.record_sent(result, chat_id, caption or None, kind=kind)

    async def download_media(self, chat_id, message_id, limit):
        row = self.get_stored_message(chat_id, message_id)
        body = self.get_message_proto(row)
        fields = {"image": "imageMessage", "document": "documentMessage", "audio": "audioMessage",
                  "video": "videoMessage", "sticker": "stickerMessage"}
        field = fields.get(row["kind"])
        if field is None:
            raise HTTPBadRequest("Message has no supported media payload")
        media = getattr(body, field)
        if not media.fileLength or media.fileLength > limit:
            raise HTTPBadRequest("Media length is unknown or exceeds the 5 MiB limit")
        content = await self.session.client.download_media(media)
        if len(content) > limit:
            raise HTTPBadRequest("Downloaded media exceeds the 5 MiB limit")
        return {"content_base64": base64.b64encode(content).decode("ascii"),
                "mimetype": media.mimetype, "size": len(content), "kind": row["kind"]}

    def get_message_range(self, chat_id):
        page = self.directory.get_messages(chat_id, 100, 0)
        if not page["items"]:
            return None
        actions = self.session.client.chat_actions
        keys = []
        for row in page["items"]:
            participant = self.jid(row["sender"]) if row["chat_id"].endswith("@g.us") \
                and not row["from_me"] and "@" in row["sender"] else None
            key = actions.build_message_key(row["id"], self.jid(row["chat_id"]),
                                            bool(row["from_me"]), participant)
            keys.append((key, row["timestamp"]))
        return actions.build_message_range(page["items"][0]["timestamp"], None, keys)

    async def set_chat_flag(self, operation, chat_id, value):
        actions = self.session.client.chat_actions
        jid = self.jid(chat_id)
        if operation == "mark_read":
            await actions.mark_chat_as_read(jid, value, self.get_message_range(chat_id))
            for row in self.directory.get_chat(chat_id)["items"]:
                self.directory.set_chat_state(row["id"], unread=not value)
        elif operation == "archive_chat":
            method = actions.archive_chat if value else actions.unarchive_chat
            await method(jid, self.get_message_range(chat_id))
            self.directory.add_chat(chat_id, archived=value)
        else:
            method = actions.mute_chat if value else actions.unmute_chat
            await method(jid)
            self.directory.set_chat_state(chat_id, muted=value)
        return {"chat_id": chat_id, "status": "returned", "value": value}

    def group_details(self, group):
        return {"id": self.peer_id(group.id), "title": group.subject,
                "description": group.description, "size": group.size,
                "locked": group.is_locked, "announcement": group.is_announcement}

    async def get_group(self, chat_id):
        group = await self.session.client.groups.get_metadata(self.jid(chat_id))
        self.directory.add_chat(chat_id, group.subject or "", source="group_metadata")
        return self.group_details(group)

    async def get_group_members(self, chat_id, limit, offset):
        group = await self.session.client.groups.get_metadata(self.jid(chat_id))
        members = sorted(group.participants, key=lambda item: self.peer_id(item.jid))
        return {"items": [{"id": self.peer_id(item.jid), "admin": item.is_admin}
                          for item in members[offset:offset+limit]],
                "next_offset": offset+limit if len(members) > offset+limit else None,
                "coverage": "provider_group_snapshot"}

    async def create_group(self, title, participants):
        options = CreateGroupOptions(title, [GroupParticipantOptions(self.jid(jid))
                                             for jid in participants])
        result = await self.session.client.groups.create_group(options)
        jid = self.peer_id(result.gid)
        self.directory.add_chat(jid, title, source="created_group")
        return {"chat_id": jid, "status": "created", "policy_changed": False}

    async def update_group_members(self, chat_id, participants, action):
        methods = {"add": self.session.client.groups.add_participants,
                   "remove": self.session.client.groups.remove_participants,
                   "promote": self.session.client.groups.promote_participants,
                   "demote": self.session.client.groups.demote_participants}
        result = await methods[action](self.jid(chat_id), [self.jid(jid) for jid in participants])
        return {"chat_id": chat_id, "action": action,
                "results": [{"id": self.peer_id(item.jid), "status": item.status,
                             "error": item.error} for item in result] if result is not None else [],
                "status": "returned"}

    async def request_history(self, chat_id, message_id, count):
        row = self.get_stored_message(chat_id, message_id)
        request_id = await self.session.client.advanced.fetch_message_history(
            self.jid(row["chat_id"]), row["id"], bool(row["from_me"]), row["timestamp"] * 1000, count)
        return {"request_id": request_id, "status": "requested",
                "coverage": "asynchronous_partial_history"}
