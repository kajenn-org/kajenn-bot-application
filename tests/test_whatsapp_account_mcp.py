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

"""Contract: authenticated MCP directory access and exact-recipient sending."""

import httpx
import pytest
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPBadRequest, HTTPUnauthorized

from examples.whatsapp_account.application import WhatsAppAccountApplication
from examples.whatsapp_account.directory import _Directory
from tests.storage_support import site_mounts


class Identity(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        if credential not in ("Bearer owner", "Bearer reader"):
            raise HTTPUnauthorized("Invalid credential")
        tags = ["whatsapp_account"] if credential == "Bearer owner" else []
        return {"identity": credential, "tags": tags, "data": {}}


class FakeConnection:
    def __init__(self, path):
        self.directory = _Directory(path)
        self.connected = True
        self.sync_status = "connected"
        self.sent = []

    async def start(self):
        self.directory.add_contact("11@s.whatsapp.net", "Carla Test")
        self.directory.add_contact("22@s.whatsapp.net", "Carla Test")
        self.directory.add_chat("11@s.whatsapp.net")
        self.directory.add_message("11@s.whatsapp.net", "m1", "other", "Hello", 1, False)

    async def stop(self):
        self.directory.close()

    async def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))
        return "sent-id"


@pytest.fixture
async def account(tmp_path):
    connection = FakeConnection(tmp_path / "directory.db")
    server = AsgiServer(
        applications=[(Identity, {"code": "identity"}),
                      (WhatsAppAccountApplication,
                       {"code": "whatsapp", "connection_factory": lambda: connection})],
        storage=site_mounts(tmp_path),
        channels={name: {"authentication_route": "/identity/check"} for name in ("mcp", "rest")},
    )
    app = server.applications["whatsapp"]
    await app.on_startup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),
                                base_url="https://test") as http:
        yield app, connection, http
    await app.on_shutdown()


class Calls:
    def __init__(self, http):
        self.http = http

    async def call(self, name, arguments=None, token="owner"):
        return await self.http.post("/whatsapp/_mcp", headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": name, "arguments": arguments or {}}})

    async def tools(self, token):
        return await self.http.post("/whatsapp/_mcp", headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})


async def test_mcp_discovery_filters_avatar_and_hides_pairing(account):
    app, connection, http = account
    calls = Calls(http)
    names = {t["name"] for t in (await calls.tools("owner")).json()["result"]["tools"]}
    assert names == {"get_status", "get_contacts", "get_chats", "get_messages", "send_text"}
    assert (await calls.tools("reader")).json()["result"]["tools"] == []
    response = await calls.call("get_contacts", token="reader")
    assert "error" in response.json() or response.json().get("result", {}).get("isError")
    assert (await calls.tools("invalid")).status_code in (401, 403)


async def test_mcp_reads_contacts_chats_messages_and_preserves_ambiguity(account):
    app, connection, http = account
    calls = Calls(http)
    result = (await calls.call("get_contacts", {"query": "carla test"})).json()
    assert len(result["result"]["structuredContent"]["items"]) == 2
    result = (await calls.call("get_chats")).json()
    assert len(result["result"]["structuredContent"]["items"]) == 1
    result = (await calls.call("get_messages", {"chat_id": "11@s.whatsapp.net"})).json()
    assert result["result"]["structuredContent"]["items"][0]["text"] == "Hello"
    assert connection.sent == []


async def test_send_requires_exact_known_peer_and_explicit_text(account):
    app, connection, http = account
    calls = Calls(http)
    with pytest.raises(HTTPBadRequest):
        await app.send_text("", "hello")
    with pytest.raises(HTTPBadRequest):
        await app.send_text("Carla Test", "hello")
    with pytest.raises(HTTPBadRequest):
        await app.send_text("unknown@s.whatsapp.net", "hello")
    result = (await calls.call("send_text", {"chat_id": "11@s.whatsapp.net", "text": "hello"})).json()
    assert result["result"]["structuredContent"]["status"] == "submitted"
    assert connection.sent == [("11@s.whatsapp.net", "hello")]


@pytest.mark.parametrize("arguments", [{"limit": True}, {"limit": 101}, {"offset": -1},
                                      {"query": 12}, {"query": "x" * 201}])
async def test_invalid_queries_are_rejected(account, arguments):
    app, _, _ = account
    with pytest.raises(HTTPBadRequest):
        await app.get_contacts(**arguments)
