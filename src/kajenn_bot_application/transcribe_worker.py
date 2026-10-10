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

"""Read audio from stdin and return local speech recognition as JSON."""

from io import BytesIO
import json
import sys

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


class _Worker:
    def run(self):
        content = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
        if not content or len(content) > 5 * 1024 * 1024:
            raise ValueError("Audio exceeds the byte limit")
        audio = decode_audio(BytesIO(content), sampling_rate=16000)
        duration = len(audio) / 16000
        if duration > 300:
            raise ValueError("Audio exceeds five minutes")
        model = WhisperModel(sys.argv[1], device="cpu", compute_type="int8",
                             local_files_only=True, cpu_threads=2)
        segments, info = model.transcribe(audio, language=None if sys.argv[2] == "auto" else sys.argv[2],
                                          beam_size=1, condition_on_previous_text=False)
        parts = []
        length = 0
        for segment in segments:
            length += len(segment.text)
            if length > 20000:
                raise ValueError("Transcript exceeds the text limit")
            parts.append(segment.text.strip())
        print(json.dumps({"text": " ".join(parts), "language": info.language, "duration": duration}))


def main():
    _Worker().run()


if __name__ == "__main__":
    main()
