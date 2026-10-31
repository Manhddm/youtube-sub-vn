"""Run: python tests/model_fallback_test.py (mocked; no Gemini quota)."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import engine

ENV_KEYS = (
    "GEMINI_MODEL", "GEMINI_MODEL_FALLBACKS",
    "GEMINI_TTS_MODEL", "GEMINI_TTS_MODEL_FALLBACKS",
)


def make_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(24000)
        file.writeframes(b"\0\0" * 4800)
    return buffer.getvalue()


ANALYSIS_OK = SimpleNamespace(output_text=json.dumps({"segments": []}))


class ScriptedInteractions:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def create(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        result = self.behaviour(model, self.calls.count(model))
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, behaviour):
        self.interactions = ScriptedInteractions(behaviour)


def build_call(model):
    return {"model": model, "input": [], "response_format": {"type": "audio"}}


def apply_env(**values) -> dict:
    old = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(values)
    engine.reset_model_cache()
    return old


def restore_env(old: dict) -> None:
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    engine.reset_model_cache()


def expect_runtime(label, fn):
    try:
        fn()
    except RuntimeError:
        return
    raise AssertionError(f"{label}: expected RuntimeError")


def main():
    engine.MODEL_RETRY_DELAY_SECONDS = 0
    engine.MODEL_RETRY_JITTER_SECONDS = 0
    old_env = apply_env(
        GEMINI_MODEL="primary",
        GEMINI_MODEL_FALLBACKS="fallback-a,fallback-b",
        GEMINI_TTS_MODEL="tts-primary",
        GEMINI_TTS_MODEL_FALLBACKS="tts-fallback",
    )
    try:
        candidates = engine.configured_models("analysis")
        assert candidates[:3] == ["primary", "fallback-a", "fallback-b"]
        assert len(candidates) == len(set(candidates)), "candidate list must be deduped"

        client = FakeClient(
            lambda model, attempt: RuntimeError("503 UNAVAILABLE: high demand")
            if model == "primary" else ANALYSIS_OK)
        assert engine.call_model("analysis", client, build_call, "503 case") is ANALYSIS_OK
        assert client.interactions.calls == ["primary", "fallback-a"]
        assert engine.current_models()["analysis"] == "fallback-a"
        print("PASS: 503 falls back to the next model")

        client = FakeClient(lambda model, attempt: ANALYSIS_OK)
        engine.call_model("analysis", client, build_call, "cache case")
        assert client.interactions.calls[:1] == ["fallback-a"], \
            "the model that worked last time must be tried first"
        engine.reset_model_cache()
        print("PASS: active model is cached and tried first")

        client = FakeClient(
            lambda model, attempt: RuntimeError("404 NOT_FOUND: model no longer available")
            if model == "primary" else ANALYSIS_OK)
        engine.call_model("analysis", client, build_call, "404 case")
        assert client.interactions.calls == ["primary", "fallback-a"]
        engine.reset_model_cache()
        print("PASS: missing model falls back")

        client = FakeClient(
            lambda model, attempt: RuntimeError("DeadlineExceeded: timeout after 30s")
            if model == "primary" else ANALYSIS_OK)
        engine.call_model("analysis", client, build_call, "timeout case")
        assert client.interactions.calls == ["primary", "fallback-a"]
        engine.reset_model_cache()
        print("PASS: timeout falls back")

        import time as _time
        engine.ANALYSIS_TIMEOUT_SECONDS = 0.2
        try:
            def stall_behaviour(model, attempt):
                if model == "primary":
                    _time.sleep(2)
                return ANALYSIS_OK

            client = FakeClient(stall_behaviour)
            started = _time.monotonic()
            engine.call_model("analysis", client, build_call, "stall case")
            elapsed = _time.monotonic() - started
            assert client.interactions.calls == ["primary", "fallback-a"]
            assert elapsed < 1.5, elapsed
        finally:
            engine.ANALYSIS_TIMEOUT_SECONDS = 30
            engine.reset_model_cache()
        print("PASS: a stalled request is abandoned at the hard deadline")

        client = FakeClient(lambda model, attempt: RuntimeError("400 INVALID_ARGUMENT: bad input"))
        expect_runtime("fatal case",
                       lambda: engine.call_model("analysis", client, build_call, "fatal case"))
        assert client.interactions.calls == ["primary"], \
            "client/input errors must not be retried across models"
        engine.reset_model_cache()
        print("PASS: 400 stops immediately without fallback")

        client = FakeClient(lambda model, attempt: RuntimeError("503 UNAVAILABLE: high demand"))
        expect_runtime("all throttled",
                       lambda: engine.call_model("analysis", client, build_call, "all throttled"))
        assert client.interactions.calls == candidates * 2, \
            "throttled candidates get exactly one retry pass"
        assert engine.current_models()["analysis"] is None
        engine.reset_model_cache()
        print("PASS: all-throttled tries every model, then one retry pass")

        with tempfile.TemporaryDirectory() as folder:
            voice = Path(folder) / "voice.wav"

            def tts_behaviour(model, attempt):
                if model == "tts-primary":
                    return RuntimeError("503 UNAVAILABLE: high demand")
                return SimpleNamespace(
                    output_audio=SimpleNamespace(data=base64.b64encode(make_wav()).decode()))

            client = FakeClient(tts_behaviour)
            engine.synth_gemini(client, "Xin chào", voice)
            assert client.interactions.calls == ["tts-primary", "tts-fallback"]
            assert voice.read_bytes()[:4] == b"RIFF"
            assert engine.current_models()["tts"] == "tts-fallback"
        print("PASS: TTS falls back and writes a WAV")

        with tempfile.TemporaryDirectory() as folder:
            voice = Path(folder) / "voice.wav"
            pcm = b"\x01\x00" * 2400
            client = FakeClient(lambda model, attempt: SimpleNamespace(
                output_audio=SimpleNamespace(
                    data=base64.b64encode(pcm).decode(),
                    mime_type="audio/l16; rate=24000; channels=1",
                    sample_rate=24000, channels=1)))
            engine.synth_gemini(client, "Xin chào", voice)
            with wave.open(str(voice)) as written:
                assert written.getnframes() == 2400, written.getnframes()
                assert written.getframerate() == 24000
        print("PASS: raw PCM TTS output is wrapped in a playable WAV")
    finally:
        restore_env(old_env)

    print("ALL FALLBACK TESTS PASSED")


if __name__ == "__main__":
    main()
