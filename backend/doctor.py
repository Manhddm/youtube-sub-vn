r"""Diagnose Gemini credentials, model availability, TTS and video analysis.

Examples:
  .\.venv\Scripts\python.exe doctor.py
  .\.venv\Scripts\python.exe doctor.py --no-live
  .\.venv\Scripts\python.exe doctor.py --video-id jNQXAC9IVRw --clip-seconds 10
  .\.venv\Scripts\python.exe doctor.py --models gemini-3.8-flash,gemini-3.7-flash

The script never prints the API key. Every live call has an explicit timeout.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv

from engine import create_with_deadline, synth_edge

API = "https://generativelanguage.googleapis.com/v1beta"
ROOT = Path(__file__).resolve().parent

# A 19-second public video used only to verify that the clip API is reachable.
DEFAULT_VIDEO_ID = "jNQXAC9IVRw"
DEFAULT_ANALYSIS_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
)
DEFAULT_TTS_MODELS = (
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "original": {"type": "string"},
                    "vi": {"type": "string"},
                },
                "required": ["start", "end", "original", "vi"],
            },
        }
    },
    "required": ["segments"],
}
RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": SCHEMA,
}
VIDEO_PROMPT = """This is a short diagnostic clip from a public YouTube video.
Return JSON only with this shape:
{"segments":[{"start":0.0,"end":1.0,"original":"spoken words","vi":"Vietnamese translation"}]}
Use seconds relative to this clip. Return {"segments":[]} when there is no speech.
"""
TTS_TEXT = "Xin chào, đây là kiểm tra giọng đọc tiếng Việt."


def label(key: str) -> str:
    if key.startswith("AQ."):
        return "Auth key (AQ.)"
    if key.startswith("AIza"):
        return "Standard key (AIza)"
    if not key:
        return "MISSING"
    return "Unrecognized prefix"


def redact(text: str, key: str, limit: int = 320) -> str:
    return str(text).replace(key, "[KEY REDACTED]")[:limit].replace("\n", " ")


def print_error(response: requests.Response, key: str) -> None:
    try:
        root = response.json()
    except ValueError:
        print("  Response was not JSON.")
        return
    error = root.get("error", root) if isinstance(root, dict) else {}
    if not isinstance(error, dict):
        error = {}
    reasons = [
        entry.get("reason")
        for entry in error.get("details", [])
        if isinstance(entry, dict) and entry.get("reason")
    ]
    print("  Status:", error.get("status", "unknown"))
    print("  Reason:", ", ".join(reasons) or "not provided")
    print("  Message:", redact(str(error.get("message", "")), key) or "not provided")


def load_key() -> tuple[str, str, str | None]:
    load_dotenv(ROOT / ".env")
    file_key = str(dotenv_values(ROOT / ".env", encoding="utf-8-sig").get("GEMINI_API_KEY") or "").strip()
    os_key = os.environ.get("GEMINI_API_KEY")
    active = (os_key if os_key is not None else file_key).strip()
    return active, file_key, os_key


def env_models(primary_name: str, fallback_name: str, defaults: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    primary = os.getenv(primary_name, "").strip()
    if primary:
        values.append(primary)
    values.extend(
        value.strip()
        for value in os.getenv(fallback_name, "").split(",")
        if value.strip()
    )
    values.extend(defaults)
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def parse_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def model_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for model in payload.get("models", []):
        name = str(model.get("name", ""))
        names.add(name.removeprefix("models/"))
    return names


def probe_tts(client, model: str, voice: str, timeout: float, key: str) -> tuple[bool, str, float]:
    started = time.monotonic()
    body = {
        "model": model,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": TTS_TEXT,
            }],
        }],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    try:
        reply = create_with_deadline(client, body, timeout)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {redact(exc, key)}", time.monotonic() - started

    audio_obj = getattr(reply, "output_audio", None)
    audio = getattr(audio_obj, "data", None)
    if not audio:
        return False, "empty output_audio", time.monotonic() - started
    mime = (getattr(audio_obj, "mime_type", "") or "").lower()
    try:
        raw = base64.b64decode(audio) if isinstance(audio, str) else bytes(audio)
    except Exception as exc:
        return False, f"audio decode failed: {redact(exc, key)}", time.monotonic() - started

    detail = f"{len(raw)} bytes"
    if raw[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(raw), "rb") as wav:
                seconds = wav.getnframes() / wav.getframerate() if wav.getframerate() else 0
                detail += f", WAV {wav.getframerate()} Hz {wav.getnchannels()} ch {seconds:.2f}s"
        except Exception:
            detail += ", WAV header not readable"
    elif mime.startswith(("audio/l16", "audio/pcm")):
        rate = getattr(audio_obj, "sample_rate", None) or 24000
        channels = getattr(audio_obj, "channels", None) or 1
        seconds = len(raw) / (rate * channels * 2)
        detail += f", PCM {rate} Hz {channels} ch {seconds:.2f}s"
    else:
        detail += f", magic={raw[:4]!r}"
    return True, detail, time.monotonic() - started


def probe_edge(timeout: float) -> tuple[bool, str, float]:
    started = time.monotonic()
    voice = os.getenv("EDGE_VOICE", "vi-VN-NamMinhNeural")
    outcome: dict = {}

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "edge.mp3"

        def run():
            try:
                synth_edge(TTS_TEXT, path)
                outcome["size"] = path.stat().st_size if path.exists() else 0
            except Exception as exc:
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            return False, f"TimeoutError: no response within {timeout:g}s", time.monotonic() - started
        if "error" in outcome:
            return False, outcome["error"], time.monotonic() - started
        if not outcome.get("size"):
            return False, "empty MP3 output", time.monotonic() - started
        return True, f"{outcome['size']} bytes MP3, voice {voice}", time.monotonic() - started


def probe_video(client, model: str, video_id: str, clip_seconds: int,
                timeout: float, key: str) -> tuple[bool, str, float]:
    started = time.monotonic()
    body = {
        "model": model,
        "input": [
            {
                "type": "video",
                "uri": f"https://www.youtube.com/watch?v={video_id}",
                "processing": {
                    "type": "static",
                    "start_offset": "0s",
                    "end_offset": f"{clip_seconds}s",
                },
            },
            {"type": "text", "text": VIDEO_PROMPT},
        ],
        "response_format": RESPONSE_FORMAT,
    }
    try:
        reply = create_with_deadline(client, body, timeout)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {redact(exc, key)}", time.monotonic() - started

    text = (getattr(reply, "output_text", "") or "").strip()
    if not text:
        return False, "empty output_text", time.monotonic() - started
    try:
        payload = json.loads(text)
    except ValueError:
        return False, f"invalid JSON: {redact(text, key, 160)}", time.monotonic() - started
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return False, f"missing segments array: {redact(text, key, 120)}", time.monotonic() - started
    status = getattr(reply, "status", None)
    return True, f"{len(segments)} segments; status={status}", time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini V2 backend diagnostic")
    parser.add_argument("--no-live", action="store_true", help="only check key and model list")
    parser.add_argument("--video-id", default=DEFAULT_VIDEO_ID,
                        help=f"public YouTube video ID for the analysis probe (default: {DEFAULT_VIDEO_ID})")
    parser.add_argument("--clip-seconds", type=int, default=10,
                        help="clip length for the video probe (default: 10)")
    parser.add_argument("--models", default="", help="comma-separated analysis model candidates")
    parser.add_argument("--tts-models", default="", help="comma-separated TTS model candidates")
    parser.add_argument("--timeout", type=float, default=25.0, help="video call timeout in seconds")
    parser.add_argument("--tts-timeout", type=float, default=20.0, help="TTS call timeout in seconds")
    args = parser.parse_args()

    print("Gemini V2 doctor")
    key, file_key, os_key = load_key()
    print(".env key type:", label(file_key), "| length:", len(file_key))
    print("Key selected by backend:", label(key), "| length:", len(key))
    if os_key is not None and os_key.strip() != file_key:
        print("WARNING: OS environment GEMINI_API_KEY overrides .env with a different value.")
    if not key or key.startswith("PASTE_"):
        print("ERROR: GEMINI_API_KEY is empty or still the template placeholder.")
        return 1
    if file_key != file_key.strip("\"'"):
        print("WARNING: .env key contains a literal quotation mark at the edge.")

    print("\n[1] Auth and model list")
    try:
        response = requests.get(
            f"{API}/models",
            headers={"x-goog-api-key": key},
            timeout=20,
        )
    except requests.RequestException as exc:
        print("Network error:", redact(exc, key))
        return 1
    print("  HTTP:", response.status_code)
    if response.status_code != 200:
        print_error(response, key)
        return 1
    names = model_names(response.json())
    print("  Models visible:", len(names))

    analysis_models = parse_list(args.models) or env_models(
        "GEMINI_MODEL", "GEMINI_MODEL_FALLBACKS", DEFAULT_ANALYSIS_MODELS)
    tts_models = parse_list(args.tts_models) or env_models(
        "GEMINI_TTS_MODEL", "GEMINI_TTS_MODEL_FALLBACKS", DEFAULT_TTS_MODELS)
    print("  Analysis candidates:",
          ", ".join(f"{name}{'' if name in names else ' (not listed)'}" for name in analysis_models))
    print("  TTS candidates:",
          ", ".join(f"{name}{'' if name in names else ' (not listed)'}" for name in tts_models))

    if args.no_live:
        print("\n[2] Live probes skipped (--no-live)")
        print("\nRESULT: auth and model list OK; live model access not verified.")
        return 0

    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", args.video_id):
        print("ERROR: invalid YouTube video ID for the probe.")
        return 1
    if not 1 <= args.clip_seconds <= 30:
        print("ERROR: --clip-seconds must be between 1 and 30.")
        return 1

    from google import genai
    from google.genai import types

    def make_client(timeout: float):
        # The client-level timeout is the one the SDK enforces on slow video
        # requests. Retries stay off so every probe is a single real attempt.
        return genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    tts_client = make_client(args.tts_timeout)
    client = make_client(args.timeout)

    provider = os.getenv("TTS_PROVIDER", "gemini").strip().lower()
    print(f"\n[2] TTS live probe (provider={provider})")
    tts_ok = None
    if provider == "edge":
        ok, detail, elapsed = probe_edge(args.tts_timeout)
        print(f"  {'OK  ' if ok else 'FAIL'} edge: {detail} ({elapsed:.1f}s)")
        if ok:
            tts_ok = "edge"
    else:
        for model in tts_models:
            if model not in names:
                print(f"  SKIP {model}: not listed by /models")
                continue
            ok, detail, elapsed = probe_tts(
                tts_client, model, os.getenv("GEMINI_VOICE", "Puck"), args.tts_timeout, key)
            print(f"  {'OK  ' if ok else 'FAIL'} {model}: {detail} ({elapsed:.1f}s)")
            if ok:
                tts_ok = model
                break

    print("\n[3] YouTube clip analysis probe")
    print(f"  Diagnostic clip: https://www.youtube.com/watch?v={args.video_id} "
          f"(0-{args.clip_seconds}s)")
    analysis_ok = None
    for model in analysis_models:
        if model not in names:
            print(f"  SKIP {model}: not listed by /models")
            continue
        ok, detail, elapsed = probe_video(
            client, model, args.video_id, args.clip_seconds, args.timeout, key)
        print(f"  {'OK  ' if ok else 'FAIL'} {model}: {detail} ({elapsed:.1f}s)")
        if ok:
            analysis_ok = model
            break

    print("\nRESULT")
    print("  auth/models: OK")
    print("  TTS:", f"OK ({tts_ok})" if tts_ok else "FAIL (no working candidate)")
    print("  video analysis:", f"OK ({analysis_ok})" if analysis_ok else "FAIL/BLOCKED (no working candidate)")
    if tts_ok or analysis_ok:
        print("\nSuggested .env values:")
        if analysis_ok:
            print(f"GEMINI_MODEL={analysis_ok}")
        if tts_ok == "edge":
            print("TTS_PROVIDER=edge")
            print(f"EDGE_VOICE={os.getenv('EDGE_VOICE', 'vi-VN-NamMinhNeural')}")
        elif tts_ok:
            print("TTS_PROVIDER=gemini")
            print(f"GEMINI_TTS_MODEL={tts_ok}")
    if not analysis_ok:
        print("Next: retry later; if all models stay unavailable, use the fallback policy in PLAN.md T3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
