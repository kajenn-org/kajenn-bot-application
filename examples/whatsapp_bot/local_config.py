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

"""Send-only installation sharing the central business number's credentials."""

from genro_bag.resolvers import EnvResolver
from examples.whatsapp_bot.config import WhatsAppDemoConfiguration


class WhatsAppLocalConfiguration(WhatsAppDemoConfiguration):
    def whatsapp_section(self, app):
        app.whatsapp(
            persistence_route="registry/bots",
            api_version=EnvResolver("KAJENN_WHATSAPP_API_VERSION"),
        )
