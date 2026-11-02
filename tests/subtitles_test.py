"""Subtitles-only progressive jobs: cues without TTS audio.

Run: python tests/subtitles_test.py (fully mocked; no Gemini quota).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ["TRANSLATION_PROVIDER"] = "gemini"
os.environ["TTS_PROVIDER"] = "edge"
import progressive  # noqa: E402

VIDEO_ID = "subtitles123"


def fake_analyze(client, video_id, duration, begin, end):
    return [{"start": begin + 2, "end": begin + 5,
             "original": "Hello there", "vi": "Xin chào"}]


def forbidden_tts(*args, **kwargs):
    raise AssertionError("subtitles-only jobs must never synthesize speech")


def no_worker(self):
    pass


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def main():
    original_client = progressive.gemini_client
    original_analyze = progressive.analyze_youtube_window
    original_synth = progressive.synthesize_voices
    progressive.gemini_client = lambda *args, **kwargs: object()
    progressive.analyze_youtube_window = fake_analyze
    progressive.synthesize_voices = forbidden_tts
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            dub_key = progressive.cache_key(VIDEO_ID, 112.0)
            subtitle_key = progressive.cache_key(VIDEO_ID, 112.0, "subtitles")
            assert dub_key != subtitle_key, "modes must not share a cache folder"

            job = progressive.ProgressiveJob(VIDEO_ID, 112.0, path, "subtitles")
            assert job.snapshot()["mode"] == "subtitles", job.snapshot()
            assert wait_for(lambda: job.snapshot()["completed_chunks"] == 4), job.snapshot()
            snap = job.snapshot()
            assert snap["mode"] == "subtitles", snap
            assert snap["tts_provider"] == "" and snap["tts_voice"] == "", snap
            assert snap["stats"]["chunks_done"] == 4, snap["stats"]
            assert snap["stats"]["tts_calls"] == 0, snap["stats"]
            assert snap["stats"]["tts_seconds"] == 0, snap["stats"]
            assert snap["playable_until"] == 60.0, snap
            assert (path / "chunk_00000.json").exists()
            assert not list(path.glob("*.wav")), "no dubbed audio should be written"
            cues = json.loads((path / "chunk_00000.json").read_text(encoding="utf-8"))
            assert cues[0]["vi"] == "Xin chào", cues

            job.seek(108)
            assert wait_for(lambda: 7 in job.snapshot()["ready_chunks"]), job.snapshot()
            assert job.snapshot()["playhead_buffered_until"] == 112.0
            job.seek(65)
            assert wait_for(lambda: job.snapshot()["stage"] == "complete"), job.snapshot()
            job.close()

            cached = progressive.ProgressiveJob(VIDEO_ID, 112.0, path, "subtitles")
            assert cached.snapshot()["stage"] == "complete", cached.snapshot()
            assert cached.snapshot()["completed_chunks"] == 8, cached.snapshot()
            cached.close()
        print("PASS: subtitles-only job writes cues, skips TTS, and reuses its cache")

        progressive.ProgressiveJob._run = no_worker
        import app as backend_app  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402

        with tempfile.TemporaryDirectory() as temp:
            backend_app.JOBS = Path(temp)
            backend_app.stream_jobs.clear()
            key = progressive.cache_key(VIDEO_ID, 60.0, "subtitles")
            folder = backend_app.JOBS / key
            folder.mkdir(parents=True)
            (folder / "chunk_00000.json").write_text(
                json.dumps([{"start": 0.0, "end": 1.0, "en": "hello", "vi": "xin chào"}]),
                encoding="utf-8")
            client = TestClient(backend_app.app)

            bad = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 60.0, "mode": "audio"})
            assert bad.status_code == 400, bad.text

            created = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 60.0, "mode": "subtitles"})
            assert created.status_code == 202, created.text
            assert created.json()["job_id"] == key, created.json()
            assert created.json()["mode"] == "subtitles", created.json()

            snap = client.get(f"/stream/jobs/{key}").json()
            assert snap["mode"] == "subtitles", snap
            assert snap["ready_chunks"] == [0], snap
            cues = client.get(f"/stream/jobs/{key}/chunks/0/cues")
            assert cues.status_code == 200 and cues.json()[0]["vi"] == "xin chào"
            audio = client.get(f"/stream/jobs/{key}/chunks/0/audio")
            assert audio.status_code == 409, audio.text

            dub = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 60.0, "mode": "dub"})
            assert dub.status_code == 202, dub.text
            assert dub.json()["job_id"] != key, "dub and subtitle jobs must be separate"
        print("PASS: stream API isolates subtitles mode and refuses fake audio")
    finally:
        progressive.gemini_client = original_client
        progressive.analyze_youtube_window = original_analyze
        progressive.synthesize_voices = original_synth


if __name__ == "__main__":
    main()
