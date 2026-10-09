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

"""Account operations and owner management use distinct authenticated surfaces."""

from typing import TYPE_CHECKING

from genro_routes import RoutingClass, route

if TYPE_CHECKING:
    from .telegram_account import TelegramAccountApplication


class _AccountOperations(RoutingClass):
    def __init__(self, application: "TelegramAccountApplication"):
        self.application: TelegramAccountApplication
        self.application = application
        self.route.plug("channel")

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_status(self) -> dict:
        """Get status."""
        return await self.application.get_status()

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_chats(self, limit: int=100, offset: int=0) -> dict:
        """List readable dialogs with numeric IDs; names are display/search data only."""
        return await self.application.get_chats(limit=limit, offset=offset)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_messages(self, chat_id: int, limit: int=100, before_id: int=0, since: str | None=None, until: str | None=None, search: str | None=None) -> dict:
        """Read newest-first pages; preserve filters when following next_before_id."""
        return await self.application.get_messages(chat_id=chat_id, limit=limit, before_id=before_id, since=since, until=until, search=search)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def send_text(self, chat_id: int, text: str, reply_to: int | None=None) -> dict:
        """Send text."""
        return await self.application.send_text(chat_id=chat_id, text=text, reply_to=reply_to)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def send_document(self, chat_id: int, filename: str, content_base64: str, caption: str='') -> dict:
        """Send document."""
        return await self.application.send_document(chat_id=chat_id, filename=filename, content_base64=content_base64, caption=caption)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def edit_message(self, chat_id: int, message_id: int, text: str) -> dict:
        """Edit message."""
        return await self.application.edit_message(chat_id=chat_id, message_id=message_id, text=text)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> dict:
        """Delete messages."""
        return await self.application.delete_messages(chat_id=chat_id, message_ids=message_ids)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def create_channel(self, title: str, description: str='') -> dict:
        """Create channel."""
        return await self.application.create_channel(title=title, description=description)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def create_group(self, title: str, description: str='') -> dict:
        """Create a supergroup; invitations and grants are separate explicit operations."""
        return await self.application.create_group(title=title, description=description)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def set_chat_details(self, chat_id: int, title: str | None=None, description: str | None=None) -> dict:
        """Set chat details."""
        return await self.application.set_chat_details(chat_id=chat_id, title=title, description=description)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def get_members(self, chat_id: int, limit: int=100, offset: int=0) -> dict:
        """Get members."""
        return await self.application.get_members(chat_id=chat_id, limit=limit, offset=offset)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def invite_members(self, chat_id: int, user_ids: list[int]) -> dict:
        """Invite members."""
        return await self.application.invite_members(chat_id=chat_id, user_ids=user_ids)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def remove_member(self, chat_id: int, user_id: int) -> dict:
        """Remove member."""
        return await self.application.remove_member(chat_id=chat_id, user_id=user_id)

    @route(channel_channels="mcp,rest", auth_rule="telegram_account", openapi_method="post")
    async def set_member_admin(self, chat_id: int, user_id: int, rights: list[str]) -> dict:
        """Set member admin."""
        return await self.application.set_member_admin(chat_id=chat_id, user_id=user_id, rights=rights)


class _AccountAdministration(RoutingClass):
    def __init__(self, application: "TelegramAccountApplication"):
        self.application: TelegramAccountApplication
        self.application = application
        self.route.plug("channel")

    @route(channel_channels="rest", auth_rule="admin", openapi_method="post")
    async def get_policy(self) -> dict:
        """Get policy."""
        return await self.application.get_policy()

    @route(channel_channels="rest", auth_rule="admin", openapi_method="post")
    async def set_policy(self, policy: dict) -> dict:
        """Replace grants durably; trusted Python or administrator-only REST."""
        return await self.application.set_policy(policy=policy)

    @route(channel_channels="rest", auth_rule="admin", openapi_method="post")
    async def revoke_session(self) -> dict:
        """Log out this Telegram device and erase the stored authorization."""
        return await self.application.revoke_session()
