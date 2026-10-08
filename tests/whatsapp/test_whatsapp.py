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

"""Implementation tests for WhatsApp delivery, isolation and conversation policies."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.telegram.support import drain
from tests.whatsapp.support import Harness

pytest_plugins = ["tests.whatsapp.support"]


async def test_status_events_and_acceptance_are_distinct_and_monotonic(wa):
    await wa.start()
    sent = await wa.app.send_template("alpha", "391234", name="new_pr", language="it")
    message_id = sent["message_id"]
    payload = wa.payload()
    value = payload["entry"][0]["changes"][0]["value"]
    del value["messages"]
    value["statuses"] = [
        {"id": message_id, "recipient_id": "391234", "timestamp": str(100 + i), "status": status}
        for i, status in enumerate(("read", "sent", "delivered", "read", "failed"))
    ]
    assert (await wa.post(payload)).status_code == 200
    await drain(wa.server)
    record = await wa.app.get_message("alpha", message_id)
    assert record["status"] == "read" and len(record["events"]) == 6
    assert (await wa.post(payload)).status_code == 200
    assert not wa.server.tasks.spool.list_pending()
    await wa.app._persist("save_message", dict(sent, timestamp=200))
    assert (await wa.app.get_message("alpha", message_id))["status"] == "read"
    with pytest.raises(LookupError):
        await wa.app.get_message("beta", message_id)


async def test_old_messages_do_not_extend_window_and_unknown_events_are_ignored(wa):
    await wa.start()
    payload = wa.payload("/hello")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message.update(type="image", image={"id": "media"}, timestamp=str(int(time.time()) - 90000))
    assert (await wa.post(payload)).status_code == 200
    await drain(wa.server)
    with pytest.raises(ValueError, match="window"):
        await wa.app.send_message("alpha", "391234", "closed")
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "391234", "last_inbound": time.time()}
    )
    old = time.time() - 90000
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "391234", "last_inbound": old}
    )
    await wa.app.send_message("alpha", "391234", "open")
    assert wa.api.calls[-1][2]["text"]["body"] == "open"


async def test_partial_batch_persistence_failure_can_be_retried_without_duplicate_work(
    wa, monkeypatch
):
    await wa.start()
    payload = wa.payload()
    payload["entry"].extend(wa.payload("/echo beta", "other", "1002")["entry"])
    original = wa.app._persist
    count = 0

    async def fail(operation, record=None):
        nonlocal count
        if operation == "save_receipt":
            count += 1
            if count == 2:
                raise RuntimeError("disk full")
        return await original(operation, record)

    monkeypatch.setattr(wa.app, "_persist", fail)
    assert (await wa.post(payload)).status_code == 500
    assert len(wa.server.tasks.spool.list_pending()) == 2
    monkeypatch.setattr(wa.app, "_persist", original)
    assert (await wa.post(payload)).status_code == 200
    assert len(wa.server.tasks.spool.list_pending()) == 2
    await drain(wa.server)
    assert len([c for c in wa.api.calls if c[0] == "POST"]) == 2


@pytest.mark.parametrize("mutation", ["id", "sender", "timestamp", "text", "entries", "messages"])
async def test_invalid_supported_payloads_create_no_tasks(wa, mutation):
    await wa.start()
    payload = wa.payload()
    value = payload["entry"][0]["changes"][0]["value"]
    item = value["messages"][0]
    if mutation == "id":
        item["id"] = None
    if mutation == "sender":
        item["from"] = 42
    if mutation == "timestamp":
        item["timestamp"] = "bad"
    if mutation == "text":
        item["text"]["body"] = []
    if mutation == "entries":
        payload["entry"] = {}
    if mutation == "messages":
        value["messages"] = {}
    assert (await wa.post(payload)).status_code == 400
    assert not wa.server.tasks.spool.list_pending()


async def test_concurrent_conversations_and_forged_actions(wa):
    await wa.start()
    await wa.post(wa.payload("/hello"))
    await drain(wa.server)
    one = await wa.app.create_conversation(
        "alpha",
        participants=[{"user_id": "391234", "chat_id": "391234"}],
        route="conversation",
        context={"pr": 1},
    )
    two = await wa.app.create_conversation(
        "alpha",
        participants=[{"user_id": "391234", "chat_id": "391234"}],
        route="conversation",
        context={"pr": 2},
    )
    first = await wa.app.send_conversation_message(
        "alpha", one["id"], "391234", "First", buttons={"Accept": "accept"}
    )
    await wa.app.send_conversation_message("alpha", two["id"], "391234", "Second")
    await wa.post(wa.payload("unclear", "m2"))
    await drain(wa.server)
    assert "Please reply" in wa.api.calls[-1][2]["text"]["body"]
    data = f"c:{one['id']}:accept"
    payload = wa.payload(
        event_id="m3",
        type="interactive",
        context={"id": first["message_id"]},
        interactive={"type": "button_reply", "button_reply": {"id": data}},
    )
    await wa.post(payload)
    await drain(wa.server)
    assert wa.api.calls[-1][2]["text"]["body"] == "PR 1: accept"
    count = len(wa.api.calls)
    payload["entry"][0]["changes"][0]["value"]["messages"][0].update(
        id="forged", **{"from": "399999"}
    )
    await wa.post(payload)
    await drain(wa.server)
    assert len(wa.api.calls) == count
    await wa.app.close_conversation("alpha", one["id"])
    payload["entry"][0]["changes"][0]["value"]["messages"][0].update(
        id="closed", **{"from": "391234"}
    )
    await wa.post(payload)
    await drain(wa.server)
    assert len(wa.api.calls) == count


@pytest.mark.parametrize("policy", ["first", "all"])
async def test_admin_decisions_use_templates_and_remain_final(wa, policy):
    await wa.start(
        access={"approval_required": True, "admins": ["39100", "39200"], "approval_policy": policy},
        notifications={
            "approval_template": {"name": "approval", "language": "it"},
            "resolution_template": {"name": "resolution", "language": "it"},
        },
    )
    await wa.post(wa.payload("/hello"))
    await drain(wa.server)
    record = (await wa.app.conversations.get_records("alpha"))[0]
    assert len(record["messages"]) == 2 and record["state"] == "open"
    templates = [p for m, _, p in wa.api.calls if m == "POST" and p["type"] == "template"]
    assert len(templates) == 2

    async def vote(index, action):
        message = record["messages"][index]
        await wa.app.conversations.handle_action(
            "alpha",
            {"id": message["user_id"]},
            message["chat_id"],
            message["message_id"],
            f"c:{record['id']}:{action}",
        )

    if policy == "all":
        await vote(0, "approve")
        assert (await wa.app.get_conversation("alpha", record["id"]))["state"] == "open"
        await vote(0, "reject")  # A recorded vote cannot be reversed.
        await vote(1, "approve")
    else:
        await asyncio.gather(vote(0, "approve"), vote(1, "reject"))
    saved = await wa.app.get_conversation("alpha", record["id"])
    assert saved["state"] == "approved"
    assert all(m["resolution"] for m in saved["messages"])
    count = len(wa.api.calls)
    await vote(0, "reject")
    assert len(wa.api.calls) == count
    await wa.post(wa.payload("/restricted", "protected"))
    await drain(wa.server)
    assert len(wa.api.calls) == count


async def test_missing_admin_template_leaves_request_pending_for_recovery(wa):
    await wa.start(access={"approval_required": True, "admins": ["39100"]})
    await wa.post(wa.payload())
    await drain(wa.server)
    record = (await wa.app.conversations.get_records("alpha"))[0]
    assert record["state"] == "open" and not record["messages"]
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "39100", "last_inbound": time.time()}
    )
    await wa.app.conversations.restore_admissions("alpha")
    assert len((await wa.app.get_conversation("alpha", record["id"]))["messages"]) == 1


async def test_rate_limit_cooldown_and_uncertain_errors_hide_secrets(wa, monkeypatch):
    await wa.start()
    waits = []

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(wa.app.delivery, "wait", wait)
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"code": 130429}}),
        httpx.Response(200, json={"messages": [{"id": "sent"}]}),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: responses.pop(0))
    ) as client:
        wa.app._client = client
        await wa.app.send_template("alpha", "391234", name="notice", language="it")
    assert waits == [7]

    def timeout(request):
        raise httpx.ReadTimeout("secret-alpha", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        wa.app._client = client
        with pytest.raises(RuntimeError, match="uncertain") as error:
            await wa.app.send_template("alpha", "391234", name="notice", language="it")
        assert "secret-alpha" not in str(error.value)
    assert waits == [7]


async def test_announcements_report_acceptance_and_per_recipient_failures(wa):
    await wa.start()
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "391234", "last_inbound": time.time()}
    )
    result = await wa.app.send_announcement("alpha", ["391234", "399999", "391234"], "hello")
    assert [r["status"] for r in result] == ["accepted", "failed"]
    code = await wa.app.queue_announcement(
        "alpha", ["399999"], template={"name": "notice", "language": "it"}
    )
    await drain(wa.server)
    assert wa.server.tasks.spool.read_result(code)[0]["status"] == "accepted"
    with pytest.raises(ValueError):
        await wa.app.send_announcement(
            "alpha", ["399999"], "hello", template={"name": "notice", "language": "it"}
        )


async def test_reminders_recheck_window_and_track_template_replies(wa):
    await wa.start()
    when = datetime.now(timezone.utc) + timedelta(days=1)
    code = await wa.app.schedule_reminder("alpha", "391234", "old text", when=when)
    with pytest.raises(ValueError, match="window"):
        await wa.app.deliver_reminder(code)
    assert (await wa.app.get_reminder(code))["delivery_state"] == "failed"
    conversation = await wa.app.create_conversation(
        "alpha",
        participants=[{"user_id": "391234", "chat_id": "391234"}],
        route="conversation",
        context={"pr": 9},
    )
    template = {"name": "notice", "language": "it"}
    code = await wa.app.schedule_reminder(
        "alpha",
        "391234",
        when=when,
        template=template,
        conversation_id=conversation["id"],
        user_id="391234",
    )
    await wa.app.deliver_reminder(code)
    reminder = await wa.app.get_reminder(code)
    assert reminder["delivery_state"] == "accepted"
    assert (await wa.app.get_message("alpha", reminder["message_ids"][0]))["status"] == "accepted"
    saved = await wa.app.get_conversation("alpha", conversation["id"])
    assert len(saved["messages"]) == 1
    await wa.post(wa.payload("answer", context={"id": saved["messages"][0]["message_id"]}))
    await drain(wa.server)
    assert wa.api.calls[-1][2]["text"]["body"] == "PR 9: answer"
    cancelled = await wa.app.schedule_reminder("alpha", "391234", when=when, template=template)
    assert await wa.app.cancel_reminder(cancelled)
    await wa.app.deliver_reminder(cancelled)
    skipped = await wa.app.schedule_reminder(
        "alpha",
        "391234",
        when=when,
        template=template,
        conversation_id=conversation["id"],
        user_id="391234",
    )
    await wa.app.close_conversation("alpha", conversation["id"])
    await wa.app.deliver_reminder(skipped)
    assert (await wa.app.get_reminder(skipped))["delivery_state"] == "skipped"


async def test_media_upload_limits_and_long_text(wa):
    await wa.start()
    await wa.app._persist(
        "advance_window", {"bot_code": "alpha", "recipient": "391234", "last_inbound": time.time()}
    )
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path.endswith("/media"):
            assert b"application/pdf" in request.content and b"PDF" in request.content
            return httpx.Response(200, json={"id": "media-1"})
        return wa.api.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        wa.app._client = client
        await wa.app.send_document("alpha", "391234", b"PDF", filename="x.pdf", caption="report")
        assert wa.api.calls[-1][2]["document"]["id"] == "media-1"
        parts = await wa.app.send_text("alpha", "391234", "😀" * 5000)
        assert len(parts) == 2
        with pytest.raises(ValueError):
            await wa.app.send_media("alpha", "391234", "image", b"x" * 5_000_001, filename="x.jpg")
        await wa.app.send_media("alpha", "391234", "image", "https://example.com/x.jpg")
    assert len(seen) == 5


async def test_restart_restores_windows_conversations_and_overdue_template_reminder(wa):
    await wa.start()
    await wa.post(wa.payload())
    await drain(wa.server)
    conversation = await wa.app.create_conversation(
        "alpha", participants=[{"user_id": "391234", "chat_id": "391234"}], route="conversation"
    )
    code = await wa.app.schedule_reminder(
        "alpha",
        "391234",
        when=datetime.now(timezone.utc) + timedelta(days=1),
        template={"name": "notice", "language": "it"},
    )
    row = await wa.app.get_reminder(code)
    row["next_run_ts"] = time.time() - 1
    wa.server.tasks.task_store.save(row)
    fresh = Harness(wa.root, storage_key=wa.storage_key)
    try:
        await fresh.app.on_startup()
        assert await fresh.app.delivery.window_open("alpha", "391234")
        assert (await fresh.app.get_conversation("alpha", conversation["id"]))["state"] == "open"
        await fresh.server.tasks.scheduler.tick()
        async with asyncio.timeout(3):
            while (await fresh.app.get_reminder(code))["delivery_state"] != "accepted":
                await asyncio.sleep(0.01)
        await fresh.app.deliver_reminder(code)
        assert len([c for c in fresh.api.calls if c[0] == "POST"]) == 1
    finally:
        await fresh.client.aclose()
