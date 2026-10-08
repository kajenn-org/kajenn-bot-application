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

"""WhatsApp example using the existing encrypted bot registry plus receipt state."""

import copy
import hashlib
import json

from genro_bag.resolvers import EnvResolver
from genro_routes import route

from examples.telegram_bot.config import DemoRegistry as _Registry
from examples.whatsapp_bot import DemoBot
from kajenn_bot_application.whatsapp import WhatsAppBotApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES

STATUS_RANK = {"accepted": 0, "sent": 1, "failed": 2, "deleted": 2, "delivered": 3, "read": 4}


class DemoRegistry(_Registry):
    """Single-process encrypted storage; window updates are atomic maxima.

    Message writes merge distinct status/timestamp events and never regress a
    delivered/read receipt. A status can arrive before its API acceptance write.
    The shared example retains the telegram_registry directory layout.
    """

    @route()
    def bots(self, operation: str, application: str, record: dict | None = None):
        if operation not in ("get_window", "advance_window", "get_message", "save_message"):
            return super().bots(operation, application, record)
        category = "windows" if operation in ("get_window", "advance_window") else "messages"
        identity = record["recipient"] if category == "windows" else record["message_id"]
        key = hashlib.sha256(identity.encode()).hexdigest()
        node = self.server.storage.node(
            f"site:telegram_registry/{application}/{category}/{record['bot_code']}/{key}.json"
        )
        with self.conversation_lock:
            current = json.loads(node.read_text()) if node.exists() else None
            if operation.startswith("get_"):
                return current
            if operation == "advance_window":
                saved = dict(
                    record,
                    last_inbound=max(
                        record["last_inbound"], (current or {}).get("last_inbound", 0)
                    ),
                )
            else:
                saved = current or dict(record, events=[])
                aliases = set(saved.get("recipient_ids", [saved["recipient"]]))
                incoming = set(record.get("recipient_ids", [record["recipient"]]))
                if not aliases.intersection(incoming):
                    raise ValueError("message recipient conflict")
                saved["recipient_ids"] = sorted(aliases | incoming)
                event = {
                    k: copy.deepcopy(record[k]) for k in ("status", "timestamp", "error_codes")
                }
                if event not in saved["events"]:
                    saved["events"].append(event)
                if STATUS_RANK[record["status"]] >= STATUS_RANK[saved["status"]]:
                    saved.update(
                        status=record["status"],
                        timestamp=max(record["timestamp"], saved["timestamp"]),
                    )
                saved["error_codes"] = (
                    copy.deepcopy(record["error_codes"])
                    if record["status"] == "failed"
                    else saved.get("error_codes", [])
                )
            node.write_text(json.dumps(saved), encrypted=True)
            return saved

    async def on_startup(self):
        token = self.config("parameters.token", default=None)
        if token:
            app = self.server.applications["whatsapp"]
            if "team" not in app.bots:
                await app.register_bot(
                    code="team",
                    bot_class=DemoBot,
                    token=token,
                    phone_number_id=self.config("parameters.phone_number_id"),
                    business_account_id=self.config("parameters.business_account_id"),
                )


class WhatsAppDemoConfiguration(CONFIGURATION_TEMPLATES["default"]):
    """Own-credential receiver; callbacks/subscriptions are configured in Meta."""

    storage_key = EnvResolver("GENRO_STORAGE_KEY")

    def applications_section(self, cfg):
        apps = cfg.applications()
        app = apps.application(code="whatsapp", app_class=WhatsAppBotApplication)
        self.whatsapp_section(app)
        registry = apps.application(code="registry", app_class=DemoRegistry)
        registry.parameters(
            token=EnvResolver("KAJENN_WHATSAPP_TOKEN", default=None),
            phone_number_id=EnvResolver("KAJENN_WHATSAPP_PHONE_NUMBER_ID", default=None),
            business_account_id=EnvResolver("KAJENN_WHATSAPP_BUSINESS_ACCOUNT_ID", default=None),
        )

    def whatsapp_section(self, app):
        app.whatsapp(
            persistence_route="registry/bots",
            api_version=EnvResolver("KAJENN_WHATSAPP_API_VERSION"),
            webhook_url=EnvResolver("KAJENN_WHATSAPP_WEBHOOK_URL"),
            app_secret=EnvResolver("KAJENN_WHATSAPP_APP_SECRET"),
            verify_token=EnvResolver("KAJENN_WHATSAPP_VERIFY_TOKEN"),
        )
