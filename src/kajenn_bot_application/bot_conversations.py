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

"""Persistent bot conversations and optional administrator admission.

Conversations belong to one bot and have explicit user/chat participants. Replies
and callbacks must match a stored message and its recipient. Free text is routed
only when exactly one open conversation matches; otherwise the sender is asked
to reply to a specific message. Conversation handlers receive text, sender,
conversation and action, and return text or None. Admission never supplies router
auth roles or application credentials.

The application-wide persistence route implements list_conversations (bot_code),
get_conversation (bot_code, id), and save_conversation (full record). Saves compare
the submitted revision atomically, increment it, and return the new record. A new
record has revision zero. Conflicts raise. A single receiving application process
serializes operations; providers must enforce revisions even for external writers.

Admission is private-chat only. The first valid admin decision wins under 'first';
'all' requires every configured admin's approval and any rejection concludes it.
Decisions are saved before notifications. Provider rendering settles each admin notification by editing it or sending a
follow-up. Failed notifications remain retryable on startup or a subsequent
callback and cannot undo the decision. Sending and saving provider message IDs
are separate operations, not an exactly-once promise.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import secrets
import time
from typing import Any

from kajenn.exceptions import HTTPForbidden, HTTPNotFound, HTTPUnauthorized


from .bot import _BotAPIError


class _Conversations:
    """Conversation operations owned by one bot application."""

    def __init__(self, application: Any) -> None:
        self.application = application
        self.lock = asyncio.Lock()

    def get_access(self, bot: Any) -> dict[str, Any]:
        return {
            "approval_required": bot.config("access.approval_required", default=False),
            "admins": (bot.config("access.admins", default=[]) or []),
            "approval_policy": bot.config("access.approval_policy", default="first"),
        }

    def validate_access(self, bot: Any) -> None:
        access = self.get_access(bot)
        admins = access["admins"]
        if not isinstance(admins, list) or any(not self.application._valid_user(a) for a in admins):
            raise ValueError("admins must be a list of valid provider user IDs")
        if len(set(admins)) != len(admins):
            raise ValueError("admins must be distinct")
        if access["approval_required"] and not admins:
            raise ValueError("approval_required needs at least one admin")
        if access["approval_policy"] not in ("first", "all"):
            raise ValueError("approval_policy must be first or all")

    async def get_records(self, bot_code: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = await self.application._persist(
            "list_conversations", {"bot_code": bot_code}
        )
        return records

    async def get_record(self, bot_code: str, conversation_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{24}", conversation_id):
            raise ValueError("invalid conversation id")
        record: dict[str, Any] | None = await self.application._persist(
            "get_conversation", {"bot_code": bot_code, "id": conversation_id}
        )
        if record is None:
            raise LookupError("conversation not found")
        return record

    async def save_record(self, record: dict[str, Any]) -> None:
        saved = await self.application._persist("save_conversation", copy.deepcopy(record))
        record.update(saved)

    async def create_record(
        self,
        bot_code: str,
        participants: list[dict[str, Any]],
        route: str,
        context: dict[str, Any] | None = None,
        expires_at: float | None = None,
        kind: str = "conversation",
    ) -> dict[str, Any]:
        self.application.get_bot(bot_code)
        if not participants:
            raise ValueError("a conversation requires participants")
        seen = set()
        for participant in participants:
            if not self.application._valid_user(
                participant.get("user_id")
            ) or not self.application._valid_recipient(participant.get("chat_id")):
                raise ValueError("participants require user_id and chat_id")
            key = (participant["user_id"], participant["chat_id"])
            if key in seen:
                raise ValueError("duplicate participant")
            seen.add(key)
        record = {
            "id": secrets.token_hex(12),
            "bot_code": bot_code,
            "revision": 0,
            "kind": kind,
            "state": "open",
            "route": route,
            "participants": copy.deepcopy(participants),
            "context": copy.deepcopy(context or {}),
            "expires_at": expires_at,
            "messages": [],
            "votes": {},
            "decision": None,
        }
        json.dumps(record)
        await self.save_record(record)
        return record

    async def send_record_message(
        self,
        record: dict[str, Any],
        user_id: int | str,
        text: str,
        buttons: dict[str, str] | None = None,
        chat_id: int | str | None = None,
    ) -> Any:
        recipients = [
            p
            for p in record["participants"]
            if p["user_id"] == user_id and (chat_id is None or p["chat_id"] == chat_id)
        ]
        if len(recipients) != 1:
            raise ValueError("specify exactly one participant and chat")
        recipient = recipients[0]
        actions = buttons or {}
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", action) for action in actions.values()):
            raise ValueError("invalid button action")
        sent = await self.send_message(record, recipient, text, actions)
        record["messages"].append(
            {
                "user_id": user_id,
                "chat_id": recipient["chat_id"],
                "message_id": sent["message_id"],
                "actions": list(actions.values()),
                "text": text,
                "resolution": None,
            }
        )
        await self.save_record(record)
        return sent

    async def expire_record(self, record: dict[str, Any]) -> None:
        if (
            record["state"] == "open"
            and record["expires_at"] is not None
            and record["expires_at"] <= time.time()
        ):
            record["state"] = "expired"
            await self.save_record(record)

    async def admit_participant(
        self, bot_code: str, sender: dict[str, Any], chat_id: Any, private: bool
    ) -> bool:
        access = self.get_access(self.application.get_bot(bot_code))
        if not access["approval_required"]:
            return True
        user_id = sender.get("id")
        if not self.application._valid_user(user_id):
            return False
        if user_id in access["admins"]:
            return True
        async with self.lock:
            records = await self.get_records(bot_code)
            record = next(
                (
                    r
                    for r in records
                    if r["kind"] == "admission" and r["context"]["user_id"] == user_id
                ),
                None,
            )
            if record is not None and record["state"] == "approved":
                return True
            if not private or chat_id != user_id:
                await self.application.send_message(
                    bot_code, chat_id, "Request access in a private chat with the bot."
                )
                return False
            if record is None:
                participants = [{"user_id": user_id, "chat_id": user_id, "role": "requester"}]
                participants.extend(
                    {"user_id": a, "chat_id": a, "role": "admin"} for a in access["admins"]
                )
                record = await self.create_record(
                    bot_code,
                    participants,
                    "",
                    {
                        "user_id": user_id,
                        "name": sender.get("first_name", str(user_id)),
                        "policy": access["approval_policy"],
                        "admins": access["admins"],
                    },
                    kind="admission",
                )
            if record["state"] == "open":
                await self.notify_admins(record)
                text = "Your access request is awaiting administrator approval."
            else:
                text = f"Your access request was {record['state']}."
            await self.application.send_message(bot_code, chat_id, text)
            return False

    async def notify_admins(self, record: dict[str, Any]) -> None:
        for admin in record["context"]["admins"]:
            if any(m["user_id"] == admin for m in record["messages"]):
                continue
            try:
                await self.send_record_message(
                    record,
                    admin,
                    f"Access request: {record['context']['name']} ({record['context']['user_id']})",
                    {"Approve": "approve", "Reject": "reject"},
                )
            except _BotAPIError:
                # Other admins can still receive and resolve the request.
                logging.getLogger(__name__).warning(
                    "Bot admission %s: admin notification pending for %s", record["id"], admin
                )
                continue

    async def sync_resolution(self, record: dict[str, Any]) -> None:
        decision = record["decision"]
        if decision is None:
            return
        text = f"{record['state'].capitalize()} by {decision['name']} ({decision['user_id']})"
        for index in range(len(record["messages"])):
            message = record["messages"][index]
            if not message["actions"] or message["resolution"] == text:
                continue
            try:
                await self.resolve_message(record, message, text)
            except _BotAPIError:
                logging.getLogger(__name__).warning(
                    "Bot admission %s: message edit pending for %s",
                    record["id"],
                    message["chat_id"],
                )
                continue
            message["resolution"] = text
            await self.save_record(record)
        if not record.get("requester_notified"):
            try:
                await self.notify_requester(record)
            except _BotAPIError:
                logging.getLogger(__name__).warning(
                    "Bot admission %s: requester notification pending", record["id"]
                )
                return
            record["requester_notified"] = True
            await self.save_record(record)

    async def restore_admissions(self, bot_code: str) -> None:
        if not self.get_access(self.application.get_bot(bot_code))["approval_required"]:
            return
        async with self.lock:
            for record in await self.get_records(bot_code):
                if record["kind"] != "admission":
                    continue
                if record["state"] == "open":
                    await self.notify_admins(record)
                else:
                    await self.sync_resolution(record)

    async def decide_admission(
        self, record: dict[str, Any], sender: dict[str, Any], action: str
    ) -> str:
        admins = self.get_access(self.application.get_bot(record["bot_code"]))["admins"]
        if sender["id"] not in admins or sender["id"] not in record["context"]["admins"]:
            return "Only configured administrators may decide."
        if record["state"] == "open":
            voter = str(sender["id"])
            if voter not in record["votes"]:
                record["votes"][voter] = action
                complete = (
                    action == "reject"
                    or record["context"]["policy"] == "first"
                    or all(
                        record["votes"].get(str(a)) == "approve"
                        for a in record["context"]["admins"]
                    )
                )
                if complete:
                    record["state"] = "rejected" if action == "reject" else "approved"
                    record["decision"] = {
                        "user_id": sender["id"],
                        "name": sender.get("first_name", str(sender["id"])),
                    }
                await self.save_record(record)
        await self.sync_resolution(record)
        return "Vote recorded." if record["state"] == "open" else f"Already {record['state']}."

    async def handle_action(
        self, bot_code: str, sender: dict[str, Any], chat_id: Any, message_id: Any, data: str
    ) -> None:
        async with self.lock:
            parts = data.split(":")
            if len(parts) != 3 or parts[0] != "c":
                return
            try:
                record = await self.get_record(bot_code, parts[1])
            except (ValueError, LookupError):
                return
            match = next(
                (
                    m
                    for m in record["messages"]
                    if m["message_id"] == message_id
                    and m["chat_id"] == chat_id
                    and m["user_id"] == sender["id"]
                    and parts[2] in m["actions"]
                ),
                None,
            )
            if match is None:
                return
            await self.expire_record(record)
            if record["kind"] == "admission":
                await self.decide_admission(record, sender, parts[2])
                return
            if record["state"] != "open":
                return
        # Handlers can call the conversation APIs, which acquire the same lock.
        if await self.admit_participant(bot_code, sender, chat_id, chat_id == sender["id"]):
            await self.dispatch_record(record, sender, "", parts[2], match["chat_id"])

    async def handle_text(
        self, bot_code: str, sender: dict[str, Any], chat_id: Any, text: str, reply_id: Any = None
    ) -> None:
        if not self.application._valid_user(sender.get("id")):
            return
        async with self.lock:
            records = await self.get_records(bot_code)
            candidates = []
            for record in records:
                if record["kind"] != "conversation":
                    continue
                if not any(
                    p["user_id"] == sender["id"] and p["chat_id"] == chat_id
                    for p in record["participants"]
                ):
                    continue
                await self.expire_record(record)
                if record["state"] != "open":
                    continue
                if reply_id is not None and not any(
                    m["chat_id"] == chat_id
                    and m["message_id"] == reply_id
                    and m["user_id"] == sender["id"]
                    for m in record["messages"]
                ):
                    continue
                candidates.append(record)
        if len(candidates) > 1:
            await self.application.send_message(
                bot_code, chat_id, "Please reply to the message for the conversation you mean."
            )
        elif candidates:
            await self.dispatch_record(candidates[0], sender, text, "", chat_id)

    async def dispatch_record(
        self,
        record: dict[str, Any],
        sender: dict[str, Any],
        text: str,
        action: str,
        chat_id: int | str,
    ) -> None:
        bot = self.application.get_bot(record["bot_code"])
        try:
            node = bot.route.node(record["route"], errors=self.application.ROUTER_ERRORS)
            result = await self.application._call(
                node,
                text=text,
                sender=copy.deepcopy(sender),
                conversation=copy.deepcopy(record),
                action=action,
            )
        except (HTTPNotFound, HTTPUnauthorized, HTTPForbidden):
            return
        if result is not None:
            if not isinstance(result, str):
                raise TypeError("Bot conversation handlers must return str or None")
            await self.application.send_conversation_message(
                record["bot_code"], record["id"], sender["id"], result, chat_id=chat_id
            )

    async def send_message(
        self, record: dict[str, Any], recipient: dict[str, Any], text: str, actions: dict[str, str]
    ) -> Any:
        raise NotImplementedError

    async def resolve_message(
        self, record: dict[str, Any], message: dict[str, Any], text: str
    ) -> None:
        raise NotImplementedError

    async def notify_requester(self, record: dict[str, Any]) -> None:
        await self.application.send_message(
            record["bot_code"],
            record["context"]["user_id"],
            f"Your access request was {record['state']}.",
        )
