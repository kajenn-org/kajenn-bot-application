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

"""Contract: authenticated REST/MCP administration bypasses bot command routing."""

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from genro_routes import route
from kajenn import AsgiServer, McpOpenApiApplication, RoutedApplication
from kajenn.exceptions import HTTPUnauthorized
from kajenn.config.templates import CONFIGURATION_TEMPLATES

from examples.telegram_bot import DemoBot as TelegramBot
from examples.whatsapp_bot import DemoBot as WhatsAppBot
from examples.whatsapp_bot.config import DemoRegistry
from kajenn_bot_application import TelegramBotApplication, WhatsAppBotApplication
from tests.storage_support import site_mounts
from tests.telegram.support import TelegramAPI, drain, webhook
from tests.whatsapp.support import GraphAPI


class Identity(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        if credential not in ("Bearer administrator", "Bearer reader"):
            raise HTTPUnauthorized("invalid credential")
        return {
            "identity": credential,
            "tags": ["admin"] if credential == "Bearer administrator" else ["reader"],
            "data": {},
        }


class Administration:
    def __init__(self, server, app, client, api, provider, recipient):
        self.server, self.app, self.client = server, app, client
        self.api, self.provider, self.recipient = api, provider, recipient

    async def rest(self, name, arguments=None, credential="administrator"):
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        return await self.client.post(f"/bots/_admin/{name}", json=arguments or {}, headers=headers)

    async def mcp(self, method, params=None, credential="administrator"):
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        response = await self.client.post(
            "/bots/_mcp", headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def call(self, name, arguments=None, credential="administrator"):
        return await self.mcp("tools/call", {"name": name, "arguments": arguments or {}}, credential)

    @property
    def sends(self):
        if self.provider == "telegram":
            return [payload for method, payload in self.api.calls if method == "sendMessage"]
        return [payload for method, _, payload in self.api.calls if method == "POST"]


@pytest.fixture(params=["telegram", "whatsapp"])
async def admin(request, tmp_path):
    provider = request.param
    telegram = provider == "telegram"
    api = TelegramAPI() if telegram else GraphAPI()
    app_class = TelegramBotApplication if telegram else WhatsAppBotApplication
    bot_class = TelegramBot if telegram else WhatsAppBot
    recipient = 42 if telegram else "391234"
    async with httpx.AsyncClient(transport=httpx.MockTransport(api.respond)) as provider_client:
        options = {
            "code": "bots", "persistence_route": "registry/bots",
            "client": provider_client, "bot_classes": {"demo": bot_class},
        }
        if not telegram:
            options["api_version"] = "v25.0"
        server = AsgiServer(
            applications=[(DemoRegistry, {"code": "registry"}),
                          (Identity, {"code": "identity"}), (app_class, options)],
            storage=site_mounts(tmp_path), storage_key=Fernet.generate_key().decode(),
            channels={name: {"authentication_route": "/identity/check"} for name in ("rest", "mcp")},
            plugins={"openapi": True},
        )
        app = server.applications["bots"]
        await app.on_startup()
        registration = {"code": "alpha", "bot_class": bot_class, "token": "1:secret"}
        if not telegram:
            registration.update(phone_number_id="1001", business_account_id="900")
        await app.register_bot(**registration)
        if not telegram:
            await app._persist("advance_window", {
                "bot_code": "alpha", "recipient": recipient, "last_inbound": time.time(),
            })
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url="https://example.com"
        ) as client:
            yield Administration(server, app, client, api, provider, recipient)
        await app.on_shutdown()


async def test_admin_tools_are_provider_specific_and_exclude_bots_and_tasks(admin):
    assert isinstance(admin.app, McpOpenApiApplication)
    response = await admin.mcp("tools/list")
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert {"list_bots", "get_bot_registration", "register_bot", "activate_bot", "send_message",
            "send_announcement", "create_conversation", "close_conversation",
            "schedule_reminder", "cancel_reminder"} <= tools.keys()
    assert not {"hello", "echo", "deliver_update", "deliver_announcement", "deliver_reminder"} & tools.keys()
    assert ("send_poll" in tools) == (admin.provider == "telegram")
    assert ("send_template" in tools) == (admin.provider == "whatsapp")
    schema = tools["send_message"]["inputSchema"]["properties"]
    assert schema["chat_id"]["type"] == ("integer" if admin.provider == "telegram" else "string")


@pytest.mark.parametrize("credential", [None, "reader"])
async def test_administration_denies_unauthorized_rest_and_mcp(admin, credential):
    args = {"bot_code": "alpha", "chat_id": admin.recipient, "text": "denied"}
    assert (await admin.rest("send_message", args, credential)).status_code in (401, 403)
    assert (await admin.mcp("tools/list", credential=credential))["result"]["tools"] == []
    assert "error" in await admin.call("send_message", args, credential)
    assert admin.sends == []


async def test_rest_and_mcp_send_directly_without_executing_bot_commands(admin):
    args = {"bot_code": "alpha", "chat_id": admin.recipient, "text": "/echo direct"}
    rest = await admin.rest("send_message", args)
    assert rest.status_code == 200, rest.text
    assert "result" in await admin.call("send_message", args)
    assert len(admin.sends) == 2
    for message in admin.sends:
        text = message["text"] if admin.provider == "telegram" else message["text"]["body"]
        assert text == "/echo direct"
    response = await admin.client.get(
        "/bots/_admin/send_message", params=args, headers={"Authorization": "Bearer administrator"}
    )
    assert response.status_code == 405
    assert len(admin.sends) == 2


async def test_registry_reads_never_return_provider_credentials_or_raw_configuration(admin):
    admin.app.registrations["alpha"]["config"] = {"private": {"token": "hidden-setting"}}
    response = await admin.rest("get_bot_registration", {"code": "alpha"})
    assert response.status_code == 200
    record = response.json()
    assert record["code"] == "alpha"
    assert not {"token", "webhook_secret", "config"} & record.keys()
    listing = await admin.call("list_bots")
    assert "1:secret" not in str(listing)
    assert "hidden-setting" not in str(listing)


async def test_registration_uses_only_configured_classes_and_returns_metadata(admin):
    args = {"code": "beta", "bot_class": "demo", "token": "2:secret"}
    if admin.provider == "whatsapp":
        args.update(phone_number_id="1002", business_account_id="900")
    response = await admin.call("register_bot", args)
    assert "result" in response, response
    assert admin.app.get_bot("beta").code == "beta"
    assert "2:secret" not in str(response)
    args.update(code="gamma", bot_class="os:system")
    assert (await admin.call("register_bot", args))["result"]["isError"] is True
    assert "gamma" not in admin.app.bots


async def test_openapi_documents_admin_routes_and_hides_task_routes(admin):
    response = await admin.client.get(
        "/bots/_meta/schema_json", headers={"Authorization": "Bearer administrator"}
    )
    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert "/_admin/send_message" in paths
    assert all(path.startswith("/_admin/") for path in paths)
    assert "post" in paths["/_admin/send_message"]
    for path in ("deliver_update", "deliver_reminder", "deliver_announcement"):
        assert (await admin.client.post(f"/bots/{path}", json={})).status_code == 404
        assert "error" in await admin.call(path)
    assert (await admin.client.get("/bots/_meta/schema_json")).status_code in (401, 403)


async def test_conversations_and_reminders_use_the_application_persistence(admin):
    response = await admin.rest("create_conversation", {
        "bot_code": "alpha", "route": "conversation", "context": {"pr": 7},
        "participants": [{"user_id": admin.recipient, "chat_id": admin.recipient}],
    })
    assert response.status_code == 200, response.text
    conversation = response.json()
    args = {"bot_code": "alpha", "conversation_id": conversation["id"]}
    assert "result" in await admin.call("close_conversation", args)
    assert (await admin.app.get_conversation(**args))["state"] == "closed"
    response = await admin.rest("schedule_reminder", {
        "bot_code": "alpha", "chat_id": admin.recipient, "text": "Later",
        "when": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    })
    assert response.status_code == 200, response.text
    code = response.text
    assert "result" in await admin.call("cancel_reminder", {"code": code})
    assert (await admin.app.get_reminder(code))["delivery_state"] == "cancelled"


async def test_admin_announcement_still_runs_through_internal_tasks(admin):
    response = await admin.rest("queue_announcement", {
        "bot_code": "alpha", "chat_ids": [admin.recipient], "text": "New PR",
    })
    assert response.status_code == 200, response.text
    assert admin.sends == []
    await drain(admin.server)
    assert len(admin.sends) == 1


async def test_telegram_webhook_and_admin_endpoints_coexist(setup):
    server, app, api = setup
    await app.register_bot(
        code="alpha", bot_class=TelegramBot, token="1:secret", config={"settings": {}}
    )
    assert (await webhook(server, app)).status_code == 200
    await drain(server)
    assert api.calls[-1][1]["text"] == "Hello [demo]"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="https://example.com"
    ) as client:
        response = await client.post("/telegram/_mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
    assert response.status_code == 200
    assert "result" in response.json()


pytest_plugins = ["tests.telegram.support"]


async def test_remote_registration_accepts_a_catalog_module_reference(admin):
    bot_class = TelegramBot if admin.provider == "telegram" else WhatsAppBot
    admin.app.bot_classes["imported"] = f"{bot_class.__module__}:{bot_class.__name__}"
    aliases = await admin.rest("list_bot_classes")
    assert aliases.json() == ["demo", "imported"]
    args = {"code": "beta", "bot_class": "imported", "token": "2:secret"}
    if admin.provider == "whatsapp":
        args.update(phone_number_id="1002", business_account_id="900")
    response = await admin.rest("register_bot", args)
    assert response.status_code == 200, response.text
    assert isinstance(admin.app.get_bot("beta"), bot_class)
    assert (await admin.rest("activate_bot", {"code": "beta"})).status_code == 200


async def test_provider_specific_operations_are_dispatched_through_mcp(admin):
    if admin.provider == "telegram":
        response = await admin.call("send_poll", {
            "bot_code": "alpha", "chat_id": admin.recipient,
            "question": "Review?", "options": ["Yes", "No"],
        })
        assert not response["result"].get("isError"), response
        assert "result" in await admin.call("get_poll", {"bot_code": "alpha", "poll_id": "poll-1"})
        assert "result" in await admin.call("stop_poll", {"bot_code": "alpha", "poll_id": "poll-1"})
    else:
        # This recipient has no open service window; only an explicit template is eligible.
        response = await admin.call("send_template", {
            "bot_code": "alpha", "chat_id": "IT.123456789", "name": "new_pr", "language": "en",
        })
        assert not response["result"].get("isError"), response
        record = response["result"]["structuredContent"]
        assert record["status"] == "accepted"
        response = await admin.rest("get_message", {"bot_code": "alpha", "message_id": record["message_id"]})
        assert response.json()["status"] == "accepted"


async def test_invalid_recipient_type_and_reminder_date_have_no_side_effects(admin):
    response = await admin.rest("send_message", {
        "bot_code": "alpha", "chat_id": "42" if admin.provider == "telegram" else 42,
        "text": "wrong type",
    })
    assert response.status_code == 400
    for when in ("tomorrow", "2026-12-01T09:00:00"):
        response = await admin.rest("schedule_reminder", {
            "bot_code": "alpha", "chat_id": admin.recipient, "text": "invalid", "when": when,
        })
        assert response.status_code == 400, response.text
    assert admin.sends == []


class CatalogConfiguration(CONFIGURATION_TEMPLATES["default"]):
    def applications_section(self, cfg):
        apps = cfg.applications()
        telegram = apps.application(code="telegram", app_class=TelegramBotApplication)
        telegram.telegram(
            persistence_route="registry/bots", bot_classes={"demo": "examples.telegram_bot:DemoBot"}
        )
        whatsapp = apps.application(code="whatsapp", app_class=WhatsAppBotApplication)
        whatsapp.whatsapp(
            persistence_route="registry/bots", api_version="v25.0",
            bot_classes={"demo": "examples.whatsapp_bot:DemoBot"},
        )


def test_registration_catalog_can_be_defined_in_both_provider_grammars():
    server = AsgiServer(config=CatalogConfiguration)
    for provider in ("telegram", "whatsapp"):
        assert server.applications[provider].bot_classes == {
            "demo": f"examples.{provider}_bot:DemoBot"
        }
