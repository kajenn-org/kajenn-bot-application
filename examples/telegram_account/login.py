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

"""Interactive local enrollment; run while the server is stopped."""

import asyncio
import getpass
import os

from kajenn_bot_application import TelegramAccountApplication


async def main():
    app = TelegramAccountApplication(
        code="personal",
        api_id=int(os.environ["KAJENN_TELEGRAM_API_ID"]),
        api_hash=os.environ["KAJENN_TELEGRAM_API_HASH"],
        session_path=os.environ["KAJENN_TELEGRAM_ACCOUNT_SESSION"],
        encryption_key=os.environ["KAJENN_TELEGRAM_ACCOUNT_KEY"],
        policy={"operations": [], "chats": {}},
    )
    await app.on_startup()
    try:
        status = await app.get_status()
        if status["authorized"]:
            print("This device is already authorized. Revoke it before changing accounts.")
            return
        await app.start_login(getpass.getpass("Telegram phone number: "))
        result = await app.complete_login(code=getpass.getpass("Telegram login code: "))
        if result["state"] == "password_required":
            result = await app.complete_login(
                password=getpass.getpass("Telegram two-step password: ")
            )
        print("Telegram account connected. Configure permissions before using the tools.")
    finally:
        await app.on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
