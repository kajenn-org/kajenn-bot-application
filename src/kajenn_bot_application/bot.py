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

"""Shared routed bot instances, persistence, conversations and background delivery.

Provider subclasses own wire formats, credential checks, recipient validation,
activation and sends. Registry records and task identifiers remain provider-owned.
The persistence route is application-wide; provider authentication never supplies
application authorization. One receiving process owns each registry.

Each provider mounts an administrative RoutingClass at /_admin and exposes the
same methods through /_mcp using McpOpenApiApplication. Only server administrators
can invoke these operations or view the administrative schema. Administration
calls application methods directly; registered bot command routers and internal
task entries are outside this HTTP/MCP surface.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import re
import secrets
import time
from datetime import datetime
from typing import Any

import httpx
from genro_builders.builder import BuilderBase, element
from genro_builders.contrib.config import ConfigHandler
from genro_routes import RoutingClass, route

from kajenn.routed_application import RoutedApplication
from kajenn.applications.mcp import McpOpenApiApplication
from kajenn.response import Response
from kajenn.server import BaseServer
from kajenn.lifespan import FatalBootError
from kajenn.tasks import new_descriptor

BOT_CODE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}")


class _BotAPIError(RuntimeError):
    """A sanitized provider failure with explicit uncertainty about delivery."""

    def __init__(self, message: str, *, outcome_uncertain: bool = False) -> None:
        super().__init__(message)
        self.outcome_uncertain = outcome_uncertain


class BotInstanceGrammar:
    """Common instance options; bot grammars inherit and add their own elements."""

    @element(sub_tags="", node_label="access")
    def access(
        self,
        approval_required: bool = False,
        admins: list[int | str] | None = None,
        approval_policy: str = "first",
    ) -> None:
        """Optional admission by configured provider users: first decision or all approvals."""


class _BotConfiguration(BuilderBase):
    """Mount one bot class's grammar and build its declared config elements."""

    def __init__(self, bot_class: type, values: dict[str, Any]) -> None:
        self.bot_class = bot_class
        self.values = values
        super().__init__()

    @element(node_label="bot", _meta={"subbuilder": "bot_class:grammar"})
    def bot(self, bot_class: type) -> None:
        """Root whose children belong to the bot class's grammar."""

    def main(self, root: Any) -> None:
        node = root.bot(bot_class=self.bot_class)
        for name, attrs in self.values.items():
            getattr(node, name)(**attrs)


class BotBaseApplication(McpOpenApiApplication):
    """Base for concrete messaging applications with class-owned bot grammars."""

    provider_name: str

    def __init__(
        self,
        *,
        persistence_route: str | None = None,
        webhook_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        bot_classes: dict[str, type | str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._bot_classes = bot_classes
        self._persistence_route = persistence_route
        self._webhook_url = webhook_url
        self._client = client
        self._owns_client = client is None
        self._bots: dict[str, RoutingClass] = {}
        self._registrations: dict[str, dict[str, Any]] = {}
        self._registry_lock = asyncio.Lock()
        self._ingress_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._conversations = self._make_conversations()
        self._delivery = self._make_delivery()
        kwargs.setdefault("mcp_name_segment", "_mcp")
        kwargs.setdefault("api_name", "_admin")
        super().__init__(routing_class=self._make_administration(), **kwargs)
        getattr(self.route.router_at_path("_meta"), "auth").configure(rule="admin")
        self.route.add_entry(
            self.deliver_reminder, metadata={"task": self.delivery.reminder_task_name}
        )

    @property
    def bot_classes(self) -> dict[str, type | str]:
        """Deployment-controlled aliases allowed for remote registration."""
        if self._bot_classes is not None:
            return self._bot_classes
        return self.config(f"{self.provider_name}.bot_classes", default={}) or {}

    def _make_administration(self) -> RoutingClass:
        raise NotImplementedError

    def _is_administration_path(self, path: str) -> bool:
        return path.strip("/").partition("/")[0] in (
            self.api_name, self.mcp_name_segment, "_meta"
        )

    def schema_filters(self) -> dict[str, Any]:
        """Describe only administrative REST routes; the schema itself requires admin."""
        return {"basepath": self.api_name, "channel_channel": self.rest_channel, "auth_tags": "admin"}

    async def __call__(self, scope, receive, send) -> None:
        """Delegate administration to REST/MCP without exposing internal task routes."""
        if scope["path"].strip("/").partition("/")[0] == self.api_name:
            if scope.get("method") != "POST":
                await Response("Method Not Allowed", status_code=405,
                               headers={"Allow": "POST"})(scope, receive, send)
                return
        await super().__call__(scope, receive, send)

    @property
    def delivery(self) -> Any:
        return self._delivery

    @property
    def persistence_route(self) -> str:
        return self._persistence_route or self.config(f"{self.provider_name}.persistence_route")

    @property
    def webhook_url(self) -> str | None:
        """The public HTTPS mount URL, or None when this app only sends."""
        url = self._webhook_url
        if url is None:
            url = self.config(f"{self.provider_name}.webhook_url", default=None)
        if url is None:
            return None
        return self._validate_webhook_url(url)

    def _validate_webhook_url(self, url: str) -> str:
        """Require an absolute HTTPS mount URL before registration side effects."""
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or not parsed.host or parsed.query or parsed.fragment:
            raise ValueError("webhook_url must be an HTTPS URL without query or fragment")
        return url.rstrip("/")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    @property
    def bots(self) -> dict[str, RoutingClass]:
        return self._bots

    @property
    def registrations(self) -> dict[str, dict[str, Any]]:
        return self._registrations

    @property
    def registry_lock(self) -> asyncio.Lock:
        return self._registry_lock

    @property
    def ingress_lock(self) -> asyncio.Lock:
        return self._ingress_lock

    @property
    def ready(self) -> asyncio.Event:
        return self._ready

    @property
    def conversations(self) -> Any:
        return self._conversations

    def get_bot(self, code: str) -> RoutingClass:
        return self.bots[code]

    def get_bot_registration(self, code: str) -> dict[str, Any]:
        """Return a copy of the trusted registration, including its credentials."""
        return copy.deepcopy(self.registrations[code])

    def _require_server(self) -> BaseServer:
        server = self.server
        if server is None:
            raise RuntimeError("Bot operations require an owning server")
        return server

    async def _call(self, node: Any, **kwargs: Any) -> Any:
        # RouterNode uses the asyncio coroutine marker on Python 3.11.
        if asyncio.iscoroutinefunction(node):
            return await node(**kwargs)
        return await self._require_server().run_sync(lambda: node(**kwargs))

    async def _persist(self, operation: str, record: dict[str, Any] | None = None) -> Any:
        mount, separator, path = self.persistence_route.strip("/").partition("/")
        if not separator or not path:
            raise ValueError("persistence_route must be '<mount>/<route>'")
        app = self._require_server().application_at(mount)
        if app is None:
            raise LookupError(f"persistence application not found: {mount}")
        if not isinstance(app, RoutedApplication):
            raise TypeError("persistence application must be a RoutedApplication")
        node = app.route.node(path)
        return await self._call(node, operation=operation, application=self.code, record=record)

    def _build_bot(self, record: dict[str, Any]) -> RoutingClass:
        module, name = record["bot_class"].split(":", 1)
        bot_class = getattr(importlib.import_module(module), name)
        if not issubclass(bot_class, RoutingClass):
            raise TypeError("bot_class must inherit RoutingClass")
        builder = _BotConfiguration(bot_class, record["config"])
        config = ConfigHandler(builder)
        errors = builder.validate_source()
        if errors:
            raise ValueError(f"invalid bot configuration: {errors}")
        bot: RoutingClass = bot_class(application=self, code=record["code"], config=config)
        self.conversations.validate_access(bot)
        bot.route.plug("auth")
        getattr(self._require_server(), "arm_router")(bot.route)
        return bot

    async def activate_bot(self, code: str) -> RoutingClass:
        """Activate a saved bot; only receivers configure a webhook."""
        async with self.registry_lock:
            bot = self.get_bot(code)
            await self._activate(self.registrations[code], bot, self.webhook_url)
            self.ready.set()
            return bot

    async def on_startup(self) -> None:
        """Restore bots, renewing webhooks only when reception is configured."""
        try:
            async with self.registry_lock:
                self.ready.clear()
                url = self.webhook_url
                records = await self._persist("list")
                if url is not None:
                    await self._persist("prune_receipts", {"now": time.time()})
                for record in records:
                    await self._activate(record, self._build_bot(record), url)
                    if url is not None:
                        await self.conversations.restore_admissions(record["code"])
                self.ready.set()
        except Exception as exc:
            raise FatalBootError(f"{self.provider_name} bot registry startup failed") from exc

    async def on_shutdown(self) -> None:
        """Close only the HTTP client owned by this application."""
        if self._owns_client and self._client is not None:
            await self.client.aclose()
            self._client = None

    async def create_conversation(
        self,
        bot_code: str,
        *,
        participants: list[dict[str, Any]],
        route: str,
        context: dict[str, Any] | None = None,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        """Persist an independent conversation; participants include user_id and chat_id."""
        async with self.conversations.lock:
            result: dict[str, Any] = await self.conversations.create_record(
                bot_code, participants, route, context, expires_at
            )
            return result

    async def get_conversation(self, bot_code: str, conversation_id: str) -> dict[str, Any]:
        """Read one conversation through the application persistence route."""
        async with self.conversations.lock:
            record: dict[str, Any] = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            return record

    async def send_conversation_message(
        self,
        bot_code: str,
        conversation_id: str,
        user_id: int | str,
        text: str,
        *,
        buttons: dict[str, str] | None = None,
        chat_id: int | str | None = None,
    ) -> Any:
        """Send to one participant; buttons map labels to routed action names."""
        async with self.conversations.lock:
            record: dict[str, Any] = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["state"] != "open":
                raise ValueError("conversation is closed")
            return await self.conversations.send_record_message(
                record, user_id, text, buttons, chat_id
            )

    async def update_conversation_context(
        self, bot_code: str, conversation_id: str, context: dict[str, Any], *, revision: int
    ) -> dict[str, Any]:
        """Replace context only when the caller's snapshot is still current."""
        json.dumps(context)
        async with self.conversations.lock:
            record: dict[str, Any] = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["kind"] != "conversation" or record["state"] != "open":
                raise ValueError("context updates require an open general conversation")
            if record["revision"] != revision:
                raise ValueError("conversation revision conflict")
            record["context"] = copy.deepcopy(context)
            await self.conversations.save_record(record)
            return record

    async def close_conversation(
        self, bot_code: str, conversation_id: str, *, state: str = "closed"
    ) -> None:
        """Conclude or cancel a general conversation; admission decisions use admin callbacks."""
        if state not in ("closed", "cancelled"):
            raise ValueError("state must be closed or cancelled")
        async with self.conversations.lock:
            record: dict[str, Any] = await self.conversations.get_record(bot_code, conversation_id)
            await self.conversations.expire_record(record)
            if record["kind"] != "conversation":
                raise ValueError("admission requires an administrator decision")
            if record["state"] == "open":
                record["state"] = state
                await self.conversations.save_record(record)

    async def send_text(self, bot_code: str, chat_id: int | str, text: str) -> list[Any]:
        """Send long plain text in ordered chunks, preserving every character."""
        return [
            await self.send_message(bot_code, chat_id, part)
            for part in self.delivery.get_text_parts(text)
        ]

    async def send_document(
        self,
        bot_code: str,
        chat_id: int | str,
        document: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        """Send a file_id, provider-fetchable URL or bytes with a filename."""
        return await self.send_media(
            bot_code, chat_id, "document", document, filename=filename, caption=caption
        )

    async def send_announcement(
        self, bot_code: str, chat_ids: list[int | str], text: str
    ) -> list[dict[str, Any]]:
        """Send once per distinct destination and report complete, partial or failed delivery."""
        self.get_bot(bot_code)
        parts = self.delivery.get_text_parts(text)
        if any(not self._valid_recipient(chat_id) for chat_id in chat_ids):
            raise ValueError("announcement destinations must be valid recipient IDs")
        return await self._send_batch(bot_code, chat_ids, parts, self.send_message)

    async def _send_batch(
        self, bot_code: str, chat_ids: list[Any], parts: list[Any], sender: Any
    ) -> list[dict[str, Any]]:
        """Collect per-recipient outcomes without replaying earlier accepted parts."""
        results = []
        for chat_id in dict.fromkeys(chat_ids):
            result: dict[str, Any] = {
                "chat_id": chat_id,
                "status": self.delivery.acceptance_state,
                "messages": [],
            }
            try:
                for part in parts:
                    result["messages"].append(await sender(bot_code, chat_id, part))
            except (_BotAPIError, ValueError) as exc:
                result.update(
                    status="uncertain"
                    if getattr(exc, "outcome_uncertain", False)
                    else "partial"
                    if result["messages"]
                    else "failed",
                    error=str(exc),
                )
            results.append(result)
        return results

    async def queue_announcement(self, bot_code: str, chat_ids: list[int | str], text: str) -> str:
        """Stage an announcement in kajenn.tasks; results are kept in the task spool."""
        self.get_bot(bot_code)
        self.delivery.get_text_parts(text)
        if not chat_ids or any(not self._valid_recipient(c) for c in chat_ids):
            raise ValueError("announcement requires valid recipient IDs")
        return await self._queue_announcement(
            {"bot_code": bot_code, "chat_ids": chat_ids, "text": text}
        )

    async def _queue_announcement(self, params: dict[str, Any]) -> str:
        manager = self.delivery.manager
        mount = self.mount
        if mount is None:
            raise RuntimeError("Bot application requires a resolved mount")
        task_id = f"{self.provider_name}-announcement-{secrets.token_hex(12)}"
        descriptor = new_descriptor(
            task_id,
            owner=f"{self.provider_name}:{params['bot_code']}",
            mount=mount,
            node_path="deliver_announcement",
        )
        await self._require_server().run_sync(lambda: manager.spool.create(descriptor, params))
        return task_id

    @route()
    async def deliver_announcement(
        self, bot_code: str, chat_ids: list[int | str], text: str
    ) -> list[dict[str, Any]]:
        """Task entry point for an announcement, available on send-only deployments too."""
        await self.ready.wait()
        return await self.send_announcement(bot_code, chat_ids, text)

    async def schedule_reminder(
        self,
        bot_code: str,
        chat_id: int | str,
        text: str,
        *,
        when: datetime,
        conversation_id: str | None = None,
        user_id: int | str | None = None,
    ) -> str:
        """Persist a one-shot reminder, optionally bound to an open conversation participant."""
        result: str = await self.delivery.schedule_reminder(
            bot_code, chat_id, text, when=when, conversation_id=conversation_id, user_id=user_id
        )
        return result

    async def get_reminder(self, code: str) -> dict[str, Any]:
        """Read this application's reminder schedule and delivery state."""
        result: dict[str, Any] = await self.delivery.get_reminder(code)
        return result

    async def cancel_reminder(self, code: str) -> bool:
        """Cancel a pending reminder; False means it has already left pending state."""
        result: bool = await self.delivery.cancel_reminder(code)
        return result

    async def deliver_reminder(self, code: str) -> None:
        """Scheduler entry point, dynamically registered with an application-specific task name."""
        await self.delivery.deliver_reminder(code)

    def _make_conversations(self) -> Any:
        raise NotImplementedError

    def _make_delivery(self) -> Any:
        raise NotImplementedError

    def _valid_user(self, value: Any) -> bool:
        raise NotImplementedError

    def _valid_recipient(self, value: Any) -> bool:
        raise NotImplementedError

    async def _activate(self, record: dict[str, Any], bot: RoutingClass, url: str | None) -> None:
        raise NotImplementedError

    async def send_message(self, bot_code: str, chat_id: Any, text: str) -> Any:
        raise NotImplementedError

    async def send_media(
        self,
        bot_code: str,
        chat_id: Any,
        kind: str,
        media: Any,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        raise NotImplementedError

    async def _stage_task(
        self, task_id: str, bot_code: str, params: dict[str, Any], retention: float
    ) -> None:
        """Durably stage work and then a bounded receipt before acknowledging a provider."""
        manager = getattr(self._require_server(), "tasks")
        mount = self.mount
        if mount is None:
            raise RuntimeError("Bot application requires a resolved mount")
        async with self.ingress_lock:
            now = time.time()
            await self._persist("prune_receipts", {"now": now})
            expires_at = await self._persist("get_receipt", {"task_id": task_id})
            if expires_at is not None and expires_at > now:
                return

            def stage():
                if manager.spool.get(task_id) is None:
                    descriptor = new_descriptor(
                        task_id,
                        owner=f"{self.provider_name}:{bot_code}",
                        mount=mount,
                        node_path="deliver_update",
                    )
                    manager.spool.create(descriptor, params)

            await self._require_server().run_sync(stage)
            await self._persist("save_receipt", {"task_id": task_id, "expires_at": now + retention})
