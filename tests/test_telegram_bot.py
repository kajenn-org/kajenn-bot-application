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
import copy

import pytest
from genro_routes import route

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


class SenderBot(DemoBot):
    """Commands that opt into all, some or none of the delivery context."""

    @route()
    def identity(self, text: str, sender: dict, chat_id: int) -> str:
        return f"{sender.get('id')}:{chat_id}:{text}"

    @route()
    async def identity_async(self, text, *, sender, chat_id):
        return f"{sender.get('id')}:{chat_id}:{text}"

    @route()
    def sender_only(self, sender):
        return str(sender.get("id"))

    @route()
    def chat_only(self, chat_id):
        return str(chat_id)

    @route()
    def no_context(self):
        return "ready"

    @route()
    def all_context(self, **kwargs):
        assert set(kwargs) == {"text", "sender", "chat_id"}
        return f"{kwargs['sender'].get('id')}:{kwargs['chat_id']}:{kwargs['text']}"

    @route()
    def edit_sender(self, sender):
        assert sender["username"] == "mario"
        assert sender["first_name"] == "Mario"
        sender["id"] = 999
        sender["extra"]["nested"].append("handler change")

    @route(auth_rule="admin")
    def restricted(self, sender, chat_id):
        raise AssertionError("Sender data must not grant router permissions")


@pytest.mark.parametrize(("command", "reply"), [
    ("identity", "73:-10042:hello"),
    ("identity_async", "73:-10042:hello"),
    ("sender_only", "73"),
    ("chat_only", "-10042"),
    ("no_context", "ready"),
    ("all_context", "73:-10042:hello"),
    ("echo", "hello"),
    ("restricted", None),
])
async def test_command_context_through_webhook_and_tasks(setup, command, reply):
    server, app, api = setup
    await app.register_bot(code="alpha", bot_class=SenderBot, token="1:secret")
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": -10042},
            "from": {"id": 73, "first_name": "Mario", "username": "mario"},
            "text": f"/{command} hello",
        },
    }
    assert (await webhook(server, app, payload=update)).status_code == 200
    assert not [call for call in api.calls if call[0] == "sendMessage"]
    await drain(server)
    replies = [payload for method, payload in api.calls if method == "sendMessage"]
    assert replies == ([] if reply is None else [{"chat_id": -10042, "text": reply}])


async def test_command_sender_is_a_deep_copy(setup):
    _, app, api = setup
    await app.register_bot(code="alpha", bot_class=SenderBot, token="1:secret")
    update = {"message": {"chat": {"id": 42}, "from": {
        "id": 73, "first_name": "Mario", "username": "mario", "extra": {"nested": []},
    }}}
    original = copy.deepcopy(update)
    await app.deliver_update("alpha", "edit_sender", "", 42, update)
    assert update == original
    assert not [call for call in api.calls if call[0] == "sendMessage"]


@pytest.mark.parametrize("update", [None, {"message": {"chat": {"id": 42}}}])
async def test_command_without_sender_receives_empty_dictionary(setup, update):
    _, app, api = setup
    await app.register_bot(code="alpha", bot_class=SenderBot, token="1:secret")
    await app.deliver_update("alpha", "identity", "hello", 42, update)
    assert api.calls[-1] == ("sendMessage", {"chat_id": 42, "text": "None:42:hello"})
