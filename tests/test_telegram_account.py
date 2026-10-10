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

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from filelock import Timeout
from telethon import errors, events, functions, types, utils

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPException, HTTPBadRequest, HTTPForbidden, HTTPUnauthorized
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
    app, _, key = account
    second = _AccountStore(app.session_path, key)
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


async def test_mcp_history_workflow_keeps_chat_and_cursor_filters(account):
    _, http, _ = account
    calls = AccountCalls(http)
    await calls.policy(
        {"operations": ["get_chats", "get_messages"], "chats": {str(CHAT): ["read"]}}
    )
    dialogs = await calls.mcp("tools/call", {"name": "get_chats", "arguments": {"limit": 1}})
    items = dialogs["result"]["structuredContent"]["items"]
    assert [item["id"] for item in items] == [CHAT]
    arguments = {"chat_id": items[0]["id"], "limit": 2, "since": "2026-10-02T00:00:00Z"}
    first = await calls.mcp("tools/call", {"name": "get_messages", "arguments": arguments})
    page = first["result"]["structuredContent"]
    arguments["before_id"] = page["next_before_id"]
    second = await calls.mcp("tools/call", {"name": "get_messages", "arguments": arguments})
    assert [
        item["id"] for item in page["items"] + second["result"]["structuredContent"]["items"]
    ] == [5, 4, 3, 2]
    arguments["chat_id"] = OTHER
    denied = await calls.mcp("tools/call", {"name": "get_messages", "arguments": arguments})
    assert denied["error"] == {"code": -32000, "message": "Not authorized"}


async def test_mcp_channel_administration_workflow_respects_owner_policy(account):
    app, http, _ = account
    calls = AccountCalls(http)
    policy = {"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}}
    await calls.policy(policy)
    saved = await http.post(
        "/personal/_admin/get_policy", json={}, headers={"Authorization": "Bearer owner"}
    )
    assert saved.json() == policy
    for operation, arguments in [
        ("create_channel", {"title": "Announcements", "description": "Releases"}),
        ("create_group", {"title": "Development"}),
        ("set_chat_details", {"chat_id": CHAT, "description": "New description"}),
        ("invite_members", {"chat_id": CHAT, "user_ids": [8, 9]}),
        ("get_members", {"chat_id": CHAT, "limit": 2}),
        ("set_member_admin", {"chat_id": CHAT, "user_id": 9, "rights": ["pin_messages"]}),
        ("remove_member", {"chat_id": CHAT, "user_id": 8}),
    ]:
        response = await calls.mcp("tools/call", {"name": operation, "arguments": arguments})
        assert response["result"].get("isError") is not True, response
        assert response["result"]["structuredContent"]
    assert any(call[0] == "kick" for call in app.client.calls)
    await calls.policy({"operations": ["get_members"], "chats": {str(CHAT): ["read"]}})
    before = len(app.client.calls)
    denied = await calls.mcp(
        "tools/call", {"name": "remove_member", "arguments": {"chat_id": CHAT, "user_id": 8}}
    )
    assert denied["error"] == {"code": -32000, "message": "Not authorized"}
    assert len(app.client.calls) == before


async def test_mcp_message_mutations_keep_ownership_checks(account):
    app, http, _ = account
    calls = AccountCalls(http)
    await calls.policy(
        {"operations": ["edit_message", "delete_messages"], "chats": {str(CHAT): ["write"]}}
    )
    result = await calls.mcp(
        "tools/call",
        {
            "name": "edit_message",
            "arguments": {"chat_id": CHAT, "message_id": 4, "text": "Updated"},
        },
    )
    assert result["result"]["structuredContent"]["id"] == 4
    result = await calls.mcp(
        "tools/call",
        {"name": "delete_messages", "arguments": {"chat_id": CHAT, "message_ids": [4]}},
    )
    assert result["result"]["structuredContent"]["deleted_ids"] == [4]
    app.client.messages[0].out = False
    before = len([call for call in app.client.calls if call[0] == "delete"])
    denied = await calls.mcp(
        "tools/call",
        {"name": "delete_messages", "arguments": {"chat_id": CHAT, "message_ids": [5]}},
    )
    assert denied["error"] == {"code": -32000, "message": "Not authorized"}
    assert len([call for call in app.client.calls if call[0] == "delete"]) == before


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("provider detail"),
        ConnectionError("provider detail"),
        errors.ChatWriteForbiddenError(None),
    ],
)
async def test_failed_mutation_returns_explicit_outcome_without_retries(account, failure):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["send_text"], "chats": {str(CHAT): ["write"]}})
    app.client.failure = failure
    response = await http.post(
        "/personal/_account/send_text",
        json={"chat_id": CHAT, "text": "once"},
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == (502 if isinstance(failure, errors.RPCError) else 503)
    assert "provider detail" not in response.text
    assert len([call for call in app.client.calls if call[0] == "send"]) == 1


@pytest.mark.parametrize("stage", ["authorization", "identity"])
@pytest.mark.parametrize(
    "failure",
    [
        errors.FloodWaitError(None, capture=30),
        errors.ServerError(None, "temporary server failure"),
        ConnectionError("offline"),
    ],
)
async def test_transient_startup_failure_preserves_session_and_identity(account, stage, failure):
    app, _, key = account
    await app.on_shutdown()
    original = app.session_path.read_bytes()
    saved = json.loads(Fernet(key.encode()).decrypt(original))
    assert saved["session"] and saved["account_id"] == 7
    method = "__call__" if stage == "authorization" else "get_me"
    with patch.object(FakeTelegram, method, new=AsyncMock(side_effect=failure)):
        with pytest.raises(type(failure)):
            await app.on_startup()
        await app.on_shutdown()
    assert app.session_path.read_bytes() == original
    # Failed startup releases its lease and a later startup needs no new login.
    await app.on_startup()
    assert (await app.get_status())["account"]["id"] == 7
    assert not any(call[0] == "login" for call in app.client.calls)


async def test_revoked_session_at_startup_clears_authorization(account):
    app, _, key = account
    await app.on_shutdown()
    with patch.object(
        FakeTelegram,
        "__call__",
        new=AsyncMock(side_effect=errors.UnauthorizedError(None, "revoked")),
    ):
        await app.on_startup()
    assert not (await app.get_status())["authorized"]
    saved = json.loads(Fernet(key.encode()).decrypt(app.session_path.read_bytes()))
    assert saved["session"] == "" and saved["account_id"] is None


@pytest.mark.parametrize(
    "operation, arguments",
    [
        ("get_members", {"chat_id": CHAT, "offset": "1"}),
        ("get_members", {"chat_id": CHAT, "offset": True}),
        ("get_chats", {"offset": True}),
        ("get_messages", {"chat_id": CHAT, "before_id": "1"}),
        ("get_messages", {"chat_id": CHAT, "before_id": True}),
        ("send_document", {"chat_id": CHAT, "filename": "a.txt", "content_base64": 12}),
        ("send_document", {"chat_id": CHAT, "filename": 12, "content_base64": "YQ=="}),
        (
            "send_document",
            {"chat_id": CHAT, "filename": "a.txt", "content_base64": "YQ==", "caption": []},
        ),
        ("set_member_admin", {"chat_id": CHAT, "user_id": 9, "rights": [[]]}),
        ("set_member_admin", {"chat_id": CHAT, "user_id": 9, "rights": None}),
        ("invite_members", {"chat_id": CHAT, "user_ids": [True]}),
        ("invite_members", {"chat_id": CHAT, "user_ids": None}),
        ("delete_messages", {"chat_id": CHAT, "message_ids": None}),
        ("create_channel", {"title": "Channel", "description": "x" * 256}),
        ("create_group", {"title": "Group", "description": "x" * 256}),
        ("set_chat_details", {"chat_id": CHAT, "description": "x" * 256}),
        ("create_channel", {"title": "Channel", "description": 12}),
        ("create_group", {"title": "Group", "description": None}),
        ("set_chat_details", {"chat_id": CHAT, "description": []}),
    ],
)
async def test_malformed_arguments_return_400_without_provider_calls(account, operation, arguments):
    app, http, _ = account
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}})
    before = list(app.client.calls)
    with pytest.raises(HTTPBadRequest):
        await getattr(app, operation)(**arguments)
    response = await http.post(
        f"/personal/_account/{operation}",
        json=arguments,
        headers={"Authorization": "Bearer operator"},
    )
    assert response.status_code == 400
    assert app.client.calls == before


@pytest.mark.parametrize("description", ["", "x" * 255])
async def test_description_accepts_empty_and_maximum_length(account, description):
    app, _, _ = account
    await app.set_policy({"operations": ["*"], "chats": {"*": ["admin"]}})
    await app.create_channel("Channel", description)
    await app.create_group("Group", description)
    await app.set_chat_details(CHAT, description=description)
    requests = [call[1] for call in app.client.calls if call[0] == "request"][-3:]
    assert all(request.about == description for request in requests)


async def test_unchanged_operations_and_restart_do_not_rewrite_encrypted_state(account):
    app, _, _ = account
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    original = app.session_path.read_bytes()
    await app.get_messages(CHAT)
    await app.get_members(CHAT)
    await app.send_text(CHAT, "hello")
    await app.set_policy(app.policy)
    assert app.session_path.read_bytes() == original
    await app.on_shutdown()
    await app.on_startup()
    assert app.session_path.read_bytes() == original


async def test_status_remains_available_while_telegram_operation_waits(account):
    app, http, _ = account
    await app.set_policy({"operations": ["send_text"], "chats": {str(CHAT): ["write"]}})
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_send(*args, **kwargs):
        entered.set()
        await release.wait()
        return app.client.message(10)

    with patch.object(app.client, "send_message", new=slow_send):
        operation = asyncio.create_task(app.send_text(CHAT, "hello"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            async with asyncio.timeout(1):
                result = await AccountCalls(http).mcp("tools/call", {"name": "get_status"})
            assert result["result"]["structuredContent"]["authorized"]
        finally:
            release.set()
            await operation


async def test_peer_lookup_stops_before_scanning_entire_account(account):
    app, http, _ = account
    await app.set_policy({"operations": ["get_messages"], "chats": {str(CHAT): ["read"]}})
    scanned = []

    async def dialogs(**kwargs):
        assert kwargs["limit"] == 100
        for i in range(1000):
            scanned.append(i)
            yield SimpleNamespace(id=OTHER, input_entity=None)

    with patch.object(app.client, "get_input_entity", side_effect=ValueError("missing")):
        with patch.object(app.client, "iter_dialogs", new=dialogs):
            response = await http.post(
                "/personal/_account/get_messages",
                json={"chat_id": CHAT},
                headers={"Authorization": "Bearer operator"},
            )
    assert response.status_code == 409
    assert "get_chats" in response.text
    assert len(scanned) == 100
    assert not any(call[0] == "history" for call in app.client.calls)


async def test_session_changes_are_still_persisted_after_operation(account):
    app, _, key = account
    await app.set_policy({"operations": ["send_text"], "chats": {str(CHAT): ["write"]}})
    previous = app.session_path.read_bytes()
    original_send = app.client.send_message

    async def migrated_send(*args, **kwargs):
        app.client.session.set_dc(4, "149.154.167.91", 443)
        return await original_send(*args, **kwargs)

    with patch.object(app.client, "send_message", new=migrated_send):
        await app.send_text(CHAT, "hello")
    saved = json.loads(Fernet(key.encode()).decrypt(app.session_path.read_bytes()))
    assert app.session_path.read_bytes() != previous
    assert saved["session"] == app.client.session.save()
    await app.on_shutdown()
    await app.on_startup()
    assert app.client.session.dc_id == 4
    assert (await app.get_status())["authorized"]


async def test_paging_dialogs_allows_retry_after_bounded_peer_lookup(account):
    app, http, _ = account
    await app.set_policy({"operations": ["get_messages", "get_chats"], "chats": {"*": ["read"]}})
    entity = await app.client.get_input_entity(CHAT)
    known = set()

    async def resolve(peer):
        if peer not in known:
            raise ValueError("unknown peer")
        return entity

    async def dialogs(**kwargs):
        for i in range(min(kwargs["limit"], 151)):
            peer = CHAT if i == 150 else OTHER - i
            known.add(peer)
            yield SimpleNamespace(
                id=peer, name="Chat", input_entity=entity, is_user=False, is_group=True
            )

    with patch.object(app.client, "get_input_entity", new=resolve):
        with patch.object(app.client, "iter_dialogs", new=dialogs):
            calls = AccountCalls(http)
            response = await calls.mcp(
                "tools/call", {"name": "get_messages", "arguments": {"chat_id": CHAT}}
            )
            assert "error" in response or response["result"].get("isError")
            page = await app.get_chats()
            assert page["next_offset"] == 100
            page = await app.get_chats(offset=page["next_offset"])
            assert any(item["id"] == CHAT for item in page["items"])
            result = await app.get_messages(CHAT)
            assert result["items"][0]["id"] == 5


async def test_unknown_peer_returns_bad_request_after_exhausted_lookup(account):
    app, _, _ = account
    await app.set_policy({"operations": ["get_messages"], "chats": {"*": ["read"]}})
    with patch.object(app.client, "get_input_entity", side_effect=ValueError("unknown")):
        with pytest.raises(HTTPBadRequest, match="not known"):
            await app.get_messages(-1000000009999)


async def test_extended_tools_are_discovered_but_denied_by_default(account):
    app, http, _ = account
    tools = (await AccountCalls(http).mcp("tools/list"))["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"transcribe_message", "download_media", "send_media", "react_message", "mark_read",
            "archive_chat", "mute_chat", "pin_message", "block_contact", "set_profile",
            "get_contacts", "forward_message", "schedule_message", "get_scheduled_messages",
            "cancel_scheduled_message", "create_poll", "get_poll", "vote_poll"} <= names
    before = len(app.client.calls)
    with pytest.raises(HTTPForbidden):
        await app.download_media(CHAT, 1)
    with pytest.raises(HTTPForbidden):
        await app.transcribe_message(CHAT, 1)
    assert len(app.client.calls) == before


async def test_audio_transcription_uses_shared_engine_without_persisting_text(account):
    app, http, _ = account
    await app.set_policy({"operations": ["transcribe_message"], "chats": {str(CHAT): ["read"]}})
    message = app.client.messages[0]
    message.file = SimpleNamespace(size=5, mime_type="audio/ogg")
    message.media = object()
    message.voice, message.audio = True, None
    app.client.download_chunks = [b"au", b"dio"]
    engine = AsyncMock(return_value={"text": "Test transcript", "language": "it"})
    app.transcriber = SimpleNamespace(transcribe=engine)
    response = await AccountCalls(http).mcp("tools/call", {"name": "transcribe_message",
                      "arguments": {"chat_id": CHAT, "message_id": message.id}})
    assert response["result"]["structuredContent"]["text"] == "Test transcript"
    engine.assert_awaited_once_with(b"audio", "audio/ogg", "it")
    assert "Test transcript" not in app.session_path.read_text()
    assert not any(call[0] == "send" for call in app.client.calls)


async def test_media_download_checks_metadata_and_actual_stream_size(account):
    app, _, _ = account
    await app.set_policy({"operations": ["download_media"], "chats": {str(CHAT): ["read"]}})
    message = app.client.messages[0]
    message.media = object()
    message.file = SimpleNamespace(size=6*1024*1024, mime_type="audio/ogg")
    app.client.download_chunks = [b"data"]
    with pytest.raises(HTTPBadRequest):
        await app.download_media(CHAT, message.id)
    assert not any(call[0] == "download" for call in app.client.calls)
    message.file.size = 4
    app.client.download_chunks = [b"x" * (5*1024*1024), b"x"]
    with pytest.raises(HTTPBadRequest):
        await app.download_media(CHAT, message.id)
    app.client.download_chunks = [b"data"]
    result = await app.download_media(CHAT, message.id)
    assert base64.b64decode(result["content_base64"]) == b"data"


async def test_transcription_requires_configuration_and_audio(account):
    app, _, _ = account
    await app.set_policy({"operations": ["transcribe_message"], "chats": {str(CHAT): ["read"]}})
    with pytest.raises(HTTPException) as error:
        await app.transcribe_message(CHAT, 1)
    assert error.value.status == 503
    app.transcriber = SimpleNamespace(transcribe=AsyncMock())
    for message in app.client.messages:
        message.voice = message.audio = None
    with pytest.raises(HTTPBadRequest):
        await app.transcribe_message(CHAT, 1)
    app.transcriber.transcribe.assert_not_awaited()


async def test_forwarding_requires_source_read_and_destination_write(account):
    app, _, _ = account
    app.client.forward_messages = AsyncMock(return_value=app.client.message(10, OTHER))
    await app.set_policy({"operations": ["forward_message", "get_messages"], "chats": {str(OTHER): ["write"]}})
    with pytest.raises(HTTPForbidden):
        await app.forward_message(OTHER, CHAT, 1)
    app.client.forward_messages.assert_not_awaited()
    await app.set_policy({"operations": ["forward_message", "get_messages"],
                          "chats": {str(OTHER): ["write"], str(CHAT): ["read"]}})
    result = await app.forward_message(OTHER, CHAT, 1)
    assert result["chat_id"] == OTHER
    assert utils.get_peer_id(app.client.forward_messages.call_args.kwargs["from_peer"]) == CHAT


@pytest.mark.parametrize("operation,args,request_type", [
    ("react_message", (CHAT, 1, "👍"), functions.messages.SendReactionRequest),
    ("pin_message", (CHAT, 1, True), functions.messages.UpdatePinnedMessageRequest),
    ("mute_chat", (CHAT, False), functions.account.UpdateNotifySettingsRequest),
    ("block_contact", (7, True), functions.contacts.BlockRequest),
    ("set_profile", ("Test", "Name", "About"), functions.account.UpdateProfileRequest),
])
async def test_extended_operations_use_typed_requests(account, operation, args, request_type):
    app, _, _ = account
    await app.set_policy({"operations": ["*"], "chats": {"*": ["read", "write", "admin"]}})
    await getattr(app, operation)(*args)
    request = [call[1] for call in app.client.calls if call[0] == "request"][-1]
    assert isinstance(request, request_type)


async def test_voice_media_and_provider_scheduling(account):
    app, _, _ = account
    await app.set_policy({"operations": ["*"], "chats": {"*": ["write"]}})
    await app.send_media(CHAT, "voice", "note.ogg", base64.b64encode(b"audio").decode())
    call = [call for call in app.client.calls if call[0] == "file"][-1]
    assert call[2] == b"audio" and call[4]["voice_note"] is True
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await app.schedule_message(CHAT, "Scheduled", due)
    sent = [call for call in app.client.calls if call[0] == "send"][-1]
    assert sent[3]["schedule"] == datetime.fromisoformat(due)
    assert sent[3]["parse_mode"] is None


async def test_poll_creation_uses_real_telethon_schema(account):
    app, _, _ = account
    await app.set_policy({"operations": ["create_poll"], "chats": {str(CHAT): ["write"]}})
    app.client.send_message = AsyncMock(return_value=app.client.message(10))
    await app.create_poll(CHAT, "Question?", ["Yes", "No"])
    media = app.client.send_message.call_args.kwargs["file"]
    assert isinstance(media, types.InputMediaPoll)
    assert media.poll.question.text == "Question?"
    assert [answer.option for answer in media.poll.answers] == [b"\x00", b"\x01"]
    bytes(media)  # Real TL serialization validates nested fields, not just mocks.


async def test_poll_results_and_votes_preserve_provider_options(account):
    app, _, _ = account
    await app.set_policy({"operations": ["get_poll", "vote_poll"], "chats": {str(CHAT): ["read", "write"]}})
    poll = types.Poll(id=1, hash=7, question=types.TextWithEntities("Question", []),
                     answers=[types.PollAnswer(types.TextWithEntities("Yes", []), b"opaque")])
    app.client.messages[0].media = types.MessageMediaPoll(poll, types.PollResults(total_voters=2))
    result = await app.get_poll(CHAT, 5)
    assert result["options"][0]["voters"] is None
    await app.vote_poll(CHAT, 5, [0])
    assert app.client.calls[-1][1].options == [b"opaque"]
    with pytest.raises(HTTPBadRequest):
        await app.vote_poll(CHAT, 5, [1])


@pytest.mark.parametrize("operation,args", [
    ("download_media", (CHAT, True)), ("archive_chat", (CHAT, 1)),
    ("block_contact", (CHAT, True)), ("vote_poll", (CHAT, 1, [True])),
    ("transcribe_message", (CHAT, 1, "../it")), ("schedule_message", (CHAT, "Text", "bad")),
    ("create_poll", (CHAT, "Question", ["same", "same"])),
])
async def test_extended_invalid_inputs_never_reach_provider(account, operation, args):
    app, _, _ = account
    before = len(app.client.calls)
    with pytest.raises(HTTPBadRequest):
        await getattr(app, operation)(*args)
    assert len(app.client.calls) == before


async def test_contact_filtering_precedes_pagination(account):
    app, _, _ = account
    await app.set_policy({"operations": ["get_contacts"], "chats": {"8": ["read"]}})
    users = [SimpleNamespace(id=i, first_name="Test", last_name=None, username=None, bot=False)
             for i in (7, 8)]
    with patch.object(FakeTelegram, "__call__", AsyncMock(return_value=SimpleNamespace(users=users))):
        result = await app.get_contacts(limit=1)
    assert [item["id"] for item in result["items"]] == [8]
    assert result["next_offset"] is None


async def test_scheduled_listing_and_cancellation_use_scheduled_ids(account):
    app, _, _ = account
    await app.set_policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    messages = [app.client.message(1), app.client.message(2)]
    with patch.object(FakeTelegram, "__call__", AsyncMock(return_value=SimpleNamespace(messages=messages))) as provider:
        page = await app.get_scheduled_messages(CHAT, limit=1)
        assert page["next_offset"] == 1
        await app.cancel_scheduled_message(CHAT, 2)
        assert isinstance(provider.call_args.args[0], functions.messages.DeleteScheduledMessagesRequest)
        assert provider.call_args.args[0].id == [2]
        before = provider.await_count
        with pytest.raises(HTTPBadRequest):
            await app.cancel_scheduled_message(CHAT, 50)
        assert provider.await_count == before + 1  # History read only; no deletion.


async def test_approval_routes_filter_roles_and_record_actual_actor(account):
    app, http, key = account
    calls = AccountCalls(http)
    await calls.policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    operator = await calls.mcp("tools/list")
    owner = await calls.mcp("tools/list", token="owner")
    assert not {"decide_message", "get_audit_log"} & {t["name"] for t in operator["result"]["tools"]}
    assert {"decide_message", "get_audit_log"} <= {t["name"] for t in owner["result"]["tools"]}
    response = await calls.mcp("tools/call", {"name": "request_message", "arguments": {
        "chat_id": CHAT, "text": "private queued text"}})
    job = response["result"]["structuredContent"]
    assert job["state"] == "pending" and job["actor"] == "Bearer operator"
    assert not any(c[0] == "send" for c in app.client.calls)
    denied = await calls.mcp("tools/call", {"name": "decide_message", "arguments": {
        "request_id": job["id"], "decision": "approve"}})
    assert "error" in denied or denied["result"].get("isError")
    approved = await calls.mcp("tools/call", {"name": "decide_message", "arguments": {
        "request_id": job["id"], "decision": "approve"}}, token="owner")
    final = approved["result"]["structuredContent"]
    assert final["state"] == "submitted" and final["message_id"] == 10
    assert final["decision_actor"] == "Bearer owner"
    audit = await app.get_audit_log()
    assert any(item["actor"] == "Bearer operator" and item["operation"] == "request_message"
               for item in audit["items"])
    assert "private queued text" not in json.dumps(audit)
    disk = app.journal.store.path.read_bytes()
    assert b"private queued text" not in disk
    assert "private queued text" in Fernet(key.encode()).decrypt(disk).decode()
    assert app.journal.store.path.stat().st_mode & 0o777 == 0o600


async def test_approval_first_decision_wins_and_restart_does_not_resend(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "once")
    results = await asyncio.gather(app.decide_message(job["id"], "approve"),
                                   app.decide_message(job["id"], "approve"))
    assert all(r["state"] == "submitted" for r in results)
    sends = [c for c in app.client.calls if c[0] == "send"]
    assert len(sends) == 1
    await app.on_shutdown()
    await app.on_startup()
    result = await app.decide_message(job["id"], "approve")
    assert result["state"] == "submitted"
    assert not any(c[0] == "send" for c in app.client.calls)


@pytest.mark.parametrize("decision", ["reject", "cancel"])
async def test_approval_reject_cancel_and_current_policy(account, decision):
    app, http, _ = account
    calls = AccountCalls(http)
    await calls.policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "never send")
    await calls.policy({"operations": ["decide_message"], "chats": {str(CHAT): ["read"]}})
    with pytest.raises(HTTPForbidden):
        await app.decide_message(job["id"], "approve")
    await calls.policy({"operations": ["decide_message"], "chats": {str(CHAT): ["read", "write"]}})
    result = await app.decide_message(job["id"], decision)
    assert result["state"] == ("rejected" if decision == "reject" else "cancelled")
    assert (await app.decide_message(job["id"], "approve"))["state"] == result["state"]
    assert not any(c[0] == "send" for c in app.client.calls)


async def test_approval_uncertain_send_and_interrupted_restart(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "uncertain")
    app.client.failure = ConnectionError("private provider failure")
    with pytest.raises(HTTPException):
        await app.decide_message(job["id"], "approve")
    assert (await app.decide_message(job["id"], "approve"))["state"] == "unconfirmed"
    second = await app.request_message(CHAT, "interrupted")
    app.journal.update_job(second, state="sending")
    await app.on_shutdown()
    await app.on_startup()
    assert (await app.decide_message(second["id"], "approve"))["state"] == "unconfirmed"
    assert not any(c[0] == "send" for c in app.client.calls)
    assert "private provider failure" not in json.dumps(await app.get_audit_log())


async def test_requests_and_journal_are_bound_to_account_identity(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    job = await app.request_message(CHAT, "old account only")
    await app.revoke_session()
    await app.start_login("+39000000000")
    app.client.me.id = 8
    await app.complete_login(code="12345")
    assert (await app.get_message_requests())["items"] == []
    with pytest.raises(HTTPException) as error:
        await app.decide_message(job["id"], "approve")
    assert error.value.status == 404


async def test_events_receive_edit_delete_filter_and_replay_after_restart(account):
    app, http, _ = account
    calls = AccountCalls(http)
    await calls.policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    assert app.client.settings["receive_updates"] is True
    assert len(app.client.handlers) == 3
    for chat_id in (OTHER, CHAT):
        message = types.Message(id=12, peer_id=utils.resolve_id(chat_id)[1](
            utils.resolve_id(chat_id)[0]), message="not journalled", date=datetime.now(timezone.utc))
        for event in (events.NewMessage.Event(message), events.MessageEdited.Event(message),
                      events.MessageDeleted.Event([12], peer=message.peer_id)):
            event._client = app.client
            await app._record_event(event)
    unknown = events.MessageDeleted.Event([42], peer=None)
    unknown._client = app.client
    await app._record_event(unknown)
    assert (await app.get_status())["unscoped_deletions"] == 1
    await calls.policy({"operations": ["*"], "chats": {str(CHAT): ["read"]}})
    first = await app.get_events(limit=2)
    assert [e["kind"] for e in first["items"]] == ["message_received", "message_edited"]
    assert all(e["chat_id"] == CHAT for e in first["items"])
    assert first["has_more"] is True and not first["retention_gap"]
    await app.on_shutdown()
    await app.on_startup()
    rest = await app.get_events(after_id=first["next_after_id"])
    assert [e["kind"] for e in rest["items"]] == ["message_deleted"]
    assert "not journalled" not in json.dumps(rest)
    assert not (await app.get_events(after_id=rest["next_after_id"]))["items"]


async def test_journal_retention_and_queue_bounds(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    app.journal.retention = 2
    for code in range(3):
        app.journal.append("events", app.account_id, chat_id=CHAT, message_id=code, kind="message_received")
    result = await app.get_events()
    assert result["retention_gap"] and len(result["items"]) == 2
    first = await app.request_message(CHAT, "one")
    await app.request_message(CHAT, "two")
    with pytest.raises(HTTPException):
        await app.request_message(CHAT, "full")
    await app.decide_message(first["id"], "reject")
    assert (await app.request_message(CHAT, "space"))["state"] == "pending"


@pytest.mark.parametrize("method,args", [
    ("get_events", {"after_id": True}), ("get_audit_log", {"limit": 0}),
    ("request_message", {"chat_id": True, "text": "no"}),
    ("get_message_requests", {"offset": -1}),
    ("decide_message", {"request_id": "x", "decision": "maybe"}),
])
async def test_journal_invalid_inputs(account, method, args):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    with pytest.raises(HTTPBadRequest):
        await getattr(app, method)(**args)


async def test_approval_only_policy_cannot_be_bypassed_with_direct_send(account):
    app, http, _ = account
    calls = AccountCalls(http)
    await calls.policy({"operations": ["request_message", "decide_message", "get_message_requests"],
                        "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "approved flow only")
    with pytest.raises(HTTPForbidden):
        await app.send_text(CHAT, "bypass")
    assert (await app.decide_message(job["id"], "approve"))["state"] == "submitted"


async def test_cancelled_approval_is_uncertain_and_cannot_be_retried(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "interrupted")
    app.client.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await app.decide_message(job["id"], "approve")
    assert (await app.decide_message(job["id"], "approve"))["state"] == "unconfirmed"
    assert len([c for c in app.client.calls if c[0] == "send"]) == 1


async def test_journal_write_failure_prevents_provider_send(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "durable before send")
    with patch.object(app.journal.store, "save", side_effect=OSError("disk unavailable")):
        with pytest.raises(OSError):
            await app.decide_message(job["id"], "approve")
    assert not any(c[0] == "send" for c in app.client.calls)
    assert app.journal.get_job(app.account_id, job["id"])["state"] == "pending"


async def test_events_from_replaced_client_are_ignored_and_callback_failure_visible(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read"]}})
    old_client = app.client
    await app.on_shutdown()
    await app.on_startup()
    event = events.MessageDeleted.Event([99], peer=types.PeerChannel(100))
    event._client = old_client
    await app._record_event(event)
    assert not (await app.get_events())["items"]
    event._client = app.client
    with patch.object(app.journal.store, "save", side_effect=OSError("disk unavailable")):
        with pytest.raises(OSError):
            await app._record_event(event)
    assert (await app.get_status())["event_errors"] == 1


async def test_queue_pagination_filters_chats_before_offset(account):
    app, http, _ = account
    calls = AccountCalls(http)
    await calls.policy({"operations": ["*"], "chats": {"*": ["read", "write"]}})
    hidden = await app.request_message(OTHER, "hidden")
    visible = await app.request_message(CHAT, "visible")
    await calls.policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    page = await app.get_message_requests(limit=1)
    assert [job["id"] for job in page["items"]] == [visible["id"]]
    assert page["next_offset"] is None
    with pytest.raises(HTTPException) as exc:
        await app.decide_message(hidden["id"], "approve")
    assert exc.value.status == 404


async def test_sending_state_is_durable_before_provider_call(account):
    app, http, key = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    job = await app.request_message(CHAT, "exact approved content")

    async def inspect_durable_request(entity, text, **kwargs):
        persisted = json.loads(Fernet(key.encode()).decrypt(app.journal.store.path.read_bytes()))
        stored = next(item for item in persisted["jobs"] if item["id"] == job["id"])
        assert stored["state"] == "sending" and stored["decision_actor"] == "trusted-python"
        assert text == stored["text"] == "exact approved content"
        return app.client.message(10)

    app.client.send_message = AsyncMock(side_effect=inspect_durable_request)
    assert (await app.decide_message(job["id"], "approve"))["state"] == "submitted"


async def test_corrupt_journal_fails_startup_preserving_session(account):
    app, http, _ = account
    await AccountCalls(http).policy({"operations": ["*"], "chats": {str(CHAT): ["read", "write"]}})
    await app.request_message(CHAT, "preserve session")
    path = app.journal.store.path
    original = path.read_bytes()
    await app.on_shutdown()
    session = app.session_path.read_bytes()
    path.write_bytes(b"corrupt")
    with pytest.raises(InvalidToken):
        await app.on_startup()
    assert app.session_path.read_bytes() == session
    path.write_bytes(original)
    await app.on_startup()
    assert (await app.get_status())["authorized"]
    assert len((await app.get_message_requests())["items"]) == 1


@pytest.mark.parametrize("name,arguments", [
    ("download_media", {"chat_id": CHAT, "message_id": 1}),
    ("react_message", {"chat_id": CHAT, "message_id": 1, "reaction": "👍"}),
    ("mark_read", {"chat_id": CHAT, "message_id": 1}),
    ("archive_chat", {"chat_id": CHAT}),
    ("mute_chat", {"chat_id": CHAT}),
    ("pin_message", {"chat_id": CHAT, "message_id": 1}),
    ("block_contact", {"chat_id": 7}),
    ("set_profile", {"first_name": "Name"}),
    ("get_contacts", {}),
    ("forward_message", {"chat_id": CHAT, "source_chat_id": OTHER, "message_id": 1}),
    ("schedule_message", {"chat_id": CHAT, "text": "Later", "due": "future"}),
    ("get_scheduled_messages", {"chat_id": CHAT}),
    ("cancel_scheduled_message", {"chat_id": CHAT, "message_id": 1}),
    ("create_poll", {"chat_id": CHAT, "question": "Which?", "options": ["A", "B"]}),
    ("get_poll", {"chat_id": CHAT, "message_id": 1}),
    ("vote_poll", {"chat_id": CHAT, "message_id": 1, "choices": []}),
    ("send_media", {"chat_id": CHAT, "kind": "photo", "filename": "x.jpg", "content_base64": "eA=="}),
    ("get_events", {}), ("get_message_requests", {}), ("get_audit_log", {}),
])
async def test_each_extended_mcp_route_enforces_account_policy(account, name, arguments):
    app, http, _ = account
    arguments = dict(arguments)
    if name == "schedule_message":
        arguments["due"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    before = list(app.client.calls)
    response = await http.post("/personal/_account/" + name, json=arguments,
                               headers={"Authorization": "Bearer owner"})
    assert response.status_code == 403
    assert app.client.calls == before
    result = await AccountCalls(http).mcp("tools/call", {"name": name, "arguments": arguments}, token="owner")
    assert "error" in result or result["result"].get("isError")
    assert app.client.calls == before


@pytest.mark.parametrize("name,args", [
    ("react_message", (CHAT, 1, "x" * 33)),
    ("mute_chat", (CHAT, 1)), ("pin_message", (CHAT, 1, 1)),
    ("block_contact", (7, 1)), ("set_profile", ("",)),
    ("set_profile", ("Name", "x" * 65)), ("set_profile", ("Name", "", "x" * 71)),
    ("schedule_message", (CHAT, "", "bad")),
    ("schedule_message", (CHAT, "Text", "2000-01-01T00:00:00Z")),
    ("send_media", (CHAT, "invalid", "x", "eA==")),
    ("send_media", (CHAT, "photo", "../x", "eA==")),
    ("send_media", (CHAT, "photo", "x", 1)),
    ("send_media", (CHAT, "photo", "x", "!!")),
    ("send_media", (CHAT, "photo", "x", "")),
    ("send_media", (CHAT, "photo", "x", "eA==", "x" * 1025)),
    ("send_media", (CHAT, "voice", "x", "eA==", "caption")),
])
async def test_extended_input_boundaries_fail_before_provider(account, name, args):
    app, _, _ = account
    before = list(app.client.calls)
    with pytest.raises(HTTPBadRequest):
        await getattr(app, name)(*args)
    assert app.client.calls == before


async def test_read_and_archive_use_selected_chat_and_message(account):
    app, _, _ = account
    await app.set_policy({"operations": ["mark_read", "archive_chat"], "chats": {str(CHAT): ["write"]}})
    app.client.send_read_acknowledge = AsyncMock()
    app.client.edit_folder = AsyncMock()
    await app.mark_read(CHAT, 1)
    assert utils.get_peer_id(app.client.send_read_acknowledge.call_args.args[0]) == CHAT
    assert app.client.send_read_acknowledge.call_args.kwargs == {"max_id": 1}
    await app.archive_chat(CHAT, False)
    assert utils.get_peer_id(app.client.edit_folder.call_args.args[0]) == CHAT
    assert app.client.edit_folder.call_args.kwargs == {"folder": 0}
