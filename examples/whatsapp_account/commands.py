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

"""Validated account commands sharing policy, serialization and audit."""

from kajenn.exceptions import HTTPBadRequest


class _ExtendedCommands:
    async def pin_chat(self, chat_id: str, pinned: bool = True):
        self.validate_peer(chat_id)
        if type(pinned) is not bool:
            raise HTTPBadRequest("pinned must be boolean")
        return await self.run_operation("pin_chat", chat_id, self.connection.pin_chat, chat_id, pinned, remote=True)

    async def star_message(self, chat_id: str, message_id: str, starred: bool = True):
        self.validate_message(chat_id, message_id)
        if type(starred) is not bool:
            raise HTTPBadRequest("starred must be boolean")
        return await self.run_operation("star_message", chat_id, self.connection.star_message, chat_id, message_id, starred, remote=True)

    async def edit_message(self, chat_id: str, message_id: str, text: str):
        self.validate_message(chat_id, message_id)
        self.validate_text(text, 4000, empty=False)
        return await self.run_operation("edit_message", chat_id, self.connection.edit_message, chat_id, message_id, text, remote=True)

    async def save_contact(self, chat_id: str, name: str):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith(("@s.whatsapp.net", "@lid")):
            raise HTTPBadRequest("A personal JID is required")
        self.validate_text(name, 200, empty=False)
        return await self.run_operation("save_contact", chat_id, self.connection.save_contact, chat_id, name, remote=True)

    async def get_profile_picture(self, chat_id: str, preview: bool = True):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith(("@s.whatsapp.net", "@lid")):
            raise HTTPBadRequest("A personal JID is required")
        if type(preview) is not bool:
            raise HTTPBadRequest("preview must be boolean")
        return await self.run_operation("get_profile_picture", chat_id, self.connection.get_profile_picture, chat_id, preview, remote=True)

    async def block_contact(self, chat_id: str, blocked: bool = True):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith(("@s.whatsapp.net", "@lid")):
            raise HTTPBadRequest("A personal JID is required")
        if type(blocked) is not bool:
            raise HTTPBadRequest("blocked must be boolean")
        return await self.run_operation("block_contact", chat_id, self.connection.block_contact, chat_id, blocked, remote=True)

    async def set_presence(self, available: bool):
        if type(available) is not bool:
            raise HTTPBadRequest("available must be boolean")
        return await self.run_operation("set_presence", None, self.connection.set_presence, available, remote=True)

    async def send_chat_state(self, chat_id: str, state: str):
        self.validate_peer(chat_id)
        if type(state) is not str or state not in ('composing', 'recording', 'paused'):
            raise HTTPBadRequest("Unsupported state")
        return await self.run_operation("send_chat_state", chat_id, self.connection.send_chat_state, chat_id, state, remote=True)

    async def set_profile_name(self, name: str):
        self.validate_text(name, 100, empty=False)
        return await self.run_operation("set_profile_name", None, self.connection.set_profile_name, name, remote=True)

    async def set_profile_about(self, text: str):
        self.validate_text(text, 139, empty=False)
        return await self.run_operation("set_profile_about", None, self.connection.set_profile_about, text, remote=True)

    async def get_privacy(self):
        return await self.run_operation("get_privacy", None, self.connection.get_privacy, remote=True)

    async def set_privacy(self, category: str, value: str):
        if type(category) is not str or category not in ('Last', 'Online', 'Profile', 'Status', 'GroupAdd', 'ReadReceipts', 'CallAdd', 'Messages', 'DefenseMode'):
            raise HTTPBadRequest("Unsupported category")
        if type(value) is not str or value not in ('All', 'Contacts', 'None_', 'ContactBlacklist', 'MatchLastSeen', 'Known', 'Off', 'OnStandard'):
            raise HTTPBadRequest("Unsupported value")
        return await self.run_operation("set_privacy", None, self.connection.set_privacy, category, value, remote=True)

    async def set_disappearing_default(self, seconds: int):
        if type(seconds) is not int or seconds not in (0, 86400, 604800, 7776000):
            raise HTTPBadRequest("Unsupported seconds")
        return await self.run_operation("set_disappearing_default", None, self.connection.set_disappearing_default, seconds, remote=True)

    async def set_group_title(self, chat_id: str, title: str):
        self.validate_group(chat_id)
        self.validate_text(title, 100, empty=False)
        return await self.run_operation("set_group_title", chat_id, self.connection.set_group_title, chat_id, title, remote=True)

    async def set_group_description(self, chat_id: str, description: str):
        self.validate_group(chat_id)
        self.validate_text(description, 2048, empty=True)
        return await self.run_operation("set_group_description", chat_id, self.connection.set_group_description, chat_id, description, remote=True)

    async def leave_group(self, chat_id: str):
        self.validate_group(chat_id)
        return await self.run_operation("leave_group", chat_id, self.connection.leave_group, chat_id, remote=True)

    async def get_group_invite(self, chat_id: str, reset: bool = False):
        self.validate_group(chat_id)
        if type(reset) is not bool:
            raise HTTPBadRequest("reset must be boolean")
        return await self.run_operation("get_group_invite", chat_id, self.connection.get_group_invite, chat_id, reset, remote=True)

    async def set_group_setting(self, chat_id: str, setting: str, enabled: bool):
        self.validate_group(chat_id)
        if type(enabled) is not bool:
            raise HTTPBadRequest("enabled must be boolean")
        if type(setting) is not str or setting not in ('locked', 'announce', 'no_frequently_forwarded', 'allow_admin_reports', 'group_history', 'limit_sharing'):
            raise HTTPBadRequest("Unsupported setting")
        return await self.run_operation("set_group_setting", chat_id, self.connection.set_group_setting, chat_id, setting, enabled, remote=True)

    async def set_group_disappearing(self, chat_id: str, seconds: int):
        self.validate_group(chat_id)
        if type(seconds) is not int or seconds not in (0, 86400, 604800, 7776000):
            raise HTTPBadRequest("Unsupported seconds")
        return await self.run_operation("set_group_disappearing", chat_id, self.connection.set_group_disappearing, chat_id, seconds, remote=True)

    async def set_group_approval(self, chat_id: str, enabled: bool):
        self.validate_group(chat_id)
        if type(enabled) is not bool:
            raise HTTPBadRequest("enabled must be boolean")
        return await self.run_operation("set_group_approval", chat_id, self.connection.set_group_approval, chat_id, enabled, remote=True)

    async def set_group_member_add(self, chat_id: str, admins_only: bool):
        self.validate_group(chat_id)
        if type(admins_only) is not bool:
            raise HTTPBadRequest("admins_only must be boolean")
        return await self.run_operation("set_group_member_add", chat_id, self.connection.set_group_member_add, chat_id, admins_only, remote=True)

    async def create_label(self, label_id: str, name: str, color: int):
        self.validate_text(label_id, 64, empty=False)
        self.validate_text(name, 100, empty=False)
        if type(color) is not int or not 0 <= color <= 19:
            raise HTTPBadRequest("color is outside the supported range")
        return await self.run_operation("create_label", None, self.connection.create_label, label_id, name, color, remote=True)

    async def delete_label(self, label_id: str):
        self.validate_text(label_id, 64, empty=False)
        return await self.run_operation("delete_label", None, self.connection.delete_label, label_id, remote=True)

    async def set_chat_label(self, chat_id: str, label_id: str, assigned: bool = True):
        self.validate_peer(chat_id)
        self.validate_text(label_id, 64, empty=False)
        if type(assigned) is not bool:
            raise HTTPBadRequest("assigned must be boolean")
        return await self.run_operation("set_chat_label", chat_id, self.connection.set_chat_label, chat_id, label_id, assigned, remote=True)

    async def create_channel(self, title: str, description: str = ""):
        self.validate_text(title, 100, empty=False)
        self.validate_text(description, 2048, empty=True)
        return await self.run_operation("create_channel", None, self.connection.create_channel, title, description, remote=True)

    async def get_channel(self, chat_id: str):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        return await self.run_operation("get_channel", chat_id, self.connection.get_channel, chat_id, remote=True)

    async def follow_channel(self, chat_id: str, follow: bool = True):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        if type(follow) is not bool:
            raise HTTPBadRequest("follow must be boolean")
        return await self.run_operation("follow_channel", chat_id, self.connection.follow_channel, chat_id, follow, remote=True)

    async def update_channel(self, chat_id: str, title: str, description: str = ""):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        self.validate_text(title, 100, empty=False)
        self.validate_text(description, 2048, empty=True)
        return await self.run_operation("update_channel", chat_id, self.connection.update_channel, chat_id, title, description, remote=True)

    async def send_channel_text(self, chat_id: str, text: str):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        self.validate_text(text, 4000, empty=False)
        return await self.run_operation("send_channel_text", chat_id, self.connection.send_channel_text, chat_id, text, remote=True)

    async def mute_channel(self, chat_id: str, muted: bool = True):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        if type(muted) is not bool:
            raise HTTPBadRequest("muted must be boolean")
        return await self.run_operation("mute_channel", chat_id, self.connection.mute_channel, chat_id, muted, remote=True)


    async def revoke_message(self, chat_id: str, message_id: str):
        self.validate_message(chat_id, message_id)
        return await self.run_operation("revoke_message", chat_id, self.connection.revoke_message, chat_id, message_id, remote=True)

    async def delete_message(self, chat_id: str, message_id: str):
        self.validate_message(chat_id, message_id)
        return await self.run_operation("delete_message", chat_id, self.connection.delete_message, chat_id, message_id, remote=True)

    async def get_channel_messages(self, chat_id: str, limit: int = 50, before: int = 0):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPBadRequest("limit is outside the supported range")
        if type(before) is not int or not 0 <= before <= 9223372036854775807:
            raise HTTPBadRequest("before is outside the supported range")
        return await self.run_operation("get_channel_messages", chat_id, self.connection.get_channel_messages, chat_id, limit, before, remote=True)

    async def react_channel_message(self, chat_id: str, server_id: int, reaction: str):
        self.validate_peer(chat_id, known=False)
        if not chat_id.endswith("@newsletter"):
            raise HTTPBadRequest("A newsletter JID is required")
        if type(server_id) is not int or not 1 <= server_id <= 9223372036854775807:
            raise HTTPBadRequest("server_id is outside the supported range")
        self.validate_text(reaction, 32, empty=True)
        return await self.run_operation("react_channel_message", chat_id, self.connection.react_channel_message, chat_id, server_id, reaction, remote=True)

    async def create_poll(self, chat_id: str, title: str, options: list[str], selectable_count: int = 1):
        self.validate_peer(chat_id)
        self.validate_text(title, 255, empty=False)
        if type(selectable_count) is not int or not 1 <= selectable_count <= 12:
            raise HTTPBadRequest("selectable_count is outside the supported range")
        if not isinstance(options, list) or not 2 <= len(options) <= 12:
            raise HTTPBadRequest("Polls require two to twelve options")
        for option in options:
            self.validate_text(option, 100)
        if len(set(options)) != len(options) or selectable_count > len(options):
            raise HTTPBadRequest("Poll options must be unique and selectable_count bounded")
        return await self.run_operation("create_poll", chat_id, self.connection.create_poll, chat_id, title, options, selectable_count, remote=True)

    async def create_group_event(self, chat_id: str, title: str, start_time: int, end_time: int, description: str = ""):
        self.validate_group(chat_id)
        self.validate_text(title, 100, empty=False)
        self.validate_text(description, 1024, empty=True)
        if type(start_time) is not int or not 1 <= start_time <= 4102444800:
            raise HTTPBadRequest("start_time is outside the supported range")
        if type(end_time) is not int or not 1 <= end_time <= 4102444800:
            raise HTTPBadRequest("end_time is outside the supported range")
        if end_time <= start_time:
            raise HTTPBadRequest("end_time must follow start_time")
        return await self.run_operation("create_group_event", chat_id, self.connection.create_group_event, chat_id, title, start_time, end_time, description, remote=True)

    async def create_community(self, title: str, description: str = ""):
        self.validate_text(title, 100, empty=False)
        self.validate_text(description, 2048, empty=True)
        return await self.run_operation("create_community", None, self.connection.create_community, title, description, remote=True)

    async def deactivate_community(self, chat_id: str):
        self.validate_group(chat_id)
        return await self.run_operation("deactivate_community", chat_id, self.connection.deactivate_community, chat_id, remote=True)

    async def get_group_requests(self, chat_id: str, limit: int = 50, offset: int = 0):
        self.validate_group(chat_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPBadRequest("limit is outside the supported range")
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise HTTPBadRequest("offset is outside the supported range")
        return await self.run_operation("get_group_requests", chat_id, self.connection.get_group_requests, chat_id, limit, offset, remote=True)


    async def vote_poll(self, chat_id, message_id, options):
        self.validate_peer(chat_id, known=False)
        self.validate_text(message_id, 200)
        if not isinstance(options, list) or len(options) > 12:
            raise HTTPBadRequest("options must contain at most twelve selections")
        for option in options:
            self.validate_text(option, 100)
        if len(set(options)) != len(options):
            raise HTTPBadRequest("Selections must be unique")
        return await self.run_operation("vote_poll", chat_id, self.connection.vote_poll,
                                        chat_id, message_id, options, remote=True)

    async def respond_group_event(self, chat_id, message_id, response):
        self.validate_group(chat_id)
        self.validate_text(message_id, 200)
        if type(response) is not str or response not in ("Going", "NotGoing", "Maybe"):
            raise HTTPBadRequest("Unsupported event response")
        return await self.run_operation("respond_group_event", chat_id, self.connection.respond_group_event,
                                        chat_id, message_id, response, remote=True)

    async def get_poll_results(self, chat_id, message_id):
        self.validate_peer(chat_id, known=False)
        self.validate_text(message_id, 200)
        return await self.run_operation("get_poll_results", chat_id, self.connection.get_poll_results,
                                        chat_id, message_id)

    async def decide_group_requests(self, chat_id, participants, approve):
        self.validate_group(chat_id)
        self.validate_participants(participants)
        if type(approve) is not bool:
            raise HTTPBadRequest("approve must be boolean")
        return await self.run_operation("decide_group_requests", chat_id, self.connection.decide_group_requests,
                                        chat_id, participants, approve, remote=True)

    async def link_community_groups(self, chat_id, groups, linked=True):
        self.validate_group(chat_id)
        if not isinstance(groups, list) or not 1 <= len(groups) <= 20:
            raise HTTPBadRequest("groups must contain one to twenty group JIDs")
        for group in groups:
            self.validate_group(group)
            if group == chat_id:
                raise HTTPBadRequest("A community cannot link itself")
        if len(set(groups)) != len(groups) or type(linked) is not bool:
            raise HTTPBadRequest("Groups must be unique and linked must be boolean")
        return await self.run_operation("link_community_groups", chat_id, self.link_checked_groups,
                                        chat_id, groups, linked, remote=True)

    async def link_checked_groups(self, chat_id, groups, linked):
        for group in groups:
            self.access.require("link_community_groups", group)
        return await self.connection.link_community_groups(chat_id, groups, linked)

    async def get_community_groups(self, chat_id, limit=50, offset=0):
        self.validate_group(chat_id)
        self.validate_page("", limit, offset)
        return await self.run_operation("get_community_groups", chat_id, self.read_community_groups,
                                        chat_id, limit, offset, remote=True)

    async def read_community_groups(self, chat_id, limit, offset):
        items = await self.connection.get_community_groups(chat_id)
        allowed = [item for item in items if self.access.allowed(item["id"], "read")]
        return {"items": allowed[offset:offset + limit],
                "next_offset": offset + limit if len(allowed) > offset + limit else None}

    async def get_channels(self, limit=50, offset=0):
        self.validate_page("", limit, offset)
        return await self.run_operation("get_channels", None, self.read_channels, limit, offset, remote=True)

    async def read_channels(self, limit, offset):
        items = await self.connection.get_channels()
        allowed = [item for item in items if self.access.allowed(item["chat_id"], "read")]
        return {"items": allowed[offset:offset + limit],
                "next_offset": offset + limit if len(allowed) > offset + limit else None}
