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

"""Shared Telegram test fixtures and transport helpers."""

import copy
import json

import httpx
import pytest
from genro_routes import route

from examples.telegram_bot import DemoBot
from kajenn import AsgiServer, RoutedApplication
from kajenn_bot_application.telegram import TelegramBotApplication
from kajenn.tasks import WORKER_ID
from tests.storage_support import site_mounts


class RestrictedBot(DemoBot):
    """The example bot plus a protected command for authorization assertions."""

    @route(auth_rule="admin")
    def restricted(self, text: str = "") -> str:
        raise AssertionError("An anonymous Telegram sender must not reach this handler")


class Registry(RoutedApplication):
    def __init__(self, **kwargs):
        self.records = {}
        self.receipts = {}
        self.conversations = {}
        self.polls = {}
        self.fail = False
        super().__init__(**kwargs)

    @route()
    def bots(self, operation, application, record=None):
        if self.fail:
            raise RuntimeError("registry unavailable")
        if operation == "list":
            return copy.deepcopy(list(self.records.get(application, {}).values()))
        if operation == "save":
            self.records.setdefault(application, {})[record["code"]] = copy.deepcopy(record)
            return None
        if operation == "prune_receipts":
            self.receipts[application] = {
                key: expiry
                for key, expiry in self.receipts.get(application, {}).items()
                if expiry > record["now"]
            }
            return None
        if operation == "get_receipt":
            return self.receipts.get(application, {}).get(record["task_id"])
        if operation == "save_receipt":
            self.receipts.setdefault(application, {})[record["task_id"]] = record["expires_at"]
            return None
        if operation == "list_conversations":
            return copy.deepcopy(
                [
                    r
                    for (a, b, _), r in self.conversations.items()
                    if a == application and b == record["bot_code"]
                ]
            )
        if operation == "get_conversation":
            return copy.deepcopy(
                self.conversations.get((application, record["bot_code"], record["id"]))
            )
        if operation == "save_conversation":
            key = (application, record["bot_code"], record["id"])
            current = self.conversations.get(key)
            revision = current["revision"] if current else 0
            if record["revision"] != revision:
                raise RuntimeError("conversation revision conflict")
            saved = copy.deepcopy(record)
            saved["revision"] += 1
            self.conversations[key] = saved
            return copy.deepcopy(saved)
        if operation == "get_poll":
            return copy.deepcopy(
                self.polls.get((application, record["bot_code"], record["poll_id"]))
            )
        if operation == "save_poll":
            self.polls[(application, record["bot_code"], record["poll_id"])] = copy.deepcopy(record)
            return None
        raise ValueError(operation)


class TelegramAPI:
    def __init__(self):
        self.calls = []
        self.messages = {}
        self.fail_edits = set()
        self.fail_webhook = False

    def respond(self, request):
        method = request.url.path.rsplit("/", 1)[1]
        payload = json.loads(request.content)
        self.calls.append((method, payload))
        if method == "getMe":
            token_id = request.url.path.split("/")[1].split(":")[0][3:]
            result = {"id": int(token_id), "is_bot": True, "username": f"bot{token_id}"}
        elif method == "sendPoll":
            result = {
                "message_id": 900,
                "poll": {
                    "id": "poll-1",
                    "is_closed": False,
                    "question": payload["question"],
                    "options": payload["options"],
                    "total_voter_count": 0,
                },
            }
        elif method == "stopPoll":
            result = {"id": "poll-1", "is_closed": True, "total_voter_count": 1}
        elif method == "sendMessage":
            message_id = len(self.messages) + 1
            self.messages[message_id] = payload
            result = {"message_id": message_id, "chat": {"id": payload["chat_id"]}}
        elif method == "editMessageText" and payload["chat_id"] in self.fail_edits:
            return httpx.Response(400, json={"ok": False})
        elif method == "setWebhook" and self.fail_webhook:
            return httpx.Response(400, json={"ok": False, "description": "failed"})
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})


@pytest.fixture
async def setup(tmp_path):
    api = TelegramAPI()
    client = httpx.AsyncClient(transport=httpx.MockTransport(api.respond))
    server = AsgiServer(
        applications=[
            (Registry, {"code": "registry"}),
            (
                TelegramBotApplication,
                {
                    "code": "telegram",
                    "persistence_route": "registry/bots",
                    "webhook_url": "https://example.com/telegram",
                    "client": client,
                },
            ),
        ],
        storage=site_mounts(tmp_path),
    )
    yield server, server.applications["telegram"], api
    await client.aclose()


async def register(app, code="alpha", token="1:secret", **settings):
    return await app.register_bot(
        code=code,
        bot_class=RestrictedBot,
        token=token,
        name=code,
        config={"settings": settings},
    )


async def webhook(
    server, app, code="alpha", text="/hello", update_id=1, secret=None, method="POST", payload=None
):
    record = app.get_bot_registration(code)
    if secret is None:
        secret = record["webhook_secret"]
    body = (
        payload
        if payload is not None
        else {
            "update_id": update_id,
            "message": {"message_id": 10, "chat": {"id": 42}, "text": text},
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="https://example.com"
    ) as client:
        return await client.request(
            method,
            f"/telegram/{code}",
            json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )


async def drain(server):
    for descriptor in server.tasks.spool.list_pending():
        task_id = descriptor["task_id"]
        server.tasks.spool.assign(task_id, WORKER_ID)
        outcome = await server.tasks.executor.execute(task_id, WORKER_ID)
        assert outcome == "ok"
