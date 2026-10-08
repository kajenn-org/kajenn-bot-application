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

"""Telegram and WhatsApp bot applications mounted on a kajenn server."""

from .bot import BotBaseApplication
from .telegram import TelegramBotApplication
from .whatsapp import WhatsAppBotApplication

__all__ = ["BotBaseApplication", "TelegramBotApplication", "WhatsAppBotApplication"]
