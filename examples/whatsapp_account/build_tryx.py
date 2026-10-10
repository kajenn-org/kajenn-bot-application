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

"""Build the pinned experimental Tryx wheel in a fresh directory."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


class _Builder:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.resources = Path(__file__).resolve().parent
        self.upstream = json.loads((self.resources / "upstream.json").read_text())

    def execute(self, *command):
        subprocess.run(command, cwd=self.directory, check=True)

    def build(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        self.execute("git", "init")
        self.execute("git", "fetch", "--depth=1", "https://github.com/krypton-byte/tryx.git",
                     self.upstream["tryx_commit"])
        self.execute("git", "switch", "--detach", "FETCH_HEAD")
        self.execute("git", "submodule", "update", "--init", "libs/whatsapp-rust")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.directory / "libs/whatsapp-rust", text=True
        ).strip()
        if revision != self.upstream["whatsapp_rust_commit"]:
            raise RuntimeError("Unexpected native dependency revision")
        self.execute("git", "apply", str(self.resources / "tryx-lifecycle.patch"))
        shutil.copyfile(self.resources / "tryx.Cargo.lock", self.directory / "Cargo.lock")
        environment = dict(os.environ, RUSTUP_TOOLCHAIN=self.upstream["rust_toolchain"])
        subprocess.run(
            [sys.executable, "-m", "maturin", "build", "--locked", "--profile", "dev",
             "--interpreter", sys.executable, "--out", str(self.directory / "dist")],
            cwd=self.directory, env=environment, check=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="New empty build directory")
    _Builder(parser.parse_args().directory).build()


if __name__ == "__main__":
    main()
