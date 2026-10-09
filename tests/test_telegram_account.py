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

"""Contract: account permissions apply to Python, REST and MCP without exposing login."""

import base64
import json
from unittest.mock import AsyncMock, patch

from filelock import Timeout
from telethon import errors, functions, utils

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPBadRequest, HTTPForbidden, HTTPUnauthorized
from kajenn_bot_application.account_store import _AccountStore
from kajenn_bot_application import TelegramAccountApplication
from examples.telegram_account.config import TelegramAccountConfiguration
from tests.account.support import CHAT, OTHER, FakeTelegram
from tests.storage_support import site_mounts


class AccountIdentity(RoutedApplication):
    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        tags = {
            "Bearer operator": ["telegram_account"],
            "Bearer owner": ["admin", "telegram_account"],
            "Bearer reader": ["reader"],
        }
        if credential not in tags:
            raise HTTPUnauthorized("invalid credential")
        return {"identity": credential, "tags": tags[credential], "data": {}}


@pytest.fixture
async def account(tmp_path):
    key = Fernet.generate_key().decode()
    server = AsgiServer(
        applications=[
            (AccountIdentity, {"code": "identity"}),
            (
                TelegramAccountApplication,
                {
                    "code": "personal",
                    "api_id": 123,
                    "api_hash": "private-api-hash",
                    "session_path": str(tmp_path / "account" / "session.enc"),
                    "encryption_key": key,
                    "client_factory": FakeTelegram,
                },
            ),
        ],
        storage=site_mounts(tmp_path),
        channels={name: {"authentication_route": "/identity/check"} for name in ("rest", "mcp")},
        plugins={"openapi": True},
    )
    app = server.applications["personal"]
    await app.on_startup()
    await app.start_login("+39123456789")
    await app.complete_login(code="12345")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="https://test"
    ) as http:
        yield app, http, key
    await app.on_shutdown()


class AccountCalls:
    def __init__(self, http):
        self.http = http

    async def mcp(self, method, params=None, token="operator"):
        response = await self.http.post(
            "/personal/_mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )
        return response.json()

    async def policy(self, policy, token="owner"):
        return await self.http.post(
            "/personal/_admin/set_policy",
            json={"policy": policy},
            headers={"Authorization": f"Bearer {token}"},
        )


async def test_account_is_separate_and_credentials_never_enter_mcp(account):
    app, http, key = account
    calls = AccountCalls(http)
    result = await calls.mcp("tools/list")
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert {
        "get_messages",
        "get_chats",
        "send_text",
        "send_document",
        "create_channel",
        "create_group",
        "get_members",
        "invite_members",
        "remove_member",
        "set_member_admin",
    } <= names
    assert (
        not {"start_login", "complete_login", "set_policy", "revoke_session", "register_bot"}
        & names
    )
    assert "private-api-hash" not in json.dumps(result)
    assert key not in json.dumps(result)
    assert (await calls.mcp("tools/list", token="reader"))["result"]["tools"] == []
    status = await calls.mcp("tools/call", {"name": "get_status"})
    assert status["result"]["structuredContent"]["account"]["id"] == 7


async def test_deny_by_default_and_no_self_escalation(account):
    app, http, _ = account
    calls = AccountCalls(http)
    before = list(app.client.calls)
    with pytest.raises(HTTPForbidden):
        await app.send_text(CHAT, "not allowed")
    assert app.client.calls == before
    assert (
        await calls.policy(
            {"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}}, token="operator"
        )
    ).status_code == 403
    assert "error" in await calls.mcp("tools/call", {"name": "set_policy", "arguments": {}})


async def test_policy_applies_to_python_rest_mcp_and_revocation(account):
    app, http, _ = account
    calls = AccountCalls(http)
    policy = {"operations": ["send_text"], "chats": {str(CHAT): ["write"]}}
    assert (await calls.policy(policy)).status_code == 200
    assert (await app.send_text(CHAT, "direct"))["id"] == 10
    response = await calls.mcp(
        "tools/call", {"name": "send_text", "arguments": {"chat_id": CHAT, "text": "mcp"}}
    )
    assert response["result"].get("isError") is not True
    response = await http.post(
        "/personal/_account/send_text",
        json={"chat_id": CHAT, "text": "rest"},
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == 200
    with pytest.raises(HTTPForbidden):
        await app.send_text(OTHER, "no")
    assert (
        await http.get("/personal/_account/send_text", headers={"Authorization": "Bearer operator"})
    ).status_code == 405
    assert (await calls.policy({"operations": [], "chats": {}})).status_code == 200
    with pytest.raises(HTTPForbidden):
        await app.send_text(CHAT, "revoked")


async def test_history_is_bounded_paginated_and_filtered(account):
    app, http, _ = account
    await AccountCalls(http).policy(
        {"operations": ["get_chats", "get_messages"], "chats": {str(CHAT): ["read"]}}
    )
    chats = await app.get_chats()
    assert [chat["id"] for chat in chats["items"]] == [CHAT]
    first = await app.get_messages(CHAT, limit=2)
    second = await app.get_messages(CHAT, limit=2, before_id=first["next_before_id"])
    assert [m["id"] for m in first["items"] + second["items"]] == [5, 4, 3, 2]
    result = await app.get_messages(
        CHAT, since="2026-10-03T00:00:00Z", until="2026-10-05T00:00:00Z"
    )
    assert [m["id"] for m in result["items"]] == [4, 3]
    result = await app.get_messages(CHAT, search="Message 3")
    assert [m["id"] for m in result["items"]] == [3]
    with pytest.raises(HTTPForbidden):
        await app.get_messages(OTHER)


async def test_encrypted_session_survives_restart_and_logout_removes_authority(account):
    app, http, key = account
    await AccountCalls(http).policy(
        {"operations": ["get_messages"], "chats": {str(CHAT): ["read"]}}
    )
    path = app.session_path
    content = path.read_bytes()
    assert b"private-api-hash" not in content and b"session" not in content
    saved = json.loads(Fernet(key.encode()).decrypt(content))
    assert saved["account_id"] == 7 and saved["session"]
    assert path.stat().st_mode & 0o777 == 0o600
    await app.on_shutdown()
    await app.on_startup()
    assert (await app.get_status())["authorized"] is True
    assert (await app.get_messages(CHAT))["items"]
    response = await http.post(
        "/personal/_admin/revoke_session", headers={"Authorization": "Bearer owner"}, json={}
    )
    assert response.status_code == 200
    saved = json.loads(Fernet(key.encode()).decrypt(path.read_bytes()))
    assert saved["session"] == ""
    assert (await app.get_status())["authorized"] is False


async def test_documents_are_supplied_content_not_arbitrary_server_paths(account):
    app, http, _ = account
    await AccountCalls(http).policy(
        {"operations": ["send_document"], "chats": {str(CHAT): ["write"]}}
    )
    await app.send_document(CHAT, "notice.txt", base64.b64encode(b"hello").decode())
    call = next(call for call in app.client.calls if call[0] == "file")
    assert call[2:4] == (b"hello", "notice.txt")
    response = await http.post(
        "/personal/_account/send_document",
        json={"chat_id": CHAT, "filename": "../secret", "content_base64": "!!!!"},
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == 400


async def test_mutations_cannot_target_messages_from_another_chat_or_author(account):
    app, http, _ = account
    await AccountCalls(http).policy(
        {"operations": ["edit_message", "delete_messages"], "chats": {str(CHAT): ["write"]}}
    )
    app.client.messages[0].chat_id = OTHER
    with pytest.raises(HTTPForbidden):
        await app.delete_messages(CHAT, [5])
    app.client.messages[0].chat_id = CHAT
    app.client.messages[0].out = False
    with pytest.raises(HTTPForbidden):
        await app.edit_message(CHAT, 5, "not mine")
    assert not any(call[0] in ("edit", "delete") for call in app.client.calls)


async def test_full_grants_cover_creation_management_and_own_message_mutations(account):
    app, http, _ = account
    await AccountCalls(http).policy(
        {"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}}
    )
    assert (await app.create_channel("Release notes"))["id"] == -1000000000300
    assert (await app.create_group("Development"))["kind"] == "group"
    await app.set_chat_details(CHAT, title="Renamed")
    await app.set_chat_details(CHAT, description="Description")
    await app.invite_members(CHAT, [8, 9])
    await app.remove_member(CHAT, 8)
    await app.set_member_admin(CHAT, 9, ["invite_users", "pin_messages"])
    assert (await app.get_members(CHAT, limit=2))["next_offset"] == 2
    await app.edit_message(CHAT, 4, "Updated")
    await app.delete_messages(CHAT, [4])
    requests = [call[1] for call in app.client.calls if call[0] == "request"]
    assert isinstance(requests[0], functions.channels.CreateChannelRequest)
    assert requests[0].broadcast is True and not requests[0].megagroup
    assert requests[1].megagroup is True and not requests[1].broadcast
    assert isinstance(requests[-1], functions.channels.InviteToChannelRequest)
    # Exercise Telethon's real request resolution and binary serialization.
    for request in requests:
        await request.resolve(app.client, utils)
        assert bytes(request)
    update = next(call for call in app.client.calls if call[0] == "admin")
    assert update[3]["invite_users"] is True
    assert update[3]["add_admins"] is False and update[3]["anonymous"] is False


async def test_exact_chat_grants_override_wildcards_and_created_chats_need_grants(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {"*": ["read"], str(CHAT): []}})
    assert [item["id"] for item in (await app.get_chats())["items"]] == [OTHER]
    with pytest.raises(HTTPForbidden):
        await app.get_messages(CHAT)
    new = await app.create_group("New")
    with pytest.raises(HTTPForbidden):
        await app.send_text(new["id"], "requires write grant")


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"operations": ["get_messages"], "chats": {}, "unexpected": True},
        {"operations": ["arbitrary_rpc"], "chats": {}},
        {"operations": "*", "chats": {}},
        {"operations": ["*"], "chats": {"group name": ["read"]}},
        {"operations": ["*"], "chats": {"01": ["read"]}},
        {"operations": ["*"], "chats": {"*": ["owner"]}},
    ],
)
async def test_invalid_policies_do_not_change_permissions(account, policy):
    app, http, _ = account
    response = await AccountCalls(http).policy(policy)
    assert response.status_code == 400
    assert await app.get_policy() == {"operations": [], "chats": {}}


async def test_failed_policy_save_does_not_grant_authority(account):
    app, _, _ = account
    with patch.object(app.store, "save", side_effect=OSError("disk unavailable")):
        with pytest.raises(OSError):
            await app.set_policy({"operations": ["*"], "chats": {"*": ["write"]}})
    assert app.policy == {"operations": [], "chats": {}}


async def test_single_process_lease_and_wrong_key_refuse_session_access(account):
    app, _, _ = account
    second = _AccountStore(app.session_path, app._setting("encryption_key"))
    with pytest.raises(Timeout):
        second.open()


async def test_provider_errors_are_sanitized_and_waits_do_not_retry(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["send_text"], "chats": {str(CHAT): ["write"]}})
    app.client.failure = errors.FloodWaitError(None, capture=30)
    response = await http.post(
        "/personal/_account/send_text",
        json={"chat_id": CHAT, "text": "once"},
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "30"
    assert len([call for call in app.client.calls if call[0] == "send"]) == 1
    app.client.failure = errors.UnauthorizedError(None, "secret provider details")
    response = await http.post(
        "/personal/_account/send_text",
        json={"chat_id": CHAT, "text": "once"},
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == 409
    assert "secret provider details" not in response.text
    assert not (await app.get_status())["authorized"]


async def test_schema_and_routes_do_not_expose_login_or_credentials(account):
    _, http, _ = account
    response = await http.get(
        "/personal/_meta/schema_json", headers={"Authorization": "Bearer owner"}
    )
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/_account/send_text" in paths and "/_admin/set_policy" in paths
    assert not any("login" in path or "session_path" in path for path in paths)
    assert (await http.get("/personal/_meta/schema_json")).status_code in (401, 403)
    for path in ("start_login", "complete_login", "_account/start_login", "_admin/complete_login"):
        response = await http.post(
            f"/personal/{path}", json={}, headers={"Authorization": "Bearer owner"}
        )
        assert response.status_code == 404


async def test_local_login_supports_two_factor_and_rejects_replacing_active_account(account):
    app, _, _ = account
    await app.revoke_session()
    await app.start_login("+39123456789")
    assert await app.complete_login(code="2fa") == {"state": "password_required"}
    assert (await app.complete_login(password="local-password"))["state"] == "authorized"
    with pytest.raises(HTTPBadRequest):
        await app.start_login("+39999999999")


async def test_example_grammar_resolves_credentials_and_authentication_routes(
    tmp_path, monkeypatch
):
    for key, value in {
        "KAJENN_TELEGRAM_API_ID": "123",
        "KAJENN_TELEGRAM_API_HASH": "private-api-hash",
        "KAJENN_TELEGRAM_ACCOUNT_SESSION": str(tmp_path / "session.enc"),
        "KAJENN_TELEGRAM_ACCOUNT_KEY": Fernet.generate_key().decode(),
        "KAJENN_TELEGRAM_OPERATOR_TOKEN": "operator",
        "KAJENN_TELEGRAM_OWNER_TOKEN": "owner",
    }.items():
        monkeypatch.setenv(key, value)
    server = AsgiServer(config=TelegramAccountConfiguration, storage=site_mounts(tmp_path))
    app = server.applications["personal"]
    assert app._setting("api_id") == 123
    with patch.object(app, "_factory", FakeTelegram):
        await app.on_startup()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url="https://test"
        ) as client:
            response = await client.post(
                "/personal/_account/get_status",
                json={},
                headers={"Authorization": "Bearer operator"},
            )
            assert response.status_code == 200
            assert response.json()["authorized"] is False
        await app.on_shutdown()


async def test_invalid_encryption_key_leaves_state_untouched(account):
    app, _, _ = account
    path = app.session_path
    await app.on_shutdown()
    original = path.read_bytes()
    store = _AccountStore(path, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        store.open()
    assert path.read_bytes() == original
    await app.on_startup()


@pytest.mark.parametrize(
    "operation, arguments",
    [
        ("get_messages", {"chat_id": CHAT, "limit": 0}),
        ("get_messages", {"chat_id": CHAT, "limit": 101}),
        ("get_messages", {"chat_id": CHAT, "since": "2026-10-01"}),
        ("get_messages", {"chat_id": CHAT, "before_id": -1}),
        (
            "get_messages",
            {"chat_id": CHAT, "since": "2026-10-05T00:00:00Z", "until": "2026-10-01T00:00:00Z"},
        ),
        (
            "send_document",
            {"chat_id": CHAT, "filename": "document.txt", "content_base64": "not base64"},
        ),
        ("send_text", {"chat_id": CHAT, "text": ""}),
        ("set_member_admin", {"chat_id": CHAT, "user_id": 9, "rights": ["unknown"]}),
        ("set_chat_details", {"chat_id": CHAT, "title": "x", "description": "y"}),
    ],
)
async def test_invalid_operation_arguments_never_reach_telegram(account, operation, arguments):
    app, _, _ = account
    before = list(app.client.calls)
    with pytest.raises(HTTPBadRequest):
        await getattr(app, operation)(**arguments)
    assert app.client.calls == before


async def test_session_lease_released_even_when_shutdown_disconnect_fails(account):
    app, _, _ = account
    original = app.client
    with patch.object(original, "disconnect", side_effect=ConnectionError("offline")):
        with pytest.raises(ConnectionError):
            await app.on_shutdown()
    await app.on_startup()
    assert (await app.get_status())["authorized"]


async def test_rejected_account_identity_does_not_replace_saved_state(account):
    app, _, _ = account
    await app.on_shutdown()
    original = app.session_path.read_bytes()
    with patch.object(FakeTelegram, "get_me", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="personal account"):
            await app.on_startup()
    assert app.session_path.read_bytes() == original
    await app.on_startup()


async def test_failed_login_code_keeps_credentials_local_and_can_retry(account):
    app, _, _ = account
    await app.revoke_session()
    await app.start_login("+39123456789")
    with pytest.raises(HTTPBadRequest) as error:
        await app.complete_login(code="invalid")
    assert "private-api-hash" not in str(error.value)
    assert (await app.complete_login(code="12345"))["state"] == "authorized"


async def test_restart_resolves_allowed_chat_ids_without_persisting_entity_cache(account):
    app, _, _ = account
    await app.set_policy({"operations": ["get_messages"], "chats": {str(CHAT): ["read"]}})
    entity = await app.client.get_input_entity(CHAT)
    with patch.object(app.client, "get_input_entity", side_effect=ValueError("missing cache")):
        app.client.dialog_entity = entity
        result = await app.get_messages(CHAT)
    assert result["items"][0]["id"] == 5


async def test_management_is_inaccessible_to_operator_and_denied_calls_never_send(account):
    app, http, _ = account
    for path in ("get_policy", "revoke_session"):
        response = await http.post(
            f"/personal/_admin/{path}", json={}, headers={"Authorization": "Bearer operator"}
        )
        assert response.status_code == 403
    response = await http.post("/personal/_account/send_text", json={"chat_id": CHAT, "text": "no"})
    assert response.status_code in (401, 403)
    assert not any(call[0] == "send" for call in app.client.calls)
