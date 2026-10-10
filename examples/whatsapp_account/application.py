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

"""Policy-checked personal account operations; transport details live in the adapter."""

import asyncio
import base64
import binascii
import copy
import inspect
import re
import time
from contextvars import ContextVar

from kajenn.applications.mcp import McpOpenApiApplication
from kajenn.exceptions import HTTPBadRequest, HTTPException, HTTPForbidden
from kajenn.lifespan import FatalBootError

from examples.whatsapp_account.outbox import _Outbox
from examples.whatsapp_account.commands import _ExtendedCommands
from examples.whatsapp_account.policy import _Policy, JID_PATTERN
from examples.whatsapp_account.routes import _Operations

MEDIA_LIMIT = 5 * 1024 * 1024


class WhatsAppAccountApplication(_ExtendedCommands, McpOpenApiApplication):
    def __init__(self, *, connection_factory, policy=None, transcriber=None, **kwargs):
        self.connection = connection_factory()
        self.initial_policy = policy
        self.transcriber = transcriber
        self.access = None
        self.outbox = None
        self.lock = asyncio.Lock()
        self.scope_context = ContextVar("whatsapp_account_scope", default=None)
        kwargs.setdefault("mcp_name_segment", "_mcp")
        kwargs.setdefault("api_name", "_account")
        super().__init__(routing_class=_Operations(self), **kwargs)
        self.route.router_at_path("_meta").auth.configure(
            rule="whatsapp_account_read|whatsapp_account_write|whatsapp_account_manage|admin")

    async def __call__(self, scope, receive, send):
        token = self.scope_context.set(scope)
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.scope_context.reset(token)

    @property
    def actor(self):
        scope = self.scope_context.get()
        if scope is None:
            return "trusted-python"
        avatar = scope.get("auth")
        return str(avatar.identity) if avatar is not None else "anonymous"

    async def on_startup(self):
        try:
            await self.connection.start()
            self.access = _Policy(self.connection.directory, self.initial_policy)
            self.outbox = _Outbox(self)
        except Exception as error:
            await self.connection.stop()
            raise FatalBootError("WhatsApp account startup failed") from error

    async def on_shutdown(self):
        if self.outbox is not None:
            await self.outbox.stop()
        async with self.lock:
            await self.connection.stop()

    @property
    def status(self):
        return {"connected": self.connection.connected,
                "counts": self.connection.directory.counts,
                "coverage": "observed_and_synchronized_subset",
                "sync": self.connection.sync_status,
                "callback_errors": getattr(self.connection, "callback_errors", 0),
                "callback_failures": getattr(self.connection, "callback_failures", {}),
                "event_delivery": getattr(self.connection, "event_status", {})}

    async def run_operation(self, operation, chat_id, handler, *args, remote=False, timeout=45):
        async with self.lock:
            store = self.connection.directory
            try:
                self.access.require(operation, chat_id)
            except HTTPForbidden:
                store.add_audit(self.actor, operation, chat_id, "denied")
                raise
            if remote and not self.connection.connected:
                raise HTTPException(503, detail="WhatsApp is disconnected")
            store.add_audit(self.actor, operation, chat_id, "started")
            try:
                async with asyncio.timeout(timeout):
                    result = handler(*args)
                    if inspect.isawaitable(result):
                        result = await result
            except HTTPException:
                store.add_audit(self.actor, operation, chat_id, "rejected")
                raise
            except asyncio.CancelledError:
                store.add_audit(self.actor, operation, chat_id, "unconfirmed")
                raise
            except Exception as error:
                store.add_audit(self.actor, operation, chat_id,
                                "unconfirmed" if remote else "failed")
                raise HTTPException(502, detail="Account operation failed; do not retry a "
                                    "mutation without checking its outcome") from error
            store.add_audit(self.actor, operation, chat_id, "returned")
            return result

    def validate_page(self, query, limit, offset):
        if not isinstance(query, str) or len(query) > 200:
            raise HTTPBadRequest("query must be a string of at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPBadRequest("limit must be an integer between 1 and 100")
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise HTTPBadRequest("offset must be a nonnegative bounded integer")

    def validate_text(self, text, limit=4000, empty=False):
        if not isinstance(text, str) or len(text) > limit or (not empty and not text.strip()):
            raise HTTPBadRequest(f"text must be a string of at most {limit} characters")

    def validate_peer(self, chat_id, known=True):
        if not isinstance(chat_id, str) or not JID_PATTERN.fullmatch(chat_id):
            raise HTTPBadRequest("chat_id must be an exact WhatsApp JID")
        if known and chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("Use the dedicated channel commands for newsletters")
        if known and not self.connection.directory.known_peer(chat_id):
            raise HTTPBadRequest("chat_id must be returned by contacts or chats")

    def validate_group(self, chat_id):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@g.us"):
            raise HTTPBadRequest("chat_id must identify a group")

    def validate_message(self, chat_id, message_id):
        self.validate_peer(chat_id)
        self.validate_text(message_id, 200)

    async def get_status(self):
        return await self.run_operation("get_status", None, self.read_status)

    def read_status(self):
        return self.status

    async def get_sync_status(self):
        return await self.run_operation("get_sync_status", None, self.read_status)

    async def get_contacts(self, query="", limit=50, offset=0):
        self.validate_page(query, limit, offset)
        return await self.run_operation("get_contacts", None, self.connection.directory.get_contacts,
                                  query, limit, offset)

    async def get_chats(self, query="", limit=50, offset=0, include_archived=False):
        self.validate_page(query, limit, offset)
        if type(include_archived) is not bool:
            raise HTTPBadRequest("include_archived must be a boolean")
        return await self.run_operation("get_chats", None, self.connection.directory.get_chats,
                                  query, limit, offset, include_archived)

    async def get_chat(self, chat_id):
        self.validate_peer(chat_id)
        return await self.run_operation("get_chat", chat_id, self.connection.directory.get_chat, chat_id)

    async def get_unread(self, limit=50, offset=0):
        self.validate_page("", limit, offset)
        return await self.run_operation("get_unread", None, self.connection.directory.get_unread,
                                  limit, offset)

    async def get_messages(self, chat_id, limit=50, offset=0):
        self.validate_page("", limit, offset)
        self.validate_peer(chat_id)
        return await self.run_operation("get_messages", chat_id, self.connection.directory.get_messages,
                                  chat_id, limit, offset)

    async def search_messages(self, query, chat_id=None, limit=50, offset=0):
        self.validate_page(query, limit, offset)
        if chat_id is not None:
            self.validate_peer(chat_id)
        return await self.run_operation("search_messages", chat_id,
                                  self.connection.directory.search_messages,
                                  query, chat_id, limit, offset)

    async def get_message_status(self, chat_id, message_id):
        self.validate_message(chat_id, message_id)
        return await self.run_operation("get_message_status", chat_id,
                                  self.connection.directory.get_message_status, chat_id, message_id)

    async def send_text(self, chat_id, text):
        self.validate_peer(chat_id)
        self.validate_text(text)
        return await self.run_operation("send_text", chat_id, self.connection.send_text,
                                  chat_id, text, remote=True)

    async def reply_message(self, chat_id, message_id, text):
        self.validate_message(chat_id, message_id)
        self.validate_text(text)
        return await self.run_operation("reply_message", chat_id, self.connection.reply_message,
                                  chat_id, message_id, text, remote=True)

    async def react_message(self, chat_id, message_id, reaction):
        self.validate_message(chat_id, message_id)
        self.validate_text(reaction, 32, empty=True)
        return await self.run_operation("react_message", chat_id, self.connection.react_message,
                                  chat_id, message_id, reaction, remote=True)

    async def send_media(self, chat_id, kind, content_base64, mimetype, filename="", caption=""):
        self.validate_peer(chat_id)
        if kind not in ("image", "document", "audio", "voice", "video", "gif", "sticker"):
            raise HTTPBadRequest("Unsupported media kind")
        self.validate_text(mimetype, 100)
        self.validate_text(filename, 255, empty=True)
        self.validate_text(caption, 1024, empty=True)
        if kind in ("audio", "voice", "sticker") and caption:
            raise HTTPBadRequest("audio captions are not supported")
        if any(c in filename for c in ("/", "\\", "\x00")):
            raise HTTPBadRequest("filename must be a display name, not a path")
        if not isinstance(content_base64, str) or len(content_base64) > 4*((MEDIA_LIMIT+2)//3):
            raise HTTPBadRequest("media exceeds the 5 MiB limit")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPBadRequest("content_base64 must contain valid base64") from error
        if not content or len(content) > MEDIA_LIMIT:
            raise HTTPBadRequest("media must contain between 1 byte and 5 MiB")
        return await self.run_operation("send_media", chat_id, self.connection.send_media,
                                  chat_id, kind, content, mimetype, filename, caption, remote=True)

    async def download_media(self, chat_id, message_id):
        self.validate_message(chat_id, message_id)
        return await self.run_operation("download_media", chat_id, self.connection.download_media,
                                  chat_id, message_id, MEDIA_LIMIT, remote=True)

    async def mark_read(self, chat_id, read=True):
        return await self.set_chat_flag("mark_read", chat_id, read)

    async def archive_chat(self, chat_id, archived=True):
        return await self.set_chat_flag("archive_chat", chat_id, archived)

    async def mute_chat(self, chat_id, muted=True):
        return await self.set_chat_flag("mute_chat", chat_id, muted)

    async def set_chat_flag(self, operation, chat_id, value):
        self.validate_peer(chat_id)
        if type(value) is not bool:
            raise HTTPBadRequest("chat state must be a boolean")
        return await self.run_operation(operation, chat_id, self.connection.set_chat_flag,
                                  operation, chat_id, value, remote=True)

    async def get_group(self, chat_id):
        self.validate_group(chat_id)
        return await self.run_operation("get_group", chat_id, self.connection.get_group,
                                  chat_id, remote=True)

    async def get_group_members(self, chat_id, limit=50, offset=0):
        self.validate_group(chat_id)
        self.validate_page("", limit, offset)
        return await self.run_operation("get_group_members", chat_id, self.connection.get_group_members,
                                  chat_id, limit, offset, remote=True)

    def validate_participants(self, participants):
        if not isinstance(participants, list) or not 1 <= len(participants) <= 100:
            raise HTTPBadRequest("participants must contain between 1 and 100 exact JIDs")
        for jid in participants:
            self.validate_peer(jid, known=False)
            if not jid.endswith(("@s.whatsapp.net", "@lid")):
                raise HTTPBadRequest("participants must be individual JIDs")
        if len(set(participants)) != len(participants):
            raise HTTPBadRequest("participants must be distinct")

    async def create_group(self, title, participants):
        self.validate_text(title, 100)
        self.validate_participants(participants)
        return await self.run_operation("create_group", None, self.create_checked_group,
                                  title, participants, remote=True)

    async def create_checked_group(self, title, participants):
        for jid in participants:
            self.validate_peer(jid)
            if not self.access.allowed(jid, "write"):
                raise HTTPForbidden("participant is outside permitted chats")
        return await self.connection.create_group(title, participants)

    async def update_group_members(self, chat_id, participants, action):
        self.validate_group(chat_id)
        self.validate_participants(participants)
        if action not in ("add", "remove", "promote", "demote"):
            raise HTTPBadRequest("action must be add, remove, promote or demote")
        return await self.run_operation("update_group_members", chat_id, self.update_checked_members,
                                  chat_id, participants, action, remote=True)

    async def update_checked_members(self, chat_id, participants, action):
        if action == "add":
            for jid in participants:
                self.validate_peer(jid)
                if not self.access.allowed(jid, "write"):
                    raise HTTPForbidden("participant is outside permitted chats")
        return await self.connection.update_group_members(chat_id, participants, action)

    async def request_history(self, chat_id, message_id, count=50):
        self.validate_message(chat_id, message_id)
        self.validate_page("", count, 0)
        return await self.run_operation("request_history", chat_id, self.connection.request_history,
                                  chat_id, message_id, count, remote=True)

    async def get_policy(self):
        async with self.lock:
            return copy.deepcopy(self.access.value)

    async def set_policy(self, policy):
        async with self.lock:
            self.connection.directory.add_audit(self.actor, "set_policy", None, "started")
            result = self.access.set_policy(policy)
            self.connection.directory.add_audit(self.actor, "set_policy", None, "returned")
            return result

    async def get_audit_log(self, limit=50, offset=0):
        self.validate_page("", limit, offset)
        async with self.lock:
            return self.connection.directory.get_audit_log(limit, offset)


    def subscribe_events(self, callback, kinds=None):
        """Register a trusted async Python listener, filtered by current read policy."""
        async def permitted(event):
            chat_id = event.get("chat_id")
            if chat_id is None or self.access.allowed(chat_id, "read"):
                await callback(event)
        return self.connection.events.subscribe_events(permitted, kinds)

    async def unsubscribe_events(self, identifier):
        await self.connection.events.unsubscribe_events(identifier)

    async def schedule_message(self, chat_id, text, due, approval_required=True):
        self.validate_peer(chat_id)
        self.validate_text(text)
        if type(due) is not int or not time.time() <= due <= time.time() + 366 * 86400:
            raise HTTPBadRequest("due must be a future Unix timestamp within one year")
        if type(approval_required) is not bool:
            raise HTTPBadRequest("approval_required must be boolean")
        return await self.run_operation("schedule_message", chat_id, self.outbox.enqueue,
                                        chat_id, text, due, approval_required)

    async def get_outbox(self, limit=50, offset=0):
        self.validate_page("", limit, offset)
        return await self.run_operation("get_outbox", None, self.outbox.list_jobs, limit, offset)

    async def decide_message(self, job_id, decision):
        self.validate_text(job_id, 64)
        if decision not in ("approve", "reject", "cancel"):
            raise HTTPBadRequest("decision must be approve, reject or cancel")
        return await self.run_operation("decide_message", None, self.outbox.decide, job_id, decision)

    async def get_events(self, after_id=0, limit=50):
        self.validate_page("", limit, 0)
        if type(after_id) is not int or not 0 <= after_id <= 9223372036854775807:
            raise HTTPBadRequest("after_id must be a nonnegative bounded cursor")
        return await self.run_operation("get_events", None, self.connection.directory.get_events,
                                        after_id, limit)

    async def transcribe_message(self, chat_id, message_id, language="it"):
        self.validate_message(chat_id, message_id)
        if not isinstance(language, str) or not re.fullmatch(r"(?:auto|[a-z]{2,3})", language):
            raise HTTPBadRequest("language must be a lowercase language code or auto")
        return await self.run_operation("transcribe_message", chat_id, self.transcribe_audio,
                                        chat_id, message_id, language, remote=True, timeout=180)

    async def transcribe_audio(self, chat_id, message_id, language):
        if self.transcriber is None:
            raise HTTPException(503, detail="No transcription engine is configured")
        row = self.connection.directory.get_message(chat_id, message_id)
        if row is None or row["kind"] not in ("audio", "voice"):
            raise HTTPBadRequest("The selected message must contain retained audio")
        media = await self.connection.download_media(chat_id, message_id, MEDIA_LIMIT)
        content = base64.b64decode(media["content_base64"], validate=True)
        result = await self.transcriber.transcribe(content, media["mimetype"], language)
        return {"chat_id": chat_id, "message_id": message_id, **result}
