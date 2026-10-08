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

"""Implementation tests for outbound Telegram operations and reminders."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.telegram.support import drain, register, webhook

pytest_plugins = ["tests.telegram.support"]


async def test_retry_after_is_observed_and_transport_errors_hide_token(setup, monkeypatch):
    server, app, api = setup
    await register(app)
    waits = []

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(app.delivery, "wait", wait)
    responses = [
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}}),
        httpx.Response(503, json={"ok": False}),
        httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: responses.pop(0))
    ) as client:
        app._client = client
        assert (await app.send_message("alpha", 42, "hello"))["message_id"] == 1
    assert waits == [7, 2]

    def timeout(request):
        raise httpx.ReadTimeout("URL containing 1:secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        app._client = client
        with pytest.raises(RuntimeError, match="transport failed") as error:
            await app.send_message("alpha", 42, "uncertain")
        assert "secret" not in str(error.value)
    assert waits == [7, 2]  # An uncertain send is not repeated.


async def test_permanent_failure_is_not_retried_and_announcement_continues(setup):
    server, app, api = setup
    await register(app)

    def respond(request):
        data = json.loads(request.content)
        if data["chat_id"] == 100:
            return httpx.Response(403, json={"ok": False})
        return api.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        app._client = client
        results = await app.send_announcement("alpha", [42, 100, 42, 200], "News")
    assert [r["chat_id"] for r in results] == [42, 100, 200]
    assert [r["status"] for r in results] == ["sent", "failed", "sent"]
    assert "1:secret" not in results[1]["error"]


async def test_long_text_is_split_without_losing_content(setup):
    server, app, api = setup
    await register(app)
    text = "🙂 hello\n" * 1300
    result = await app.send_text("alpha", 42, text)
    sent = [p["text"] for m, p in api.calls if m == "sendMessage"]
    assert len(result) > 1
    assert "".join(sent) == text
    assert all(len(s.encode("utf-16-le")) // 2 <= 4096 for s in sent)


async def test_media_upload_and_reference_and_typing(setup):
    server, app, api = setup
    await register(app)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        app._client = client
        await app.send_document(
            "alpha", 42, b"document contents", filename="report.txt", caption="Report"
        )
        await app.send_media("alpha", 42, "photo", "telegram-file-id", caption="Photo")
        await app.send_typing("alpha", 42)
    assert "multipart/form-data" in requests[0].headers["content-type"]
    assert b'filename="report.txt"' in requests[0].content
    assert b"document contents" in requests[0].content
    assert json.loads(requests[1].content)["photo"] == "telegram-file-id"
    assert json.loads(requests[2].content)["action"] == "typing"
    with pytest.raises(ValueError):
        await app.send_media("alpha", 42, "unknown", "id")


async def test_poll_answers_are_persistent_and_ignore_older_votes(setup):
    server, app, api = setup
    await register(app)
    message = await app.send_poll(
        "alpha", -42, "When?", ["Today", "Tomorrow"], is_anonymous=False, route="poll_event"
    )
    poll_id = message["poll"]["id"]
    for update_id, options in [(3, [1]), (2, [0]), (4, [])]:
        payload = {
            "update_id": update_id,
            "poll_answer": {"poll_id": poll_id, "user": {"id": 42}, "option_ids": options},
        }
        assert (await webhook(server, app, payload=payload)).status_code == 200
        await drain(server)
    saved = await app.get_poll("alpha", poll_id)
    assert saved["answers"]["user:42"]["option_ids"] == []
    assert saved["answers"]["user:42"]["update_id"] == 4
    assert app.get_bot("alpha").poll_events == [3, 4]
    await app.stop_poll("alpha", poll_id)
    assert (await app.get_poll("alpha", poll_id))["poll"]["is_closed"] is True


async def test_reminder_survives_registration_restore_and_is_sent_once(setup):
    server, app, api = setup
    await register(app)
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    code = await app.schedule_reminder("alpha", 42, "Remember", when=when)
    row = server.tasks.task_store.get(code)
    assert row["next_run_ts"] == pytest.approx(when.timestamp())
    assert "secret" not in json.dumps(row)
    await app.on_startup()
    assert row["task_name"] in server.tasks.scheduler.scan()
    row["next_run_ts"] = time.time() - 1
    server.tasks.task_store.save(row)
    await server.tasks.scheduler.tick()
    async with asyncio.timeout(3):
        while (await app.get_reminder(code))["delivery_state"] != "sent":
            await asyncio.sleep(0.01)
    await app.deliver_reminder(code)
    assert len([p for m, p in api.calls if m == "sendMessage" and p["text"] == "Remember"]) == 1


async def test_cancelled_and_closed_conversation_reminders_do_not_send(setup):
    server, app, api = setup
    await register(app)
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    code = await app.schedule_reminder("alpha", 42, "Cancelled", when=when)
    await app.cancel_reminder(code)
    await app.deliver_reminder(code)
    conversation = await app.create_conversation(
        "alpha", participants=[{"user_id": 42, "chat_id": 42}], route="conversation"
    )
    code2 = await app.schedule_reminder(
        "alpha", 42, "Closed", when=when, conversation_id=conversation["id"], user_id=42
    )
    await app.close_conversation("alpha", conversation["id"])
    await app.deliver_reminder(code2)
    assert (await app.get_reminder(code2))["delivery_state"] == "skipped"
    assert not [p for m, p in api.calls if m == "sendMessage"]


async def test_announcement_task_keeps_per_recipient_results(setup):
    server, app, api = setup
    await register(app)
    task_id = await app.queue_announcement("alpha", [42, 200, 42], "Release")
    assert not [p for m, p in api.calls if m == "sendMessage"]
    await drain(server)
    results = server.tasks.spool.read_result(task_id)
    assert [r["chat_id"] for r in results] == [42, 200]
    assert all(r["status"] == "sent" for r in results)
    assert "secret" not in json.dumps(server.tasks.spool.read_params(task_id))


async def test_retry_exhaustion_keeps_cooldown_for_next_request(setup, monkeypatch):
    server, app, api = setup
    await register(app)
    waits = []

    async def wait(delay):
        waits.append(delay)

    monkeypatch.setattr(app.delivery, "wait", wait)
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 20}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
        app._client = client
        for _ in range(2):
            with pytest.raises(RuntimeError, match="429"):
                await app.send_message("alpha", 42, "limited")
    assert len(calls) == 6
    assert len(waits) == 5
    assert all(19 < delay <= 20 for delay in waits)


async def test_connect_failure_retries_but_forbidden_does_not(setup, monkeypatch):
    server, app, api = setup
    await register(app)
    attempts = []

    async def wait(delay):
        pass

    monkeypatch.setattr(app.delivery, "wait", wait)

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("1:secret", request=request)
        return httpx.Response(403, json={"ok": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        app._client = client
        with pytest.raises(RuntimeError, match="403"):
            await app.send_message("alpha", 42, "blocked")
    assert len(attempts) == 2


async def test_interrupted_reminder_is_uncertain_and_never_resent(setup):
    server, app, api = setup
    await register(app)
    code = await app.schedule_reminder(
        "alpha", 42, "Maybe delivered", when=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    record = await app.get_reminder(code)
    record["delivery_state"] = "sending"
    server.tasks.task_store.save(record)
    await app.deliver_reminder(code)
    assert (await app.get_reminder(code))["delivery_state"] == "uncertain"
    await app.deliver_reminder(code)
    assert not [p for m, p in api.calls if m == "sendMessage"]


async def test_poll_aggregates_and_invalid_events_and_missing_polls(setup):
    server, app, api = setup
    await register(app)
    await app.send_poll("alpha", -42, "Yes?", ["Yes", "No"])
    for update_id, votes in [(10, 5), (9, 3)]:
        payload = {
            "update_id": update_id,
            "poll": {"id": "poll-1", "total_voter_count": votes, "is_closed": False},
        }
        assert (await webhook(server, app, payload=payload)).status_code == 200
        await drain(server)
    assert (await app.get_poll("alpha", "poll-1"))["poll"]["total_voter_count"] == 5
    await app.delivery.receive_poll("alpha", {"update_id": 11, "poll": {"id": "missing"}})
    assert (
        await webhook(
            server,
            app,
            payload={
                "update_id": 12,
                "poll_answer": {"poll_id": "poll-1", "user": {"id": 42}, "option_ids": "oops"},
            },
        )
    ).status_code == 400


async def test_reminder_input_validation_and_active_conversation_delivery(setup):
    server, app, api = setup
    await register(app)
    with pytest.raises(ValueError, match="timezone"):
        await app.schedule_reminder("alpha", 42, "test", when=datetime.now())
    conversation = await app.create_conversation(
        "alpha", participants=[{"user_id": 42, "chat_id": 42}], route="conversation"
    )
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValueError, match="participant"):
        await app.schedule_reminder(
            "alpha", 42, "test", when=when, conversation_id=conversation["id"], user_id=200
        )
    code = await app.schedule_reminder(
        "alpha", 42, "Reply here", when=when, conversation_id=conversation["id"], user_id=42
    )
    await app.deliver_reminder(code)
    assert (await app.get_reminder(code))["delivery_state"] == "sent"
    saved = await app.get_conversation("alpha", conversation["id"])
    assert saved["messages"][0]["text"] == "Reply here"


async def test_announcement_records_partial_and_uncertain_deliveries(setup):
    server, app, api = setup
    await register(app)
    counts = {}

    def respond(request):
        payload = json.loads(request.content)
        chat_id = payload["chat_id"]
        counts[chat_id] = counts.get(chat_id, 0) + 1
        if chat_id == 100:
            raise httpx.ReadTimeout("1:secret", request=request)
        if counts[chat_id] > 1:
            return httpx.Response(403, json={"ok": False})
        return api.respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        app._client = client
        results = await app.send_announcement("alpha", [42, 100], "a" * 5000)
    assert [r["status"] for r in results] == ["partial", "uncertain"]
    assert len(results[0]["messages"]) == 1
    assert counts == {42: 2, 100: 1}


async def test_reminder_read_timeout_is_uncertain_and_not_repeated(setup):
    server, app, api = setup
    await register(app)
    code = await app.schedule_reminder(
        "alpha", 42, "Uncertain", when=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("1:secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        app._client = client
        with pytest.raises(RuntimeError, match="uncertain"):
            await app.deliver_reminder(code)
        await app.deliver_reminder(code)
    saved = await app.get_reminder(code)
    assert saved["delivery_state"] == "uncertain"
    assert "secret" not in saved["delivery_error"]
    assert len(calls) == 1
