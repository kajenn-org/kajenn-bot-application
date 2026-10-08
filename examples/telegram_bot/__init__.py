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

"""A small Telegram bot package with class-owned configuration and commands."""

from genro_builders.builder import element
from genro_routes import RoutingClass, route

from kajenn_bot_application.telegram import TelegramBotInstanceGrammar


class DemoBotGrammar(TelegramBotInstanceGrammar):
    """Each registered instance chooses its greeting and dataset label."""

    @element(node_label="settings", sub_tags="")
    def settings(self, greeting: str = "Hello", dataset: str = "demo") -> None:
        """Options consumed by DemoBot; unknown options are rejected."""


class DemoBot(RoutingClass):
    """Two commands, independently configured for every registered bot."""

    grammar = DemoBotGrammar

    def __init__(self, application, code, config):
        self.application = application
        self.code = code
        self.config = config
        self.poll_events = []
        super().__init__()

    @route()
    def hello(self, text: str = "") -> str:
        """Return the configured greeting and the instance's dataset label."""
        return f"{self.config('settings.greeting')} [{self.config('settings.dataset')}]"

    @route()
    async def echo(self, text: str = "") -> str:
        """Repeat the text following /echo."""
        return text or "Send /echo followed by some text."

    @route()
    async def conversation(self, text, sender=None, conversation=None, action=""):
        """Reply within the selected PR conversation without broadcasting to others."""
        if conversation is None:
            return "Reply to a conversation message to continue."
        return f"PR {conversation['context'].get('pr', '?')}: {action or text}"

    @route()
    async def poll_event(self, event=None, poll=None, text=""):
        """Record accepted poll events in this example instance."""
        if event is None:
            return "Answer a native poll to submit a vote."
        self.poll_events.append(event["update_id"])
