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

"""Implementation checks: offline subprocess configuration and cancellation."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from examples.whatsapp_account.transcription import _LocalTranscriber


async def test_local_engine_is_offline_and_receives_audio_on_stdin(tmp_path, monkeypatch):
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(b'{"text":"hello","language":"en","duration":1}', None))
    process.wait = AsyncMock()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await _LocalTranscriber(tmp_path).transcribe(b"audio", "audio/ogg", "auto")
    assert result["text"] == "hello"
    assert spawn.call_args.kwargs["env"]["HF_HUB_OFFLINE"] == "1"
    assert "audio" not in spawn.call_args.args
    process.communicate.assert_awaited_once_with(b"audio")
    process.kill.assert_not_called()


async def test_cancelled_transcription_kills_and_reaps_worker(tmp_path, monkeypatch):
    process = Mock(returncode=None)
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    process.wait = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(asyncio.CancelledError):
        await _LocalTranscriber(tmp_path).transcribe(b"audio", "audio/ogg", "it")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
