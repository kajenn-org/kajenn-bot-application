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

"""Serve the experimental account MCP endpoint on authenticated loopback HTTP."""

import argparse
import hmac
import os
from pathlib import Path
import secrets

import uvicorn
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.exceptions import HTTPUnauthorized
from kajenn.response import Response

from examples.whatsapp_account.application import WhatsAppAccountApplication
from examples.whatsapp_account.connection import _Connection


class _Identity(RoutedApplication):
    def __init__(self, *, token_path, **kwargs):
        self.token = Path(token_path).read_text().strip()
        if not self.token:
            raise ValueError("An authentication token is required")
        super().__init__(**kwargs)

    @route()
    def check(self, credential: str = "", channel: str = "") -> dict:
        if not hmac.compare_digest(credential, f"Bearer {self.token}"):
            raise HTTPUnauthorized("Invalid account credential")
        return {"identity": "local-owner", "tags": ["whatsapp_account"], "data": {}}

    async def __call__(self, scope, receive, send):
        if scope.get("kajenn.kbus"):
            await super().__call__(scope, receive, send)
        else:
            await Response("Not Found", status_code=404)(scope, receive, send)


class _Server:
    def __init__(self, arguments):
        self.arguments = arguments

    def create_connection(self):
        return _Connection(self.arguments.session_dir, self.arguments.resync)

    def run(self):
        directory = self.arguments.session_dir
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise PermissionError("Account directory must be private (0700)")
        token_path = directory / "mcp.token"
        if not token_path.exists():
            descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as token_file:
                token_file.write(secrets.token_urlsafe(32))
        if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
            raise PermissionError("Token must be a private regular file (0600)")
        os.environ["KAJENN_HOME"] = str(directory / "server")
        (directory / "site").mkdir(mode=0o700, exist_ok=True)
        server = AsgiServer(
            applications=[
                (_Identity, {"code": "identity", "token_path": str(token_path)}),
                (WhatsAppAccountApplication,
                 {"code": "whatsapp", "connection_factory": self.create_connection}),
            ],
            storage=[{"name": "site", "protocol": "local", "base_path": str(directory / "site")}],
            channels={name: {"authentication_route": "/identity/check"}
                      for name in ("rest", "mcp")},
        )
        uvicorn.run(server, host="127.0.0.1", port=self.arguments.port, access_log=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--resync", action="store_true", help="Replay app-state directory metadata")
    _Server(parser.parse_args()).run()


if __name__ == "__main__":
    main()
