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

"""Authenticated MCP/REST account operations over an explicitly supplied connection.

This source-tree application requires the whatsapp_account avatar tag. Pairing,
credentials and directory resynchronization are local operations, never tools.
Message sending accepts an exact known JID; name lookup never chooses a recipient.
"""

from genro_routes import RoutingClass, route
from kajenn.applications.mcp import McpOpenApiApplication
from kajenn.exceptions import HTTPBadRequest, HTTPException
from kajenn.lifespan import FatalBootError


class _Operations(RoutingClass):
    def __init__(self, application):
        self.application = application
        self.route.plug("channel")

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account", openapi_method="post")
    async def get_status(self) -> dict:
        """Read connection state and synchronized record counts."""
        return self.application.status

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account", openapi_method="post")
    async def get_contacts(self, query: str = "", limit: int = 50, offset: int = 0) -> dict:
        """Search observed contact names; return all candidates on ambiguity."""
        return await self.application.get_contacts(query, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account", openapi_method="post")
    async def get_chats(self, query: str = "", limit: int = 50, offset: int = 0,
                        include_archived: bool = False) -> dict:
        """List observed chats; an address-book entry alone is not a chat."""
        return await self.application.get_chats(query, limit, offset, include_archived)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account", openapi_method="post")
    async def get_messages(self, chat_id: str, limit: int = 50, offset: int = 0) -> dict:
        """Read locally synchronized text messages; remote history may be incomplete."""
        return await self.application.get_messages(chat_id, limit, offset)

    @route(channel_channels="mcp,rest", auth_rule="whatsapp_account", openapi_method="post")
    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send an explicitly requested text to an exact known JID, never a name."""
        return await self.application.send_text(chat_id, text)


class WhatsAppAccountApplication(McpOpenApiApplication):
    def __init__(self, *, connection_factory, **kwargs):
        self.connection = connection_factory()
        kwargs.setdefault("mcp_name_segment", "_mcp")
        kwargs.setdefault("api_name", "_account")
        super().__init__(routing_class=_Operations(self), **kwargs)
        self.route.router_at_path("_meta").auth.configure(rule="whatsapp_account")

    async def on_startup(self):
        try:
            await self.connection.start()
        except Exception as error:
            await self.connection.stop()
            raise FatalBootError("WhatsApp account startup failed") from error

    async def on_shutdown(self):
        await self.connection.stop()

    @property
    def status(self):
        return {"connected": self.connection.connected,
                "counts": self.connection.directory.counts,
                "coverage": "observed_and_synchronized_subset",
                "sync": self.connection.sync_status,
                "callback_errors": getattr(self.connection, "callback_errors", 0),
                "callback_failures": getattr(self.connection, "callback_failures", {})}

    def validate_page(self, query, limit, offset):
        if not isinstance(query, str) or len(query) > 200:
            raise HTTPBadRequest("query must be a string of at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPBadRequest("limit must be an integer between 1 and 100")
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise HTTPBadRequest("offset must be a nonnegative bounded integer")

    def validate_peer(self, chat_id):
        if (not isinstance(chat_id, str) or not chat_id or "@" not in chat_id
                or not self.connection.directory.known_peer(chat_id)):
            raise HTTPBadRequest("chat_id must be an exact JID returned by contacts or chats")

    async def get_contacts(self, query="", limit=50, offset=0):
        self.validate_page(query, limit, offset)
        return self.connection.directory.get_contacts(query, limit, offset)

    async def get_chats(self, query="", limit=50, offset=0, include_archived=False):
        self.validate_page(query, limit, offset)
        if type(include_archived) is not bool:
            raise HTTPBadRequest("include_archived must be a boolean")
        return self.connection.directory.get_chats(query, limit, offset, include_archived)

    async def get_messages(self, chat_id, limit=50, offset=0):
        self.validate_page("", limit, offset)
        self.validate_peer(chat_id)
        return self.connection.directory.get_messages(chat_id, limit, offset)

    async def send_text(self, chat_id, text):
        self.validate_peer(chat_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise HTTPBadRequest("text must contain between 1 and 4000 characters")
        if not self.connection.connected:
            raise HTTPException(503, detail="WhatsApp is disconnected")
        message_id = await self.connection.send_text(chat_id, text)
        return {"id": str(message_id), "chat_id": chat_id, "status": "submitted"}
