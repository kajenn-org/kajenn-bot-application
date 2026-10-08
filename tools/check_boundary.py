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

"""Check the bot package against the installed kajenn dependency boundary."""

import importlib.util
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    server = importlib.util.find_spec("kajenn")
    if server is None or server.origin is None:
        raise RuntimeError("Install kajenn before checking the dependency boundary")
    return subprocess.call([
        sys.executable, str(root / "tools/import_graph.py"),
        "--package", f"kajenn_bot_application={root / 'src/kajenn_bot_application'}",
        "--package", f"kajenn={Path(server.origin).parent}",
        "--package", f"kajenn_server_app={Path(server.origin).parent.parent / 'kajenn_server_app'}",
        "--forbid", "kajenn->kajenn_bot_application",
        "--forbid", "kajenn_server_app->kajenn_bot_application",
        "--forbid", "kajenn_bot_application->kajenn_server_app",
        "--allowlist", f"kajenn_bot_application->kajenn:{root / 'tools/kajenn-imports.txt'}",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
