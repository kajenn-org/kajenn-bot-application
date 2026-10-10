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

"""A personal Telegram account, mounted separately from bot applications.

One process owns the encrypted local session and policy. Login is a trusted local
Python operation, never a REST/MCP tool. Operational routes require the server's
telegram_account role; policy changes and revocation require admin and are REST
only. Every provider operation checks the same operation and chat grants, even
when called from Python. Empty grants deny access. A policy may explicitly grant
all operations and chats. Telegram's own permissions remain authoritative.

MTProto uses a connected client, with no webhook or application polling loop.
History and member reads are bounded and paginated. Provider operations are
serialized with policy and lifecycle changes; status reads never wait for them.
Startup preserves stored authorization on transient errors. Encrypted state is
written only when its contents change. Tools send supplied document
bytes, never arbitrary server files. The calling client owns user confirmation;
the server enforces configured grants and offers explicit approval for queued texts.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import io
import re
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from genro_bag import BagResolver

from genro_builders.builder import element
from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.sessions import StringSession

from kajenn.application import ApplicationGrammar
from kajenn.applications.mcp import McpOpenApiApplication
from kajenn.exceptions import HTTPBadRequest, HTTPException, HTTPForbidden
from kajenn.response import Response
from .transcription import _LocalTranscriber
from .account_store import _AccountStore
from .account_journal import _AccountJournal
from .account_routes import _AccountOperations, _AccountAdministration

# Chat grant required by each operation; None marks an account-wide operation.
ACCOUNT_OPERATIONS = {
    "get_events": None,
    "get_audit_log": None,
    "request_message": "write",
    "get_message_requests": None,
    "decide_message": "write",
    "download_media": 'read',
    "transcribe_message": 'read',
    "react_message": 'write',
    "mark_read": 'write',
    "archive_chat": 'write',
    "mute_chat": 'write',
    "pin_message": 'admin',
    "block_contact": 'admin',
    "set_profile": None,
    "get_contacts": None,
    "forward_message": 'write',
    "schedule_message": 'write',
    "get_scheduled_messages": 'read',
    "cancel_scheduled_message": 'write',
    "create_poll": 'write',
    "get_poll": 'read',
    "vote_poll": 'write',
    "send_media": 'write',

    "get_chats": "read",
    "get_messages": "read",
    "get_members": "read",
    "send_text": "write",
    "send_document": "write",
    "edit_message": "write",
    "delete_messages": "write",
    "create_channel": None,
    "create_group": None,
    "set_chat_details": "admin",
    "invite_members": "admin",
    "remove_member": "admin",
    "set_member_admin": "admin",
}
ADMIN_RIGHTS = {
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "manage_call",
}
DOCUMENT_LIMIT = 5 * 1024 * 1024
PEER_LOOKUP_LIMIT = 100


class _AccountGrammar(ApplicationGrammar):
    @element(sub_tags="", node_label="telegram_account")
    def telegram_account(
        self,
        api_id: int | BagResolver,
        api_hash: str | BagResolver,
        session_path: str | BagResolver,
        encryption_key: str | BagResolver,
        policy: dict | None = None,
        transcription_model: str | BagResolver | None = None,
    ) -> None:
        """Local account credentials, encrypted state and initial operation/chat grants."""


class TelegramAccountApplication(McpOpenApiApplication):
    """Own one personal Telegram session with policy-checked REST/MCP operations."""

    grammar = _AccountGrammar

    def __init__(
        self,
        *,
        api_id=None,
        api_hash=None,
        session_path=None,
        encryption_key=None,
        policy=None,
        client_factory=TelegramClient,
        transcriber=None,
        transcription_model=None,
        **kwargs,
    ):
        self._settings = dict(
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
            encryption_key=encryption_key,
            policy=policy,
            transcription_model=transcription_model,
        )
        self.transcriber = transcriber
        self._factory = client_factory
        self._client = None
        self._store = None
        self._saved_state = None
        self._policy = {"operations": [], "chats": {}}
        self._account = None
        self._authorized = False
        self._phone = None
        self._phone_code_hash = None
        self._password_pending = False
        self._lock = asyncio.Lock()
        self.journal = None
        self.scope_context = ContextVar("telegram_account_scope", default=None)
        self.event_errors = 0
        self.unscoped_deletions = 0
        kwargs.setdefault("mcp_name_segment", "_mcp")
        kwargs.setdefault("api_name", "_account")
        super().__init__(routing_class=_AccountOperations(self), **kwargs)
        self.route.add_branches({"name": "_admin", "instance": _AccountAdministration(self)})
        self.route.router_at_path("_meta").auth.configure(rule="admin|telegram_account")

    def _setting(self, name):
        value = self._settings[name]
        return value if value is not None else self.config(f"telegram_account.{name}", default=None)

    @property
    def session_path(self) -> Path:
        value = self._setting("session_path")
        if not value:
            raise ValueError("telegram_account.session_path is required")
        return Path(value).expanduser().absolute()

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("account application has not started")
        return self._client

    @property
    def policy(self) -> dict[str, Any]:
        return copy.deepcopy(self._policy)

    @property
    def store(self):
        if self._store is None:
            raise RuntimeError("account storage is not open")
        return self._store

    @property
    def lock(self):
        return self._lock

    async def __call__(self, scope, receive, send):
        segment = scope["path"].strip("/").partition("/")[0]
        if segment in (self.api_name, "_admin") and scope.get("method") != "POST":
            await Response("Method Not Allowed", status_code=405, headers={"Allow": "POST"})(
                scope, receive, send
            )
            return
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

    @property
    def account_id(self):
        return self._account["id"] if self._account else None

    async def on_startup(self):
        async with self.lock:
            if self._client is not None:
                return
            if self.transcriber is None and self._setting("transcription_model"):
                self.transcriber = _LocalTranscriber(self._setting("transcription_model"))
            api_id, api_hash = self._setting("api_id"), self._setting("api_hash")
            if (
                not isinstance(api_id, int)
                or isinstance(api_id, bool)
                or api_id <= 0
                or not api_hash
            ):
                raise ValueError("telegram_account requires api_id and api_hash")
            key = self._setting("encryption_key")
            if not key:
                raise ValueError("telegram_account.encryption_key is required")
            self._store = _AccountStore(self.session_path, key)
            saved = self.store.open()
            self._saved_state = copy.deepcopy(saved)
            self._authorized = False
            self._account = None
            try:
                self._policy = self._validate_policy(
                    saved["policy"]
                    if saved
                    else (self._setting("policy") or {"operations": [], "chats": {}})
                )
                self._client = self._new_client(saved["session"] if saved else "")
                await self.client.connect()
                # Telethon's convenience probe hides every RPCError, including
                # flood waits and server failures. Only a 401 invalidates a session.
                try:
                    await self.client(functions.updates.GetStateRequest())
                except errors.UnauthorizedError:
                    self._authorized = False
                else:
                    self._authorized = True
                if self._authorized:
                    me = await self.client.get_me()
                    if me is None or me.bot or (saved and saved.get("account_id") != me.id):
                        raise ValueError(
                            "session does not belong to the configured personal account"
                        )
                    self._account = self._person(me)
                self.journal = _AccountJournal(
                    self.session_path.with_name(self.session_path.name + ".journal.enc"), key)
                self.journal.open()
                self._save()
            except BaseException:
                try:
                    if self._client is not None:
                        await self.client.disconnect()
                finally:
                    if self.journal is not None:
                        self.journal.close()
                        self.journal = None
                    self._client = None
                    self._authorized = False
                    self._account = None
                    self.store.close()
                    self._store = None
                raise

    async def on_shutdown(self):
        async with self.lock:
            if self._client is not None:
                try:
                    self._save()
                finally:
                    try:
                        await self.client.disconnect()
                    finally:
                        self._client = None
                        if self.journal is not None:
                            self.journal.close()
                            self.journal = None
                        self.store.close()
                        self._store = None
                        self._clear_login()

    def _new_client(self, session=""):
        client = self._factory(
            StringSession(session),
            self._setting("api_id"),
            self._setting("api_hash"),
            device_model="kajenn Telegram account",
            receive_updates=True,
            flood_sleep_threshold=0,
            request_retries=0,
            connection_retries=2,
            raise_last_call_error=True,
        )
        client.add_event_handler(self._record_event, events.NewMessage())
        client.add_event_handler(self._record_event, events.MessageEdited())
        client.add_event_handler(self._record_event, events.MessageDeleted())
        return client

    def _save(self, policy=None):
        state = {
            "session": self.client.session.save() if self._authorized else "",
            "account_id": self._account["id"] if self._account else None,
            "policy": policy if policy is not None else self.policy,
        }
        if state != self._saved_state:
            self.store.save(state)
            self._saved_state = copy.deepcopy(state)

    def _clear_login(self):
        self._phone = self._phone_code_hash = None
        self._password_pending = False

    async def start_login(self, phone: str) -> dict:
        """Request a login code locally; no credential is returned or exposed as a tool."""
        async with self.lock:
            if self._authorized:
                raise HTTPBadRequest("revoke the existing session before changing account")
            self._clear_login()
            await self.client.disconnect()
            self._client = self._new_client()
            await self.client.connect()
            try:
                result = await self.client.send_code_request(phone)
            except errors.RPCError as exc:
                raise HTTPBadRequest(f"Telegram login failed: {type(exc).__name__}") from None
            self._phone, self._phone_code_hash = phone, result.phone_code_hash
            return {"state": "code_required"}

    async def complete_login(self, *, code: str | None = None, password: str | None = None) -> dict:
        """Complete local code/2FA login and persist the encrypted session."""
        async with self.lock:
            if not self._phone or (password is not None and not self._password_pending):
                raise HTTPBadRequest("start the login flow first")
            try:
                if password is not None:
                    me = await self.client.sign_in(password=password)
                else:
                    me = await self.client.sign_in(
                        phone=self._phone, code=code, phone_code_hash=self._phone_code_hash
                    )
            except errors.SessionPasswordNeededError:
                self._password_pending = True
                return {"state": "password_required"}
            except errors.RPCError as exc:
                raise HTTPBadRequest(f"Telegram login failed: {type(exc).__name__}") from None
            if me.bot:
                await self.client.log_out()
                raise HTTPBadRequest("a personal account is required")
            self._authorized = True
            self._account = self._person(me)
            self._clear_login()
            try:
                self._save()
            except BaseException:
                self._authorized = False
                self._account = None
                await self.client.log_out()
                raise
            return {"state": "authorized", "account": copy.deepcopy(self._account)}

    async def get_status(self) -> dict:
        # A synchronous snapshot has no await point and needs no provider lock.
        return {
            "connected": self.client.is_connected(),
            "authorized": self._authorized,
            "account": copy.deepcopy(self._account),
            "event_errors": self.event_errors,
            "unscoped_deletions": self.unscoped_deletions,
        }

    async def get_policy(self) -> dict:
        return self.policy

    def _validate_policy(self, policy):
        if not isinstance(policy, dict) or set(policy) != {"operations", "chats"}:
            raise HTTPBadRequest("policy requires operations and chats")
        operations, chats = policy["operations"], policy["chats"]
        if not isinstance(operations, list) or any(
            not isinstance(op, str) or op not in {*ACCOUNT_OPERATIONS, "*"} for op in operations
        ):
            raise HTTPBadRequest("unknown account operation")
        if not isinstance(chats, dict):
            raise HTTPBadRequest("chats must map numeric chat IDs or * to grants")
        for chat, grants in chats.items():
            if not isinstance(chat, str) or (
                chat != "*"
                and (not chat.lstrip("-").isdigit() or str(int(chat)) != chat or int(chat) == 0)
            ):
                raise HTTPBadRequest("chat keys must be canonical numeric IDs or *")
            if not isinstance(grants, list) or any(
                grant not in ("read", "write", "admin") for grant in grants
            ):
                raise HTTPBadRequest("chat grants must be read, write or admin")
        return copy.deepcopy(policy)

    async def set_policy(self, policy: dict) -> dict:
        """Replace grants durably; trusted Python or administrator-only REST."""
        validated = self._validate_policy(policy)
        async with self.lock:
            self._save(policy=validated)
            self._policy = validated
            return self.policy

    async def revoke_session(self) -> dict:
        """Log out this Telegram device and erase the stored authorization."""
        async with self.lock:
            if self._authorized:
                try:
                    if not await self.client.log_out():
                        raise HTTPException(503, "Telegram logout was not confirmed")
                except errors.UnauthorizedError:
                    pass
                except errors.RPCError as exc:
                    raise HTTPException(
                        502, f"Telegram logout failed: {type(exc).__name__}"
                    ) from None
            self._authorized = False
            self._account = None
            self._clear_login()
            self._save()
            return {"authorized": False}

    def _allowed_chat(self, chat_id, grant):
        chats = self.policy["chats"]
        # An exact entry replaces wildcard grants, permitting explicit exclusions.
        return grant in chats.get(str(chat_id), chats.get("*", []))

    def _require_permission(self, operation, chat_id):
        operations = self.policy["operations"]
        if operation not in operations and "*" not in operations:
            raise HTTPForbidden("account operation is not permitted")
        if chat_id is not None:
            if not isinstance(chat_id, int) or isinstance(chat_id, bool) or not chat_id:
                raise HTTPBadRequest("chat_id must be a nonzero numeric Telegram ID")
            if not self._allowed_chat(chat_id, ACCOUNT_OPERATIONS[operation]):
                raise HTTPForbidden("account operation is not permitted in this chat")

    async def _run(self, operation, chat_id, handler, *args, timeout=45) -> dict[str, Any]:
        async with self.lock:
            account_id = self.account_id
            self._audit(account_id, operation, chat_id, "started")
            try:
                result = await self._execute(operation, chat_id, handler, *args, timeout=timeout)
            except BaseException as exc:
                self._audit(account_id, operation, chat_id, type(exc).__name__)
                raise
            self._audit(account_id, operation, chat_id, "returned")
            return result

    def _audit(self, account_id, operation, chat_id, outcome):
        if self.journal is not None:
            self.journal.append("audit", account_id, operation=operation,
                                chat_id=chat_id if isinstance(chat_id, int) else None,
                                actor=self.actor, outcome=outcome)

    async def _execute(self, operation, chat_id, handler, *args, timeout=45) -> dict[str, Any]:
        self._require_permission(operation, chat_id)
        if not self._authorized:
            raise HTTPException(409, "personal Telegram account requires local login")
        try:
            async with asyncio.timeout(timeout):
                result: dict[str, Any] = await handler(*args)
            self._save()
            return result
        except errors.FloodWaitError as exc:
            raise HTTPException(
                429,
                f"Telegram rate limit; retry after {exc.seconds} seconds",
                headers=[(b"retry-after", str(exc.seconds).encode())],
            ) from None
        except errors.UnauthorizedError:
            self._authorized = False
            self._account = None
            self._save()
            raise HTTPException(
                409, "Telegram session was revoked; local login is required"
            ) from None
        except errors.RPCError as exc:
            raise HTTPException(
                502, f"Telegram rejected the operation: {type(exc).__name__}"
            ) from None
        except (TimeoutError, ConnectionError, OSError):
            raise HTTPException(
                503, "Telegram operation outcome is uncertain; inspect before retrying"
            ) from None

    async def _record_event(self, event):
        async with self.lock:
            if (self.journal is None or not self._authorized
                    or event.client is not self._client):
                return
            chat_id = event.chat_id
            if chat_id is None:
                if isinstance(event, events.MessageDeleted.Event):
                    self.unscoped_deletions += len(event.deleted_ids)
                return
            if not self._allowed_chat(chat_id, "read"):
                return
            if isinstance(event, events.MessageDeleted.Event):
                kind, ids = "message_deleted", event.deleted_ids
            elif isinstance(event, events.MessageEdited.Event):
                kind, ids = "message_edited", [event.message.id]
            else:
                kind, ids = "message_received", [event.message.id]
            try:
                for message_id in ids:
                    self.journal.append("events", self.account_id, kind=kind,
                                        chat_id=chat_id, message_id=message_id)
            except Exception:
                # Telethon logs callback exceptions; expose a content-free counter as well.
                self.event_errors += 1
                raise

    async def get_events(self, after_id: int = 0, limit: int = 100) -> dict:
        self._cursor(after_id)
        self._limit(limit)
        return await self._run("get_events", None, self._get_events, after_id, limit)

    async def _get_events(self, after_id, limit):
        return self.journal.page("events", self.account_id, after_id, limit,
                                 lambda item: self._allowed_chat(item["chat_id"], "read"))

    async def get_audit_log(self, after_id: int = 0, limit: int = 100) -> dict:
        self._cursor(after_id)
        self._limit(limit)
        # Reading audit must not append records: otherwise clients can never catch up.
        async with self.lock:
            self._require_permission("get_audit_log", None)
            if not self._authorized:
                raise HTTPException(409, "personal Telegram account requires local login")
            result: dict = self.journal.page("audit", self.account_id, after_id, limit,
                                     lambda item: item["chat_id"] is None
                                     or self._allowed_chat(item["chat_id"], "read"))
            return result

    def _cursor(self, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HTTPBadRequest("after_id must be a nonnegative integer")

    async def request_message(self, chat_id: int, text: str) -> dict:
        self._text(text)
        return await self._run("request_message", chat_id, self._request_message, chat_id, text)

    async def _request_message(self, chat_id, text):
        return self.journal.enqueue(self.account_id, chat_id, text, self.actor)

    async def get_message_requests(self, limit: int = 100, offset: int = 0) -> dict:
        self._limit(limit)
        self._offset(offset)
        return await self._run("get_message_requests", None, self._get_message_requests,
                               limit, offset)

    async def _get_message_requests(self, limit, offset):
        return self.journal.get_jobs(self.account_id, limit, offset,
                                     lambda item: self._allowed_chat(item["chat_id"], "read"))

    async def decide_message(self, request_id: str, decision: str) -> dict:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 100:
            raise HTTPBadRequest("request_id must identify a queued message")
        if decision not in ("approve", "reject", "cancel"):
            raise HTTPBadRequest("decision must be approve, reject or cancel")
        return await self._run("decide_message", None, self._decide_message,
                               request_id, decision)

    async def _decide_message(self, request_id, decision):
        job = self.journal.get_job(self.account_id, request_id)
        if job is None or not self._allowed_chat(job["chat_id"], "read"):
            raise HTTPException(404, "message request not found")
        self._require_permission("decide_message", job["chat_id"])
        if job["state"] != "pending":
            return job
        if decision != "approve":
            return self.journal.update_job(job, state="rejected" if decision == "reject"
                                           else "cancelled", decision_actor=self.actor)
        # Persist before touching the provider. Restart never resends a sending job.
        job = self.journal.update_job(job, state="sending", decision_actor=self.actor)
        try:
            result = await self._send_text(job["chat_id"], job["text"], None)
        except BaseException:
            self.journal.update_job(job, state="unconfirmed")
            raise
        return self.journal.update_job(job, state="submitted", message_id=result["id"])

    def _limit(self, value, maximum=100):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise HTTPBadRequest(f"limit must be between 1 and {maximum}")
        return value

    def _offset(self, value):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10000:
            raise HTTPBadRequest("offset must be an integer between 0 and 10000")

    def _description(self, value):
        if not isinstance(value, str) or len(value) > 255:
            raise HTTPBadRequest("description must be a string of at most 255 characters")

    def _date(self, value):
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError):
            raise HTTPBadRequest("dates require ISO 8601 with a timezone") from None

    def _person(self, person):
        return {
            "id": person.id,
            "username": person.username,
            "name": " ".join(filter(None, [person.first_name, getattr(person, "last_name", None)])),
            "bot": bool(person.bot),
        }

    def _message(self, message):
        return {
            "id": message.id,
            "chat_id": message.chat_id,
            "sender_id": message.sender_id,
            "date": message.date.isoformat(),
            "text": message.raw_text,
            "outgoing": bool(message.out),
            "has_media": message.media is not None,
        }

    async def get_chats(self, limit: int = 100, offset: int = 0) -> dict:
        """List readable dialogs with numeric IDs; names are display/search data only."""
        self._limit(limit)
        self._offset(offset)
        return await self._run("get_chats", None, self._get_chats, limit, offset)

    async def _get_chats(self, limit, offset):
        items = []
        count = 0
        more = False
        async for dialog in self.client.iter_dialogs(limit=offset + limit + 1):
            count += 1
            if count <= offset:
                continue
            if count > offset + limit:
                more = True
                break
            if self._allowed_chat(dialog.id, "read"):
                items.append(
                    {
                        "id": dialog.id,
                        "name": dialog.name,
                        "kind": "user"
                        if dialog.is_user
                        else "group"
                        if dialog.is_group
                        else "channel",
                    }
                )
        return {"items": items, "next_offset": offset + limit if more else None}

    async def get_messages(
        self,
        chat_id: int,
        limit: int = 100,
        before_id: int = 0,
        since: str | None = None,
        until: str | None = None,
        search: str | None = None,
    ) -> dict:
        """Read newest-first pages; preserve filters when following next_before_id."""
        self._limit(limit)
        if not isinstance(before_id, int) or isinstance(before_id, bool) or before_id < 0:
            raise HTTPBadRequest("before_id must be a nonnegative integer")
        start, end = self._date(since), self._date(until)
        if start and end and start >= end:
            raise HTTPBadRequest("since must precede until")
        return await self._run(
            "get_messages",
            chat_id,
            self._get_messages,
            chat_id,
            limit,
            before_id,
            start,
            end,
            search,
        )

    async def _entity(self, peer_id):
        """Resolve stable IDs after restart; StringSession does not persist entity hashes."""
        try:
            return await self.client.get_input_entity(peer_id)
        except ValueError:
            count = 0
            async for dialog in self.client.iter_dialogs(limit=PEER_LOOKUP_LIMIT):
                count += 1
                if dialog.id == peer_id:
                    return dialog.input_entity
                if count == PEER_LOOKUP_LIMIT:
                    raise HTTPException(
                        409,
                        "Telegram peer lookup reached its limit; page through get_chats "
                        "to load older dialogs before retrying",
                    )
            raise HTTPBadRequest("Telegram peer is not known to this account") from None

    async def _get_messages(self, chat_id, limit, before_id, start, end, search):
        entity = await self._entity(chat_id)
        items = []
        async for message in self.client.iter_messages(
            entity, limit=limit + 1, offset_id=before_id, offset_date=end, search=search
        ):
            if start and message.date < start:
                break
            if end and message.date >= end:
                continue
            items.append(self._message(message))
        more = len(items) > limit
        items = items[:limit]
        return {"items": items, "next_before_id": items[-1]["id"] if more else None}

    def _text(self, text, maximum=4096):
        if not isinstance(text, str) or not text or len(text.encode("utf-16-le")) // 2 > maximum:
            raise HTTPBadRequest(f"text must contain 1 to {maximum} UTF-16 code units")

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> dict:
        self._text(text)
        return await self._run("send_text", chat_id, self._send_text, chat_id, text, reply_to)

    async def _send_text(self, chat_id, text, reply_to):
        entity = await self._entity(chat_id)
        return self._message(
            await self.client.send_message(
                entity, text, reply_to=reply_to, parse_mode=None, link_preview=False
            )
        )

    async def send_document(
        self, chat_id: int, filename: str, content_base64: str, caption: str = ""
    ) -> dict:
        if (
            not isinstance(filename, str)
            or not filename
            or filename in (".", "..")
            or any(c in filename for c in ("/", "\\", "\x00"))
        ):
            raise HTTPBadRequest("filename must be a simple file name")
        if not isinstance(content_base64, str):
            raise HTTPBadRequest("content_base64 must be a base64 string")
        if len(content_base64) > 4 * ((DOCUMENT_LIMIT + 2) // 3):
            raise HTTPBadRequest("document exceeds 5 MiB")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPBadRequest("content_base64 must be valid base64") from None
        if not content or len(content) > DOCUMENT_LIMIT:
            raise HTTPBadRequest("document must contain 1 byte to 5 MiB")
        if not isinstance(caption, str):
            raise HTTPBadRequest("caption must be a string")
        if caption:
            self._text(caption, 1024)
        return await self._run(
            "send_document", chat_id, self._send_document, chat_id, filename, content, caption
        )

    async def _send_document(self, chat_id, filename, content, caption):
        entity = await self._entity(chat_id)
        stream = io.BytesIO(content)
        stream.name = filename
        return self._message(
            await self.client.send_file(
                entity, stream, caption=caption, force_document=True, parse_mode=None
            )
        )

    async def _own_messages(self, entity, chat_id, ids):
        self._limit(len(ids))
        if any(not isinstance(code, int) or isinstance(code, bool) or code <= 0 for code in ids):
            raise HTTPBadRequest("message IDs must be positive integers")
        messages = await self.client.get_messages(entity, ids=ids)
        if len(messages) != len(ids) or any(
            message is None or message.chat_id != chat_id or not message.out for message in messages
        ):
            raise HTTPForbidden("only your own messages in the selected chat may be changed")

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> dict:
        self._text(text)
        return await self._run(
            "edit_message", chat_id, self._edit_message, chat_id, message_id, text
        )

    async def _edit_message(self, chat_id, message_id, text):
        entity = await self._entity(chat_id)
        await self._own_messages(entity, chat_id, [message_id])
        return self._message(
            await self.client.edit_message(entity, message_id, text, parse_mode=None)
        )

    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> dict:
        if not isinstance(message_ids, list):
            raise HTTPBadRequest("message_ids must be a list of positive integers")
        return await self._run(
            "delete_messages", chat_id, self._delete_messages, chat_id, message_ids
        )

    async def _delete_messages(self, chat_id, ids):
        entity = await self._entity(chat_id)
        await self._own_messages(entity, chat_id, ids)
        await self.client.delete_messages(entity, ids, revoke=True)
        return {"deleted_ids": ids}

    async def create_channel(self, title: str, description: str = "") -> dict:
        self._text(title, 128)
        self._description(description)
        return await self._run("create_channel", None, self._create_chat, title, description, False)

    async def create_group(self, title: str, description: str = "") -> dict:
        """Create a supergroup; invitations and grants are separate explicit operations."""
        self._text(title, 128)
        self._description(description)
        return await self._run("create_group", None, self._create_chat, title, description, True)

    async def _create_chat(self, title, description, group):
        result = await self.client(
            functions.channels.CreateChannelRequest(
                title=title, about=description, broadcast=not group, megagroup=group
            )
        )
        chat = result.chats[0]
        return {
            "id": utils.get_peer_id(chat),
            "title": chat.title,
            "kind": "group" if group else "channel",
        }

    async def set_chat_details(
        self, chat_id: int, title: str | None = None, description: str | None = None
    ) -> dict:
        if (title is None) == (description is None):
            raise HTTPBadRequest("set exactly one of title or description per call")
        if title is not None:
            self._text(title, 128)
        if description is not None:
            self._description(description)
        return await self._run(
            "set_chat_details", chat_id, self._set_chat_details, chat_id, title, description
        )

    async def _set_chat_details(self, chat_id, title, description):
        entity = await self._entity(chat_id)
        if title is not None:
            request = (
                functions.channels.EditTitleRequest(entity, title)
                if isinstance(entity, types.InputPeerChannel)
                else functions.messages.EditChatTitleRequest(-chat_id, title)
            )
        else:
            request = functions.messages.EditChatAboutRequest(entity, description)
        await self.client(request)
        return {"chat_id": chat_id, "updated": True}

    async def get_members(self, chat_id: int, limit: int = 100, offset: int = 0) -> dict:
        self._limit(limit)
        self._offset(offset)
        return await self._run("get_members", chat_id, self._get_members, chat_id, limit, offset)

    async def _get_members(self, chat_id, limit, offset):
        entity = await self._entity(chat_id)
        items = []
        count = 0
        async for person in self.client.iter_participants(entity, limit=offset + limit + 1):
            count += 1
            if count > offset:
                items.append(self._person(person))
        return {
            "items": items[:limit],
            "next_offset": offset + limit if len(items) > limit else None,
        }

    async def invite_members(self, chat_id: int, user_ids: list[int]) -> dict:
        if not isinstance(user_ids, list):
            raise HTTPBadRequest("user_ids must be a list of positive integers")
        self._limit(len(user_ids), 20)
        for user_id in user_ids:
            self._user_id(user_id)
        return await self._run("invite_members", chat_id, self._invite_members, chat_id, user_ids)

    async def _invite_members(self, chat_id, ids):
        entity = await self._entity(chat_id)
        if not isinstance(entity, types.InputPeerChannel):
            raise HTTPBadRequest("member invitations support channels and supergroups")
        users = [utils.get_input_user(await self._entity(code)) for code in ids]
        result = await self.client(functions.channels.InviteToChannelRequest(entity, users))
        missing = [user.user_id for user in getattr(result, "missing_invitees", [])]
        return {"chat_id": chat_id, "requested_user_ids": ids, "missing_user_ids": missing}

    async def remove_member(self, chat_id: int, user_id: int) -> dict:
        self._user_id(user_id)
        return await self._run("remove_member", chat_id, self._remove_member, chat_id, user_id)

    def _user_id(self, user_id):
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise HTTPBadRequest("user_id must be a positive Telegram user ID")

    async def _remove_member(self, chat_id, user_id):
        entity = await self._entity(chat_id)
        user = await self._entity(user_id)
        await self.client.kick_participant(entity, user)
        return {"chat_id": chat_id, "removed_user_id": user_id}

    async def set_member_admin(self, chat_id: int, user_id: int, rights: list[str]) -> dict:
        self._user_id(user_id)
        if not isinstance(rights, list) or any(
            not isinstance(right, str) or right not in ADMIN_RIGHTS for right in rights
        ):
            raise HTTPBadRequest("unknown administrator right")
        return await self._run(
            "set_member_admin", chat_id, self._set_member_admin, chat_id, user_id, rights
        )

    async def _set_member_admin(self, chat_id, user_id, rights):
        entity = await self._entity(chat_id)
        if not isinstance(entity, types.InputPeerChannel):
            raise HTTPBadRequest("administrator rights support channels and supergroups")
        user = await self._entity(user_id)
        await self.client.edit_admin(
            entity,
            user,
            **{right: right in rights for right in ADMIN_RIGHTS},
            anonymous=False,
            is_admin=bool(rights),
        )
        return {"chat_id": chat_id, "user_id": user_id, "rights": rights}

    async def download_media(self, chat_id: int, message_id: int) -> dict:
        self._message_id(message_id)
        return await self._run("download_media", chat_id, self._extra_download_media, chat_id, message_id)

    async def _extra_download_media(self, chat_id: int, message_id: int):
        message = await self._selected_message(chat_id, message_id)
        content, mime = await self._media_bytes(message)
        return {"chat_id": chat_id, "message_id": message_id, "content_base64": base64.b64encode(content).decode(), "mimetype": mime, "size": len(content)}

    async def transcribe_message(self, chat_id: int, message_id: int, language: str = "it") -> dict:
        self._message_id(message_id)
        if not isinstance(language, str) or not re.fullmatch(r"(?:auto|[a-z]{2,3})", language):
            raise HTTPBadRequest("Invalid language code")
        return await self._run("transcribe_message", chat_id, self._extra_transcribe_message, chat_id, message_id, language, timeout=180)

    async def _extra_transcribe_message(self, chat_id: int, message_id: int, language: str = "it"):
        if self.transcriber is None:
            raise HTTPException(503, "No transcription engine is configured")
        message = await self._selected_message(chat_id, message_id)
        if not message.voice and not message.audio:
            raise HTTPBadRequest("The selected message must contain audio")
        content, mime = await self._media_bytes(message)
        result = await self.transcriber.transcribe(content, mime, language)
        return {"chat_id": chat_id, "message_id": message_id, **result}

    async def react_message(self, chat_id: int, message_id: int, reaction: str) -> dict:
        self._message_id(message_id)
        if not isinstance(reaction, str) or len(reaction) > 32:
            raise HTTPBadRequest("Invalid reaction")
        return await self._run("react_message", chat_id, self._extra_react_message, chat_id, message_id, reaction)

    async def _extra_react_message(self, chat_id: int, message_id: int, reaction: str):
        await self._selected_message(chat_id, message_id)
        await self.client(functions.messages.SendReactionRequest(await self._entity(chat_id), message_id,
                          reaction=[types.ReactionEmoji(reaction)] if reaction else []))
        return {"status": "returned"}

    async def mark_read(self, chat_id: int, message_id: int) -> dict:
        self._message_id(message_id)
        return await self._run("mark_read", chat_id, self._extra_mark_read, chat_id, message_id)

    async def _extra_mark_read(self, chat_id: int, message_id: int):
        await self._selected_message(chat_id, message_id)
        await self.client.send_read_acknowledge(await self._entity(chat_id), max_id=message_id)
        return {"status": "returned"}

    async def archive_chat(self, chat_id: int, archived: bool = True) -> dict:
        if type(archived) is not bool:
            raise HTTPBadRequest("archived must be boolean")
        return await self._run("archive_chat", chat_id, self._extra_archive_chat, chat_id, archived)

    async def _extra_archive_chat(self, chat_id: int, archived: bool = True):
        await self.client.edit_folder(await self._entity(chat_id), folder=1 if archived else 0)
        return {"status": "returned"}

    async def mute_chat(self, chat_id: int, muted: bool = True) -> dict:
        if type(muted) is not bool:
            raise HTTPBadRequest("muted must be boolean")
        return await self._run("mute_chat", chat_id, self._extra_mute_chat, chat_id, muted)

    async def _extra_mute_chat(self, chat_id: int, muted: bool = True):
        until = datetime(2038, 1, 1, tzinfo=timezone.utc) if muted else datetime(1970, 1, 1, tzinfo=timezone.utc)
        await self.client(functions.account.UpdateNotifySettingsRequest(
            types.InputNotifyPeer(await self._entity(chat_id)), types.InputPeerNotifySettings(mute_until=until)))
        return {"status": "returned"}

    async def pin_message(self, chat_id: int, message_id: int, pinned: bool = True) -> dict:
        self._message_id(message_id)
        if type(pinned) is not bool:
            raise HTTPBadRequest("pinned must be boolean")
        return await self._run("pin_message", chat_id, self._extra_pin_message, chat_id, message_id, pinned)

    async def _extra_pin_message(self, chat_id: int, message_id: int, pinned: bool = True):
        await self._selected_message(chat_id, message_id)
        await self.client(functions.messages.UpdatePinnedMessageRequest(await self._entity(chat_id), message_id, silent=True, unpin=not pinned))
        return {"status": "returned"}

    async def block_contact(self, chat_id: int, blocked: bool = True) -> dict:
        if type(blocked) is not bool:
            raise HTTPBadRequest("blocked must be boolean")
        if type(chat_id) is not int or chat_id <= 0:
            raise HTTPBadRequest("A positive personal user ID is required")
        return await self._run("block_contact", chat_id, self._extra_block_contact, chat_id, blocked)

    async def _extra_block_contact(self, chat_id: int, blocked: bool = True):
        request = functions.contacts.BlockRequest if blocked else functions.contacts.UnblockRequest
        await self.client(request(await self._entity(chat_id)))
        return {"status": "returned"}

    async def set_profile(self, first_name: str, last_name: str = "", about: str = "") -> dict:
        if not isinstance(first_name, str) or len(first_name) > 64 or not first_name.strip():
            raise HTTPBadRequest("Invalid first_name")
        if not isinstance(last_name, str) or len(last_name) > 64:
            raise HTTPBadRequest("Invalid last_name")
        if not isinstance(about, str) or len(about) > 70:
            raise HTTPBadRequest("Invalid about")
        return await self._run("set_profile", None, self._extra_set_profile, first_name, last_name, about)

    async def _extra_set_profile(self, first_name: str, last_name: str = "", about: str = ""):
        await self.client(functions.account.UpdateProfileRequest(first_name=first_name,last_name=last_name,about=about))
        return {"status": "returned"}

    async def get_contacts(self, limit: int = 100, offset: int = 0) -> dict:
        self._limit(limit)
        self._offset(offset)
        return await self._run("get_contacts", None, self._extra_get_contacts, limit, offset)

    async def _extra_get_contacts(self, limit: int = 100, offset: int = 0):
        result = await self.client(functions.contacts.GetContactsRequest(hash=0))
        items = [self._person(user) for user in result.users if self._allowed_chat(user.id, "read")]
        return {"items": items[offset:offset+limit], "next_offset": offset+limit if len(items)>offset+limit else None}

    async def forward_message(self, chat_id: int, source_chat_id: int, message_id: int) -> dict:
        self._message_id(message_id)
        return await self._run("forward_message", chat_id, self._extra_forward_message, chat_id, source_chat_id, message_id)

    async def _extra_forward_message(self, chat_id: int, source_chat_id: int, message_id: int):
        self._require_permission("get_messages", source_chat_id)
        await self._selected_message(source_chat_id, message_id)
        result = await self.client.forward_messages(await self._entity(chat_id), message_id,
                                                    from_peer=await self._entity(source_chat_id))
        return self._message(result)

    async def schedule_message(self, chat_id: int, text: str, due: str) -> dict:
        if not isinstance(text, str) or len(text) > 4096 or not text.strip():
            raise HTTPBadRequest("Invalid text")
        date = self._date(due)
        if date is None or not datetime.now(timezone.utc) < date < datetime.now(timezone.utc) + timedelta(days=366):
            raise HTTPBadRequest("due must be a future date within one year")
        return await self._run("schedule_message", chat_id, self._extra_schedule_message, chat_id, text, due)

    async def _extra_schedule_message(self, chat_id: int, text: str, due: str):
        result = await self.client.send_message(await self._entity(chat_id), text, parse_mode=None, schedule=self._date(due))
        return {**self._message(result), "status": "scheduled"}

    async def get_scheduled_messages(self, chat_id: int, limit: int = 100, offset: int = 0) -> dict:
        self._limit(limit)
        self._offset(offset)
        return await self._run("get_scheduled_messages", chat_id, self._extra_get_scheduled_messages, chat_id, limit, offset)

    async def _extra_get_scheduled_messages(self, chat_id: int, limit, offset):
        result = await self.client(functions.messages.GetScheduledHistoryRequest(await self._entity(chat_id), hash=0))
        return {"items": [self._message(message) for message in result.messages[offset:offset+limit]],
                "next_offset": offset+limit if len(result.messages)>offset+limit else None}

    async def cancel_scheduled_message(self, chat_id: int, message_id: int) -> dict:
        self._message_id(message_id)
        return await self._run("cancel_scheduled_message", chat_id, self._extra_cancel_scheduled_message, chat_id, message_id)

    async def _extra_cancel_scheduled_message(self, chat_id: int, message_id: int):
        entity = await self._entity(chat_id)
        result = await self.client(functions.messages.GetScheduledHistoryRequest(entity, hash=0))
        if not any(message.id == message_id and message.out for message in result.messages):
            raise HTTPBadRequest("Scheduled outgoing message not found")
        await self.client(functions.messages.DeleteScheduledMessagesRequest(entity, [message_id]))
        return {"status": "returned"}

    async def create_poll(self, chat_id: int, question: str, options: list[str], multiple_choice: bool = False) -> dict:
        if not isinstance(question, str) or len(question) > 255 or not question.strip():
            raise HTTPBadRequest("Invalid question")
        if type(multiple_choice) is not bool:
            raise HTTPBadRequest("multiple_choice must be boolean")
        if not isinstance(options, list) or not 2 <= len(options) <= 10:
            raise HTTPBadRequest("Polls require two to ten options")
        for option in options:
            self._text(option, 100)
        if len(set(options)) != len(options):
            raise HTTPBadRequest("Poll options must be unique")
        return await self._run("create_poll", chat_id, self._extra_create_poll, chat_id, question, options, multiple_choice)

    async def _extra_create_poll(self, chat_id: int, question: str, options: list[str], multiple_choice: bool = False):
        answers = [types.PollAnswer(types.TextWithEntities(text=option, entities=[]), bytes([i])) for i, option in enumerate(options)]
        poll = types.Poll(id=0, question=types.TextWithEntities(text=question, entities=[]), answers=answers, hash=0, multiple_choice=multiple_choice)
        result = await self.client.send_message(await self._entity(chat_id), file=types.InputMediaPoll(poll))
        return self._message(result)

    async def get_poll(self, chat_id: int, message_id: int) -> dict:
        self._message_id(message_id)
        return await self._run("get_poll", chat_id, self._extra_get_poll, chat_id, message_id)

    async def _extra_get_poll(self, chat_id: int, message_id: int):
        message = await self._selected_message(chat_id, message_id)
        if not isinstance(message.media, types.MessageMediaPoll):
            raise HTTPBadRequest("Selected message is not a poll")
        poll, results = message.media.poll, message.media.results
        counts = {item.option: item.voters for item in results.results or []}
        return {"message_id": message_id, "question": poll.question.text, "closed": bool(poll.closed),
                "multiple_choice": bool(poll.multiple_choice), "total_voters": results.total_voters,
                "partial": bool(results.min), "options": [{"index": i, "text": answer.text.text,
                "voters": counts.get(answer.option)} for i, answer in enumerate(poll.answers)]}

    async def vote_poll(self, chat_id: int, message_id: int, choices: list[int]) -> dict:
        self._message_id(message_id)
        if not isinstance(choices, list) or len(choices)>10 or any(type(i) is not int or i<0 for i in choices):
            raise HTTPBadRequest("choices must be bounded nonnegative integer indexes")
        if len(set(choices)) != len(choices):
            raise HTTPBadRequest("Choices must be unique")
        return await self._run("vote_poll", chat_id, self._extra_vote_poll, chat_id, message_id, choices)

    async def _extra_vote_poll(self, chat_id: int, message_id: int, choices: list[int]):
        message = await self._selected_message(chat_id, message_id)
        if not isinstance(message.media, types.MessageMediaPoll):
            raise HTTPBadRequest("Selected message is not a poll")
        poll = message.media.poll
        if any(i >= len(poll.answers) for i in choices) or (not poll.multiple_choice and len(choices)>1):
            raise HTTPBadRequest("Selection does not match the poll")
        await self.client(functions.messages.SendVoteRequest(await self._entity(chat_id), message_id,
                          [poll.answers[i].option for i in choices]))
        return {"status": "returned"}

    def _message_id(self, value):
        if type(value) is not int or not 0 < value <= 2147483647:
            raise HTTPBadRequest("message_id must be a positive integer")

    async def _selected_message(self, chat_id, message_id):
        messages = await self.client.get_messages(await self._entity(chat_id), ids=[message_id])
        if not messages or messages[0] is None or messages[0].chat_id != chat_id:
            raise HTTPBadRequest("Message not found in the selected chat")
        return messages[0]

    async def _media_bytes(self, message):
        limit = 5 * 1024 * 1024
        if not message.file or not message.file.size or message.file.size > limit:
            raise HTTPBadRequest("Media size must be known and at most 5 MiB")
        content = bytearray()
        async for chunk in self.client.iter_download(message.media):
            if len(content) + len(chunk) > limit:
                raise HTTPBadRequest("Downloaded media exceeds 5 MiB")
            content.extend(chunk)
        if not content:
            raise HTTPBadRequest("Media contains no bytes")
        return bytes(content), message.file.mime_type or "application/octet-stream"

    async def send_media(self, chat_id, kind, filename, content_base64, caption="") -> dict:
        if kind not in ("photo", "video", "audio", "voice", "sticker"):
            raise HTTPBadRequest("Unsupported media kind")
        if not isinstance(filename, str) or not filename or len(filename)>255 or any(c in filename for c in ("/", "\\", "\x00")):
            raise HTTPBadRequest("filename must be a simple display name")
        if not isinstance(content_base64, str) or len(content_base64)>4*((5*1024*1024+2)//3):
            raise HTTPBadRequest("Media exceeds 5 MiB")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except ValueError:
            raise HTTPBadRequest("Invalid base64") from None
        if not content or len(content)>5*1024*1024:
            raise HTTPBadRequest("Media must contain 1 byte to 5 MiB")
        if not isinstance(caption, str) or len(caption)>1024:
            raise HTTPBadRequest("Invalid caption")
        if kind in ("voice", "sticker") and caption:
            raise HTTPBadRequest("This media kind does not support captions")
        return await self._run("send_media", chat_id, self._send_typed_media,
                               chat_id, kind, filename, content, caption)

    async def _send_typed_media(self, chat_id, kind, filename, content, caption):
        stream = io.BytesIO(content)
        stream.name = filename
        result = await self.client.send_file(await self._entity(chat_id), stream,
            caption=caption, parse_mode=None, voice_note=kind == "voice",
            supports_streaming=kind == "video", force_document=kind in ("audio", "voice", "sticker"),
            attributes=[types.DocumentAttributeSticker(alt="", stickerset=types.InputStickerSetEmpty())]
            if kind == "sticker" else None)
        return self._message(result)
