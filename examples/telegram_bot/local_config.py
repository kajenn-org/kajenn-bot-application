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

"""Use the same bot classes locally without claiming the central webhook."""

from examples.telegram_bot.config import TelegramDemoConfiguration


class TelegramLocalConfiguration(TelegramDemoConfiguration):
    """Persist registrations locally and send directly through the shared bot token."""

    def telegram_section(self, app):
        """No webhook URL: this installation only sends messages."""
        app.telegram(persistence_route="registry/bots")
