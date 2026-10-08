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

"""Contract: authenticated WhatsApp batches, encrypted persistence and local sending."""

import httpx
import pytest
from kajenn_bot_application.bot import BotBaseApplication
from kajenn_bot_application.whatsapp import WhatsAppBotApplication
from examples.whatsapp_bot import DemoBot
from tests.telegram.support import drain

pytest_plugins = ["tests.whatsapp.support"]


async def test_signed_batch_is_durable_before_ack_and_routes_each_number(wa):
    await wa.start()
    assert isinstance(wa.app, BotBaseApplication)
    payload = wa.payload()
    payload["entry"].extend(wa.payload("/echo beta", "wamid.in2", "1002")["entry"])
    assert (await wa.post(payload)).status_code == 200
    assert not [c for c in wa.api.calls if c[0] == "POST"]
    await drain(wa.server)
    sends = [c for c in wa.api.calls if c[0] == "POST"]
    assert {(c[1], c[2]["text"]["body"]) for c in sends} == {
        ("/v25.0/1001/messages", "hello"),
        ("/v25.0/1002/messages", "beta"),
    }
    assert (await wa.post(payload)).status_code == 200
    assert not wa.server.tasks.spool.list_pending()
    files = list((wa.root / "telegram_registry" / "whatsapp").glob("*.json"))
    assert files and all(b"secret-alpha" not in node.read_bytes() for node in files)


async def test_handshake_tampering_unknown_number_and_protected_routes(wa):
    await wa.start()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wa.server), base_url="https://example.com"
    ) as client:
        response = await client.get(
            "/whatsapp",
            params={"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "123"},
        )
        assert response.status_code == 200 and response.text == "123"
        assert (
            await client.get("/whatsapp", params={"hub.verify_token": "wrong"})
        ).status_code == 403
    assert (await wa.post(wa.payload(), "sha256=invalid")).status_code == 403
    assert (await wa.post(wa.payload(number="9999"))).status_code == 200
    assert not wa.server.tasks.spool.list_pending()
    assert (await wa.post(wa.payload("/restricted"))).status_code == 200
    await drain(wa.server)
    assert not [c for c in wa.api.calls if c[0] == "POST"]


async def test_local_sender_requires_template_without_window_and_never_subscribes(wa):
    await wa.start()
    local = WhatsAppBotApplication(
        code="local", persistence_route="registry/bots", api_version="v25.0", client=wa.client
    )
    local.server = wa.server
    await local.on_startup()
    await local.register_bot(
        code="alpha",
        bot_class=DemoBot,
        token="secret-alpha",
        phone_number_id="1001",
        business_account_id="900",
    )
    with pytest.raises(ValueError, match="window"):
        await local.send_message("alpha", "391234", "New PR")
    sent = await local.send_template("alpha", "391234", name="new_pr", language="it", components=[])
    assert sent["status"] == "accepted"
    assert wa.api.calls[-1][2]["type"] == "template"
    assert all("subscribed_apps" not in path for _, path, _ in wa.api.calls)
