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

"""Contract: extracting the bot base preserves persisted Telegram state."""

import copy
from datetime import datetime, timedelta, timezone

from kajenn_bot_application.bot import BotBaseApplication
from kajenn_bot_application.telegram import TelegramBotApplication
from tests.telegram.support import register, webhook, drain

pytest_plugins = ["tests.telegram.support"]


async def test_base_restores_existing_telegram_records_and_pending_work(setup):
    server, app, api = setup
    assert isinstance(app, BotBaseApplication)
    await register(app)
    conversation = await app.create_conversation(
        "alpha",
        participants=[{"user_id": 42, "chat_id": 42}],
        route="conversation",
        context={"pr": 39},
    )
    await app.send_conversation_message("alpha", conversation["id"], 42, "Review")
    reminder = await app.schedule_reminder(
        "alpha", 42, "Old reminder", when=datetime.now(timezone.utc) + timedelta(days=1)
    )
    record = copy.deepcopy(app.get_bot_registration("alpha"))
    assert (await webhook(server, app)).status_code == 200
    await drain(server)
    sent = len(api.calls)
    restored = TelegramBotApplication(
        code="telegram",
        persistence_route="registry/bots",
        webhook_url="https://example.com/telegram",
        client=app.client,
    )
    restored.server = server
    await restored.on_startup()
    assert restored.get_bot_registration("alpha") == record
    assert (await restored.get_conversation("alpha", conversation["id"]))["context"] == {"pr": 39}
    assert (await restored.get_reminder(reminder))["delivery_state"] == "pending"
    assert restored.delivery.reminder_task_name == app.delivery.reminder_task_name
    assert len(api.calls) == sent + 1  # Only restore the webhook.
    assert (await webhook(server, app)).status_code == 200
    assert not server.tasks.spool.list_pending()
