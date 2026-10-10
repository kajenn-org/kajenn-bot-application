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
import time

from tryx.backend import SqliteStore
from tryx.client import Tryx
from tryx.events import (
    EvArchiveUpdate, EvConnected, EvContactUpdate, EvHistorySync,
    EvMarkChatAsReadUpdate, EvMessage, EvMuteUpdate, EvPairingQrCode, EvPinUpdate,
)
from tryx.types import JID

from examples.whatsapp_account.directory import _Directory
from examples.whatsapp_account.session import _Session


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

    def create_runtime(self, database):
        self.directory = _Directory(self.path / "directory.db")
        runtime = Tryx(SqliteStore(str(database), 0))
        if not callable(getattr(runtime.get_client().advanced, "resync_directory", None)):
            raise RuntimeError("The Tryx directory patch is required")
        for event, handler in (
            (EvConnected, self.on_connected), (EvPairingQrCode, self.on_pairing_required),
            (EvContactUpdate, self.on_contact), (EvHistorySync, self.on_history),
            (EvMessage, self.on_message), (EvArchiveUpdate, self.on_archive),
            (EvMarkChatAsReadUpdate, self.on_chat_state),
            (EvMuteUpdate, self.on_chat_state), (EvPinUpdate, self.on_chat_state),
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
        if self.directory is not None:
            self.directory.close()
            self.directory = None

    async def on_pairing_required(self, client, event):
        self.sync_status = "pairing_required"

    async def on_connected(self, client, event):
        self.sync_status = "connected"
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
            for item in chat.messages:
                message = item.message
                body = message.message
                text = body.conversation or body.extendedTextMessage.text or None
                self.directory.add_message(
                    chat.id, message.key.id, message.participant or message.key.participant,
                    text, message.messageTimestamp, message.key.fromMe,
                )

    async def on_message(self, client, event):
        data = event.data
        info = data.message_info
        self.directory.add_message(
            self.peer_id(info.source.chat), info.id, self.peer_id(info.source.sender),
            data.get_text(), int(info.timestamp.timestamp()), info.source.is_from_me,
        )
        if info.push_name:
            self.directory.add_contact(self.peer_id(info.source.sender), info.push_name, source="profile")

    async def send_text(self, chat_id, text):
        message_id = await self.session.client.send_text(JID(*chat_id.rsplit("@", 1)), text)
        self.directory.add_message(chat_id, str(message_id), "self", text, int(time.time()), True)
        return message_id
