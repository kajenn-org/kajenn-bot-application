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

"""Encrypted, owner-readable account state with a single-process lease."""

import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from filelock import FileLock


class _AccountStore:
    def __init__(self, path: Path, key: str):
        self.path = path
        self.cipher = Fernet(key.encode())
        self.lease = FileLock(str(path) + ".lock", timeout=0, mode=0o600)

    def open(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("account state must not be a symbolic link")
        self.lease.acquire()
        try:
            if self.path.exists():
                os.chmod(self.path, 0o600)
                return json.loads(self.cipher.decrypt(self.path.read_bytes()))
            return None
        except BaseException:
            self.close()
            raise

    def save(self, record):
        payload = self.cipher.encrypt(json.dumps(record).encode())
        fd, name = tempfile.mkstemp(prefix=".account-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.chmod(name, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def close(self):
        self.lease.release()
