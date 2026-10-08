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

"""Implementation tests for Telegram registration, webhook delivery and isolation."""

import asyncio
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from examples.telegram_bot import DemoBot
from examples.telegram_bot.config import DemoRegistry, TelegramDemoConfiguration
from kajenn import AsgiServer
from kajenn_bot_application.telegram import TelegramBotApplication
from kajenn.lifespan import FatalBootError
from tests.storage_support import site_mounts
from tests.telegram.support import drain, register, webhook

pytest_plugins = ["tests.telegram.support"]


async def test_two_instances_route_sync_async_and_keep_config_separate(setup):
    server, app, api = setup
    await register(app, dataset="one", greeting="Hi")
    await register(app, code="beta", token="2:secret", dataset="two")
    assert (await webhook(server, app)).status_code == 200
    assert (await webhook(server, app, code="beta")).status_code == 200
    assert not [call for call in api.calls if call[0] == "sendMessage"]
    await drain(server)
    replies = [payload["text"] for method, payload in api.calls if method == "sendMessage"]
    assert sorted(replies) == ["Hello [two]", "Hi [one]"]
    await webhook(server, app, text="/echo@bot1 hello world", update_id=2)
    await drain(server)
    assert api.calls[-1][1]["text"] == "hello world"


async def test_webhook_auth_method_and_invalid_json_shape(setup):
    server, app, api = setup
    await register(app)
    assert (await webhook(server, app, secret="wrong")).status_code == 403
    assert (await webhook(server, app, method="GET")).status_code == 405
    assert (await webhook(server, app, payload=[])).status_code == 400
    assert server.tasks.spool.list_pending() == []


async def test_duplicates_are_scoped_to_bot_and_survive_restore(setup):
    server, app, api = setup
    await register(app)
    responses = await asyncio.gather(*[webhook(server, app) for _ in range(4)])
    assert all(r.status_code == 200 for r in responses)
    assert len(server.tasks.spool.list_pending()) == 1
    await drain(server)
    restored = TelegramBotApplication(
        code="telegram",
        persistence_route="registry/bots",
        webhook_url="https://example.com/telegram",
        client=app.client,
    )
    restored.server = server
    await restored.on_startup()
    server._by_mount["telegram"] = restored
    assert restored.get_bot("alpha").config("settings.greeting") == "Hello"
    assert (await webhook(server, restored)).status_code == 200
    assert server.tasks.spool.list_pending() == []


async def test_other_bot_unknown_command_and_protected_route_do_not_execute(setup):
    server, app, api = setup
    await register(app)
    for i, command in enumerate(["/hello@someone_else", "/missing", "/restricted", "hello"]):
        assert (await webhook(server, app, text=command, update_id=i)).status_code == 200
    await drain(server)
    assert not [call for call in api.calls if call[0] == "sendMessage"]


async def test_registration_validates_before_side_effects(setup):
    server, app, api = setup
    with pytest.raises((AttributeError, ValueError)):
        await app.register_bot(
            code="alpha", bot_class=DemoBot, token="1:secret", config={"typo": {}}
        )
    assert api.calls == []
    await register(app)
    with pytest.raises(ValueError, match="registered"):
        await register(app)
    with pytest.raises(ValueError, match="token"):
        await register(app, code="beta")


async def test_failed_persistence_does_not_activate_webhook(setup):
    server, app, api = setup
    server.applications["registry"].fail = True
    with pytest.raises(RuntimeError, match="registry unavailable"):
        await register(app)
    assert not [call for call in api.calls if call[0] == "setWebhook"]
    with pytest.raises(KeyError):
        app.get_bot("alpha")


async def test_failed_webhook_leaves_registration_retryable_on_startup(setup):
    server, app, api = setup
    api.fail_webhook = True
    with pytest.raises(RuntimeError, match="setWebhook"):
        await register(app)
    api.fail_webhook = False
    await app.on_startup()
    assert app.get_bot("alpha").code == "alpha"


async def test_task_waits_for_restoration_before_resolving_bot(setup):
    server, app, api = setup
    await register(app)
    restored = TelegramBotApplication(
        code="telegram",
        persistence_route="registry/bots",
        webhook_url="https://example.com/telegram",
        client=app.client,
    )
    restored.server = server
    task = asyncio.create_task(restored.deliver_update("alpha", "hello", "", 42))
    await asyncio.sleep(0)
    assert not task.done()
    await restored.on_startup()
    await asyncio.wait_for(task, timeout=1)
    assert api.calls[-1][0] == "sendMessage"


async def test_example_registry_encrypts_credentials_and_has_no_http_surface(tmp_path):
    server = AsgiServer(
        applications=[(DemoRegistry, {"code": "registry"})],
        storage=site_mounts(tmp_path),
        storage_key=Fernet.generate_key().decode(),
    )
    registry = server.applications["registry"]
    record = {"code": "alpha", "token": "123:never_plaintext"}
    registry.bots(operation="save", application="telegram", record=record)
    assert registry.bots(operation="list", application="telegram") == [record]
    contents = next((tmp_path / "telegram_registry" / "telegram").iterdir()).read_bytes()
    assert b"never_plaintext" not in contents
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="https://example.com"
    ) as client:
        assert (await client.get("/registry/bots")).status_code == 404


def test_example_configuration_mounts_application_grammar(monkeypatch):
    monkeypatch.setenv("GENRO_STORAGE_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("KAJENN_TELEGRAM_WEBHOOK_URL", "https://example.com/telegram")
    server = AsgiServer(config=TelegramDemoConfiguration)
    app = server.applications["telegram"]
    assert app.persistence_route == "registry/bots"
    assert app.webhook_url == "https://example.com/telegram"


async def test_boot_error_preserves_registry_cause(setup):
    server, app, api = setup
    server.applications["registry"].fail = True
    with pytest.raises(FatalBootError) as caught:
        await app.on_startup()
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == "registry unavailable"


async def test_retry_webhook_in_process_preserves_saved_secret(setup):
    server, app, api = setup
    api.fail_webhook = True
    with pytest.raises(RuntimeError):
        await register(app)
    secret = app.get_bot_registration("alpha")["webhook_secret"]
    api.fail_webhook = False
    bot = await app.activate_bot("alpha")
    assert bot is app.get_bot("alpha")
    assert app.ready.is_set()
    assert api.calls[-1][1]["secret_token"] == secret
    assert len(server.applications["registry"].records["telegram"]) == 1


async def test_dedup_survives_spool_purge_and_application_restart(setup):
    server, app, api = setup
    await register(app)
    await webhook(server, app)
    task_id = server.tasks.spool.list_pending()[0]["task_id"]
    await drain(server)
    assert server.tasks.spool.purge(task_id)
    restored = TelegramBotApplication(
        code="telegram",
        persistence_route="registry/bots",
        webhook_url="https://example.com/telegram",
        client=app.client,
    )
    restored.server = server
    await restored.on_startup()
    server._by_mount["telegram"] = restored
    assert (await webhook(server, restored)).status_code == 200
    assert server.tasks.spool.list_pending() == []
    assert len([call for call in api.calls if call[0] == "sendMessage"]) == 1


async def test_receipts_expire_without_extending_retention_on_duplicate(setup, monkeypatch):
    server, app, api = setup
    await register(app)
    monkeypatch.setattr("kajenn_bot_application.bot.time.time", lambda: 1_000_000.0)
    await webhook(server, app)
    task_id = server.tasks.spool.list_pending()[0]["task_id"]
    await drain(server)
    server.tasks.spool.purge(task_id)
    registry = server.applications["registry"]
    expires_at = registry.receipts["telegram"][task_id]
    assert expires_at == 1_000_000.0 + 48 * 3600
    monkeypatch.setattr("kajenn_bot_application.bot.time.time", lambda: expires_at - 1)
    await webhook(server, app)
    assert registry.receipts["telegram"][task_id] == expires_at
    monkeypatch.setattr("kajenn_bot_application.bot.time.time", lambda: expires_at)
    await app.on_startup()
    assert registry.receipts["telegram"] == {}
    assert (await webhook(server, app)).status_code == 200
    assert len(server.tasks.spool.list_pending()) == 1


async def test_receipt_write_failure_does_not_ack_or_enqueue_twice(setup, monkeypatch):
    server, app, api = setup
    await register(app)
    persist = app._persist

    async def fail_receipt(operation, record=None):
        if operation == "save_receipt":
            raise RuntimeError("receipt unavailable")
        return await persist(operation, record)

    monkeypatch.setattr(app, "_persist", fail_receipt)
    assert (await webhook(server, app)).status_code == 500
    assert len(server.tasks.spool.list_pending()) == 1
    monkeypatch.setattr(app, "_persist", persist)
    assert (await webhook(server, app)).status_code == 200
    assert len(server.tasks.spool.list_pending()) == 1


def test_filesystem_receipts_are_separate_and_pruned(tmp_path):
    server = AsgiServer(
        applications=[(DemoRegistry, {"code": "registry"})],
        storage=site_mounts(tmp_path),
        storage_key=Fernet.generate_key().decode(),
    )
    registry = server.applications["registry"]
    registry.bots("save_receipt", "telegram", {"task_id": "telegram-test", "expires_at": 100})
    assert registry.bots("list", "telegram") == []
    assert registry.bots("get_receipt", "telegram", {"task_id": "telegram-test"}) == 100
    registry.bots("prune_receipts", "telegram", {"now": 100})
    assert registry.bots("get_receipt", "telegram", {"task_id": "telegram-test"}) is None


@pytest.mark.parametrize("url", ["", "http://example.com/telegram"])
async def test_invalid_webhook_url_does_not_silently_enable_send_only(setup, url):
    server, app, api = setup
    app._webhook_url = url
    with pytest.raises(ValueError, match="HTTPS"):
        await register(app)
    assert api.calls == []


async def test_sender_does_not_use_receipts_or_execute_inbound_tasks(setup, monkeypatch):
    server, central, api = setup
    app = TelegramBotApplication(
        code="sender", persistence_route="registry/bots", client=central.client
    )
    app.server = server
    operations = []
    persist = app._persist

    async def registry_only(operation, record=None):
        operations.append(operation)
        assert operation in {"list", "save"}
        return await persist(operation, record)

    monkeypatch.setattr(app, "_persist", registry_only)
    await app.on_startup()
    await register(app)
    assert operations == ["list", "save"]
    with pytest.raises(RuntimeError, match="disabled"):
        await app.deliver_update("alpha", "hello", "", 42)
    assert [method for method, _ in api.calls] == ["getMe"]


def test_local_recipe_omits_webhook_even_with_central_url_in_environment(monkeypatch):
    monkeypatch.setenv("GENRO_STORAGE_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("KAJENN_TELEGRAM_WEBHOOK_URL", "https://central.example.com/telegram")
    recipe = Path(__file__).resolve().parents[2] / "examples/telegram_bot/local_config.py"
    server = AsgiServer(config=recipe)
    app = server.applications["telegram"]
    assert app.webhook_url is None
    assert app.persistence_route == "registry/bots"
