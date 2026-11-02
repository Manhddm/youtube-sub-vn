"""Voice selector: Edge TTS voice specs, pitch/rate presets and cache isolation.

Run: python tests/voice_selector_test.py (fully mocked; no Edge or Gemini calls).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from pydub import AudioSegment  # noqa: E402

os.environ["TRANSLATION_PROVIDER"] = "gemini"
import engine  # noqa: E402
import progressive  # noqa: E402

VIDEO_ID = "voicesel123"
NAM = "vi-VN-NamMinhNeural"
HOAI = "vi-VN-HoaiMyNeural"
DEEP = "vi-VN-NamMinhNeural|pitch=-10Hz"


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def fake_analyze(client, video_id, duration, begin, end):
    return [{"start": begin + 1, "end": begin + 4,
             "original": "Hello there", "vi": "Xin chào"}]


def no_worker(self):
    pass


def main():
    os.environ["TTS_PROVIDER"] = "edge"
    os.environ["EDGE_VOICE"] = NAM

    voice, pitch, rate = engine.parse_voice_spec(f"{HOAI}|pitch=+8Hz|rate=+5%")
    assert (voice, pitch, rate) == (HOAI, "+8Hz", "+5%"), (voice, pitch, rate)
    assert engine.parse_voice_spec(NAM) == (NAM, "", "")
    for bad in ("", "not a voice", f"{NAM}|pitch=loud", f"{NAM}|speed=+5%",
                f"{NAM}|pitch=;rm", f"{NAM}|pitch=+8%", f"{NAM}|rate=+8Hz"):
        try:
            engine.parse_voice_spec(bad)
        except ValueError:
            continue
        raise AssertionError(f"voice spec must be rejected: {bad!r}")
    print("PASS: voice spec parser accepts presets and rejects malformed input")

    calls = []

    class NoAudioReceived(Exception):
        pass

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            calls.append({"text": text, "voice": voice, "kwargs": kwargs})

        async def save(self, path):
            Path(path).write_bytes(b"mock")

    fake_module = types.ModuleType("edge_tts")
    fake_module.Communicate = FakeCommunicate
    fake_module.exceptions = types.SimpleNamespace(NoAudioReceived=NoAudioReceived)
    original_module = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_module
    try:
        with tempfile.TemporaryDirectory() as folder:
            engine.synth_edge("Xin chào", Path(folder) / "a.mp3", DEEP)
            engine.synth_edge("Xin chào", Path(folder) / "b.mp3", NAM)
    finally:
        if original_module is not None:
            sys.modules["edge_tts"] = original_module
        else:
            sys.modules.pop("edge_tts", None)
    assert calls[0]["voice"] == NAM and calls[0]["kwargs"] == {"pitch": "-10Hz", "rate": "+15%"}, calls[0]
    assert calls[1]["voice"] == NAM and calls[1]["kwargs"] == {"rate": "+15%"}, calls[1]
    print("PASS: synth_edge forwards voice, pitch and rate to edge-tts")

    attempts: list[str] = []
    sleeps: list[float] = []

    class FlakyCommunicate:
        raise_always = False

        def __init__(self, text, voice, **kwargs):
            attempts.append(voice)

        async def save(self, path):
            if FlakyCommunicate.raise_always or len(attempts) < engine.EDGE_TTS_ATTEMPTS:
                raise NoAudioReceived("empty stream")
            Path(path).write_bytes(b"mock")

    flaky_module = types.ModuleType("edge_tts")
    flaky_module.Communicate = FlakyCommunicate
    flaky_module.exceptions = types.SimpleNamespace(NoAudioReceived=NoAudioReceived)
    original_pause = engine._edge_retry_pause

    async def record_pause(seconds):
        sleeps.append(seconds)

    engine._edge_retry_pause = record_pause
    sys.modules["edge_tts"] = flaky_module
    try:
        with tempfile.TemporaryDirectory() as folder:
            recovered = Path(folder) / "c.mp3"
            engine.synth_edge("Xin chào", recovered, DEEP)
            assert recovered.read_bytes() == b"mock", "transient failure must be retried"
            assert len(attempts) == engine.EDGE_TTS_ATTEMPTS, attempts
            assert sleeps == [0.5 * i for i in range(1, engine.EDGE_TTS_ATTEMPTS)], sleeps
            FlakyCommunicate.raise_always = True
            attempts.clear()
            sleeps.clear()
            exhausted = Path(folder) / "d.mp3"
            try:
                engine.synth_edge("Xin chào", exhausted, DEEP)
            except NoAudioReceived:
                pass
            else:
                raise AssertionError("persistent failure must reach the caller")
            assert len(attempts) == engine.EDGE_TTS_ATTEMPTS, attempts
            assert not exhausted.exists(), exhausted
    finally:
        engine._edge_retry_pause = original_pause
        sys.modules.pop("edge_tts", None)
    print("PASS: synth_edge retries transient edge-tts failures and reports persistent ones")

    assert progressive.cache_key(VIDEO_ID, 40.0, "dub", NAM) == \
        progressive.cache_key(VIDEO_ID, 40.0, "dub")
    keys = {progressive.cache_key(VIDEO_ID, 40.0, "dub", v)
            for v in (NAM, HOAI, DEEP)}
    assert len(keys) == 3, "each voice needs its own cache folder"
    print("PASS: cache keys isolate voices and keep the default voice stable")

    synthesized: list[str] = []

    def record_synth(items, provider, client=None, voice="", max_parallel=4):
        for text, path in items:
            synthesized.append(voice)
            Path(path).write_bytes(b"mock")

    original_analyze = progressive.analyze_youtube_window
    original_synth = progressive.synthesize_voices
    original_fit = progressive.fit_to_slot
    original_client = progressive.gemini_client
    progressive.analyze_youtube_window = fake_analyze
    progressive.synthesize_voices = record_synth
    progressive.fit_to_slot = lambda source, max_ms, work: AudioSegment.silent(
        duration=min(max_ms, 50), frame_rate=24000).set_sample_width(2)
    progressive.gemini_client = lambda *args, **kwargs: object()
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            job = progressive.ProgressiveJob(VIDEO_ID, 40.0, path, "dub", DEEP)
            assert wait_for(lambda: job.snapshot()["completed_chunks"] == 3), job.snapshot()
            snap = job.snapshot()
            assert snap["tts_voice"] == DEEP, snap
            assert snap["tts_provider"] == "edge", snap
            assert snap["stats"]["tts_calls"] == 3, snap["stats"]
            assert (path / "chunk_00000.wav").exists()
            job.close()
        assert synthesized and set(synthesized) == {DEEP}, synthesized
        print("PASS: progressive job renders every chunk with the selected voice spec")

        progressive.ProgressiveJob._run = no_worker
        import app as backend_app  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402

        with tempfile.TemporaryDirectory() as temp:
            backend_app.JOBS = Path(temp)
            backend_app.stream_jobs.clear()
            client = TestClient(backend_app.app)

            rejected = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 40.0,
                "voice": f"{NAM}|pitch=anything"})
            assert rejected.status_code == 400, rejected.text

            created = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 40.0, "voice": HOAI})
            assert created.status_code == 202, created.text
            hoai_key = created.json()["job_id"]
            assert hoai_key == progressive.cache_key(VIDEO_ID, 40.0, "dub", HOAI)

            deep = client.post("/stream/jobs", json={
                "video_id": VIDEO_ID, "declared_duration": 40.0, "voice": DEEP})
            assert deep.status_code == 202, deep.text
            assert deep.json()["job_id"] != hoai_key, "voice switch must not reuse a cache"
            assert client.get(f"/stream/jobs/{hoai_key}").json()["tts_voice"] == HOAI
            assert client.get(f"/stream/jobs/{deep.json()['job_id']}").json()["tts_voice"] == DEEP

            classic = client.post("/jobs/from-youtube", json={
                "video_id": VIDEO_ID, "declared_duration": 40.0, "voice": "bad voice"})
            assert classic.status_code == 400, classic.text
        print("PASS: stream API validates voice and keys jobs per selected voice")
    finally:
        progressive.analyze_youtube_window = original_analyze
        progressive.synthesize_voices = original_synth
        progressive.fit_to_slot = original_fit
        progressive.gemini_client = original_client


if __name__ == "__main__":
    main()
