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

"""Implementation tests for durable conversations and admission decisions."""

import asyncio
import copy

import pytest
import httpx

from examples.telegram_bot import DemoBot
from kajenn_bot_application.telegram import TelegramBotApplication
from tests.telegram.support import drain, register, webhook

pytest_plugins = ["tests.telegram.support"]


async def submit(server, app, update_id, user_id, text="/start", reply=None):
    message = {
        "message_id": update_id,
        "from": {"id": user_id, "first_name": "Mario"},
        "chat": {"id": user_id, "type": "private"},
        "text": text,
    }
    if reply is not None:
        message["reply_to_message"] = {"message_id": reply}
    response = await webhook(server, app, payload={"update_id": update_id, "message": message})
    assert response.status_code == 200
    await drain(server)


async def click(server, app, api, update_id, user_id, button=0, chat_id=None):
    requests = [
        p
        for m, p in api.calls
        if m == "sendMessage" and p["chat_id"] == user_id and "reply_markup" in p
    ]
    request = requests[-1]
    message_id = next(message_id for message_id, p in api.messages.items() if p is request)
    payload = {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": {"id": user_id, "first_name": f"Admin {user_id}"},
            "data": request["reply_markup"]["inline_keyboard"][0][button]["callback_data"],
            "message": {
                "message_id": message_id,
                "chat": {"id": user_id if chat_id is None else chat_id},
            },
        },
    }
    response = await webhook(server, app, payload=payload)
    assert response.status_code == 200
    return payload


async def configure(app, policy="first"):
    return await app.register_bot(
        code="alpha",
        bot_class=DemoBot,
        token="1:secret",
        config={
            "access": {"approval_required": True, "admins": [100, 200], "approval_policy": policy}
        },
    )


async def test_first_decision_wins_updates_every_admin_and_survives_restart(setup):
    server, app, api = setup
    await configure(app)
    await submit(server, app, 1, 42)
    await submit(server, app, 2, 42)  # Repeated request does not notify admins twice.
    assert len([p for m, p in api.calls if m == "sendMessage" and "reply_markup" in p]) == 2
    await click(server, app, api, 3, 100)
    await drain(server)
    await click(server, app, api, 4, 200, button=1)
    await drain(server)
    edits = [p for m, p in api.calls if m == "editMessageText"]
    assert {p["chat_id"] for p in edits} == {100, 200}
    assert all("Approved by Admin 100" in p["text"] for p in edits)
    assert all(p["reply_markup"] == {"inline_keyboard": []} for p in edits)
    await submit(server, app, 5, 42, "/echo admitted")
    assert api.calls[-1][1]["text"] == "admitted"
    restored = TelegramBotApplication(
        code="telegram",
        persistence_route="registry/bots",
        webhook_url="https://example.com/telegram",
        client=app.client,
    )
    restored.server = server
    await restored.on_startup()
    server._by_mount["telegram"] = restored
    await submit(server, restored, 6, 42, "/echo restored")
    assert api.calls[-1][1]["text"] == "restored"


async def test_all_policy_and_rejection_gate_commands(setup):
    server, app, api = setup
    await configure(app, "all")
    await submit(server, app, 1, 42)
    await click(server, app, api, 2, 100)
    await drain(server)
    await submit(server, app, 3, 42, "/echo forbidden")
    assert not any(p.get("text") == "forbidden" for m, p in api.calls)
    await click(server, app, api, 4, 200, button=1)
    await drain(server)
    assert any("Rejected by Admin 200" in p.get("text", "") for m, p in api.calls)
    await submit(server, app, 5, 42, "/hello")
    assert "rejected" in api.calls[-1][1]["text"].lower()


async def test_forged_callback_and_wrong_chat_cannot_approve(setup):
    server, app, api = setup
    await configure(app)
    await submit(server, app, 1, 42)
    forged = await click(server, app, api, 2, 100, chat_id=999)
    forged["update_id"] = 3
    forged["callback_query"]["from"]["id"] = 999
    assert (await webhook(server, app, payload=forged)).status_code == 200
    await drain(server)
    assert not [p for m, p in api.calls if m == "editMessageText"]
    assert len([p for m, p in api.calls if m == "answerCallbackQuery"]) == 2


async def test_concurrent_conversations_reply_correlation_and_participant_isolation(setup):
    server, app, api = setup
    await register(app)
    one = await app.create_conversation(
        "alpha",
        participants=[
            {"user_id": 42, "chat_id": 42, "role": "requester"},
            {"user_id": 100, "chat_id": 100, "role": "reviewer"},
        ],
        route="conversation",
        context={"pr": 1},
    )
    two = await app.create_conversation(
        "alpha",
        participants=[{"user_id": 42, "chat_id": 42}],
        route="conversation",
        context={"pr": 2},
    )
    sent = await app.send_conversation_message("alpha", one["id"], 42, "PR one")
    await app.send_conversation_message("alpha", two["id"], 42, "PR two")
    await submit(server, app, 1, 42, "which?")
    assert "reply" in api.calls[-1][1]["text"].lower()
    await submit(server, app, 2, 42, "yes", reply=sent["message_id"])
    assert api.calls[-1][1]["text"] == "PR 1: yes"
    before = len(api.calls)
    await submit(server, app, 3, 999, "stolen", reply=sent["message_id"])
    assert len(api.calls) == before
    saved = await app.get_conversation("alpha", one["id"])
    assert saved["context"] == {"pr": 1}
    await app.close_conversation("alpha", one["id"], state="cancelled")
    await submit(server, app, 4, 42, "late", reply=sent["message_id"])
    assert not any(p.get("text") == "PR 1: late" for m, p in api.calls)
    with pytest.raises(ValueError, match="participant"):
        await app.send_conversation_message("alpha", two["id"], 999, "secret")


async def test_simultaneous_requests_create_one_admission(setup):
    server, app, api = setup
    await configure(app)
    message = {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/start"}
    await asyncio.gather(
        *[
            app.deliver_update(
                "alpha", "start", "", 42, update={"update_id": n, "message": message}
            )
            for n in range(5)
        ]
    )
    conversations = await app._persist("list_conversations", {"bot_code": "alpha"})
    assert len(conversations) == 1
    assert len([p for m, p in api.calls if m == "sendMessage" and "reply_markup" in p]) == 2


async def test_configuration_rejected_before_network(setup):
    server, app, api = setup
    with pytest.raises(ValueError, match="admin"):
        await app.register_bot(
            code="alpha",
            bot_class=DemoBot,
            token="1:secret",
            config={"access": {"approval_required": True}},
        )
    assert api.calls == []


async def test_all_admins_must_approve_and_votes_cannot_be_reversed(setup):
    server, app, api = setup
    await configure(app, "all")
    await submit(server, app, 1, 42)
    await click(server, app, api, 2, 100)
    await drain(server)
    await click(server, app, api, 3, 100, button=1)
    await drain(server)
    record = (await app.conversations.get_records("alpha"))[0]
    assert record["state"] == "open"
    assert record["votes"] == {"100": "approve"}
    await click(server, app, api, 4, 200)
    await drain(server)
    assert (await app.get_conversation("alpha", record["id"]))["state"] == "approved"


async def test_failed_edit_is_retried_without_repeating_decision_or_requester_notice(setup):
    server, app, api = setup
    await configure(app)
    await submit(server, app, 1, 42)
    api.fail_edits.add(200)
    await click(server, app, api, 2, 100)
    await drain(server)
    record = (await app.conversations.get_records("alpha"))[0]
    assert record["state"] == "approved"
    assert record["messages"][1]["resolution"] is None
    api.fail_edits.clear()
    await app.on_startup()
    saved = await app.get_conversation("alpha", record["id"])
    assert all(m["resolution"] for m in saved["messages"])
    notices = [
        p
        for m, p in api.calls
        if m == "sendMessage" and p["chat_id"] == 42 and "was approved" in p["text"]
    ]
    assert len(notices) == 1


async def test_decision_write_failure_does_not_grant_access_or_edit_messages(setup, monkeypatch):
    server, app, api = setup
    await configure(app)
    await submit(server, app, 1, 42)
    payload = await click(server, app, api, 2, 100)
    persist = app._persist

    async def fail_decision(operation, record=None):
        if operation == "save_conversation" and record["state"] == "approved":
            raise RuntimeError("database unavailable")
        return await persist(operation, record)

    monkeypatch.setattr(app, "_persist", fail_decision)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await app.conversations.handle_callback("alpha", payload["callback_query"])
    record = (await app.conversations.get_records("alpha"))[0]
    assert record["state"] == "open"
    assert not [p for m, p in api.calls if m == "editMessageText"]


async def test_generic_buttons_context_expiry_and_bot_isolation(setup):
    server, app, api = setup
    await register(app)
    await register(app, "beta", "2:secret")
    record = await app.create_conversation(
        "alpha",
        participants=[{"user_id": 42, "chat_id": 42}],
        route="conversation",
        context={"pr": 5},
    )
    sent = await app.send_conversation_message(
        "alpha", record["id"], 42, "Choose", buttons={"Yes": "yes"}
    )
    payload = await click(server, app, api, 1, 42)
    await drain(server)
    assert any(p.get("text") == "PR 5: yes" for m, p in api.calls)
    with pytest.raises(LookupError):
        await app.get_conversation("beta", record["id"])
    current = await app.get_conversation("alpha", record["id"])
    changed = await app.update_conversation_context(
        "alpha", record["id"], {"pr": 6}, revision=current["revision"]
    )
    with pytest.raises(ValueError, match="revision"):
        await app.update_conversation_context(
            "alpha", record["id"], {}, revision=current["revision"]
        )
    assert changed["context"] == {"pr": 6}
    await app.close_conversation("alpha", record["id"])
    before = len([p for m, p in api.calls if m == "sendMessage"])
    await app.conversations.handle_callback("alpha", payload["callback_query"])
    assert len([p for m, p in api.calls if m == "sendMessage"]) == before
    expired = await app.create_conversation(
        "alpha", participants=[{"user_id": 42, "chat_id": 42}], route="conversation", expires_at=1
    )
    assert (await app.get_conversation("alpha", expired["id"]))["state"] == "expired"
    with pytest.raises(ValueError, match="closed"):
        await app.send_conversation_message("alpha", expired["id"], 42, "too late")
    assert sent["message_id"] > 0


async def test_approved_member_and_admin_still_cannot_reach_protected_routes(setup):
    server, app, api = setup
    await register(app)
    record = await app.create_conversation(
        "alpha", participants=[{"user_id": 42, "chat_id": 42}], route="restricted"
    )
    sent = await app.send_conversation_message("alpha", record["id"], 42, "Question")
    before = len(api.calls)
    await submit(server, app, 1, 42, "answer", reply=sent["message_id"])
    assert len(api.calls) == before


@pytest.mark.parametrize(
    "message", [None, [], {"chat": None}, {"message_id": 1, "chat": {"id": "42"}}]
)
async def test_malformed_callback_messages_do_not_create_tasks(setup, message):
    server, app, api = setup
    await register(app)
    payload = {
        "update_id": 1,
        "callback_query": {"id": "q", "from": {"id": 42}, "data": "invalid", "message": message},
    }
    response = await webhook(server, app, payload=payload)
    if message is None:
        assert response.status_code == 200
        await drain(server)
        assert api.calls[-1][0] == "answerCallbackQuery"
    else:
        assert response.status_code == 400
        assert server.tasks.spool.list_pending() == []


async def test_conversation_revision_provider_rejects_stale_writer(setup):
    server, app, api = setup
    await register(app)
    record = await app.create_conversation(
        "alpha", participants=[{"user_id": 42, "chat_id": 42}], route="conversation"
    )
    stale = copy.deepcopy(record)
    record["context"] = {"winner": True}
    await app.conversations.save_record(record)
    with pytest.raises(RuntimeError, match="revision"):
        await app.conversations.save_record(stale)


async def test_competing_admin_decisions_produce_one_consistent_outcome(setup):
    server, app, api = setup
    await configure(app)
    await submit(server, app, 1, 42)
    approve = await click(server, app, api, 2, 100)
    reject = await click(server, app, api, 3, 200, button=1)
    await asyncio.gather(
        app.conversations.handle_callback("alpha", approve["callback_query"]),
        app.conversations.handle_callback("alpha", reject["callback_query"]),
    )
    record = (await app.conversations.get_records("alpha"))[0]
    expected = "Approved" if record["decision"]["user_id"] == 100 else "Rejected"
    assert len(record["votes"]) == 1
    assert all(expected in p["text"] for m, p in api.calls if m == "editMessageText")
    assert len([p for m, p in api.calls if m == "sendMessage" and "was " in p["text"]]) == 1


async def test_already_applied_edit_counts_as_success(setup):
    server, app, api = setup
    await register(app)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"ok": False, "description": "Bad Request: message is not modified"}
            )
        )
    ) as client:
        app._client = client
        assert (
            await app._telegram(
                "1:secret", "editMessageText", chat_id=100, message_id=1, text="Approved"
            )
            is True
        )
        with pytest.raises(RuntimeError, match="sendMessage"):
            await app.send_message("alpha", 42, "hello")


async def test_resolution_persists_every_admin_copy_and_is_idempotent(setup):
    server, app, api = setup
    await register(app)
    record = await app.conversations.create_record(
        "alpha",
        [{"user_id": 100, "chat_id": 100}, {"user_id": 200, "chat_id": 200}],
        "",
        {"user_id": 42, "name": "Requester", "admins": [100, 200]},
        kind="admission",
    )
    for admin in (100, 200):
        await app.conversations.send_record_message(
            record, admin, "Approval", {"Approve": "approve"}
        )
    record.update(state="approved", decision={"name": "Admin", "user_id": 100})
    await app.conversations.save_record(record)
    await app.conversations.sync_resolution(record)
    saved = await app.get_conversation("alpha", record["id"])
    assert all(message["resolution"] for message in saved["messages"])
    count = len(api.calls)
    await app.conversations.sync_resolution(saved)
    assert len(api.calls) == count
