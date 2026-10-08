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

"""Storage mounts for isolated bot integration tests."""

from typing import Any


def site_mounts(base_dir: object) -> list[dict[str, Any]]:
    """Mount the test directory through kajenn's public storage configuration."""
    return [{"name": "site", "protocol": "local", "base_path": str(base_dir)}]
