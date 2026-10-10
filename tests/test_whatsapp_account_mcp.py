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

import asyncio

import httpx
import pytest
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPBadRequest, HTTPUnauthorized, HTTPForbidden

from examples.whatsapp_account.application import WhatsAppAccountApplication
from examples.whatsapp_account.directory import _Directory
from tests.storage_support import site_mounts


class Identity(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        if credential not in ("Bearer owner", "Bearer reader", "Bearer readonly"):
            raise HTTPUnauthorized("Invalid credential")
        tags = (["whatsapp_account_read", "whatsapp_account_write", "whatsapp_account_manage", "admin"]
                if credential == "Bearer owner" else
                ["whatsapp_account_read"] if credential == "Bearer readonly" else [])
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
        return {"id": "sent-id", "chat_id": chat_id, "status": "submitted"}


@pytest.fixture
async def account(tmp_path):
    connection = FakeConnection(tmp_path / "directory.db")
    server = AsgiServer(
        applications=[(Identity, {"code": "identity"}),
                      (WhatsAppAccountApplication,
                       {"code": "whatsapp", "connection_factory": lambda: connection,
                        "policy": {"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}}})],
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
    assert names == {"get_status", "get_sync_status", "get_contacts", "get_chats", "get_chat",
                     "get_messages", "get_message_status", "get_unread", "search_messages",
                     "send_text", "reply_message", "react_message", "send_media", "download_media",
                     "mark_read", "archive_chat", "mute_chat", "get_group", "get_group_members",
                     "create_group", "update_group_members", "request_history", "get_policy",
                     "set_policy", "get_audit_log"}
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


async def test_readonly_avatar_cannot_discover_or_execute_writes_and_policy(account):
    app, connection, http = account
    calls = Calls(http)
    names = {t["name"] for t in (await calls.tools("readonly")).json()["result"]["tools"]}
    assert "get_contacts" in names
    assert not names & {"send_text", "set_policy", "get_audit_log", "create_group"}
    for name, args in [("send_text", {"chat_id": "11@s.whatsapp.net", "text": "secret"}),
                       ("set_policy", {"policy": {"operations": ["*"], "chats": {}}})]:
        body = (await calls.call(name, args, token="readonly")).json()
        assert "error" in body or body.get("result", {}).get("isError")
    assert connection.sent == []


async def test_policy_filters_search_before_pagination_and_denies_alias_bypass(account):
    app, connection, http = account
    store = connection.directory
    store.add_contact("11@lid", "Carla Test", pn="11@s.whatsapp.net", lid="11@lid")
    store.add_message("11@lid", "private", "sender", "hidden", 2, False)
    store.add_message("22@s.whatsapp.net", "public", "sender", "visible", 3, False)
    store.set_chat_state("11@lid", unread=True)
    store.set_chat_state("22@s.whatsapp.net", unread=True)
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read", "write"],
                                                       "11@s.whatsapp.net": []}})
    assert [i["id"] for i in (await app.get_contacts(limit=1))["items"]] == ["22@s.whatsapp.net"]
    assert (await app.search_messages("hidden"))["items"] == []
    assert (await app.get_unread())["items"][0]["id"] == "22@s.whatsapp.net"
    with pytest.raises(HTTPForbidden):
        await app.send_text("11@lid", "must not be sent")
    with pytest.raises(HTTPForbidden):
        await app.get_messages("11@lid")
    assert connection.sent == []
    assert connection.directory.get_setting("policy") == await app.get_policy()
    audit = await app.get_audit_log()
    assert any(i["outcome"] == "denied" for i in audit["items"])
    assert "must not be sent" not in str(audit)


async def test_policy_applies_to_python_and_persists_reconstruction(account):
    app, connection, http = account
    await app.set_policy({"operations": [], "chats": {}})
    with pytest.raises(HTTPForbidden):
        await app.get_contacts()
    assert connection.directory.get_setting("policy") == {"operations": [], "chats": {}}


@pytest.mark.parametrize("value", [True, "yes", 1, None])
async def test_invalid_state_and_media_fail_without_remote_effects(account, value):
    app, connection, http = account
    if value is not True:
        with pytest.raises(HTTPBadRequest):
            await app.mark_read("11@s.whatsapp.net", value)
    with pytest.raises(HTTPBadRequest):
        await app.send_media("11@s.whatsapp.net", "image", "!!!", "image/png")
    assert connection.sent == []


async def test_audit_uses_authenticated_avatar_and_policy_update_is_atomic(account):
    app, connection, http = account
    calls = Calls(http)
    before = await app.get_policy()
    response = await calls.call("get_contacts", token="owner")
    assert "structuredContent" in response.json()["result"]
    audit = await app.get_audit_log()
    assert any(i["actor"] == "Bearer owner" and i["operation"] == "get_contacts"
               for i in audit["items"])
    for bad in ({"operations": ["unknown"], "chats": {}},
                {"operations": ["*"], "chats": {"name": ["read"]}}):
        with pytest.raises(HTTPBadRequest):
            await app.set_policy(bad)
        assert await app.get_policy() == before


async def test_policy_change_waits_for_running_provider_operation(account):
    app, connection, http = account
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(chat_id, text):
        entered.set()
        await release.wait()
        return {"id": "one", "status": "submitted"}

    connection.send_text = delayed
    sending = asyncio.create_task(app.send_text("11@s.whatsapp.net", "one"))
    await asyncio.wait_for(entered.wait(), 1)
    changing = asyncio.create_task(app.set_policy({"operations": [], "chats": {}}))
    await asyncio.sleep(0)
    assert not changing.done()
    release.set()
    await sending
    await changing
    with pytest.raises(HTTPForbidden):
        await app.send_text("11@s.whatsapp.net", "two")


async def test_rest_uses_the_same_avatar_and_policy_rules(account):
    app, connection, http = account
    response = await http.post("/whatsapp/_account/get_contacts",
                               headers={"Authorization": "Bearer readonly"}, json={"limit": 1})
    assert response.status_code == 200
    response = await http.post("/whatsapp/_account/set_policy",
                               headers={"Authorization": "Bearer readonly"},
                               json={"policy": {"operations": ["*"], "chats": {}}})
    assert response.status_code == 403
    response = await http.post("/whatsapp/_account/send_text",
                               headers={"Authorization": "Bearer readonly"},
                               json={"chat_id": "11@s.whatsapp.net", "text": "no"})
    assert response.status_code == 403
    assert connection.sent == []
