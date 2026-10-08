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

"""Contract: WhatsApp routes authenticated batches through real server tasks."""

import hashlib
import hmac
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from kajenn import AsgiServer
from kajenn_bot_application.whatsapp import WhatsAppBotApplication
from examples.whatsapp_bot import DemoBot
from examples.whatsapp_bot.config import DemoRegistry
from tests.storage_support import site_mounts
from genro_routes import route


class RestrictedBot(DemoBot):
    @route(auth_rule="admin")
    def restricted(self, text=""):
        raise AssertionError("Protected command executed")


class GraphAPI:
    def __init__(self):
        self.calls = []

    def respond(self, request):
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, payload))
        if request.method == "GET":
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[1]})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.sent{len(self.calls)}"}]})


class Harness:
    def __init__(self, tmp_path, storage_key=None):
        self.root = tmp_path
        self.storage_key = storage_key or Fernet.generate_key().decode()
        self.api = GraphAPI()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.api.respond))
        self.server = AsgiServer(
            applications=[
                (DemoRegistry, {"code": "registry"}),
                (
                    WhatsAppBotApplication,
                    {
                        "code": "whatsapp",
                        "persistence_route": "registry/bots",
                        "webhook_url": "https://example.com/whatsapp",
                        "api_version": "v25.0",
                        "app_secret": "app-secret",
                        "verify_token": "verify",
                        "client": self.client,
                    },
                ),
            ],
            storage=site_mounts(tmp_path),
            storage_key=self.storage_key,
        )
        self.app = self.server.applications["whatsapp"]

    async def start(self, **config):
        await self.app.on_startup()
        for code, number in (("alpha", "1001"), ("beta", "1002")):
            await self.app.register_bot(
                code=code,
                bot_class=RestrictedBot,
                token=f"secret-{code}",
                phone_number_id=number,
                business_account_id="900",
                config=config,
            )

    def payload(
        self, text="/echo hello", event_id="wamid.in1", number="1001", user="391234", **extra
    ):
        message = {
            "id": event_id,
            "from": user,
            "timestamp": str(int(time.time())),
            "type": "text",
            "text": {"body": text},
            **extra,
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "900",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": number},
                                "messages": [message],
                            },
                        }
                    ],
                }
            ],
        }

    async def post(self, payload, signature=None):
        body = json.dumps(payload).encode()
        signature = (
            signature or "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.server), base_url="https://example.com"
        ) as client:
            return await client.post(
                "/whatsapp", content=body, headers={"X-Hub-Signature-256": signature}
            )


@pytest.fixture
async def wa(tmp_path):
    fixture = Harness(tmp_path)
    yield fixture
    await fixture.client.aclose()
