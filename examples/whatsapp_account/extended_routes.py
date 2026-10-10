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

"""Explicit extended account MCP and REST commands."""

from genro_routes import RoutingClass, route


class _ExtendedRoutes(RoutingClass):
    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def pin_chat(self, chat_id: str, pinned: bool = True) -> dict:
        """Pin or unpin a chat on this account."""
        return await self.application.pin_chat(chat_id, pinned)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def star_message(self, chat_id: str, message_id: str, starred: bool = True) -> dict:
        """Star or unstar an observed message."""
        return await self.application.star_message(chat_id, message_id, starred)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def edit_message(self, chat_id: str, message_id: str, text: str) -> dict:
        """Edit an observed outgoing message; provider time limits still apply."""
        return await self.application.edit_message(chat_id, message_id, text)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def save_contact(self, chat_id: str, name: str) -> dict:
        """Save a contact using an exact personal JID."""
        return await self.application.save_contact(chat_id, name)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_profile_picture(self, chat_id: str, preview: bool = True) -> dict:
        """Get profile picture metadata; availability depends on privacy settings."""
        return await self.application.get_profile_picture(chat_id, preview)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def block_contact(self, chat_id: str, blocked: bool = True) -> dict:
        """Block or unblock an exact contact."""
        return await self.application.block_contact(chat_id, blocked)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_presence(self, available: bool) -> dict:
        """Set account online availability."""
        return await self.application.set_presence(available)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def send_chat_state(self, chat_id: str, state: str) -> dict:
        """Send a typing, recording or paused indication."""
        return await self.application.send_chat_state(chat_id, state)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_profile_name(self, name: str) -> dict:
        """Set the account display name."""
        return await self.application.set_profile_name(name)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_profile_about(self, text: str) -> dict:
        """Set the account about text."""
        return await self.application.set_profile_about(text)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_privacy(self) -> dict:
        """Read account privacy settings."""
        return await self.application.get_privacy()

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_privacy(self, category: str, value: str) -> dict:
        """Set a named privacy setting; unsupported combinations are rejected by WhatsApp."""
        return await self.application.set_privacy(category, value)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_disappearing_default(self, seconds: int) -> dict:
        """Set the default disappearing-message duration for new chats."""
        return await self.application.set_disappearing_default(seconds)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_title(self, chat_id: str, title: str) -> dict:
        """Change a group title."""
        return await self.application.set_group_title(chat_id, title)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_description(self, chat_id: str, description: str) -> dict:
        """Change a group description using its current revision."""
        return await self.application.set_group_description(chat_id, description)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def leave_group(self, chat_id: str) -> dict:
        """Leave a group."""
        return await self.application.leave_group(chat_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def get_group_invite(self, chat_id: str, reset: bool = False) -> dict:
        """Get a group invitation link, optionally revoking the previous link."""
        return await self.application.get_group_invite(chat_id, reset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_setting(self, chat_id: str, setting: str, enabled: bool) -> dict:
        """Set an explicitly supported group permission or sharing setting."""
        return await self.application.set_group_setting(chat_id, setting, enabled)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_disappearing(self, chat_id: str, seconds: int) -> dict:
        """Set disappearing messages for a group."""
        return await self.application.set_group_disappearing(chat_id, seconds)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_approval(self, chat_id: str, enabled: bool) -> dict:
        """Enable or disable approval of group membership requests."""
        return await self.application.set_group_approval(chat_id, enabled)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def set_group_member_add(self, chat_id: str, admins_only: bool) -> dict:
        """Choose who may add group members."""
        return await self.application.set_group_member_add(chat_id, admins_only)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def create_label(self, label_id: str, name: str, color: int) -> dict:
        """Create an account label with a WhatsApp color index."""
        return await self.application.create_label(label_id, name, color)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def delete_label(self, label_id: str) -> dict:
        """Delete an account label."""
        return await self.application.delete_label(label_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def set_chat_label(self, chat_id: str, label_id: str, assigned: bool = True) -> dict:
        """Assign or remove a label from a chat."""
        return await self.application.set_chat_label(chat_id, label_id, assigned)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def create_channel(self, title: str, description: str = "") -> dict:
        """Create a WhatsApp newsletter channel."""
        return await self.application.create_channel(title, description)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_channel(self, chat_id: str) -> dict:
        """Read newsletter channel metadata."""
        return await self.application.get_channel(chat_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def follow_channel(self, chat_id: str, follow: bool = True) -> dict:
        """Follow or unfollow a newsletter channel."""
        return await self.application.follow_channel(chat_id, follow)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def update_channel(self, chat_id: str, title: str, description: str = "") -> dict:
        """Change newsletter channel title and description."""
        return await self.application.update_channel(chat_id, title, description)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def send_channel_text(self, chat_id: str, text: str) -> dict:
        """Publish text to a newsletter channel you administer."""
        return await self.application.send_channel_text(chat_id, text)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def mute_channel(self, chat_id: str, muted: bool = True) -> dict:
        """Mute or unmute a followed newsletter channel."""
        return await self.application.mute_channel(chat_id, muted)


    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def revoke_message(self, chat_id: str, message_id: str) -> dict:
        """Revoke your own observed message for everyone; provider limits apply."""
        return await self.application.revoke_message(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def delete_message(self, chat_id: str, message_id: str) -> dict:
        """Delete an observed message for this account only, preserving downloaded media."""
        return await self.application.delete_message(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_channel_messages(self, chat_id: str, limit: int = 50, before: int = 0) -> dict:
        """Read one provider page from a newsletter; server IDs differ from message IDs."""
        return await self.application.get_channel_messages(chat_id, limit, before)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def react_channel_message(self, chat_id: str, server_id: int, reaction: str) -> dict:
        """React to a newsletter message using its numeric server ID."""
        return await self.application.react_channel_message(chat_id, server_id, reaction)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def create_poll(self, chat_id: str, title: str, options: list[str], selectable_count: int = 1) -> dict:
        """Create a poll with two to twelve unique options; retain its secret privately."""
        return await self.application.create_poll(chat_id, title, options, selectable_count)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def create_group_event(self, chat_id: str, title: str, start_time: int, end_time: int, description: str = "") -> dict:
        """Create a scheduled group event using Unix timestamps; never returns its secret."""
        return await self.application.create_group_event(chat_id, title, start_time, end_time, description)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def create_community(self, title: str, description: str = "") -> dict:
        """Create a WhatsApp community."""
        return await self.application.create_community(title, description)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def deactivate_community(self, chat_id: str) -> dict:
        """Deactivate a community administered by this account."""
        return await self.application.deactivate_community(chat_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def get_group_requests(self, chat_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List pending group membership requests."""
        return await self.application.get_group_requests(chat_id, limit, offset)


    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def vote_poll(self, chat_id: str, message_id: str, options: list[str]) -> dict:
        """Vote in a retained poll; an empty selection withdraws a vote."""
        return await self.application.vote_poll(chat_id, message_id, options)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_write", openapi_method="post")
    async def respond_group_event(self, chat_id: str, message_id: str, response: str) -> dict:
        """Respond Going, NotGoing or Maybe to a retained group event."""
        return await self.application.respond_group_event(chat_id, message_id, response)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_poll_results(self, chat_id: str, message_id: str) -> dict:
        """Aggregate retained poll votes; reports gaps and decryption failures."""
        return await self.application.get_poll_results(chat_id, message_id)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def decide_group_requests(self, chat_id: str, participants: list[str], approve: bool) -> dict:
        """Approve or reject explicit group membership requests."""
        return await self.application.decide_group_requests(chat_id, participants, approve)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_manage", openapi_method="post")
    async def link_community_groups(self, chat_id: str, groups: list[str], linked: bool = True) -> dict:
        """Link or unlink subgroups; requires admin grants on every affected group."""
        return await self.application.link_community_groups(chat_id, groups, linked)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_community_groups(self, chat_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List permitted community subgroups."""
        return await self.application.get_community_groups(chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account_read", openapi_method="post")
    async def get_channels(self, limit: int = 50, offset: int = 0) -> dict:
        """List permitted subscribed newsletter channels."""
        return await self.application.get_channels(limit, offset)
