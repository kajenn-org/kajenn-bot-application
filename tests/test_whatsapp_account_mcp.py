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
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPException, HTTPBadRequest, HTTPUnauthorized, HTTPForbidden

from examples.whatsapp_account.application import WhatsAppAccountApplication
from examples.whatsapp_account.directory import _Directory
from examples.whatsapp_account.outbox import _Outbox
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
    assert names == {"transcribe_message", "decide_group_requests", "link_community_groups", "get_community_groups", "get_channels", "vote_poll", "respond_group_event", "get_poll_results", "get_events", "schedule_message", "get_outbox", "decide_message", "revoke_message", "delete_message", "get_channel_messages", "react_channel_message", "create_poll", "create_group_event", "create_community", "deactivate_community", "get_group_requests", "get_status", "get_sync_status", "get_contacts", "get_chats", "get_chat",
                     "get_messages", "get_message_status", "get_unread", "search_messages",
                     "send_text", "reply_message", "react_message", "send_media", "download_media",
                     "mark_read", "archive_chat", "mute_chat", "get_group", "get_group_members",
                     "create_group", "update_group_members", "request_history", "get_policy",
                     "set_policy", "get_audit_log",
                     "pin_chat",
                     "star_message",
                     "edit_message",
                     "save_contact",
                     "get_profile_picture",
                     "block_contact",
                     "set_presence",
                     "send_chat_state",
                     "set_profile_name",
                     "set_profile_about",
                     "get_privacy",
                     "set_privacy",
                     "set_disappearing_default",
                     "set_group_title",
                     "set_group_description",
                     "leave_group",
                     "get_group_invite",
                     "set_group_setting",
                     "set_group_disappearing",
                     "set_group_approval",
                     "set_group_member_add",
                     "create_label",
                     "delete_label",
                     "set_chat_label",
                     "create_channel",
                     "get_channel",
                     "follow_channel",
                     "update_channel",
                     "send_channel_text",
                     "mute_channel",
                     }
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


@pytest.mark.parametrize("operation,arguments", [
    ("pin_chat", {"chat_id": "11@s.whatsapp.net", "pinned": 1}),
    ("set_presence", {"available": "yes"}),
    ("set_privacy", {"category": "Other", "value": "All"}),
    ("set_disappearing_default", {"seconds": True}),
    ("create_poll", {"chat_id": "11@s.whatsapp.net", "title": "Q", "options": ["a", "a"]}),
    ("create_poll", {"chat_id": "11@s.whatsapp.net", "title": "Q", "options": ["a", 3]}),
    ("create_poll", {"chat_id": "11@s.whatsapp.net", "title": "Q", "options": ["a", "b"], "selectable_count": 3}),
    ("create_group_event", {"chat_id": "123@g.us", "title": "Event", "start_time": 50, "end_time": 40}),
    ("get_channel", {"chat_id": "11@s.whatsapp.net"}),
    ("block_contact", {"chat_id": "123@g.us"}),
    ("create_label", {"label_id": "a", "name": "Label", "color": 20}),
])
async def test_extended_validation_never_calls_provider(account, operation, arguments):
    app, connection, http = account
    provider = AsyncMock()
    setattr(connection, operation, provider)
    with pytest.raises(HTTPBadRequest):
        await getattr(app, operation)(**arguments)
    provider.assert_not_awaited()


async def test_extended_mcp_role_and_alias_policy(account):
    app, connection, http = account
    connection.pin_chat = AsyncMock(return_value={"status": "returned"})
    calls = Calls(http)
    denied = await calls.call("pin_chat", {"chat_id": "11@s.whatsapp.net"}, token="readonly")
    assert denied.json().get("error") or denied.json().get("result", {}).get("isError")
    connection.pin_chat.assert_not_awaited()
    connection.directory.add_alias("11@s.whatsapp.net", "99@lid")
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read", "write"], "99@lid": []}})
    with pytest.raises(HTTPForbidden):
        await app.pin_chat("11@s.whatsapp.net")
    connection.pin_chat.assert_not_awaited()


async def test_outbox_approval_is_first_wins_and_policy_rechecked(account):
    app, connection, http = account
    job = await app.schedule_message("11@s.whatsapp.net", "Queued", int(time.time()) + 3600)
    assert job["state"] == "pending"
    decision = await app.decide_message(job["id"], "approve")
    assert decision["state"] == "scheduled"
    assert (await app.decide_message(job["id"], "reject"))["state"] == "scheduled"
    await app.set_policy({"operations": ["get_outbox"], "chats": {"*": ["read", "write"]}})
    await app.outbox.dispatch(job["id"])
    assert not connection.sent
    assert (await app.get_outbox())["items"][0]["state"] == "blocked"


async def test_outbox_provider_failure_is_never_retried(account):
    app, connection, http = account
    connection.send_text = AsyncMock(side_effect=ConnectionError("private provider detail"))
    job = await app.schedule_message("11@s.whatsapp.net", "Queued", int(time.time()) + 3600, False)
    await app.outbox.dispatch(job["id"])
    await app.outbox.dispatch(job["id"])
    connection.send_text.assert_awaited_once()
    row = (await app.get_outbox())["items"][0]
    assert row["state"] == "unconfirmed"
    assert row["error"] == "provider_failure"


async def test_outbox_success_and_cancellation(account):
    app, connection, http = account
    job = await app.schedule_message("11@s.whatsapp.net", "Queued", int(time.time()) + 3600, False)
    await app.outbox.dispatch(job["id"])
    assert connection.sent == [("11@s.whatsapp.net", "Queued")]
    assert (await app.get_outbox())["items"][0]["message_id"] == "sent-id"
    cancelled = await app.schedule_message("11@s.whatsapp.net", "Cancelled", int(time.time()) + 3601)
    await app.decide_message(cancelled["id"], "cancel")
    await app.outbox.dispatch(cancelled["id"])
    assert len(connection.sent) == 1


async def test_event_journal_filters_before_pagination_and_redacts(account):
    app, connection, http = account
    store = connection.directory
    store.add_event("message", {"chat_id": "22@s.whatsapp.net", "message_id": "hidden"})
    store.add_event("message", {"chat_id": "11@s.whatsapp.net", "message_id": "visible", "text": "secret"})
    await app.set_policy({"operations": ["get_events"], "chats": {"11@s.whatsapp.net": ["read"]}})
    page = await app.get_events(0, 1)
    assert page["items"][0]["message_id"] == "visible"
    assert "secret" not in str(page)
    assert (await app.get_events(page["next_cursor"]))["items"] == []


async def test_outbox_recovery_never_replays_inflight_message(account):
    app, connection, http = account
    job = await app.schedule_message("11@s.whatsapp.net", "Queued", int(time.time()) + 3600, False)
    await app.outbox.stop()
    app.outbox.finish(job["id"], "sending")
    app.outbox = _Outbox(app)
    await asyncio.sleep(0)
    assert (await app.get_outbox())["items"][0]["state"] == "unconfirmed"
    assert connection.sent == []


async def test_community_link_checks_every_group_and_filters_pages(account):
    app, connection, http = account
    connection.link_community_groups = AsyncMock()
    connection.get_community_groups = AsyncMock(return_value=[{"id": "2@g.us"}, {"id": "3@g.us"}])
    await app.set_policy({"operations": ["*"], "chats": {"1@g.us": ["read", "admin"], "3@g.us": ["read"]}})
    with pytest.raises(HTTPForbidden):
        await app.link_community_groups("1@g.us", ["2@g.us"])
    connection.link_community_groups.assert_not_awaited()
    page = await app.get_community_groups("1@g.us", limit=1)
    assert page["items"] == [{"id": "3@g.us"}]


async def test_newsletter_is_not_a_group_participant_or_text_chat(account):
    app, connection, http = account
    connection.directory.add_chat("1@newsletter")
    with pytest.raises(HTTPBadRequest):
        app.validate_participants(["1@newsletter"])
    with pytest.raises(HTTPBadRequest):
        await app.send_text("1@newsletter", "Wrong transport")


async def test_transcription_contract_audio_result_and_no_persistence(account):
    app, connection, http = account
    connection.directory.set_message_data("11@s.whatsapp.net", "m1", kind="audio")
    connection.download_media = AsyncMock(return_value={"content_base64": "YXVkaW8=", "mimetype": "audio/ogg"})
    engine = AsyncMock(return_value={"text": "Buongiorno", "language": "it", "duration": 2})
    app.transcriber = SimpleNamespace(transcribe=engine)
    result = await Calls(http).call("transcribe_message", {"chat_id": "11@s.whatsapp.net", "message_id": "m1"})
    assert result.json()["result"]["structuredContent"]["text"] == "Buongiorno"
    engine.assert_awaited_once_with(b"audio", "audio/ogg", "it")
    assert connection.directory.get_message("11@s.whatsapp.net", "m1")["text"] == "Hello"
    assert "Buongiorno" not in str(connection.directory.get_audit_log(100, 0))
    assert connection.sent == []


async def test_transcription_denied_unconfigured_and_non_audio_do_not_download(account):
    app, connection, http = account
    connection.download_media = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await app.transcribe_message("11@s.whatsapp.net", "m1")
    assert error.value.status == 503
    app.transcriber = SimpleNamespace(transcribe=AsyncMock())
    with pytest.raises(HTTPBadRequest):
        await app.transcribe_message("11@s.whatsapp.net", "m1")
    with pytest.raises(HTTPBadRequest):
        await app.transcribe_message("11@s.whatsapp.net", "m1", "../it")
    connection.directory.add_alias("11@s.whatsapp.net", "99@lid")
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read"], "99@lid": []}})
    with pytest.raises(HTTPForbidden):
        await app.transcribe_message("11@s.whatsapp.net", "m1")
    connection.download_media.assert_not_awaited()
    app.transcriber.transcribe.assert_not_awaited()
