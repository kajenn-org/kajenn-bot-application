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

"""Contract: the example bot handles commands through webhook and server tasks."""

import asyncio

from examples.telegram_bot import DemoBot
from kajenn import AsgiServer
from kajenn_bot_application.telegram import TelegramBotApplication
from tests.storage_support import site_mounts
from tests.telegram.support import Registry, drain, register, webhook

pytest_plugins = ["tests.telegram.support"]


async def test_registered_example_replies_after_webhook_ack(setup):
    server, app, api = setup
    await app.on_startup()
    await app.register_bot(
        code="alpha",
        bot_class=DemoBot,
        token="1:example",
        config={"settings": {"greeting": "Welcome", "dataset": "alpha"}},
    )
    assert (await webhook(server, app)).status_code == 200
    assert not [method for method, _ in api.calls if method == "sendMessage"]
    server.tasks.start()
    try:
        async with asyncio.timeout(3):
            while not [method for method, _ in api.calls if method == "sendMessage"]:
                await asyncio.sleep(0.01)
        assert api.calls[-1] == ("sendMessage", {"chat_id": 42, "text": "Welcome [alpha]"})
    finally:
        await server.tasks.stop()


async def test_local_sender_leaves_central_webhook_and_replies_on_central(setup, tmp_path):
    central_server, central, api = setup
    await register(central)
    central_webhook = [payload for method, payload in api.calls if method == "setWebhook"]
    (tmp_path / "local").mkdir()
    local_server = AsgiServer(
        applications=[
            (Registry, {"code": "registry"}),
            (
                TelegramBotApplication,
                {
                    "code": "telegram",
                    "persistence_route": "registry/bots",
                    "client": central.client,
                },
            ),
        ],
        storage=site_mounts(tmp_path / "local"),
        tasks=False,
    )
    local = local_server.applications["telegram"]
    await local.on_startup()
    await register(local)
    await local.send_message("alpha", 42, "You have a new PR")
    assert api.calls[-1] == ("sendMessage", {"chat_id": 42, "text": "You have a new PR"})
    assert (await webhook(local_server, local)).status_code == 404
    await local.activate_bot("alpha")
    await local.on_shutdown()

    restored = TelegramBotApplication(
        code="telegram", persistence_route="registry/bots", client=central.client
    )
    restored.server = local_server
    await restored.on_startup()
    await restored.send_message("alpha", 42, "Another PR")
    await restored.on_shutdown()
    assert [payload for method, payload in api.calls if method == "setWebhook"] == central_webhook
    assert {method for method, _ in api.calls} == {"getMe", "setWebhook", "sendMessage"}

    assert (await webhook(central_server, central, text="/echo thanks")).status_code == 200
    await drain(central_server)
    assert api.calls[-1] == ("sendMessage", {"chat_id": 42, "text": "thanks"})
