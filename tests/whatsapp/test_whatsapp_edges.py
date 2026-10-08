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

"""Implementation checks at the WhatsApp protocol and persistence boundaries."""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from kajenn import AsgiServer
from examples.whatsapp_bot.config import WhatsAppDemoConfiguration
from examples.whatsapp_bot.local_config import WhatsAppLocalConfiguration

from examples.whatsapp_bot import DemoBot
from kajenn_bot_application.whatsapp import WhatsAppBotApplication
from tests.telegram.support import drain

pytest_plugins = ["tests.whatsapp.support"]


async def test_business_scoped_ids_use_recipient_and_correlate_status_aliases(wa):
    await wa.start()
    payload = wa.payload()
    item = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del item["from"]
    item["from_user_id"] = "IT.123456789"
    assert (await wa.post(payload)).status_code == 200
    await drain(wa.server)
    outgoing = wa.api.calls[-1][2]
    assert outgoing["recipient"] == "IT.123456789" and "to" not in outgoing
    sent = await wa.app.send_message("alpha", "IT.123456789", "follow up")
    value = payload["entry"][0]["changes"][0]["value"]
    del value["messages"]
    value["statuses"] = [
        {
            "id": sent["message_id"],
            "timestamp": str(int(time.time())),
            "status": "delivered",
            "recipient_id": "391234",
            "recipient_user_id": "IT.123456789",
        }
    ]
    assert (await wa.post(payload)).status_code == 200
    await drain(wa.server)
    assert (await wa.app.get_message("alpha", sent["message_id"]))["status"] == "delivered"


@pytest.mark.parametrize("case", ["code", "number", "token", "duplicate", "config"])
async def test_registration_validation_has_no_network_side_effects(wa, case):
    await wa.start()
    params = dict(
        code="new",
        bot_class=DemoBot,
        token="secret",
        phone_number_id="1003",
        business_account_id="900",
    )
    if case == "code":
        params["code"] = "../bad"
    if case == "number":
        params["phone_number_id"] = "/bad"
    if case == "token":
        params["token"] = "new\nsecret"
    if case == "duplicate":
        params["phone_number_id"] = "1001"
    if case == "config":
        params["config"] = {"notifications": {"approval_template": {"name": "x"}}}
    count = len(wa.api.calls)
    with pytest.raises(ValueError):
        await wa.app.register_bot(**params)
    assert len(wa.api.calls) == count


async def test_send_only_endpoint_and_connection_configuration(wa):
    await wa.start()
    wa.app._webhook_url = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wa.server), base_url="https://example.com"
    ) as client:
        assert (await client.post("/whatsapp")).status_code == 404
    with pytest.raises(RuntimeError, match="disabled"):
        await wa.app.deliver_update("alpha", {})
    invalid = WhatsAppBotApplication(
        code="invalid", persistence_route="registry/bots", api_version="bad", client=wa.client
    )
    invalid.server = wa.server
    with pytest.raises(ValueError, match="api_version"):
        await invalid.on_startup()
    invalid._api_version = "v25.0"
    invalid._webhook_url = "https://example.com/invalid"
    with pytest.raises(ValueError, match="app_secret"):
        await invalid.on_startup()
    invalid._app_secret = "secret"
    with pytest.raises(ValueError, match="verify_token"):
        await invalid.on_startup()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": {"message": "secret"}}),
        httpx.Response(200, content=b"bad-json"),
        httpx.Response(200, json={"messages": []}),
    ],
)
async def test_uncertain_responses_are_never_retried(wa, response):
    await wa.start()
    calls = []

    def respond(request):
        calls.append(request)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        wa.app._client = client
        with pytest.raises(RuntimeError) as failure:
            await wa.app.send_template("alpha", "391234", name="notice", language="it")
        assert failure.value.outcome_uncertain
        assert "secret" not in str(failure.value)
    assert len(calls) == 1


async def test_accepted_send_with_failed_persistence_is_uncertain(wa, monkeypatch):
    await wa.start()
    original = wa.app._persist

    async def fail(operation, record=None):
        if operation == "save_message":
            raise RuntimeError("storage broke secret")
        return await original(operation, record)

    monkeypatch.setattr(wa.app, "_persist", fail)
    results = await wa.app.send_announcement(
        "alpha", ["391234"], template={"name": "notice", "language": "it"}
    )
    assert results[0]["status"] == "uncertain" and "secret" not in results[0]["error"]


async def test_exhausted_throttling_retains_cooldown_and_connect_errors_retry(wa, monkeypatch):
    await wa.start()
    waits = []

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(wa.app.delivery, "wait", wait)
    calls = []

    def throttled(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"code": 131056}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(throttled)) as client:
        wa.app._client = client
        with pytest.raises(RuntimeError, match="rate limit"):
            await wa.app.send_template("alpha", "391234", name="notice", language="it")
    assert len(calls) == 3 and "1001" in wa.app.delivery.cooldowns
    counter = 0

    def reconnect(request):
        nonlocal counter
        counter += 1
        if counter == 1:
            raise httpx.ConnectError("secret", request=request)
        return wa.api.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reconnect)) as client:
        wa.app._client = client
        await wa.app.send_template("alpha", "391234", name="notice", language="it")
    assert counter == 2 and len(waits) == 4


async def test_expired_credentials_fail_once_and_other_recipients_continue(wa):
    await wa.start()

    def respond(request):
        payload = json.loads(request.content)
        if payload["to"] == "391234":
            return httpx.Response(401, json={"error": {"code": 190, "message": "secret"}})
        return wa.api.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        wa.app._client = client
        results = await wa.app.send_announcement(
            "alpha", ["391234", "392345"], template={"name": "notice", "language": "it"}
        )
    assert [r["status"] for r in results] == ["failed", "accepted"]
    assert "secret" not in results[0]["error"]


async def test_reminder_interruption_requires_explicit_recovery(wa):
    await wa.start()
    code = await wa.app.schedule_reminder(
        "alpha",
        "391234",
        when=datetime.now(timezone.utc) + timedelta(days=1),
        template={"name": "notice", "language": "it"},
    )
    record = await wa.app.get_reminder(code)
    record["delivery_state"] = "sending"
    await wa.app.delivery.save_reminder(record)
    count = len(wa.api.calls)
    await wa.app.deliver_reminder(code)
    assert (await wa.app.get_reminder(code))["delivery_state"] == "uncertain"
    assert not await wa.app.cancel_reminder(code)
    assert len(wa.api.calls) == count


async def test_receipt_survives_spool_cleanup_and_has_eight_day_retention(wa):
    await wa.start()
    payload = wa.payload()
    before = time.time()
    await wa.post(payload)
    await drain(wa.server)
    rows = wa.server.storage.node("site:telegram_registry/whatsapp/receipts").children()
    receipt = json.loads(next(iter(rows)).read_text())
    assert 8 * 86400 - 1 <= receipt - before <= 8 * 86400 + 5
    for descriptor in wa.server.tasks.spool.list_by_owner("whatsapp:alpha"):
        wa.server.tasks.spool.purge(descriptor["task_id"])
    await wa.post(payload)
    assert not wa.server.tasks.spool.list_pending()


@pytest.mark.parametrize(
    "recipe,receiver", [(WhatsAppDemoConfiguration, True), (WhatsAppLocalConfiguration, False)]
)
def test_example_grammar_selects_connection_mode(monkeypatch, recipe, receiver):
    monkeypatch.setenv("GENRO_STORAGE_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("KAJENN_WHATSAPP_API_VERSION", "v25.0")
    monkeypatch.setenv("KAJENN_WHATSAPP_WEBHOOK_URL", "https://example.com/whatsapp")
    monkeypatch.setenv("KAJENN_WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setenv("KAJENN_WHATSAPP_VERIFY_TOKEN", "verify")
    server = AsgiServer(config=recipe)
    app = server.applications["whatsapp"]
    assert app.api_version == "v25.0"
    assert bool(app.webhook_url) == receiver
    assert app.persistence_route == "registry/bots"


async def test_unapproved_general_callbacks_do_not_bypass_admission(wa):
    await wa.start(
        access={"approval_required": True, "admins": ["39100"]},
        notifications={"approval_template": {"name": "approval", "language": "it"}},
    )
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "391234", "last_inbound": time.time()}
    )
    conversation = await wa.app.create_conversation(
        "alpha", participants=[{"user_id": "391234", "chat_id": "391234"}], route="conversation"
    )
    sent = await wa.app.send_conversation_message(
        "alpha", conversation["id"], "391234", "Proceed?", buttons={"Go": "go"}
    )
    payload = wa.payload(
        type="interactive",
        context={"id": sent["message_id"]},
        interactive={"type": "button_reply", "button_reply": {"id": f"c:{conversation['id']}:go"}},
    )
    await wa.post(payload)
    await drain(wa.server)
    records = await wa.app.conversations.get_records("alpha")
    assert any(r["kind"] == "admission" and r["state"] == "open" for r in records)
    assert not any(
        p.get("text", {}).get("body", "").startswith("PR ")
        for m, _, p in wa.api.calls
        if m == "POST"
    )
