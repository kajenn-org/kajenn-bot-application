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

"""Administrative routes call their owning application, never a bot command.

The provider-specific routers are mounted at /_admin and exposed at /_mcp.
Every operation requires the server's admin role. Registration selects a class
from the deployment's bot_classes catalog; callers cannot import arbitrary code.
Registry responses contain explicit non-secret metadata, excluding raw config,
provider tokens and webhook credentials. Webhooks and internal task routes are
outside this surface. REST operations use POST with JSON arguments.
"""

from __future__ import annotations

import copy
import importlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from genro_routes import RoutingClass, route
from kajenn.exceptions import HTTPBadRequest


if TYPE_CHECKING:
    from .bot import BotBaseApplication
    from .telegram import TelegramBotApplication
    from .whatsapp import WhatsAppBotApplication


class _Administration(RoutingClass):
    """Shared administrative operations, scoped to one owning application."""

    def __init__(self, application: BotBaseApplication):
        self.application = application
        self.route.plug("channel")

    def _get_bot_class(self, name):
        catalog = self.application.bot_classes
        if name not in catalog:
            raise HTTPBadRequest("bot_class must name a configured class")
        bot_class = catalog[name]
        if isinstance(bot_class, str):
            module, class_name = bot_class.split(":", 1)
            bot_class = getattr(importlib.import_module(module), class_name)
        return bot_class

    def _require_recipient(self, chat_id):
        if not self.application._valid_recipient(chat_id):
            raise HTTPBadRequest("invalid recipient for this provider")

    def _get_when(self, when: str) -> datetime:
        try:
            value = datetime.fromisoformat(when)
            if value.utcoffset() is None:
                raise ValueError("timezone is required")
            return value
        except ValueError as error:
            raise HTTPBadRequest("when must be an ISO 8601 datetime with timezone") from error

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    def list_bot_classes(self) -> list[str]:
        """List the deployment's allowed registration aliases."""
        return sorted(self.application.bot_classes)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    def list_bots(self) -> list[dict[str, Any]]:
        """List registered bot metadata without credentials or raw configuration."""
        return [self._get_registration(code) for code in sorted(self.application.registrations)]

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    def get_bot_registration(self, code: str) -> dict[str, Any]:
        """Read non-secret registration metadata; never return tokens or config."""
        return self._get_registration(code)

    def _get_registration(self, code: str) -> dict[str, Any]:
        record = self.application.get_bot_registration(code)
        fields = ("code", "bot_class", "name", "icon", "username", "phone_number_id",
                  "business_account_id")
        return {key: copy.deepcopy(record[key]) for key in fields if key in record}

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def activate_bot(self, code: str) -> dict[str, Any]:
        """Activate a saved registration, renewing its webhook where applicable."""
        await self.application.activate_bot(code)
        return self._get_registration(code)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_text(self, bot_code: str, chat_id: int | str, text: str) -> list[Any]:
        """Send text in provider-sized chunks directly through the application."""
        self._require_recipient(chat_id)
        return await self.application.send_text(bot_code, chat_id, text)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_announcement(
        self, bot_code: str, chat_ids: list[int | str], text: str
    ) -> list[dict[str, Any]]:
        """Send an announcement and report each destination's outcome."""
        return await self.application.send_announcement(bot_code, chat_ids, text)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def queue_announcement(self, bot_code: str, chat_ids: list[int | str], text: str) -> str:
        """Queue an announcement and return its task identifier."""
        return await self.application.queue_announcement(bot_code, chat_ids, text)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def create_conversation(
        self, bot_code: str, participants: list[dict[str, Any]], route: str,
        context: dict[str, Any] | None = None, expires_at: float | None = None,
    ) -> dict[str, Any]:
        """Create a conversation using an existing bot route for subsequent replies."""
        return await self.application.create_conversation(
            bot_code, participants=participants, route=route, context=context, expires_at=expires_at
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_conversation(self, bot_code: str, conversation_id: str) -> dict[str, Any]:
        """Read a persisted conversation and its state."""
        return await self.application.get_conversation(bot_code, conversation_id)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_conversation_message(
        self, bot_code: str, conversation_id: str, user_id: int | str, text: str,
        buttons: dict[str, str] | None = None, chat_id: int | str | None = None,
    ) -> Any:
        """Send to one conversation participant, optionally with action buttons."""
        return await self.application.send_conversation_message(
            bot_code, conversation_id, user_id, text, buttons=buttons, chat_id=chat_id
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def update_conversation_context(
        self, bot_code: str, conversation_id: str, context: dict[str, Any], revision: int,
    ) -> dict[str, Any]:
        """Replace conversation context only if its revision still matches."""
        return await self.application.update_conversation_context(
            bot_code, conversation_id, context, revision=revision
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def close_conversation(
        self, bot_code: str, conversation_id: str, state: str = "closed"
    ) -> None:
        """Close or cancel a conversation without making an admission decision."""
        await self.application.close_conversation(bot_code, conversation_id, state=state)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def schedule_reminder(
        self, bot_code: str, chat_id: int | str, text: str, when: str,
        conversation_id: str | None = None, user_id: int | str | None = None,
    ) -> str:
        """Schedule a reminder; when is an ISO 8601 datetime including its timezone."""
        self._require_recipient(chat_id)
        return await self.application.schedule_reminder(
            bot_code, chat_id, text, when=self._get_when(when),
            conversation_id=conversation_id, user_id=user_id,
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_reminder(self, code: str) -> dict[str, Any]:
        """Read a reminder's schedule and delivery state."""
        return await self.application.get_reminder(code)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def cancel_reminder(self, code: str) -> bool:
        """Cancel a pending reminder; false means it already left pending state."""
        return await self.application.cancel_reminder(code)


class _TelegramAdministration(_Administration):
    """Telegram administration, with integer chat IDs and native polls."""

    application: TelegramBotApplication

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def register_bot(
        self, code: str, bot_class: str, token: str, name: str = "",
        icon: str | None = None, config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register BotFather credentials using an allowed bot-class alias."""
        await self.application.register_bot(
            code=code, bot_class=self._get_bot_class(bot_class), token=token,
            name=name, icon=icon, config=config,
        )
        return self._get_registration(code)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_message(
        self, bot_code: str, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None,
    ) -> Any:
        """Send one Telegram message without invoking a bot command."""
        return await self.application.send_message(bot_code, chat_id, text, reply_markup=reply_markup)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_poll(
        self, bot_code: str, chat_id: int, question: str, options: list[str],
        is_anonymous: bool = True, allows_multiple_answers: bool = False, route: str | None = None,
    ) -> Any:
        """Send a native Telegram poll and optionally route later updates to a bot."""
        return await self.application.send_poll(
            bot_code, chat_id, question, options, is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers, route=route,
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_poll(self, bot_code: str, poll_id: str) -> dict[str, Any]:
        """Read tracked Telegram poll results."""
        return await self.application.get_poll(bot_code, poll_id)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def stop_poll(self, bot_code: str, poll_id: str) -> Any:
        """Close a native Telegram poll."""
        return await self.application.stop_poll(bot_code, poll_id)


class _WhatsAppAdministration(_Administration):
    """WhatsApp administration, preserving string IDs and template semantics."""

    application: WhatsAppBotApplication

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def register_bot(
        self, code: str, bot_class: str, token: str, phone_number_id: str, business_account_id: str,
        name: str = "", icon: str | None = None, config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a business number using an allowed bot-class alias."""
        await self.application.register_bot(
            code=code, bot_class=self._get_bot_class(bot_class), token=token,
            phone_number_id=phone_number_id, business_account_id=business_account_id,
            name=name, icon=icon, config=config,
        )
        return self._get_registration(code)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_message(self, bot_code: str, chat_id: str, text: str) -> Any:
        """Send a WhatsApp message inside the recipient's open service window."""
        return await self.application.send_message(bot_code, chat_id, text)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_template(
        self, bot_code: str, chat_id: str, name: str, language: str,
        components: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Send an approved template, including outside the service window."""
        return await self.application.send_template(
            bot_code, chat_id, name=name, language=language, components=components
        )

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_message(self, bot_code: str, message_id: str) -> dict[str, Any]:
        """Read the provider acceptance and delivery status of a sent message."""
        return await self.application.get_message(bot_code, message_id)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def send_announcement(
        self, bot_code: str, chat_ids: list[str], text: str = "",
        template: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Send text or an explicit template and report each destination's outcome."""
        return await self.application.send_announcement(bot_code, chat_ids, text, template=template)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def queue_announcement(
        self, bot_code: str, chat_ids: list[str], text: str = "",
        template: dict[str, Any] | None = None,
    ) -> str:
        """Queue text or a template; sending eligibility is checked at execution."""
        return await self.application.queue_announcement(bot_code, chat_ids, text, template=template)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def schedule_reminder(
        self, bot_code: str, chat_id: str, when: str, text: str = "",
        conversation_id: str | None = None, user_id: str | None = None,
        template: dict[str, Any] | None = None,
    ) -> str:
        """Schedule text or a template with an ISO 8601 datetime including timezone."""
        return await self.application.schedule_reminder(
            bot_code, chat_id, text, when=self._get_when(when),
            conversation_id=conversation_id, user_id=user_id, template=template,
        )
