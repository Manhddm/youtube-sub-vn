"""Run: python tests/smoke_test.py (no Gemini key or Internet required)."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import engine


def make_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(24000)
        file.writeframes(b"\0\0" * 24000)
    return buffer.getvalue()


class FakeFiles:
    def upload(self, file):
        assert Path(file).exists()
        return SimpleNamespace(uri="files/test", name="files/test", mime_type="audio/mpeg")

    def delete(self, name):
        assert name == "files/test"


class FakeInteractions:
    def create(self, model, **kwargs):
        if model.endswith("-tts"):
            assert kwargs["response_format"]["type"] == "audio"
            return SimpleNamespace(output_audio=SimpleNamespace(data=base64.b64encode(make_wav()).decode()))
        assert any(item["type"] in ("audio", "video") for item in kwargs["input"])
        return SimpleNamespace(output_text=json.dumps({"segments": [
            {"start": 0.3, "end": 2.1, "original": "Hello!", "vi": "Xin chao!"},
            {"start": 2.5, "end": 4.0, "original": "Thank you!", "vi": "Cam on ban!"}
        ]}))


class FakeClient:
    files = FakeFiles()
    interactions = FakeInteractions()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        video = folder / "capture.webm"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=5", "-c:a", "libopus", str(video)],
                       check=True)
        original_factory = engine.gemini_client
        engine.gemini_client = lambda *args, **kwargs: FakeClient()
        os.environ["TTS_PROVIDER"] = "gemini"
        os.environ["TRANSLATION_PROVIDER"] = "gemini"
        try:
            messages = []
            result = engine.process_recording(video, folder, lambda a,b,c: messages.append((a,b,c)))
            assert result["segments"] == 2
            assert result["duration"] > 4.5
            assert (folder / "dub.mp3").stat().st_size > 1000
            assert '00:00:00,300' in (folder / "subtitles.vi.srt").read_text(encoding='utf-8-sig')
            assert messages[-1][0] == 'ready'
            print('PASS: recorded audio -> mocked Gemini -> MP3 + SRT')
            result2 = engine.process_youtube_url('testAB1234', 5.0, folder,
                                                      lambda a,b,c: messages.append((a,b,c)))
            assert result2['input_mode'] == 'youtube_url'
            assert (folder / 'dub.mp3').stat().st_size > 1000
            print('PASS: YouTube URL -> mocked Gemini -> MP3 + SRT')
        finally:
            engine.gemini_client = original_factory


if __name__ == '__main__':
    main()
