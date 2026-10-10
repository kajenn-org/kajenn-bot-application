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

"""Explicit Telegram account MCP and REST extensions."""

from typing import TYPE_CHECKING

from genro_routes import RoutingClass, route


if TYPE_CHECKING:
    from .telegram_account import TelegramAccountApplication


class _AccountExtraRoutes(RoutingClass):
    application: "TelegramAccountApplication"

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def download_media(self, chat_id: int, message_id: int) -> dict:
        """Download selected media as bytes, bounded to 5 MiB."""
        return await self.application.download_media(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def transcribe_message(self, chat_id: int, message_id: int, language: str = "it") -> dict:
        """Transcribe selected audio with the configured engine; no automatic reply."""
        return await self.application.transcribe_message(chat_id, message_id, language)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def react_message(self, chat_id: int, message_id: int, reaction: str) -> dict:
        """Set an emoji reaction, or remove it with an empty string."""
        return await self.application.react_message(chat_id, message_id, reaction)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def mark_read(self, chat_id: int, message_id: int) -> dict:
        """Mark messages read up to the selected message."""
        return await self.application.mark_read(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def archive_chat(self, chat_id: int, archived: bool = True) -> dict:
        """Archive or unarchive a chat."""
        return await self.application.archive_chat(chat_id, archived)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def mute_chat(self, chat_id: int, muted: bool = True) -> dict:
        """Mute a chat until 2038 or restore notifications."""
        return await self.application.mute_chat(chat_id, muted)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def pin_message(self, chat_id: int, message_id: int, pinned: bool = True) -> dict:
        """Pin or unpin a message without a notification."""
        return await self.application.pin_message(chat_id, message_id, pinned)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def block_contact(self, chat_id: int, blocked: bool = True) -> dict:
        """Block or unblock a personal Telegram contact."""
        return await self.application.block_contact(chat_id, blocked)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def set_profile(self, first_name: str, last_name: str = "", about: str = "") -> dict:
        """Set account name and about text; empty optional fields clear them."""
        return await self.application.set_profile(first_name, last_name, about)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_contacts(self, limit: int = 100, offset: int = 0) -> dict:
        """List readable contacts with policy filtering before pagination."""
        return await self.application.get_contacts(limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def forward_message(self, chat_id: int, source_chat_id: int, message_id: int) -> dict:
        """Forward one message; requires source history permission and destination write permission."""
        return await self.application.forward_message(chat_id, source_chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def schedule_message(self, chat_id: int, text: str, due: str) -> dict:
        """Schedule text on Telegram using an ISO 8601 date with timezone."""
        return await self.application.schedule_message(chat_id, text, due)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_scheduled_messages(self, chat_id: int, limit: int = 100, offset: int = 0) -> dict:
        """Read messages scheduled on Telegram for this chat."""
        return await self.application.get_scheduled_messages(chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def cancel_scheduled_message(self, chat_id: int, message_id: int) -> dict:
        """Cancel an outgoing scheduled message by its scheduled-message ID."""
        return await self.application.cancel_scheduled_message(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def create_poll(self, chat_id: int, question: str, options: list[str], multiple_choice: bool = False) -> dict:
        """Create an anonymous poll with two to ten options."""
        return await self.application.create_poll(chat_id, question, options, multiple_choice)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_poll(self, chat_id: int, message_id: int) -> dict:
        """Read poll choices and provider results; unknown counts remain null."""
        return await self.application.get_poll(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def vote_poll(self, chat_id: int, message_id: int, choices: list[int]) -> dict:
        """Vote using option indexes from get_poll; an empty list retracts your vote."""
        return await self.application.vote_poll(chat_id, message_id, choices)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def send_media(self, chat_id: int, kind: str, filename: str, content_base64: str, caption: str = "") -> dict:
        """Send photo, video, audio, voice or sticker bytes; never read server paths."""
        return await self.application.send_media(chat_id, kind, filename, content_base64, caption)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_events(self, after_id: int = 0, limit: int = 100) -> dict:
        """Replay permitted message event identifiers from the encrypted local journal."""
        return await self.application.get_events(after_id, limit)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_audit_log(self, after_id: int = 0, limit: int = 100) -> dict:
        """Read bounded operation outcomes and authenticated callers; excludes message text."""
        return await self.application.get_audit_log(after_id, limit)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def request_message(self, chat_id: int, text: str) -> dict:
        """Queue a text for explicit administrator approval without sending it."""
        return await self.application.request_message(chat_id, text)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account|admin", openapi_method="post")
    async def get_message_requests(self, limit: int = 100, offset: int = 0) -> dict:
        """Inspect queued texts and decisions for readable chats."""
        return await self.application.get_message_requests(limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def decide_message(self, request_id: str, decision: str) -> dict:
        """Approve and send once, reject or cancel a pending text; first decision wins."""
        return await self.application.decide_message(request_id, decision)
