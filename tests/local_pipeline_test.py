"""Local translation pipeline: caption slicing, ASR fallback and routing.

Run: python tests/local_pipeline_test.py (all models and network mocked).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

os.environ["TRANSLATION_PROVIDER"] = "local"
os.environ["TTS_PROVIDER"] = "edge"

import engine  # noqa: E402
import local_pipeline  # noqa: E402
import progressive  # noqa: E402

VIDEO_ID = "localpipe123"
DURATION = 112.0


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def captions() -> list[dict]:
    rows = []
    for start in range(0, 112, 5):
        rows.append({"start": float(start), "end": float(start + 4),
                     "text": f"line {start}"})
    return rows


class FakePipeline(local_pipeline.LocalPipeline):
    def __init__(self) -> None:
        super().__init__()
        self.translated: list[list[str]] = []
        self.asr_calls: list[tuple[float, float]] = []

    def captions(self, video_id):
        return captions()

    def translate(self, texts):
        self.translated.append(list(texts))
        return [f"VI {text}" for text in texts]

    def window_transcript(self, video_id, duration, begin, end):
        clip_begin, clip_end = local_pipeline.clip_bounds(begin, end, duration)
        if getattr(self, "no_captions", False):
            self.asr_calls.append((clip_begin, clip_end))
            return [{"start": begin + 1, "end": begin + 3, "text": "spoken words"}]
        return [cue for cue in captions()
                if clip_begin <= local_pipeline.midpoint(cue) <= clip_end]


def fake_synthesize(items, provider, client=None, voice="", max_parallel=4):
    for text, path in items:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 6000)


def main():
    pipeline = FakePipeline()

    cues = pipeline.cues_for_window(VIDEO_ID, DURATION, 20.0, 40.0)
    assert cues, cues
    assert all(20.0 <= (c["start"] + c["end"]) / 2 < 40.0 for c in cues), cues
    assert cues[0]["original"].startswith("line"), cues
    assert cues[0]["vi"] == f"VI {cues[0]['original']}", cues
    assert pipeline.translated and len(pipeline.translated[0]) == len(cues), pipeline.translated
    print("PASS: caption window slicing keeps only cues whose midpoint is inside the chunk")

    pipeline.no_captions = True
    asr = pipeline.cues_for_window(VIDEO_ID, DURATION, 60.0, 80.0)
    assert pipeline.asr_calls and pipeline.asr_calls[0] == (59.0, 81.0), pipeline.asr_calls
    assert asr and asr[0]["original"] == "spoken words", asr
    print("PASS: videos without captions fall back to the ASR window")

    original_getter = progressive.get_local_pipeline
    original_analyze = progressive.analyze_youtube_window
    original_client = progressive.gemini_client
    original_synth = progressive.synthesize_voices
    progressive.get_local_pipeline = lambda: pipeline
    progressive.gemini_client = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("local translation must never build a Gemini client"))
    progressive.synthesize_voices = fake_synthesize

    def forbidden_client(*args, **kwargs):
        raise AssertionError("local translation must never build a Gemini client")

    progressive.analyze_youtube_window = original_analyze
    try:
        window = progressive.analyze_youtube_window(None, VIDEO_ID, DURATION, 20.0, 40.0)
        assert window and all(20.0 <= c["start"] < 40.0 for c in window), window
        print("PASS: analyze_youtube_window uses the local provider without a client")

        release = threading.Event()
        started = threading.Event()
        calls = {"n": 0}

        def blocking_synthesize(items, provider, client=None, voice="", max_parallel=4):
            calls["n"] += 1
            started.set()
            release.wait(10)
            fake_synthesize(items, provider, client, voice, max_parallel)

        progressive.synthesize_voices = blocking_synthesize
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            job = progressive.ProgressiveJob(VIDEO_ID, DURATION, path)
            assert started.wait(10), "the renderer should start on the first chunk"
            assert wait_for(lambda: len(job.snapshot()["cues_ready_chunks"]) >= 3), job.snapshot()
            snap = job.snapshot()
            assert snap["ready_chunks"] == [], snap["ready_chunks"]
            assert snap["stage"] == "processing", snap
            release.set()
            assert wait_for(lambda: 0 in job.snapshot()["ready_chunks"]), job.snapshot()
            job.close()
        assert calls["n"] >= 1
        print("PASS: cues are cached ahead of the audio renderer that consumes them")

        progressive.synthesize_voices = fake_synthesize
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            job = progressive.ProgressiveJob(VIDEO_ID, DURATION, path)
            assert wait_for(lambda: job.snapshot()["completed_chunks"] >= 4), job.snapshot()
            snap = job.snapshot()
            assert snap["translation_provider"] == "local", snap
            assert snap["tts_provider"] == "edge", snap
            assert snap["stats"]["analysis_calls"] >= 4, snap["stats"]
            assert snap["stats"]["tts_calls"] >= 4, snap["stats"]
            assert snap["models"]["analysis"].startswith("local"), snap["models"]
            cues = json.loads((path / "chunk_00000.json").read_text(encoding="utf-8"))
            assert cues and cues[0]["vi"].startswith("VI "), cues
            with wave.open(str(path / "chunk_00000.wav")) as audio:
                assert audio.getnframes() == 15 * 24000, audio.getnframes()
            job.close()
        print("PASS: progressive job completes chunks locally with Edge TTS")

        original_engine_getter = engine.get_pipeline
        engine.get_pipeline = lambda: pipeline
        try:
            class Api:
                @staticmethod
                def create(**kwargs):
                    raise AssertionError("local translation must not call Gemini")

            class Client:
                interactions = Api()

            cues = engine.analyze_public_youtube(Client(), VIDEO_ID, DURATION,
                                                 lambda *args: None)
            assert cues and cues[0]["vi"].startswith("VI "), cues
            print("PASS: analyze_public_youtube serves the transcript locally")
        finally:
            engine.get_pipeline = original_engine_getter
    finally:
        progressive.get_local_pipeline = original_getter
        progressive.analyze_youtube_window = original_analyze
        progressive.gemini_client = original_client
        progressive.synthesize_voices = original_synth


if __name__ == "__main__":
    main()
