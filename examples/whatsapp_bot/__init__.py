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

"""Small business bot with two public commands and an optional conversation route."""

from genro_builders.builder import element
from genro_routes import RoutingClass, route
from kajenn_bot_application.whatsapp import WhatsAppBotInstanceGrammar


class DemoBotGrammar(WhatsAppBotInstanceGrammar):
    @element(node_label="settings", sub_tags="")
    def settings(self, greeting: str = "Hello", dataset: str = "demo") -> None:
        """Per-instance example content."""


class DemoBot(RoutingClass):
    grammar = DemoBotGrammar

    def __init__(self, application, code, config):
        self.application, self.code, self.config = application, code, config
        super().__init__()

    @route()
    def hello(self, text: str = "") -> str:
        return f"{self.config('settings.greeting', default='Hello')} [{self.config('settings.dataset', default='demo')}]"

    @route()
    async def echo(self, text: str = "") -> str:
        return text or "Send /echo followed by some text."

    @route()
    async def conversation(self, text, sender=None, conversation=None, action=""):
        return (
            f"PR {conversation['context'].get('pr', '?')}: {action or text}"
            if conversation
            else "Reply to a conversation message."
        )
