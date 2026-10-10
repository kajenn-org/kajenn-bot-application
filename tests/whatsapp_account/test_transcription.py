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
import json
import runpy
import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from examples.whatsapp_account.transcription import _LocalTranscriber
from kajenn.exceptions import HTTPException


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


@pytest.mark.parametrize("failure,returncode,output,status", [
    (None, 1, b"", 502),
    (None, 0, b"x" * 100001, 502),
    (TimeoutError(), None, b"", 504),
])
async def test_transcriber_reaps_worker_on_error(tmp_path, monkeypatch, failure, returncode, output, status):
    process = Mock(returncode=returncode)
    process.communicate = AsyncMock(return_value=(output, None), side_effect=failure)
    process.wait = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(HTTPException) as error:
        await _LocalTranscriber(tmp_path).transcribe(b"audio", "audio/ogg", "it")
    assert error.value.status == status
    process.wait.assert_awaited_once()
    if returncode is None:
        process.kill.assert_called_once()


def test_transcriber_rejects_file_instead_of_model_directory(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"not a model directory")
    with pytest.raises(ValueError, match="directory"):
        _LocalTranscriber(path)


@pytest.mark.parametrize("scenario", ["success", "empty", "large", "long_audio", "long_text"])
def test_worker_bounds_and_offline_inference(monkeypatch, capsys, scenario):
    # Simulate only the optional speech backend; execute the real installed worker.
    library = ModuleType("faster_whisper")
    decoder = ModuleType("faster_whisper.audio")
    decoder.decode_audio = Mock(return_value=range(16000 * (301 if scenario == "long_audio" else 1)))
    model = Mock()
    text = "x" * 20001 if scenario == "long_text" else " hello "
    model.transcribe.return_value = ([SimpleNamespace(text=text)], SimpleNamespace(language="en"))
    library.WhisperModel = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "faster_whisper", library)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", decoder)
    content = b"" if scenario == "empty" else b"x" * (5 * 1024 * 1024 + 1) if scenario == "large" else b"audio"
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(content)))
    monkeypatch.setattr(sys, "argv", ["worker", "/private/model", "auto"])
    if scenario == "success":
        runpy.run_module("kajenn_bot_application.transcribe_worker", run_name="__main__")
        result = json.loads(capsys.readouterr().out)
        assert result == {"text": "hello", "language": "en", "duration": 1.0}
        library.WhisperModel.assert_called_once_with("/private/model", device="cpu", compute_type="int8",
                                                    local_files_only=True, cpu_threads=2)
        assert model.transcribe.call_args.kwargs["language"] is None
    else:
        with pytest.raises(ValueError):
            runpy.run_module("kajenn_bot_application.transcribe_worker", run_name="__main__")
        assert not capsys.readouterr().out
        if scenario in ("empty", "large", "long_audio"):
            library.WhisperModel.assert_not_called()
