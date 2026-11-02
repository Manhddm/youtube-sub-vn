"""Backend restart must rebuild the same progressive job from its disk cache.

No Gemini call is made: the worker thread is replaced with a no-op so this
test only exercises cache key stability, snapshot restoration and recovery.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("TTS_PROVIDER", "edge")

import app as backend_app  # noqa: E402
import progressive  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

VIDEO_ID = "dQw4w9WgXcQ"
DURATION = 60.0


def no_worker(self):
    pass


def make_chunk(folder: Path, index: int) -> bytes:
    path = folder / f"chunk_{index:05}.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24000)
    (folder / f"chunk_{index:05}.json").write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "en": "hello", "vi": "xin chào"}]),
        encoding="utf-8")
    return path.read_bytes()


def main():
    progressive.ProgressiveJob._run = no_worker
    client = TestClient(backend_app.app)
    with tempfile.TemporaryDirectory() as temp:
        backend_app.JOBS = Path(temp)
        backend_app.stream_jobs.clear()
        key = progressive.cache_key(VIDEO_ID, DURATION)
        assert len(key) == 32, key
        folder = backend_app.JOBS / key
        folder.mkdir(parents=True)
        original_audio = make_chunk(folder, 0)

        first = client.post("/stream/jobs", json={
            "video_id": VIDEO_ID, "declared_duration": DURATION})
        assert first.status_code == 202, first.text
        assert first.json()["job_id"] == key, first.json()

        snap = client.get(f"/stream/jobs/{key}").json()
        assert snap["ready_chunks"] == [0], snap["ready_chunks"]
        assert snap["total_chunks"] == 4, snap["total_chunks"]
        assert snap["stage"] == "buffered", snap["stage"]
        assert "model_order" in snap and snap["model_order"]["analysis"], snap
        assert snap["tts_provider"] in ("gemini", "edge"), snap
        assert snap["tts_voice"], snap
        assert "stats" in snap and snap["stats"]["chunks_done"] == 0, snap

        audio = client.get(f"/stream/jobs/{key}/chunks/0/audio")
        assert audio.status_code == 200 and audio.content == original_audio
        cues = client.get(f"/stream/jobs/{key}/chunks/0/cues")
        assert cues.status_code == 200 and cues.json()[0]["vi"] == "xin chào"

        # Simulated backend restart: memory is lost, the jobs folder survives.
        for job in backend_app.stream_jobs.values():
            job.close()
        backend_app.stream_jobs.clear()
        assert client.get(f"/stream/jobs/{key}").status_code == 404

        # The extension re-POSTs video_id + duration and lands on the same key.
        again = client.post("/stream/jobs", json={
            "video_id": VIDEO_ID, "declared_duration": DURATION})
        assert again.status_code == 202, again.text
        assert again.json()["job_id"] == key, again.json()
        restored = client.get(f"/stream/jobs/{key}").json()
        assert restored["ready_chunks"] == [0], restored["ready_chunks"]
        assert restored["completed_chunks"] == 1, restored
        audio = client.get(f"/stream/jobs/{key}/chunks/0/audio")
        assert audio.status_code == 200 and audio.content == original_audio

    print("PASS: simulated restart reuses cached chunks and the deterministic job id")
    print("PASS: snapshot exposes model order for popup fallback labels")
    print("PASS: snapshot exposes the TTS provider and voice for the popup")


if __name__ == "__main__":
    main()
