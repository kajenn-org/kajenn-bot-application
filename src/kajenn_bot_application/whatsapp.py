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

"""WhatsApp Business Cloud API application with independently routed bot instances.

One application mount represents one Meta app connection. Signed webhook batches
map account/phone-number pairs to registered bot instances. Supported events are
staged before ACK, with eight-day receipts. Unknown numbers and unsupported event
kinds are ignored; malformed supported payloads return 400 before staging.

Command handlers receive the declared parameters among ``text`` (the command
tail), ``sender`` (a deep copy of the normalized user dictionary) and ``chat_id``
(the reply destination). Both sender ID and chat ID remain strings. Extra
parameters are dropped; handlers accepting ``**kwargs`` receive all three.
Sender metadata does not grant router permissions or establish an application
identity. Handlers return a reply string or None.

The API version is explicit. Send-only deployments need no app secret or verify
token and never change remote subscriptions. Existing numbers and access tokens
must be provisioned in Meta; registration validates access, not remote onboarding.
Free-form sending requires a persisted open service window. Template sends are
explicit and return acceptance, not delivery; status webhooks update stored state.
"""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs

from genro_bag import BagResolver
from genro_builders.builder import element
from genro_routes import RoutingClass, route

from kajenn.application import ApplicationGrammar
from kajenn.exceptions import HTTPForbidden, HTTPNotFound, HTTPUnauthorized
from kajenn.request import Request
from kajenn.response import Response
from .bot import BOT_CODE, BotBaseApplication, BotInstanceGrammar
from .whatsapp_delivery import _Delivery
from .whatsapp_conversations import _Conversations

UPDATE_RETENTION_SECONDS = 8 * 24 * 60 * 60
COMMAND = re.compile(r"/([a-z0-9_]{1,32})(?:\s+(.*))?", re.DOTALL)


class WhatsAppBotGrammar(ApplicationGrammar):
    """Application-wide connection and persistence settings."""

    @element(sub_tags="", node_label="whatsapp")
    def whatsapp(
        self,
        persistence_route: str | BagResolver,
        api_version: str | BagResolver,
        webhook_url: str | BagResolver | None = None,
        app_secret: str | BagResolver | None = None,
        verify_token: str | BagResolver | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        send_interval: float = 0.0,
    ) -> None:
        """Omit webhook_url for a sender; receivers require both webhook secrets."""


class WhatsAppBotInstanceGrammar(BotInstanceGrammar):
    """Shared admission options plus explicit templates for administrator notices."""

    @element(sub_tags="", node_label="notifications")
    def notifications(
        self, approval_template: dict | None = None, resolution_template: dict | None = None
    ) -> None:
        """Optional name/language/component templates used outside service windows."""


class WhatsAppBotApplication(BotBaseApplication):
    """Receive authenticated WhatsApp events and send through Cloud API."""

    grammar = WhatsAppBotGrammar
    provider_name = "whatsapp"

    def __init__(
        self,
        *,
        api_version: str | None = None,
        app_secret: str | None = None,
        verify_token: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._api_version = api_version
        self._app_secret = app_secret
        self._verify_token = verify_token
        super().__init__(**kwargs)

    @property
    def api_version(self) -> str:
        value = self._api_version or self.config("whatsapp.api_version", default=None)
        if not isinstance(value, str) or not re.fullmatch(r"v[1-9][0-9]*\.0", value):
            raise ValueError("api_version must explicitly select a supported Graph version")
        return value

    @property
    def app_secret(self) -> str:
        value = self._app_secret or self.config("whatsapp.app_secret", default=None)
        if not isinstance(value, str) or not value:
            raise ValueError("receivers require app_secret")
        return value

    @property
    def verify_token(self) -> str:
        value = self._verify_token or self.config("whatsapp.verify_token", default=None)
        if not isinstance(value, str) or not value:
            raise ValueError("receivers require verify_token")
        return value

    def _make_conversations(self):
        return _Conversations(self)

    def _make_delivery(self):
        return _Delivery(self)

    def _valid_user(self, value):
        return isinstance(value, str) and bool(
            re.fullmatch(r"[0-9]{1,20}|[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]{1,128}", value)
        )

    def _valid_recipient(self, value):
        return self._valid_user(value)

    def _validate_connection(self) -> None:
        version = self.api_version
        if self.webhook_url is not None:
            secrets = (self.app_secret, self.verify_token)
            if not version or not all(secrets):
                raise ValueError("invalid connection")

    async def on_startup(self) -> None:
        self._validate_connection()
        await super().on_startup()

    async def _activate(self, record, bot, url):
        # Callback configuration and remote subscriptions are explicitly operator-owned.
        self.bots[record["code"]] = bot
        self.registrations[record["code"]] = record

    async def register_bot(
        self,
        *,
        code: str,
        bot_class: type[RoutingClass],
        token: str,
        phone_number_id: str,
        business_account_id: str,
        name: str = "",
        icon: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> RoutingClass:
        """Register an already provisioned business number; never alter its subscriptions."""
        self._validate_connection()
        async with self.registry_lock:
            if not BOT_CODE.fullmatch(code) or code in self.registrations:
                raise ValueError("invalid or duplicate bot code")
            if not all(
                isinstance(v, str) and re.fullmatch(r"[0-9]+", v)
                for v in (phone_number_id, business_account_id)
            ):
                raise ValueError("business and phone number IDs must be numeric strings")
            if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
                raise ValueError("invalid access token")
            if any(r["phone_number_id"] == phone_number_id for r in self.registrations.values()):
                raise ValueError("phone number already registered")
            if "." in bot_class.__qualname__:
                raise ValueError("bot_class must be a module-level class")
            record = {
                "code": code,
                "bot_class": f"{bot_class.__module__}:{bot_class.__name__}",
                "token": token,
                "phone_number_id": phone_number_id,
                "business_account_id": business_account_id,
                "name": name or code,
                "icon": icon,
                "config": copy.deepcopy(config or {}),
            }
            json.dumps(record)
            bot = self._build_bot(record)
            identity = await self.delivery.request(
                record, "GET", phone_number_id, params={"fields": "id"}
            )
            if identity.get("id") != phone_number_id:
                raise ValueError("credentials did not resolve the configured phone number")
            await self._persist("save", record)
            await self._activate(record, bot, self.webhook_url)
            self.ready.set()
            return bot

    async def __call__(self, scope, receive, send):
        if self.webhook_url is None or scope["path"].strip("/"):
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        if scope["method"] == "GET":
            query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace"))
            valid = (
                query.get("hub.mode") == ["subscribe"]
                and len(query.get("hub.challenge", [])) == 1
                and hmac.compare_digest(
                    query.get("hub.verify_token", [""])[0].encode(), self.verify_token.encode()
                )
            )
            await Response(
                query["hub.challenge"][0] if valid else "Forbidden",
                status_code=200 if valid else 403,
            )(scope, receive, send)
            return
        if scope["method"] != "POST":
            await Response("Method Not Allowed", status_code=405)(scope, receive, send)
            return
        body = await Request(scope, receive, server=self.server, application=self).read_body()
        headers = dict((k.lower(), v) for k, v in scope.get("headers", []))
        expected = (
            b"sha256="
            + hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest().encode()
        )
        if not hmac.compare_digest(headers.get(b"x-hub-signature-256", b""), expected):
            await Response("Forbidden", status_code=403)(scope, receive, send)
            return
        try:
            events = self._decode_events(json.loads(body))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            await Response("Bad Request", status_code=400)(scope, receive, send)
            return
        for code, key, event in events:
            digest = hashlib.sha256(json.dumps([self.code, code, key]).encode()).hexdigest()
            await self._stage_task(
                f"whatsapp-{digest}",
                code,
                {"bot_code": code, "event": event},
                UPDATE_RETENTION_SECONDS,
            )
        await Response("OK")(scope, receive, send)

    def _decode_events(self, payload):
        if (
            not isinstance(payload, dict)
            or payload.get("object") != "whatsapp_business_account"
            or not isinstance(payload.get("entry"), list)
        ):
            raise ValueError("invalid webhook envelope")
        mappings = {
            (r["business_account_id"], r["phone_number_id"]): code
            for code, r in self.registrations.items()
        }
        events = []
        for entry in payload["entry"]:
            if not isinstance(entry.get("changes"), list):
                raise ValueError("invalid changes")
            for change in entry["changes"]:
                if change["field"] != "messages":
                    continue
                value = change["value"]
                code = mappings.get((entry["id"], value["metadata"]["phone_number_id"]))
                if code is None:
                    logging.getLogger(__name__).warning(
                        "Ignoring WhatsApp events for an unregistered account/number"
                    )
                    continue
                for kind, items in (
                    ("message", value.get("messages", [])),
                    ("status", value.get("statuses", [])),
                ):
                    if not isinstance(items, list):
                        raise ValueError("invalid event list")
                    for item in items:
                        if not isinstance(item.get("id"), str) or not item["id"]:
                            raise ValueError("invalid message id")
                        timestamp = int(item["timestamp"])
                        if timestamp < 0:
                            raise ValueError("invalid timestamp")
                        if kind == "status":
                            if item.get("status") not in (
                                "sent",
                                "delivered",
                                "read",
                                "failed",
                                "deleted",
                            ):
                                continue
                            recipient_ids = [
                                item[k]
                                for k in ("recipient_id", "recipient_user_id")
                                if item.get(k)
                            ]
                            if not recipient_ids or any(
                                not self._valid_recipient(v) for v in recipient_ids
                            ):
                                raise ValueError("invalid status recipient")
                            key = [kind, item["id"], item["status"], timestamp]
                            event = {
                                "kind": kind,
                                "message_id": item["id"],
                                "recipient": recipient_ids[0],
                                "recipient_ids": recipient_ids,
                                "status": item["status"],
                                "timestamp": timestamp,
                                "error_codes": [e.get("code") for e in item.get("errors", [])],
                            }
                        else:
                            sender_id = item.get("from") or item.get("from_user_id")
                            if not self._valid_user(sender_id):
                                raise ValueError("unsupported sender identifier")
                            text, action = "", ""
                            message_type = item["type"]
                            if message_type == "text":
                                text = item["text"]["body"]
                            elif message_type == "interactive":
                                interactive = item["interactive"]
                                if interactive["type"] not in ("button_reply", "list_reply"):
                                    continue
                                action = interactive[interactive["type"]]["id"]
                            elif message_type == "button":
                                action = item["button"]["payload"]
                            else:
                                # Even unsupported user messages open the service window.
                                message_type = "unsupported"
                            if not isinstance(text, str) or not isinstance(action, str):
                                raise ValueError("invalid message text or action")
                            reply_id = item.get("context", {}).get("id")
                            if reply_id is not None and not isinstance(reply_id, str):
                                raise ValueError("invalid reply id")
                            key = [kind, item["id"]]
                            event = {
                                "kind": kind,
                                "message_id": item["id"],
                                "sender": {"id": sender_id},
                                "destination": sender_id,
                                "text": text,
                                "action": action,
                                "reply_id": reply_id,
                                "timestamp": min(timestamp, time.time()),
                                "supported": message_type != "unsupported",
                            }
                        events.append((code, key, event))
        return events

    @route()
    async def deliver_update(self, bot_code: str, event: dict[str, Any]) -> None:
        """Process one normalized event under the provider's original bot scope."""
        if self.webhook_url is None:
            raise RuntimeError("WhatsApp webhook reception is disabled")
        await self.ready.wait()
        if event["kind"] == "status":
            await self._persist("save_message", dict(event, bot_code=bot_code))
            return
        await self._persist(
            "advance_window",
            {
                "bot_code": bot_code,
                "recipient": event["destination"],
                "last_inbound": event["timestamp"],
            },
        )
        if not event["supported"]:
            return
        if event["action"]:
            await self.conversations.handle_action(
                bot_code, event["sender"], event["destination"], event["reply_id"], event["action"]
            )
            return
        if not await self.conversations.admit_participant(
            bot_code, event["sender"], event["destination"], True
        ):
            return
        command = COMMAND.fullmatch(event["text"])
        if command:
            try:
                node = self.get_bot(bot_code).route.node(command[1], errors=self.ROUTER_ERRORS)
                kwargs = self.spread_over_params(node, {
                    "text": command[2] or "",
                    "sender": copy.deepcopy(event["sender"]),
                    "chat_id": event["destination"],
                })
                result = await self._call(node, **kwargs)
            except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
                return
            if result is not None:
                if not isinstance(result, str):
                    raise TypeError("Bot command handlers must return str or None")
                await self.send_text(bot_code, event["destination"], result)
        else:
            await self.conversations.handle_text(
                bot_code, event["sender"], event["destination"], event["text"], event["reply_id"]
            )

    async def send_message(self, bot_code: str, chat_id: str, text: str) -> Any:
        """Submit one free-form text inside a known open service window."""
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise ValueError("WhatsApp text must contain 1-4096 characters")
        return await self.delivery.send(bot_code, chat_id, {"type": "text", "text": {"body": text}})

    async def send_template(
        self,
        bot_code: str,
        chat_id: str,
        *,
        name: str,
        language: str,
        components: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Submit an explicitly selected approved template; approval is checked by Meta."""
        template = self.delivery.get_template(name, language, components)
        return await self.delivery.send(
            bot_code, chat_id, {"type": "template", "template": template}, template=True
        )

    async def send_buttons(
        self, bot_code: str, chat_id: str, text: str, buttons: dict[str, str]
    ) -> Any:
        """Submit at most three label-to-payload reply buttons inside the service window."""
        if not 1 <= len(text) <= 1024 or not 1 <= len(buttons) <= 3:
            raise ValueError("buttons require 1-1024 text characters and 1-3 choices")
        if any(not 1 <= len(k) <= 20 or not 1 <= len(v) <= 256 for k, v in buttons.items()):
            raise ValueError("invalid button label or payload length")
        payload = {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": v, "title": k}}
                        for k, v in buttons.items()
                    ]
                },
            },
        }
        return await self.delivery.send(bot_code, chat_id, payload)

    async def send_media(
        self,
        bot_code: str,
        chat_id: str,
        kind: str,
        media: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        """Submit media by provider ID, HTTPS URL or uploaded bytes."""
        return await self.delivery.send_media(
            bot_code, chat_id, kind, media, filename=filename, caption=caption
        )

    async def get_message(self, bot_code: str, message_id: str) -> dict[str, Any]:
        """Read API acceptance and the most advanced delivery status received."""
        record: dict[str, Any] | None = await self._persist(
            "get_message", {"bot_code": bot_code, "message_id": message_id}
        )
        if record is None:
            raise LookupError("message not found")
        return record

    def _announcement_parts(self, bot_code, chat_ids, text, template):
        self.get_bot(bot_code)
        if not chat_ids or any(not self._valid_recipient(c) for c in chat_ids):
            raise ValueError("announcement requires valid recipient IDs")
        if template is not None:
            if text:
                raise ValueError("choose text or template explicitly")
            return [{"type": "template", "template": self.delivery.get_template(**template)}]
        return [
            {"type": "text", "text": {"body": part}} for part in self.delivery.get_text_parts(text)
        ]

    async def _send_payload(self, bot_code, recipient, payload):
        return await self.delivery.send(
            bot_code, recipient, payload, template=payload["type"] == "template"
        )

    async def send_announcement(
        self,
        bot_code: str,
        chat_ids: list[Any],
        text: str = "",
        *,
        template: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Submit explicit text or a template once per destination and collect outcomes."""
        parts = self._announcement_parts(bot_code, chat_ids, text, template)
        return await self._send_batch(bot_code, chat_ids, parts, self._send_payload)

    async def queue_announcement(
        self,
        bot_code: str,
        chat_ids: list[Any],
        text: str = "",
        *,
        template: dict[str, Any] | None = None,
    ) -> str:
        """Queue an announcement whose sending eligibility is checked at execution time."""
        self._announcement_parts(bot_code, chat_ids, text, template)
        return await self._queue_announcement(
            {
                "bot_code": bot_code,
                "chat_ids": chat_ids,
                "text": text,
                "template": copy.deepcopy(template),
            }
        )

    @route()
    async def deliver_announcement(
        self,
        bot_code: str,
        chat_ids: list[Any],
        text: str = "",
        template: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        await self.ready.wait()
        return await self.send_announcement(bot_code, chat_ids, text, template=template)

    async def schedule_reminder(
        self,
        bot_code: str,
        chat_id: Any,
        text: str = "",
        *,
        when: datetime,
        conversation_id: str | None = None,
        user_id: Any = None,
        template: dict[str, Any] | None = None,
    ) -> str:
        """Schedule explicit text or a template; record API acceptance separately from delivery."""
        if not self._valid_recipient(chat_id):
            raise ValueError("invalid recipient")
        result: str = await self.delivery.schedule_reminder(
            bot_code,
            chat_id,
            text,
            when=when,
            conversation_id=conversation_id,
            user_id=user_id,
            message_options=copy.deepcopy(template),
        )
        return result
