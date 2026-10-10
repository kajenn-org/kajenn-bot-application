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

"""Explicit authenticated MCP/REST commands for the personal WhatsApp account.

Avatar roles filter discovery and execution through kajenn. Persistent account
policy further restricts operations and chat aliases, including trusted Python
calls. Provider mutations, policy replacement and reads share one lock. Audit
contains operation metadata, never message content or credentials. No provider
mutation is retried automatically after an uncertain result.
"""

from genro_routes import route

from examples.whatsapp_account.extended_routes import _ExtendedRoutes


class _Operations(_ExtendedRoutes):
    def __init__(self, application):
        self.application = application
        self.route.plug("channel")

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_status(self) -> dict:
        """Get connection state and visible record counts."""
        return await self.application.get_status()

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_sync_status(self) -> dict:
        """Get history coverage and callback failures without initiating sync."""
        return await self.application.get_sync_status()

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_contacts(self, query: str = "", limit: int = 50, offset: int = 0) -> dict:
        """Search permitted contacts; preserve ambiguous names."""
        return await self.application.get_contacts(query, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_chats(self, query: str = "", limit: int = 50, offset: int = 0, include_archived: bool = False) -> dict:
        """List locally observed permitted chats."""
        return await self.application.get_chats(query, limit, offset, include_archived)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_chat(self, chat_id: str) -> dict:
        """Read locally observed metadata for one chat."""
        return await self.application.get_chat(chat_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_unread(self, limit: int = 50, offset: int = 0) -> dict:
        """List chats observed as unread; unknown states are excluded."""
        return await self.application.get_unread(limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_messages(self, chat_id: str, limit: int = 50, offset: int = 0) -> dict:
        """Read synchronized messages, including provider-known aliases."""
        return await self.application.get_messages(chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def search_messages(self, query: str, chat_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        """Search local text in permitted chats only."""
        return await self.application.search_messages(query, chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_message_status(self, chat_id: str, message_id: str) -> dict:
        """Get submission state and per-recipient observed receipts."""
        return await self.application.get_message_status(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send explicit text to an exact known JID."""
        return await self.application.send_text(chat_id, text)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def reply_message(self, chat_id: str, message_id: str, text: str) -> dict:
        """Reply quoting a synchronized message."""
        return await self.application.reply_message(chat_id, message_id, text)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def react_message(self, chat_id: str, message_id: str, reaction: str) -> dict:
        """React to a synchronized message; empty reaction removes it."""
        return await self.application.react_message(chat_id, message_id, reaction)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def send_media(self, chat_id: str, kind: str, content_base64: str, mimetype: str, filename: str = "", caption: str = "") -> dict:
        """Send supplied image, document or audio bytes, never server paths."""
        return await self.application.send_media(chat_id, kind, content_base64, mimetype, filename, caption)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def download_media(self, chat_id: str, message_id: str) -> dict:
        """Download bounded media from a synchronized message."""
        return await self.application.download_media(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def mark_read(self, chat_id: str, read: bool = True) -> dict:
        """Mark a chat read or unread on WhatsApp."""
        return await self.application.mark_read(chat_id, read)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def archive_chat(self, chat_id: str, archived: bool = True) -> dict:
        """Archive or unarchive a chat on WhatsApp."""
        return await self.application.archive_chat(chat_id, archived)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def mute_chat(self, chat_id: str, muted: bool = True) -> dict:
        """Mute or unmute a chat on WhatsApp."""
        return await self.application.mute_chat(chat_id, muted)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_group(self, chat_id: str) -> dict:
        """Fetch group metadata from WhatsApp."""
        return await self.application.get_group(chat_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_group_members(self, chat_id: str, limit: int = 50, offset: int = 0) -> dict:
        """Fetch a page of current group participants."""
        return await self.application.get_group_members(chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def create_group(self, title: str, participants: list[str]) -> dict:
        """Create a group with explicitly selected known participants."""
        return await self.application.create_group(title, participants)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def update_group_members(self, chat_id: str, participants: list[str], action: str) -> dict:
        """Add, remove, promote or demote explicitly selected group participants."""
        return await self.application.update_group_members(chat_id, participants, action)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def request_history(self, chat_id: str, message_id: str, count: int = 50) -> dict:
        """Request older history from a known anchor; completion is asynchronous."""
        return await self.application.request_history(chat_id, message_id, count)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_policy(self) -> dict:
        """Read account operation and chat grants."""
        return await self.application.get_policy()

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def set_policy(self, policy: dict) -> dict:
        """Replace account grants durably; administrator only."""
        return await self.application.set_policy(policy)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def get_audit_log(self, limit: int = 50, offset: int = 0) -> dict:
        """Read operation metadata without message text or credentials."""
        return await self.application.get_audit_log(limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def schedule_message(self, chat_id: str, text: str, due: int, approval_required: bool = True) -> dict:
        """Queue a text for a Unix timestamp; approval is required by default."""
        return await self.application.schedule_message(chat_id, text, due, approval_required)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_outbox(self, limit: int = 50, offset: int = 0) -> dict:
        """Read permitted queued texts and dispatch outcomes."""
        return await self.application.get_outbox(limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="admin", openapi_method="post")
    async def decide_message(self, job_id: str, decision: str) -> dict:
        """Approve, reject or cancel a queued text; the first applicable decision wins."""
        return await self.application.decide_message(job_id, decision)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_events(self, after_id: int = 0, limit: int = 50) -> dict:
        """Replay permitted event identifiers from the bounded persistent journal."""
        return await self.application.get_events(after_id, limit)
