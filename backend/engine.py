"""Audio -> timestamped Vietnamese subtitles via Gemini -> synced dubbed MP3.

The default configuration uses Gemini for both translation and TTS.
No third-party model weights are needed locally. The API has free-tier quotas.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from pydub import AudioSegment

Progress = Callable[[str, float, str], None]

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

# May 2026 Interactions API: JSON schema is nested under a typed text response.
JSON_RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": SCHEMA,
}

PROMPT = """Listen to this AUDIO CLIP and create Vietnamese dubbing cues.
- Transcribe ALL intelligible speech accurately, even when not English.
- Translate every cue to natural, concise, SPOKEN VIETNAMESE. Preserve names,
  numeric values and technical terms; do not invent missing speech.
- Return start and end as numeric SECONDS relative to the start of THIS CLIP.
- Split into short phrases/sentences (roughly 3-10 seconds each).
- Use the actual audio timeline; include silence between speech cues.
- Never include background music or sound effects as dialogue.
- Do not explain or summarize; return the JSON schema only.
"""

DEFAULT_ANALYSIS_FALLBACKS = (
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)
DEFAULT_TTS_FALLBACKS = (
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
)
ANALYSIS_TIMEOUT_SECONDS = float(os.getenv("GEMINI_ANALYSIS_TIMEOUT_SECONDS", "30"))
TTS_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TTS_TIMEOUT_SECONDS", "20"))
MODEL_RETRY_DELAY_SECONDS = float(os.getenv("GEMINI_RETRY_DELAY_SECONDS", "2"))
MODEL_RETRY_JITTER_SECONDS = float(os.getenv("GEMINI_RETRY_JITTER_SECONDS", "1"))

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict[str, str | None] = {"analysis": None, "tts": None}


def configured_models(role: str) -> list[str]:
    if role == "analysis":
        primary_env, fallback_env, defaults = (
            "GEMINI_MODEL", "GEMINI_MODEL_FALLBACKS", DEFAULT_ANALYSIS_FALLBACKS)
    elif role == "tts":
        primary_env, fallback_env, defaults = (
            "GEMINI_TTS_MODEL", "GEMINI_TTS_MODEL_FALLBACKS", DEFAULT_TTS_FALLBACKS)
    else:
        raise ValueError(f"Unknown model role: {role}")
    values: list[str] = []
    primary = os.getenv(primary_env, "").strip()
    if primary:
        values.append(primary)
    values.extend(
        value.strip()
        for value in os.getenv(fallback_env, "").split(",")
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


def _candidate_order(role: str) -> list[str]:
    candidates = configured_models(role)
    with _MODEL_LOCK:
        current = _MODEL_CACHE.get(role)
    if current:
        candidates = [current] + [model for model in candidates if model != current]
    return candidates


def classify_model_error(exc: Exception) -> str:
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    if any(token in text for token in (
            "api key not valid", "invalid api key", "unauthenticated",
            "permission_denied", "401", "403")):
        return "fatal"
    if "404" in text or "not found" in text or "no longer available" in text:
        return "model_missing"
    if any(token in text for token in ("429", "resource_exhausted", "quota", "rate limit")):
        return "throttled"
    if any(token in text for token in (
            "503", "502", "500", "504", "unavailable", "high demand",
            "overloaded", "service_unavailable")):
        return "unavailable"
    if "400" in text or "invalid_argument" in text or "invalid argument" in text:
        return "fatal"
    if any(token in text for token in ("timeout", "timed out", "deadline")) or "timeouterror" in name:
        return "transient"
    if "api" in name or "http" in name or "connection" in text or "network" in text:
        return "transient"
    return "fatal"


def current_models() -> dict[str, str | None]:
    with _MODEL_LOCK:
        return dict(_MODEL_CACHE)


def reset_model_cache() -> None:
    with _MODEL_LOCK:
        for key in _MODEL_CACHE:
            _MODEL_CACHE[key] = None


def _role_timeout(role: str) -> float:
    if role == "analysis":
        return ANALYSIS_TIMEOUT_SECONDS
    if role == "tts":
        return TTS_TIMEOUT_SECONDS
    raise ValueError(f"Unknown model role: {role}")


def create_with_deadline(client, body: dict, timeout: float):
    """Hard wall-clock deadline; google-genai 2.34 did not consistently
    enforce its own timeout on slow video requests."""
    outcome: dict[str, object] = {}

    def run():
        try:
            outcome["reply"] = client.interactions.create(**body)
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True, name="gemini-call")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no response within {timeout:.0f}s")
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    return outcome.get("reply")


def call_model(role: str, client, build_call: Callable[[str], dict],
               label: str):
    candidates = _candidate_order(role)
    deadline = _role_timeout(role)
    errors: list[str] = []
    retry_models: list[str] = []
    for model in candidates:
        try:
            reply = create_with_deadline(client, build_call(model), deadline)
        except Exception as exc:
            detail = f"{model}: {str(exc)[:240]}"
            errors.append(detail)
            kind = classify_model_error(exc)
            if kind == "fatal":
                raise RuntimeError(f"{label}: {detail}") from exc
            if kind in ("throttled", "unavailable"):
                retry_models.append(model)
            continue
        with _MODEL_LOCK:
            _MODEL_CACHE[role] = model
        return reply
    if retry_models:
        time.sleep(MODEL_RETRY_DELAY_SECONDS + random.uniform(0, MODEL_RETRY_JITTER_SECONDS))
        for model in retry_models:
            try:
                reply = create_with_deadline(client, build_call(model), deadline)
            except Exception as exc:
                errors.append(f"{model}: {str(exc)[:240]}")
                continue
            with _MODEL_LOCK:
                _MODEL_CACHE[role] = model
            return reply
    with _MODEL_LOCK:
        _MODEL_CACHE[role] = None
    raise RuntimeError(f"{label}: all models failed ({'; '.join(errors[-4:])})")


def _run(args: list[str]) -> None:
    done = subprocess.run(args, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"FFmpeg failed: {done.stderr[-650:]}")


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def prepare_chunks(capture: Path, work: Path) -> tuple[list[Path], float]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe must be installed and available in PATH")
    # Preserve the whole timeline, including pauses, while reducing upload size.
    chunk_s = max(30, min(180, int(os.getenv("CHUNK_SECONDS", "90"))))
    chunk_dir = work / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    pattern = chunk_dir / "part_%03d.mp3"
    _run(["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-i", str(capture),
          "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
          "-f", "segment", "-segment_time", str(chunk_s),
          "-reset_timestamps", "1", str(pattern)])
    pieces = sorted(chunk_dir.glob("part_*.mp3"))
    if not pieces:
        raise RuntimeError("The recording contains no usable audio")
    # Sum measured durations rather than assume chunk boundaries are exact.
    duration = sum(ffprobe_duration(p) for p in pieces)
    return pieces, duration


def gemini_client(timeout_seconds: float | None = None):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or key.startswith("PASTE_"):
        raise RuntimeError("Missing GEMINI_API_KEY in backend/.env")
    from google import genai
    from google.genai import types
    budget = ANALYSIS_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    # The SDK retries 503s internally by default, and its timeouts proved
    # unreliable on slow video requests; call_model enforces a hard deadline.
    # This client-level budget still bounds direct calls such as file uploads.
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=max(1000, int(budget * 1000)),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def request_retry(fn, label: str, attempts: int = 4):
    """Limited exponential backoff for temporary throttling/network failures."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            detail = str(exc)
            retryable = any(code in detail.lower() for code in (
                "429", "resource_exhausted", "503", "unavailable", "deadline", "timeout"))
            if (not retryable) or i == attempts - 1:
                raise RuntimeError(
                    f"{label}: {detail}. Check Google AI Studio free-tier quotas/model access."
                ) from exc
            time.sleep(min(40, 5 * (2 ** i)))


def parse_segments(body: dict, offset: float, duration: float) -> list[dict]:
    raw = body.get("segments")
    if not isinstance(raw, list):
        raise ValueError("Gemini returned JSON without a 'segments' array")
    output = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        try:
            start = max(0.0, min(float(value["start"]), duration))
            end = max(start + 0.1, min(float(value["end"]), duration))
        except (KeyError, TypeError, ValueError):
            continue
        if start >= duration:
            continue
        orig = str(value.get("original") or "").strip()
        vi = str(value.get("vi") or "").strip()
        if not vi or not orig:
            continue
        output.append({
            "start": round(start + offset, 3), "end": round(end + offset, 3),
            "original": orig, "vi": vi,
        })
    return sorted(output, key=lambda item: item["start"])


def analyze_audio(client, pieces: list[Path], report: Progress) -> list[dict]:
    all_segments: list[dict] = []
    offset = 0.0
    for index, chunk in enumerate(pieces):
        measured = ffprobe_duration(chunk)
        report("gemini", 0.06 + 0.44 * index / len(pieces),
               f"Gemini analyzing audio {index + 1}/{len(pieces)}...")
        uploaded = request_retry(lambda: client.files.upload(file=str(chunk)), "Upload audio")
        try:
            def build_call(model, uploaded=uploaded):
                return {
                    "model": model,
                    "input": [
                        {"type": "text", "text": PROMPT},
                        {"type": "audio", "uri": uploaded.uri,
                         "mime_type": uploaded.mime_type or "audio/mpeg"},
                    ],
                    "response_format": JSON_RESPONSE_FORMAT,
                }
            reply = call_model("analysis", client, build_call,
                               f"Gemini audio analysis {index + 1}/{len(pieces)}")
            text = reply.output_text or ""
            parsed = json.loads(text)
            all_segments.extend(parse_segments(parsed, offset, measured))
        finally:
            # Remove audio from Google's temporary Files API as soon as possible.
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        offset += measured
    report("gemini", 0.5, f"Transcribed and translated {len(all_segments)} segments")
    if not all_segments:
        raise RuntimeError("Gemini found no speech. Check the video has audible speech.")
    return sorted(all_segments, key=lambda item: item["start"])



def analyze_public_youtube(client, video_id: str, duration: float, report: Progress) -> list[dict]:
    """Public videos can be submitted by URL without extracting/downloading media."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    report("gemini", 0.12, "Gemini is reading the public YouTube URL...")
    prompt = PROMPT + f"""
This is a complete public YouTube video, approximately {duration:.1f} seconds long.
Use timestamps relative to the start of the entire video.
Transcribe EVERY intelligible sentence in time order; do NOT merely summarize
selected moments. Include speech throughout the beginning, middle and end.
Return a full list of original speech and its Vietnamese translations.
"""
    def build_call(model):
        return {
            "model": model,
            "input": [
                {"type": "text", "text": prompt},
                {"type": "video", "uri": url},
            ],
            "response_format": JSON_RESPONSE_FORMAT,
        }
    reply = call_model("analysis", client, build_call,
                       "Gemini YouTube URL analysis")
    data = json.loads(reply.output_text or "{}")
    cues = parse_segments(data, 0, duration)
    if not cues:
        raise RuntimeError("Gemini returned no spoken cues for this public YouTube video")
    report("gemini", 0.5, f"Translated {len(cues)} spoken cues from YouTube")
    return cues


def process_youtube_url(video_id: str, duration: float, work: Path, report: Progress) -> dict:
    report("preparing", 0.02, "Sending the public YouTube URL to Gemini...")
    client = gemini_client(ANALYSIS_TIMEOUT_SECONDS)
    cues = analyze_public_youtube(client, video_id, duration, report)
    (work / "transcript.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(cues, work / "subtitles.vi.srt")
    build_dub(cues, duration, work, report)
    report("ready", 1.0, "Gemini direct YouTube dubbing is ready")
    return {"segments": len(cues), "duration": round(duration, 2), "input_mode": "youtube_url"}

def srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hh, ms = divmod(ms, 3600000)
    mm, ms = divmod(ms, 60000)
    ss, ms = divmod(ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def write_srt(cues: list[dict], path: Path) -> None:
    rows = []
    for idx, cue in enumerate(cues, 1):
        rows.append(f"{idx}\n{srt_time(cue['start'])} --> {srt_time(cue['end'])}\n{cue['vi']}\n")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")


def group_cues(cues: list[dict]) -> list[dict]:
    """Reduce TTS requests, at the cost of less fine-grained spoken timing."""
    groups = []
    for cue in cues:
        if groups:
            last = groups[-1]
            gap = cue["start"] - last["end"]
            span = cue["end"] - last["start"]
            if 0 <= gap <= 0.40 and span <= 13.0 and len(last["vi"]) + len(cue["vi"]) < 230:
                last["end"] = cue["end"]
                last["vi"] += " " + cue["vi"]
                continue
        groups.append({"start": cue["start"], "end": cue["end"], "vi": cue["vi"]})
    return groups


def synth_gemini(client, text: str, path: Path) -> None:
    voice = os.getenv("GEMINI_VOICE", "Puck")
    def build_call(model):
        # Keep the payload free of speech_metadata annotations: the 2.5/3.1 TTS
        # fallbacks reject them with HTTP 400, which would abort the job.
        return {
            "model": model,
            "input": [{
                "type": "user_input",
                "content": [{"type": "text", "text": text}],
            }],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]},
        }
    reply = call_model("tts", client, build_call,
                       "Gemini speech synthesis")
    audio = reply.output_audio
    if not audio or not audio.data:
        raise RuntimeError("Gemini TTS returned an empty audio response")
    raw = base64.b64decode(audio.data)
    mime = (getattr(audio, "mime_type", "") or "").lower()
    if raw.startswith(b"RIFF"):
        path.write_bytes(raw)
        return
    if mime.startswith(("audio/l16", "audio/pcm")):
        # Some TTS fallbacks return headerless little-endian PCM; pydub needs a
        # WAV container, so wrap the samples instead of writing them verbatim.
        rate = getattr(audio, "sample_rate", None) or 24000
        channels = getattr(audio, "channels", None) or 1
        with wave.open(str(path), "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(raw)
        return
    path.write_bytes(raw)


def synth_edge(text: str, path: Path) -> None:
    import edge_tts
    voice = os.getenv("EDGE_VOICE", "vi-VN-NamMinhNeural")
    asyncio.run(edge_tts.Communicate(text, voice).save(str(path)))


def atempo_chain(rate: float) -> str:
    factors = []
    while rate > 2:
        factors.append(2.0)
        rate /= 2
    factors.append(rate)
    return ",".join(f"atempo={value:.5f}" for value in factors)


def fit_to_slot(source_file: Path, max_ms: int, work: Path) -> AudioSegment:
    src = AudioSegment.from_file(source_file).set_channels(1).set_frame_rate(24000)
    if len(src) <= max_ms:
        return src
    factor = len(src) / max_ms
    # Stretch the voice without pitch change; very large compressions are clipped.
    rate = min(factor, 2.5)
    output = work / "short.wav"
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_file),
          "-af", atempo_chain(rate), "-ac", "1", "-ar", "24000", str(output)])
    return AudioSegment.from_file(output)[:max_ms]


def build_dub(cues: list[dict], duration: float, work: Path, report: Progress) -> None:
    provider = os.getenv("TTS_PROVIDER", "gemini").lower().strip()
    if provider not in {"gemini", "edge"}:
        raise ValueError("TTS_PROVIDER must be 'gemini' or 'edge'")
    tts_client = gemini_client(TTS_TIMEOUT_SECONDS) if provider == "gemini" else None
    groups = group_cues(cues)
    total_ms = max(1000, math.ceil(duration * 1000))
    result = AudioSegment.silent(duration=total_ms, frame_rate=24000).set_channels(1)
    for i, group in enumerate(groups):
        start = min(total_ms, max(0, round(group["start"] * 1000)))
        next_start = (max(0, round(groups[i+1]["start"] * 1000))
                      if i+1 < len(groups) else total_ms)
        available = min(total_ms - start, next_start - start - 35)
        if available < 150:
            continue
        report("tts", 0.52 + 0.44 * i / len(groups),
               f"{provider.upper()} generating voice {i + 1}/{len(groups)}...")
        with tempfile.TemporaryDirectory(dir=work) as temp:
            folder = Path(temp)
            voice_file = folder / ("line.wav" if provider == "gemini" else "line.mp3")
            if provider == "gemini":
                synth_gemini(tts_client, group["vi"], voice_file)
            else:
                synth_edge(group["vi"], voice_file)
            result = result.overlay(fit_to_slot(voice_file, available, folder), position=start)
    # MP3 for seamless seek/playback from a YouTube content script.
    result.export(str(work / "dub.mp3"), format="mp3", bitrate="80k")
    report("encoding", 0.98, "Dubbed MP3 rendered")


def process_recording(capture: Path, work: Path, report: Progress) -> dict:
    report("preparing", 0.02, "Preparing captured audio with FFmpeg...")
    pieces, duration = prepare_chunks(capture, work)
    max_seconds = float(os.getenv("MAX_VIDEO_SECONDS", "600"))
    if duration > max_seconds + 2:
        raise RuntimeError(f"Recording is longer than the {max_seconds:g} second demo limit")
    client = gemini_client(ANALYSIS_TIMEOUT_SECONDS)
    cues = analyze_audio(client, pieces, report)
    (work / "transcript.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_srt(cues, work / "subtitles.vi.srt")
    build_dub(cues, duration, work, report)
    report("ready", 1.0, "Vietnamese dub and subtitles are ready")
    return {"segments": len(cues), "duration": round(duration, 2)}
