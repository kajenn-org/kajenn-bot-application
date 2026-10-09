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

"""Mount a personal account with authenticated operator and owner surfaces."""

import hmac

from genro_bag.resolvers import EnvResolver
from genro_routes import route

from kajenn import RoutedApplication
from kajenn.config.templates import CONFIGURATION_TEMPLATES
from kajenn.exceptions import HTTPUnauthorized
from kajenn.response import Response
from kajenn_bot_application import TelegramAccountApplication


class AccountIdentity(RoutedApplication):
    """Example local tokens: operator cannot modify policy or revoke the device."""

    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        for name, tags in (
            ("owner", ["admin", "telegram_account"]),
            ("operator", ["telegram_account"]),
        ):
            token = self.config(f"parameters.{name}_token", default=None)
            if token and hmac.compare_digest(credential, f"Bearer {token}"):
                return {"identity": name, "tags": tags, "data": {}}
        raise HTTPUnauthorized("invalid account application credential")

    async def __call__(self, scope, receive, send):
        if scope.get("kajenn.kbus"):
            await super().__call__(scope, receive, send)
            return
        await Response("Not Found", status_code=404)(scope, receive, send)


class TelegramAccountConfiguration(CONFIGURATION_TEMPLATES["default"]):
    """Supply secrets from the environment; listen on loopback for local use."""

    def applications_section(self, cfg):
        apps = cfg.applications()
        app = apps.application(code="personal", app_class=TelegramAccountApplication)
        app.telegram_account(
            api_id=EnvResolver("KAJENN_TELEGRAM_API_ID", dtype="L"),
            api_hash=EnvResolver("KAJENN_TELEGRAM_API_HASH"),
            session_path=EnvResolver("KAJENN_TELEGRAM_ACCOUNT_SESSION"),
            encryption_key=EnvResolver("KAJENN_TELEGRAM_ACCOUNT_KEY"),
            policy={"operations": [], "chats": {}},
        )
        identity = apps.application(code="identity", app_class=AccountIdentity)
        identity.parameters(
            owner_token=EnvResolver("KAJENN_TELEGRAM_OWNER_TOKEN"),
            operator_token=EnvResolver("KAJENN_TELEGRAM_OPERATOR_TOKEN"),
        )
        self.channels_section(cfg)

    def channels_section(self, cfg):
        channels = cfg.channels()
        channels.channel(name="rest", authentication_route="/identity/check")
        channels.channel(name="mcp", authentication_route="/identity/check")
