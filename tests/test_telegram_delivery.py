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

"""Contract: poll persistence and overdue reminders survive a new server instance."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet

from examples.telegram_bot import DemoBot
from examples.telegram_bot.config import DemoRegistry
from kajenn import AsgiServer
from kajenn_bot_application.telegram import TelegramBotApplication
from tests.storage_support import site_mounts
from tests.telegram.support import TelegramAPI


async def test_poll_and_reminder_restore_with_real_storage(tmp_path):
    api = TelegramAPI()
    storage_key = Fernet.generate_key().decode()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api.respond)) as client:
        applications = [
            (DemoRegistry, {"code": "registry"}),
            (
                TelegramBotApplication,
                {
                    "code": "telegram",
                    "persistence_route": "registry/bots",
                    "webhook_url": "https://example.com/telegram",
                    "client": client,
                },
            ),
        ]
        server = AsgiServer(
            applications=applications, storage=site_mounts(tmp_path), storage_key=storage_key
        )
        app = server.applications["telegram"]
        await app.on_startup()
        await app.register_bot(code="alpha", bot_class=DemoBot, token="1:secret")
        await app.send_poll("alpha", -42, "Ship?", ["Yes", "No"], is_anonymous=False)
        await app.delivery.receive_poll(
            "alpha",
            {
                "update_id": 1,
                "poll_answer": {"poll_id": "poll-1", "user": {"id": 42}, "option_ids": [0]},
            },
        )
        code = await app.schedule_reminder(
            "alpha", 42, "Ship tomorrow", when=datetime.now(timezone.utc) + timedelta(days=1)
        )
        row = server.tasks.task_store.get(code)
        row["next_run_ts"] = time.time() - 1
        server.tasks.task_store.save(row)

        fresh = AsgiServer(
            applications=applications, storage=site_mounts(tmp_path), storage_key=storage_key
        )
        restored = fresh.applications["telegram"]
        await restored.on_startup()
        poll = await restored.get_poll("alpha", "poll-1")
        assert poll["answers"]["user:42"]["option_ids"] == [0]
        files = list((tmp_path / "telegram_registry" / "telegram" / "polls" / "alpha").iterdir())
        assert files and all(b"Ship?" not in f.read_bytes() for f in files)
        await fresh.tasks.scheduler.tick()
        async with asyncio.timeout(3):
            while code in fresh.tasks.scheduler.running:
                await asyncio.sleep(0.01)
        assert (await restored.get_reminder(code))["delivery_state"] == "sent"
        assert fresh.tasks.task_store.get(code)["last_outcome"] == "ok"
        await restored.deliver_reminder(code)
        assert (
            len([p for m, p in api.calls if m == "sendMessage" and p["text"] == "Ship tomorrow"])
            == 1
        )


async def test_multiple_apps_have_distinct_reminder_tasks(tmp_path):
    api = TelegramAPI()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api.respond)) as client:
        server = AsgiServer(
            applications=[
                (DemoRegistry, {"code": "registry"}),
                (
                    TelegramBotApplication,
                    {"code": "one", "persistence_route": "registry/bots", "client": client},
                ),
                (
                    TelegramBotApplication,
                    {"code": "two", "persistence_route": "registry/bots", "client": client},
                ),
            ],
            storage=site_mounts(tmp_path),
            storage_key=Fernet.generate_key().decode(),
        )
        for code in ("one", "two"):
            app = server.applications[code]
            await app.on_startup()
            await app.register_bot(code="alpha", bot_class=DemoBot, token="1:secret")
            await app.schedule_reminder(
                "alpha", 42, code, when=datetime.now(timezone.utc) + timedelta(days=1)
            )
        registry = server.tasks.scheduler.scan()
        assert server.applications["one"].delivery.reminder_task_name in registry
        assert server.applications["two"].delivery.reminder_task_name in registry
        assert not [p for m, p in api.calls if m == "setWebhook"]
