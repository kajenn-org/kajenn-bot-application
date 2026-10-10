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

"""Optional local speech recognition in a cancellable subprocess."""

import asyncio
import json
import os
from pathlib import Path
import sys

from kajenn.exceptions import HTTPException


class _LocalTranscriber:
    def __init__(self, model_path, python=sys.executable):
        self.model_path = Path(model_path).resolve(strict=True)
        if not self.model_path.is_dir():
            raise ValueError("Transcription requires a local model directory")
        self.python = python

    async def transcribe(self, content, mimetype, language):
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        process = await asyncio.create_subprocess_exec(
            self.python, "-m", "kajenn_bot_application.transcribe_worker",
            str(self.model_path), language, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=environment)
        try:
            async with asyncio.timeout(170):
                output, _ = await process.communicate(content)
            if process.returncode != 0:
                raise HTTPException(502, detail="Local transcription failed; verify the model and audio")
            if len(output) > 100000:
                raise HTTPException(502, detail="Transcription result exceeds the output limit")
            result = json.loads(output)
            return {"text": result["text"], "language": result["language"],
                    "duration": result["duration"], "engine": "faster-whisper-local"}
        except TimeoutError as error:
            raise HTTPException(504, detail="Local transcription timed out") from error
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
