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

"""Common persistent reminder lifecycle using the server's task scheduler."""

from __future__ import annotations
import hashlib
import secrets
import time
from datetime import datetime
from typing import Any


class _Reminders:
    """Scheduling shared by provider delivery services; payloads contain no credentials."""

    acceptance_state = "sent"
    application: Any
    reminder_lock: Any

    def get_text_parts(self, text: str) -> list[str]:
        raise NotImplementedError

    @property
    def reminder_task_name(self) -> str:
        digest = hashlib.sha256(self.application.code.encode()).hexdigest()[:16]
        return f"{self.application.provider_name}_{digest}_reminder"

    @property
    def manager(self) -> Any:
        manager = getattr(self.application._require_server(), "tasks", None)
        if manager is None:
            raise RuntimeError("Bot reminders require kajenn.tasks")
        return manager

    async def get_reminder(self, code: str) -> dict[str, Any]:
        record: dict[str, Any] | None = await self.application._require_server().run_sync(
            lambda: self.manager.task_store.get(code)
        )
        if record is None or record["task_name"] != self.reminder_task_name:
            raise LookupError("reminder not found in this application")
        return record

    async def save_reminder(self, record: dict[str, Any]) -> None:
        await self.application._require_server().run_sync(
            lambda: self.manager.task_store.save(record)
        )

    async def schedule_reminder(
        self,
        bot_code: str,
        chat_id: int | str,
        text: str,
        *,
        when: datetime,
        conversation_id: str | None = None,
        user_id: int | str | None = None,
        message_options: dict[str, Any] | None = None,
    ) -> str:
        self.application.get_bot(bot_code)
        self.validate_reminder(text, message_options)
        if when.tzinfo is None or when.utcoffset() is None or when.timestamp() <= time.time():
            raise ValueError("when must be a future timezone-aware datetime")
        if conversation_id is not None:
            record = await self.application.get_conversation(bot_code, conversation_id)
            if record["state"] != "open" or not any(
                p["user_id"] == user_id and p["chat_id"] == chat_id for p in record["participants"]
            ):
                raise ValueError("reminder requires an open conversation and matching participant")
            if text and len(self.get_text_parts(text)) > 1:
                raise ValueError("conversation reminders must fit one message")
        code = f"{self.reminder_task_name}_{secrets.token_hex(12)}"
        await self.save_reminder(
            {
                "code": code,
                "task_name": self.reminder_task_name,
                "target_kind": "task",
                "kwargs": {"code": code},
                "kind": "at",
                "spec": [when.isoformat()],
                "enabled": True,
                "next_run_ts": when.timestamp(),
                "delivery_state": "pending",
                "bot_code": bot_code,
                "chat_id": chat_id,
                "text": text,
                "conversation_id": conversation_id,
                "user_id": user_id,
                **({"message_options": message_options} if message_options is not None else {}),
            }
        )
        return code

    async def cancel_reminder(self, code: str) -> bool:
        async with self.reminder_lock:
            record = await self.get_reminder(code)
            if record["delivery_state"] != "pending":
                return False
            record.update(enabled=False, delivery_state="cancelled")
            await self.save_reminder(record)
            return True

    async def deliver_reminder(self, code: str) -> None:
        await self.application.ready.wait()
        async with self.reminder_lock:
            record = await self.get_reminder(code)
            if record["delivery_state"] == "sending":
                record.update(delivery_state="uncertain", enabled=False)
                await self.save_reminder(record)
                return
            if not record["enabled"] or record["delivery_state"] != "pending":
                return
            record["delivery_state"] = "sending"
            await self.save_reminder(record)
            try:
                if record["conversation_id"]:
                    async with self.application.conversations.lock:
                        conversation = await self.application.conversations.get_record(
                            record["bot_code"], record["conversation_id"]
                        )
                        await self.application.conversations.expire_record(conversation)
                        if conversation["state"] != "open":
                            record["delivery_state"] = "skipped"
                        else:
                            await self.send_reminder(record, conversation)
                else:
                    await self.send_reminder(record)
            except Exception as exc:
                record.update(
                    delivery_state="uncertain"
                    if getattr(exc, "outcome_uncertain", False)
                    else "failed",
                    enabled=False,
                    delivery_error=f"{type(exc).__name__}: {exc}",
                )
                await self.save_reminder(record)
                raise
            if record["delivery_state"] == "sending":
                record["delivery_state"] = self.acceptance_state
            record["enabled"] = False
            await self.save_reminder(record)

    def validate_reminder(self, text: str, message_options: dict[str, Any] | None) -> None:
        self.get_text_parts(text)

    async def send_reminder(
        self, record: dict[str, Any], conversation: dict[str, Any] | None = None
    ) -> None:
        if conversation is not None:
            await self.application.conversations.send_record_message(
                conversation, record["user_id"], record["text"], chat_id=record["chat_id"]
            )
        else:
            await self.application.send_text(record["bot_code"], record["chat_id"], record["text"])
