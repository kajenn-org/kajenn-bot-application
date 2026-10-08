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

"""WhatsApp interaction rendering for the shared conversation and admission engine."""

import copy

from .bot import _BotAPIError
from .bot_conversations import _Conversations as _BaseConversations


class _Conversations(_BaseConversations):
    """Correlate provider message IDs and settle decisions with follow-up notices."""

    def validate_access(self, bot):
        super().validate_access(bot)
        for setting in ("approval_template", "resolution_template"):
            template = bot.config(f"notifications.{setting}", default=None)
            if template is not None:
                if not isinstance(template, dict) or set(template) != {"name", "language"}:
                    raise ValueError("notice templates require exactly name and language")
                self.application.delivery.get_template(**template)

    async def send_message(self, record, recipient, text, actions):
        buttons = {label: f"c:{record['id']}:{action}" for label, action in actions.items()}
        if record["kind"] == "admission" and not await self.application.delivery.window_open(
            record["bot_code"], recipient["chat_id"]
        ):
            sent = await self.send_notice(
                record, recipient["chat_id"], "approval_template", text, buttons
            )
        elif buttons:
            sent = await self.application.send_buttons(
                record["bot_code"], recipient["chat_id"], text, buttons
            )
        else:
            sent = await self.application.send_message(
                record["bot_code"], recipient["chat_id"], text
            )
        return sent

    async def send_notice(self, record, recipient, setting, text, buttons=None):
        bot = self.application.get_bot(record["bot_code"])
        template = bot.config(f"notifications.{setting}", default=None)
        if not template:
            raise _BotAPIError(
                "WhatsApp notice requires a configured template outside the service window"
            )
        template = copy.deepcopy(template)
        # Templates are explicit: the configured body must contain one text parameter.
        components = [{"type": "body", "parameters": [{"type": "text", "text": text}]}]
        for index, payload in enumerate((buttons or {}).values()):
            components.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(index),
                    "parameters": [{"type": "payload", "payload": payload}],
                }
            )
        return await self.application.send_template(
            record["bot_code"],
            recipient,
            name=template["name"],
            language=template["language"],
            components=components,
        )

    async def resolve_message(self, record, message, text):
        if await self.application.delivery.window_open(record["bot_code"], message["chat_id"]):
            await self.application.send_message(
                record["bot_code"], message["chat_id"], f"{message['text']}\n{text}"
            )
        else:
            await self.send_notice(
                record, message["chat_id"], "resolution_template", f"{message['text']}\n{text}"
            )

    async def notify_requester(self, record):
        recipient = record["context"]["user_id"]
        if await self.application.delivery.window_open(record["bot_code"], recipient):
            await super().notify_requester(record)
        else:
            await self.send_notice(
                record,
                recipient,
                "resolution_template",
                f"Your access request was {record['state']}.",
            )
