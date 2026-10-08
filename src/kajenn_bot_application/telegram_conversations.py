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

"""Telegram message rendering and event decoding for shared bot conversations."""

from typing import Any
import logging
from .bot import _BotAPIError as _TelegramAPIError
from .bot_conversations import _Conversations as _BaseConversations


class _Conversations(_BaseConversations):
    """Preserve Telegram callback and message formats at the transport boundary."""

    async def send_message(self, record, recipient, text, actions):
        markup = None
        if actions:
            markup = {
                "inline_keyboard": [
                    [
                        {"text": label, "callback_data": f"c:{record['id']}:{action}"}
                        for label, action in actions.items()
                    ]
                ]
            }
        return await self.application.send_message(
            record["bot_code"], recipient["chat_id"], text, reply_markup=markup
        )

    async def resolve_message(self, record, message, text):
        await self.application._telegram(
            self.application.registrations[record["bot_code"]]["token"],
            "editMessageText",
            chat_id=message["chat_id"],
            message_id=message["message_id"],
            text=f"{message['text']}\n{text}",
            reply_markup={"inline_keyboard": []},
        )

    async def admit_sender(self, bot_code: str, message: dict[str, Any]) -> bool:
        return await self.admit_participant(
            bot_code,
            message.get("from", {}),
            message["chat"]["id"],
            message["chat"].get("type") == "private",
        )

    async def handle_callback(self, bot_code: str, query: dict[str, Any]) -> None:
        try:
            await self.application._telegram(
                self.application.registrations[bot_code]["token"],
                "answerCallbackQuery",
                callback_query_id=query["id"],
            )
        except _TelegramAPIError:
            logging.getLogger(__name__).warning("Telegram callback acknowledgement failed")
        message = query.get("message") or {}
        await self.handle_action(
            bot_code,
            query["from"],
            message.get("chat", {}).get("id"),
            message.get("message_id"),
            query.get("data", ""),
        )

    async def handle_message(self, bot_code: str, message: dict[str, Any]) -> None:
        await self.handle_text(
            bot_code,
            message.get("from", {}),
            message["chat"]["id"],
            message["text"],
            message.get("reply_to_message", {}).get("message_id"),
        )
