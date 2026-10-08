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

"""Contract: admission decisions persist and settle every administrator's copy."""

import copy

import httpx
import pytest
from cryptography.fernet import Fernet

from examples.telegram_bot import DemoBot
from examples.telegram_bot.config import DemoRegistry
from kajenn import AsgiServer
from kajenn_bot_application.telegram import TelegramBotApplication
from tests.storage_support import site_mounts
from tests.telegram.support import TelegramAPI, drain, webhook


async def test_admission_webhook_with_encrypted_filesystem_and_restart(tmp_path):
    api = TelegramAPI()
    storage_key = Fernet.generate_key().decode()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api.respond)) as client:
        server = AsgiServer(
            applications=[
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
            ],
            storage=site_mounts(tmp_path),
            storage_key=storage_key,
        )
        app = server.applications["telegram"]
        await app.on_startup()
        await app.register_bot(
            code="alpha",
            bot_class=DemoBot,
            token="1:secret",
            config={"access": {"approval_required": True, "admins": [100, 200]}},
        )
        assert (
            await webhook(
                server,
                app,
                payload={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "from": {"id": 42, "first_name": "Mario"},
                        "chat": {"id": 42, "type": "private"},
                        "text": "/start",
                    },
                },
            )
        ).status_code == 200
        assert not api.messages  # The webhook ACK precedes command processing.
        await drain(server)
        message_id, message = next((i, m) for i, m in api.messages.items() if m["chat_id"] == 100)
        callback = {
            "update_id": 2,
            "callback_query": {
                "id": "approval",
                "from": {"id": 100, "first_name": "Giovanni"},
                "message": {"message_id": message_id, "chat": {"id": 100}},
                "data": message["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            },
        }
        assert (await webhook(server, app, payload=callback)).status_code == 200
        await drain(server)
        edits = [m for method, m in api.calls if method == "editMessageText"]
        assert {m["chat_id"] for m in edits} == {100, 200}
        assert all(
            "Approved by Giovanni" in m["text"] and m["reply_markup"] == {"inline_keyboard": []}
            for m in edits
        )
        registry = server.applications["registry"]
        stored = registry.bots("list_conversations", "telegram", {"bot_code": "alpha"})[0]
        stale = copy.deepcopy(stored)
        registry.bots("save_conversation", "telegram", stored)
        with pytest.raises(RuntimeError, match="revision"):
            registry.bots("save_conversation", "telegram", stale)
        files = list(
            (tmp_path / "telegram_registry" / "telegram" / "conversations" / "alpha").iterdir()
        )
        assert files and all(b"Giovanni" not in f.read_bytes() for f in files)
        fresh_server = AsgiServer(
            applications=[
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
            ],
            storage=site_mounts(tmp_path),
            storage_key=storage_key,
        )
        restored = fresh_server.applications["telegram"]
        await restored.on_startup()
        assert (
            await webhook(
                fresh_server,
                restored,
                payload={
                    "update_id": 3,
                    "message": {
                        "message_id": 2,
                        "from": {"id": 42},
                        "chat": {"id": 42, "type": "private"},
                        "text": "/echo admitted after restart",
                    },
                },
            )
        ).status_code == 200
        await drain(fresh_server)
        assert api.calls[-1][1]["text"] == "admitted after restart"
