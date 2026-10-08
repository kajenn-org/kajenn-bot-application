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

"""Keep bot integration tests independent from the developer's configuration."""

from pathlib import Path

import pytest
from genro_toolbox.smartasync import set_sync

from kajenn.config import HOME_ENV

set_sync()


@pytest.fixture(autouse=True)
def kajenn_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "kajenn_home"
    home.mkdir()
    monkeypatch.setenv(HOME_ENV, str(home))
    return home
