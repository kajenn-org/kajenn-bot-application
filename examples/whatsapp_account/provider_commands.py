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

"""Typed provider operations; never retries a mutation."""

import hashlib
import itertools
import json

from kajenn.exceptions import HTTPBadRequest, HTTPException
from tryx.client import PrivacyCategory, PrivacyValue, MembershipApprovalMode, MemberAddMode, CreateCommunityOptions, EventResponse, PollsClient
from tryx.waproto.whatsapp_pb2 import Message


class _ProviderCommands:
    async def pin_chat(self, chat_id: str, pinned: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.chat_actions.pin_chat(jid) if pinned else await client.chat_actions.unpin_chat(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def star_message(self, chat_id: str, message_id: str, starred: bool = True):
        client = self.session.client
        row = self.get_stored_message(chat_id, message_id)
        participant = self.jid(row["sender"]) if row["chat_id"].endswith("@g.us") and not row["from_me"] else None
        method = client.chat_actions.star_message if starred else client.chat_actions.unstar_message
        await method(self.jid(row["chat_id"]), participant, row["id"], bool(row["from_me"]))
        return {"status": "returned", "chat_id": chat_id}

    async def edit_message(self, chat_id: str, message_id: str, text: str):
        client = self.session.client
        row = self.get_stored_message(chat_id, message_id)
        if not row["from_me"]:
            raise HTTPBadRequest("Only your own messages can be edited")
        await client.chat_actions.edit_message(self.jid(row["chat_id"]), row["id"], Message(conversation=text))
        self.directory.add_message(row["chat_id"], row["id"], row["sender"], text, row["timestamp"], True)
        self.directory.set_message_data(row["chat_id"], row["id"], Message(conversation=text).SerializeToString(), "text", "submitted")
        return {"status": "returned", "chat_id": chat_id}

    async def save_contact(self, chat_id: str, name: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.chat_actions.save_contact(jid, full_name=name)
        self.directory.add_contact(chat_id, name)
        return {"status": "returned", "chat_id": chat_id}

    async def get_profile_picture(self, chat_id: str, preview: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        picture = await client.contact.get_profile_picture(jid, preview)
        return {"chat_id": chat_id, "id": picture.id, "url": picture.url}

    async def block_contact(self, chat_id: str, blocked: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.blocking.block(jid) if blocked else await client.blocking.unblock(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def set_presence(self, available: bool):
        client = self.session.client
        await client.presence.set_available() if available else await client.presence.set_unavailable()
        return {"status": "returned"}

    async def send_chat_state(self, chat_id: str, state: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await getattr(client.chatstate, "send_" + state)(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def set_profile_name(self, name: str):
        client = self.session.client
        await client.profile.set_push_name(name)
        return {"status": "returned"}

    async def set_profile_about(self, text: str):
        client = self.session.client
        await client.profile.set_status_text(text)
        return {"status": "returned"}

    async def get_privacy(self):
        client = self.session.client
        settings = await client.privacy.fetch_settings()
        return {"items": [{"category": str(item.category), "value": str(item.value)} for item in settings]}

    async def set_privacy(self, category: str, value: str):
        client = self.session.client
        result = await client.privacy.set_setting(getattr(PrivacyCategory, category), getattr(PrivacyValue, value))
        return {"status": "returned", "provider_result": result}

    async def set_disappearing_default(self, seconds: int):
        client = self.session.client
        await client.privacy.set_default_disappearing_mode(seconds)
        return {"status": "returned"}

    async def set_group_title(self, chat_id: str, title: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.groups.set_subject(jid, title)
        self.directory.add_chat(chat_id, name=title)
        return {"status": "returned", "chat_id": chat_id}

    async def set_group_description(self, chat_id: str, description: str):
        client = self.session.client
        jid = self.jid(chat_id)
        metadata = await client.groups.get_metadata(jid)
        await client.groups.set_description(jid, description or None, metadata.description_id)
        return {"status": "returned", "chat_id": chat_id}

    async def leave_group(self, chat_id: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.groups.leave(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def get_group_invite(self, chat_id: str, reset: bool = False):
        client = self.session.client
        jid = self.jid(chat_id)
        link = await client.groups.get_invite_link(jid, reset)
        return {"chat_id": chat_id, "invite_link": link}

    async def set_group_setting(self, chat_id: str, setting: str, enabled: bool):
        client = self.session.client
        jid = self.jid(chat_id)
        await getattr(client.groups, "set_" + setting)(jid, enabled)
        return {"status": "returned", "chat_id": chat_id}

    async def set_group_disappearing(self, chat_id: str, seconds: int):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.groups.set_ephemeral(jid, seconds)
        return {"status": "returned", "chat_id": chat_id}

    async def set_group_approval(self, chat_id: str, enabled: bool):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.groups.set_membership_approval(jid, MembershipApprovalMode.On if enabled else MembershipApprovalMode.Off)
        return {"status": "returned", "chat_id": chat_id}

    async def set_group_member_add(self, chat_id: str, admins_only: bool):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.groups.set_member_add_mode(jid, MemberAddMode.AdminAdd if admins_only else MemberAddMode.AllMemberAdd)
        return {"status": "returned", "chat_id": chat_id}

    async def create_label(self, label_id: str, name: str, color: int):
        client = self.session.client
        await client.labels.create_label(label_id, name, color)
        return {"status": "returned"}

    async def delete_label(self, label_id: str):
        client = self.session.client
        await client.labels.delete_label(label_id)
        return {"status": "returned"}

    async def set_chat_label(self, chat_id: str, label_id: str, assigned: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.labels.add_chat_label(jid, label_id) if assigned else await client.labels.remove_chat_label(jid, label_id)
        return {"status": "returned", "chat_id": chat_id}

    async def create_channel(self, title: str, description: str = ""):
        client = self.session.client
        channel = await client.newsletter.create(title, description or None)
        return self.channel_details(channel)

    async def get_channel(self, chat_id: str):
        client = self.session.client
        jid = self.jid(chat_id)
        return self.channel_details(await client.newsletter.get_metadata(jid))

    async def follow_channel(self, chat_id: str, follow: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        if follow:
            return self.channel_details(await client.newsletter.join(jid))
        await client.newsletter.leave(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def update_channel(self, chat_id: str, title: str, description: str = ""):
        client = self.session.client
        jid = self.jid(chat_id)
        return self.channel_details(await client.newsletter.update(jid, name=title, description=description))

    async def send_channel_text(self, chat_id: str, text: str):
        client = self.session.client
        jid = self.jid(chat_id)
        message_id = await client.newsletter.send_message(jid, Message(conversation=text))
        return {"chat_id": chat_id, "id": message_id, "status": "submitted"}

    async def mute_channel(self, chat_id: str, muted: bool = True):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.newsletter.set_follower_mute(jid, muted)
        return {"status": "returned", "chat_id": chat_id}

    def channel_details(self, channel):
        jid = self.peer_id(channel.jid)
        self.directory.add_chat(jid, name=channel.name)
        return {"chat_id": jid, "name": channel.name, "description": channel.description,
                "subscriber_count": channel.subscriber_count, "role": str(channel.role)}

    async def revoke_message(self, chat_id: str, message_id: str):
        client = self.session.client
        row = self.get_stored_message(chat_id, message_id)
        if not row["from_me"]:
            raise HTTPBadRequest("Only your own messages can be revoked by this command")
        await client.chat_actions.revoke_message(self.jid(row["chat_id"]), row["id"])
        self.forget_message(row)
        return {"status": "returned", "chat_id": chat_id}

    async def delete_message(self, chat_id: str, message_id: str):
        client = self.session.client
        row = self.get_stored_message(chat_id, message_id)
        participant = self.jid(row["sender"]) if row["chat_id"].endswith("@g.us") and not row["from_me"] else None
        await client.chat_actions.delete_message_for_me(self.jid(row["chat_id"]), participant, row["id"], bool(row["from_me"]), False, row["timestamp"])
        self.forget_message(row)
        return {"status": "returned", "chat_id": chat_id}

    async def get_channel_messages(self, chat_id: str, limit: int = 50, before: int = 0):
        client = self.session.client
        jid = self.jid(chat_id)
        messages = await client.newsletter.get_messages(jid, limit, before or None)
        return {"items": [{"server_id": item.server_id, "timestamp": item.timestamp,
                           "text": self.message_text(item.message) if item.message else None,
                           "from_me": item.is_sender} for item in messages],
                "coverage": "provider_page"}
        return {"status": "returned", "chat_id": chat_id}

    async def react_channel_message(self, chat_id: str, server_id: int, reaction: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.newsletter.send_reaction(jid, server_id, reaction)
        return {"status": "returned", "chat_id": chat_id}

    async def create_poll(self, chat_id: str, title: str, options: list[str], selectable_count: int = 1):
        client = self.session.client
        jid = self.jid(chat_id)
        creator = self.own_interaction_identity(chat_id)
        message_id, secret = await client.polls.create(jid, title, options, selectable_count)
        self.directory.set_setting("poll:" + chat_id + ":" + message_id,
                                   {"secret": bytes(secret).hex(), "creator": creator, "options": options,
                                    "selectable_count": selectable_count})
        return {"id": message_id, "chat_id": chat_id, "status": "submitted"}

    async def create_group_event(self, chat_id: str, title: str, start_time: int, end_time: int, description: str = ""):
        client = self.session.client
        jid = self.jid(chat_id)
        creator = self.own_interaction_identity(chat_id)
        result = await client.events.create(jid, title, start_time=start_time, end_time=end_time, description=description or None)
        message_id = result["message_id"]
        self.directory.set_setting("event:" + chat_id + ":" + message_id,
                                   {"secret": bytes(result["message_secret"]).hex(), "creator": creator})
        return {"id": message_id, "chat_id": chat_id, "status": "submitted"}

    async def create_community(self, title: str, description: str = ""):
        client = self.session.client
        result = await client.community.create(CreateCommunityOptions(title, description=description or None))
        return {"chat_id": self.peer_id(result.gid), "status": "returned"}

    async def deactivate_community(self, chat_id: str):
        client = self.session.client
        jid = self.jid(chat_id)
        await client.community.deactivate(jid)
        return {"status": "returned", "chat_id": chat_id}

    async def get_group_requests(self, chat_id: str, limit: int = 50, offset: int = 0):
        client = self.session.client
        jid = self.jid(chat_id)
        items = await client.groups.get_membership_requests(jid)
        return {"items": [{"id": self.peer_id(item.jid), "request_time": item.request_time}
                          for item in items[offset:offset + limit]],
                "next_offset": offset + limit if len(items) > offset + limit else None}
        return {"status": "returned", "chat_id": chat_id}


    def forget_message(self, row):
        # Keep a tombstone so a delayed synchronization cannot silently restore text.
        with self.directory.database:
            self.directory.database.execute(
                "UPDATE messages SET text='' WHERE chat_id=? AND id=?", (row["chat_id"], row["id"]))
            self.directory.database.execute(
                "INSERT INTO message_data(chat_id,id,kind,status) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id,id) DO UPDATE SET proto=NULL,kind=excluded.kind,status=excluded.status",
                (row["chat_id"], row["id"], "deleted", "deleted"))

    def own_interaction_identity(self, chat_id):
        advanced = self.session.client.advanced
        own = advanced.get_pn() if chat_id.endswith("@s.whatsapp.net") else advanced.get_lid() or advanced.get_pn()
        if own is None:
            raise HTTPException(409, detail="Own account identity is unavailable")
        return self.peer_id(own)

    def interaction(self, kind, chat_id, message_id):
        for alias in self.directory.get_aliases(chat_id):
            saved = self.directory.get_setting(kind + ":" + alias + ":" + message_id)
            if saved:
                return saved
        row = self.get_stored_message(chat_id, message_id)
        body = self.get_message_proto(row)
        secret = body.messageContextInfo.messageSecret
        result = {"secret": secret.hex(), "creator": self.own_interaction_identity(chat_id)
                  if row["from_me"] else row["sender"]}
        if kind == "poll":
            field = next((name for name in ("pollCreationMessage", "pollCreationMessageV2", "pollCreationMessageV3")
                          if body.HasField(name)), None)
            if field is None:
                raise HTTPBadRequest("The selected message is not a retained poll")
            poll = getattr(body, field)
            result.update(options=[option.optionName for option in poll.options],
                          selectable_count=poll.selectableOptionsCount)
            result["secret"] = (secret or poll.encKey).hex()
        elif not body.HasField("eventMessage"):
            raise HTTPBadRequest("The selected message is not a retained event")
        if not result["secret"] or "@" not in result["creator"]:
            raise HTTPException(409, detail="Interaction secret or creator was not retained")
        return result

    async def vote_poll(self, chat_id, message_id, options):
        poll = self.interaction("poll", chat_id, message_id)
        if len(options) > poll["selectable_count"] or any(option not in poll["options"] for option in options):
            raise HTTPBadRequest("Selection does not match the retained poll")
        result = await self.session.client.polls.vote(self.jid(chat_id), message_id,
            self.jid(poll["creator"]), bytes.fromhex(poll["secret"]), options)
        return {"chat_id": chat_id, "id": result, "status": "submitted"}

    async def respond_group_event(self, chat_id, message_id, response):
        event = self.interaction("event", chat_id, message_id)
        result = await self.session.client.events.respond(self.jid(chat_id), message_id,
            self.jid(event["creator"]), bytes.fromhex(event["secret"]), getattr(EventResponse, response))
        return {"chat_id": chat_id, "id": result, "status": "submitted"}

    async def get_poll_results(self, chat_id, message_id):
        poll = self.interaction("poll", chat_id, message_id)
        rows = self.directory.database.execute(
            "SELECT m.sender,m.from_me,m.timestamp,m.id,d.proto FROM messages m JOIN message_data d "
            "ON m.chat_id=d.chat_id AND m.id=d.id WHERE m.chat_id IN "
            "(SELECT value FROM json_each(?)) AND d.proto IS NOT NULL "
            "ORDER BY m.timestamp DESC,m.id DESC LIMIT 10001",
            (json.dumps(self.directory.get_aliases(chat_id)),)).fetchall()
        votes = {}
        failed = 0
        for row in rows[:10000]:
            body = Message.FromString(row["proto"])
            if not body.HasField("pollUpdateMessage"):
                continue
            update = body.pollUpdateMessage
            if update.pollCreationMessageKey.id != message_id:
                continue
            voter = self.own_interaction_identity(chat_id) if row["from_me"] else row["sender"]
            aliases = self.directory.get_aliases(voter)
            canonical = min(aliases)
            stamp = (update.senderTimestampMs or row["timestamp"] * 1000, row["id"])
            if canonical in votes and votes[canonical][0] >= stamp:
                continue
            decoded = None
            for creator, candidate in itertools.product(self.directory.get_aliases(poll["creator"]), aliases):
                if not creator.endswith(("@lid", "@s.whatsapp.net")) or not candidate.endswith(("@lid", "@s.whatsapp.net")):
                    continue
                try:
                    decoded = PollsClient.decrypt_vote(bytes(update.vote.encPayload), bytes(update.vote.encIv),
                        bytes.fromhex(poll["secret"]), message_id, self.jid(creator), self.jid(candidate))
                    break
                except (ValueError, RuntimeError):
                    continue
            if decoded is None:
                failed += 1
            # An unreadable newer vote must not leave an old vote looking current.
            votes[canonical] = (stamp, None if decoded is None else {bytes(value) for value in decoded})
        return {"items": [{"option": option, "voters": [voter for voter, (_, choices) in votes.items()
                           if choices is not None and hashlib.sha256(option.encode()).digest() in choices]}
                          for option in poll["options"]],
                "coverage": "observed_votes_only", "undecryptable_updates": failed,
                "scan_truncated": len(rows) > 10000}

    async def decide_group_requests(self, chat_id, participants, approve):
        client = self.session.client.groups
        method = client.approve_membership_requests if approve else client.reject_membership_requests
        results = await method(self.jid(chat_id), [self.jid(value) for value in participants])
        return {"chat_id": chat_id, "items": [{"id": self.peer_id(item.jid),
                "status": str(item.status), "error": item.error} for item in results]}

    async def link_community_groups(self, chat_id, groups, linked):
        client = self.session.client.community
        ids = [self.jid(value) for value in groups]
        if linked:
            result = await client.link_subgroups(self.jid(chat_id), ids)
            changed = result.linked_jids
        else:
            result = await client.unlink_subgroups(self.jid(chat_id), ids, False)
            changed = result.unlinked_jids
        return {"changed": [self.peer_id(jid) for jid in changed],
                "failed": [{"id": self.peer_id(jid), "code": code} for jid, code in result.failed_groups]}

    async def get_community_groups(self, chat_id):
        items = await self.session.client.community.get_subgroups(self.jid(chat_id))
        return [{"id": self.peer_id(item.id), "title": item.subject,
                 "participant_count": item.participant_count} for item in items]

    async def get_channels(self):
        items = await self.session.client.newsletter.list_subscribed()
        return [self.channel_details(item) for item in items]
