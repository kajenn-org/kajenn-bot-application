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

"""Telegram application: registered RoutingClass bots, outbound messages and webhooks.

``webhook_url`` selects reception on this deployment. With an HTTPS mount URL,
the application owns the bots' webhooks and executes incoming commands. Without
it, this application only sends: registration, restoration and activation never
set or delete a webhook, inbound HTTP returns 404, and task delivery is disabled.
A sender can share the central bot's token while replies go to the central
webhook. Send-only deployments need registration persistence but no task manager
or receipt operations.

One application owns an application-wide persistence route. That trusted route
accepts ``operation``, ``application=<code>`` and ``record=<dict>``. ``list``
returns registration dictionaries and ``save`` durably upserts a registration.
``get_receipt``/``save_receipt``/``prune_receipts`` keep expiring update receipts
separate from registrations and the task spool. The provider must protect the
token and webhook secret at rest.

Each bot class declares ``grammar`` and accepts ``application``, ``code`` and a
callable ``config`` read door. Registration config maps element names to their
attributes, validated by that grammar. Only importable module-level classes are
persisted. Name/icon are local registry metadata, not Telegram profile changes.

POST /<application mount>/<bot code> verifies Telegram's secret, then stages a
command in kajenn.tasks before acknowledging it. Task IDs include the application,
bot and update IDs. Receipts deduplicate for 48 hours even after spool cleanup;
expired receipts are pruned on startup and ingress. The task resolves the
bot's command route with anonymous auth filters: provider credentials authenticate
delivery, never the sender's application identity. Protected commands stay closed.
Handlers receive the declared parameters among ``text`` (the command tail),
``sender`` (a deep copy of the Telegram user dictionary, or an empty dictionary
when absent) and ``chat_id`` (the originating chat). Extra parameters are dropped;
handlers accepting **kwargs receive all three. Handlers return text or None.

Message commands, conversation text and inline callbacks are staged before ACK.
Conversation state and admission decisions use the same persistence route, with
atomic revision checks; see telegram_conversations for its persistence contract.
Bot grammars may inherit TelegramBotInstanceGrammar to opt into admin admission.
Outbound delivery supports bounded retries, text splitting, media, announcements
and native polls. Reminders reuse kajenn.tasks. Polling and application identity
provisioning are outside this contract.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from genro_bag import BagResolver
from genro_builders.builder import element
from genro_routes import RoutingClass, route

from kajenn.application import ApplicationGrammar
from kajenn.exceptions import HTTPForbidden, HTTPNotFound, HTTPUnauthorized
from kajenn.request import Request
from kajenn.response import Response
from kajenn.types import Receive, Scope, Send
from .administration import _TelegramAdministration
from .bot import BotBaseApplication, BotInstanceGrammar
from .telegram_conversations import _Conversations
from .telegram_delivery import _Delivery

__all__ = ["TelegramBotApplication", "TelegramBotGrammar", "TelegramBotInstanceGrammar"]

BOT_CODE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}")
UPDATE_RETENTION_SECONDS = 48 * 60 * 60
COMMAND = re.compile(r"/([a-z0-9_]{1,32})(?:@([a-zA-Z0-9_]+))?(?:\s+(.*))?", re.DOTALL)


class TelegramBotGrammar(ApplicationGrammar):
    """The Telegram application's registry and optional public webhook location."""

    @element(sub_tags="", node_label="telegram")
    def telegram(
        self,
        persistence_route: str | BagResolver,
        bot_classes: dict[str, str] | None = None,
        webhook_url: str | BagResolver | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        send_interval: float = 0.0,
    ) -> None:
        """One persistence route; omit webhook_url for a send-only application."""


class TelegramBotInstanceGrammar(BotInstanceGrammar):
    """Shared access options for Telegram bot classes."""


class TelegramBotApplication(BotBaseApplication):
    """Own bot instances, sending directly and optionally receiving webhooks.

    ``register_bot`` is a trusted in-process API, not a public HTTP route.
    ``client`` optionally supplies an httpx client (owned by the caller).
    Constructor registry/URL options override the application's grammar values.
    """

    grammar = TelegramBotGrammar
    provider_name = "telegram"

    def _make_administration(self):
        return _TelegramAdministration(self)

    def _make_conversations(self):
        return _Conversations(self)

    def _make_delivery(self):
        return _Delivery(self)

    def _valid_user(self, value):
        return type(value) is int and value > 0

    def _valid_recipient(self, value):
        return type(value) is int

    async def _telegram(self, token: str, method: str, **payload: Any) -> Any:
        """Call Telegram with bounded retries and sanitized transport errors."""
        return await self.delivery.request(token, method, **payload)

    async def _activate(self, record: dict[str, Any], bot: RoutingClass, url: str | None) -> None:
        code = record["code"]
        # Expose the verified endpoint before Telegram can deliver its first update.
        self.bots[code] = bot
        self.registrations[code] = record
        if url is None:
            return
        await self._telegram(
            record["token"],
            "setWebhook",
            url=f"{url}/{code}",
            secret_token=record["webhook_secret"],
            allowed_updates=["message", "callback_query", "poll", "poll_answer"],
        )

    async def register_bot(
        self,
        *,
        code: str,
        bot_class: type[RoutingClass],
        token: str,
        name: str = "",
        icon: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> RoutingClass:
        """Validate, durably register, then activate a bot created with BotFather.

        Failed persistence leaves the bot inactive. Failed webhook activation
        leaves its registration available for ``activate_bot`` or startup. Duplicate
        codes and tokens within this application are rejected. A separate
        send-only application can use the same token without replacing its webhook.
        """
        async with self.registry_lock:
            if not BOT_CODE.fullmatch(code) or self._is_administration_path(code):
                raise ValueError("invalid bot code")
            if code in self.registrations:
                raise ValueError(f"bot already registered: {code}")
            if any(r["token"] == token for r in self.registrations.values()):
                raise ValueError("bot token already registered")
            if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
                raise ValueError("invalid bot token format")
            if "." in bot_class.__qualname__:
                raise ValueError("bot_class must be a module-level class")
            record = {
                "code": code,
                "bot_class": f"{bot_class.__module__}:{bot_class.__name__}",
                "token": token,
                "name": name or code,
                "icon": icon,
                "config": copy.deepcopy(config or {}),
                "webhook_secret": secrets.token_urlsafe(32),
            }
            json.dumps(record)
            bot = self._build_bot(record)
            url = self.webhook_url
            user = await self._telegram(token, "getMe")
            if not user["is_bot"]:
                raise ValueError("Telegram credentials must identify a bot")
            record["username"] = user["username"]
            await self._persist("save", record)
            await self._activate(record, bot, url)
            self.ready.set()
            return bot

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._is_administration_path(scope["path"]):
            await super().__call__(scope, receive, send)
            return
        if self.webhook_url is None:
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        code = scope["path"].strip("/")
        if code not in self.registrations:
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await Response("Method Not Allowed", status_code=405)(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        secret = headers.get(b"x-telegram-bot-api-secret-token", b"")
        record = self.registrations[code]
        if not hmac.compare_digest(secret, record["webhook_secret"].encode()):
            await Response("Forbidden", status_code=403)(scope, receive, send)
            return
        request = Request(scope, receive, server=self.server, application=self)
        try:
            update = json.loads(await request.read_body())
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                raise ValueError("invalid update")
            poll = update.get("poll")
            answer = update.get("poll_answer")
            if poll is not None and (
                not isinstance(poll, dict) or not isinstance(poll.get("id"), str)
            ):
                raise ValueError("invalid poll")
            if answer is not None:
                if not isinstance(answer, dict) or not isinstance(answer.get("poll_id"), str):
                    raise ValueError("invalid poll answer")
                voter = answer.get("user") or answer.get("voter_chat")
                if not isinstance(voter, dict) or type(voter.get("id")) is not int:
                    raise ValueError("invalid poll voter")
                if not isinstance(answer.get("option_ids"), list) or any(
                    type(i) is not int or i < 0 for i in answer["option_ids"]
                ):
                    raise ValueError("invalid poll options")
            message = update.get("message", {})
            query = update.get("callback_query")
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            text = message.get("text", "")
            if not isinstance(text, str):
                raise ValueError("invalid text")
            command = COMMAND.fullmatch(text)
            chat_id = 0
            if text:
                chat_id = message["chat"]["id"]
                if type(chat_id) is not int:
                    raise ValueError("invalid chat")
                sender = message.get("from", {})
                if not isinstance(sender, dict):
                    raise ValueError("invalid sender")
            if query is not None:
                if (
                    not isinstance(query, dict)
                    or not isinstance(query.get("id"), str)
                    or not isinstance(query.get("data", ""), str)
                    or type(query["from"]["id"]) is not int
                ):
                    raise ValueError("invalid callback")
                callback_message = query.get("message")
                if callback_message is not None and (
                    not isinstance(callback_message, dict)
                    or type(callback_message.get("message_id")) is not int
                    or not isinstance(callback_message.get("chat"), dict)
                    or type(callback_message["chat"].get("id")) is not int
                ):
                    raise ValueError("invalid callback message")
        except (ValueError, KeyError, TypeError):
            await Response("Bad Request", status_code=400)(scope, receive, send)
            return
        addressed = (
            not command or not command[2] or command[2].lower() == record["username"].lower()
        )
        if poll is not None or answer is not None or query is not None or (text and addressed):
            digest = hashlib.sha256(
                f"{self.code}:{code}:{update['update_id']}".encode()
            ).hexdigest()
            task_id = f"telegram-{digest}"
            await self._stage_update(
                task_id,
                code,
                command[1] if command else "",
                command[3] or "" if command else text,
                chat_id,
                update,
            )
        await Response("OK")(scope, receive, send)

    async def _stage_update(
        self,
        task_id: str,
        code: str,
        command: str,
        text: str,
        chat_id: int,
        update: dict[str, Any] | None = None,
    ) -> None:
        """Persist the task and receipt before ACK; serialize concurrent deliveries.

        A receipt write failure leaves the task available for the retry to find.
        Receipt expiry is fixed at acceptance and never extended by redeliveries.
        """
        await self._stage_task(
            task_id,
            code,
            {
                "bot_code": code,
                "command": command,
                "text": text,
                "chat_id": chat_id,
                "update": update,
            },
            UPDATE_RETENTION_SECONDS,
        )

    @route()
    async def deliver_update(
        self,
        bot_code: str,
        command: str,
        text: str,
        chat_id: int,
        update: dict[str, Any] | None = None,
    ) -> None:
        """Task entry point; resolve only public commands, then deliver their text."""
        if self.webhook_url is None:
            raise RuntimeError("Telegram webhook reception is disabled")
        await self.ready.wait()
        bot = self.get_bot(bot_code)
        if update is not None and ("poll" in update or "poll_answer" in update):
            await self.delivery.receive_poll(bot_code, update)
            return
        if update is not None and "callback_query" in update:
            await self.conversations.handle_callback(bot_code, update["callback_query"])
            return
        message = (update or {}).get("message", {"chat": {"id": chat_id}})
        if not await self.conversations.admit_sender(bot_code, message):
            return
        if not command:
            await self.conversations.handle_message(bot_code, message)
            return
        try:
            node = bot.route.node(command, errors=self.ROUTER_ERRORS)
            kwargs = self.spread_over_params(node, {
                "text": text,
                "sender": copy.deepcopy(message.get("from", {})),
                "chat_id": chat_id,
            })
            result = await self._call(node, **kwargs)
        except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
            return
        if result is not None:
            if not isinstance(result, str):
                raise TypeError("Telegram command handlers must return str or None")
            await self.send_message(bot_code, chat_id, result)

    async def send_message(
        self, bot_code: str, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> Any:
        """Send a plain-text message to a known Telegram chat."""
        if not 1 <= len(text) <= 4096:
            raise ValueError("Telegram text must contain between 1 and 4096 characters")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._telegram(self.registrations[bot_code]["token"], "sendMessage", **payload)

    async def send_typing(self, bot_code: str, chat_id: int) -> Any:
        """Emit one typing indication; no background refresh loop is started."""
        return await self._telegram(
            self.registrations[bot_code]["token"],
            "sendChatAction",
            chat_id=chat_id,
            action="typing",
        )

    async def send_media(
        self,
        bot_code: str,
        chat_id: int,
        kind: str,
        media: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        """Send a document, photo, video, audio, voice or animation by reference or upload."""
        return await self.delivery.send_media(
            bot_code, chat_id, kind, media, filename=filename, caption=caption
        )

    async def send_poll(
        self,
        bot_code: str,
        chat_id: int,
        question: str,
        options: list[str],
        *,
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        route: str | None = None,
    ) -> Any:
        """Send and track a native regular poll; optionally route result events to the bot."""
        return await self.delivery.send_poll(
            bot_code,
            chat_id,
            question,
            options,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
            route=route,
        )

    async def get_poll(self, bot_code: str, poll_id: str) -> dict[str, Any]:
        """Read persisted poll totals and the latest received answer per voter."""
        result: dict[str, Any] = await self.delivery.get_poll(bot_code, poll_id)
        return result

    async def stop_poll(self, bot_code: str, poll_id: str) -> Any:
        """Close a tracked native poll and save its final totals."""
        return await self.delivery.stop_poll(bot_code, poll_id)
