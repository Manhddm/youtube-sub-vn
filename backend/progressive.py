"""Progressively translates public YouTube clips into timestamp-aligned WAV chunks.

Translation defaults to the local pipeline: existing English captions are
sliced in memory, so a 20 second window is usually ready in well under a second.
`TRANSLATION_PROVIDER=gemini` keeps the URL-clip path. Subtitle generation and
speech rendering are decoupled, so lookahead chunks carry their cues almost
immediately while the renderer turns them into audio in playback order.
The local cache is resumable across backend restarts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from pydub import AudioSegment

from engine import (
    ANALYSIS_TIMEOUT_SECONDS, PROMPT, JSON_RESPONSE_FORMAT, TTS_TIMEOUT_SECONDS,
    call_model, configured_models, current_models, fit_to_slot, gemini_client,
    group_cues, parse_segments, speech_slots, synthesize_voices, translation_provider,
    tts_provider,
)

CHUNK_SECONDS = max(10, min(60, int(os.getenv("STREAM_CHUNK_SECONDS", "15"))))
MAX_SECONDS = max(60, int(os.getenv("MAX_PROGRESSIVE_SECONDS", "43200")))
LOOKAHEAD = max(2, min(10, int(os.getenv("STREAM_LOOKAHEAD_CHUNKS", "4"))))
LOOKAHEAD_LOCAL = max(LOOKAHEAD, min(120, int(os.getenv("STREAM_LOOKAHEAD_LOCAL_CHUNKS", "12"))))
CHUNK_ATTEMPTS = max(1, min(5, int(os.getenv("STREAM_CHUNK_ATTEMPTS", "3"))))
CHUNK_RETRY_SECONDS = max(0.0, float(os.getenv("STREAM_CHUNK_RETRY_SECONDS", "5")))
TTS_PARALLEL = max(1, min(16, int(os.getenv("STREAM_TTS_PARALLEL", "8"))))


def cache_key(video_id: str, duration: float, mode: str = "dub",
              voice: str | None = None) -> str:
    parts = [
        video_id, f"{duration:.2f}", str(CHUNK_SECONDS),
        translation_provider(),
        os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        tts_provider(),
        os.getenv("GEMINI_VOICE", "Puck"),
        voice or os.getenv("EDGE_VOICE", ""),
        os.getenv("LOCAL_MT_MODEL", ""),
        "progressive-3",
    ]
    # Dub caches keep the default-voice key; subtitles get an isolated folder.
    if mode == "subtitles":
        parts.insert(-1, "subtitles")
    key = "|".join(parts)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def analyze_youtube_window(client: Any, video_id: str, duration: float,
                           begin: float, end: float) -> list[dict]:
    """Absolute cues for [begin, end); local slicing or a Gemini URL clip."""
    if translation_provider() == "local":
        return get_local_pipeline().cues_for_window(video_id, duration, begin, end)
    # A small overlap gives the model context at sentence boundaries. Each
    # line belongs to exactly one chunk, based on its midpoint.
    clip_begin = max(0, int(math.floor(begin - 1)))
    clip_end = min(math.ceil(duration), int(math.ceil(end + 1)))
    prompt = PROMPT + f"""
    You are viewing ONLY a clip of a longer video.
    The clip begins at original time {clip_begin:.2f} seconds and ends at
    original time {clip_end:.2f} seconds.
    Output every speech cue as seconds RELATIVE TO THE CLIPPED INPUT,
    starting at 0; not original absolute YouTube timestamps.
    This chunk's target range in original time is [{begin:.2f}, {end:.2f}).
    Include cues near the edges, even if a sentence crosses an edge.
    JSON only. Empty segments array is correct if there is no speech.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    def build_call(model):
        return {
            "model": model,
            "input": [
                {"type": "video", "uri": url,
                 # Interactions API expects duration STRINGS with an 's' suffix.
                 "processing": {"type": "static", "start_offset": f"{clip_begin}s",
                                "end_offset": f"{clip_end}s"}},
                {"type": "text", "text": prompt},
            ],
            "response_format": JSON_RESPONSE_FORMAT,
        }
    response = call_model("analysis", client, build_call,
                          f"Gemini clip {begin:.0f}-{end:.0f}s")
    payload = json.loads(response.output_text or "{}")
    # Some models may return full-video timestamps in spite of instructions.
    raw = payload.get("segments", [])
    if isinstance(raw, list):
        all_start = [float(c.get("start", 0)) for c in raw if isinstance(c, dict)]
        if all_start and min(all_start) >= clip_begin - .5 and max(all_start) > (clip_end - clip_begin) + 1:
            for c in raw:
                if isinstance(c, dict) and isinstance(c.get("start"), (float, int)):
                    c["start"] -= clip_begin
                    c["end"] -= clip_begin
    cues = parse_segments(payload, clip_begin, clip_end - clip_begin)
    filtered: list[dict] = []
    for c in cues:
        if not begin <= (c["start"] + c["end"]) / 2 < end:
            continue
        filtered.append({**c,
                         "start": max(begin, c["start"]),
                         "end": min(end, c["end"])})
    return filtered


def get_local_pipeline():
    from local_pipeline import get_pipeline
    return get_pipeline()


def render_chunk(tts_client: Any, cues: list[dict], begin: float, end: float,
                 folder: Path, report, voice: str = "") -> int:
    """Render sample-accurate silent+speech WAV for this segment, no MP3 gap."""
    duration_ms = max(1, round((end - begin) * 1000))
    out = AudioSegment.silent(duration=duration_ms, frame_rate=24000)
    out = out.set_channels(1).set_sample_width(2)
    groups = group_cues(sorted(cues, key=lambda x: x["start"]))
    provider = tts_provider()
    slots = speech_slots(groups, begin, duration_ms)
    if not slots:
        out.export(folder / "pending.wav", format="wav")
        return 0
    report(f"Generating speech {len(slots)}/{len(groups)} for {begin:.0f}-{end:.0f}s")
    # A single event loop with an asyncio.Semaphore keeps every cue in flight
    # without a thread per utterance or a fresh loop per voice.
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        scratch = Path(temp)
        suffix = "wav" if provider == "gemini" else "mp3"
        paths = [scratch / f"voice_{slot['index']:03d}.{suffix}" for slot in slots]
        synthesize_voices(list(zip([slot["text"] for slot in slots], paths)),
                          provider, tts_client, voice, TTS_PARALLEL)
        for slot, voice_file in zip(slots, paths):
            piece = fit_to_slot(voice_file, slot["available"], scratch)
            out = out.overlay(piece.set_sample_width(2), position=slot["position"])
    out.export(folder / "pending.wav", format="wav")
    return len(slots)


class ProgressiveJob:
    def __init__(self, video_id: str, duration: float, folder: Path,
                 mode: str = "dub", voice: str = ""):
        self.video_id = video_id
        self.duration = duration
        self.mode = mode
        self.voice = voice or os.getenv("EDGE_VOICE", "vi-VN-NamMinhNeural")
        self.id = folder.name
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.chunk_seconds = CHUNK_SECONDS
        self.count = math.ceil(duration / self.chunk_seconds)
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.playhead = 0
        self.translate_index: int | None = None
        self.render_index: int | None = None
        self.error = ""
        self.closed = False
        self.message = ("Preparing first Vietnamese subtitle..."
                        if mode == "subtitles"
                        else "Preparing first Vietnamese audio buffer...")
        self.cues_ready = [self._cues_valid(i) for i in range(self.count)]
        self.ready = [self.cues_ready[i] and (self.mode == "subtitles" or self._audio_valid(i))
                      for i in range(self.count)]
        self.stats = self._load_stats()
        self._start_worker()

    @property
    def processing_index(self) -> int | None:
        return self.translate_index if self.translate_index is not None else self.render_index

    def _load_stats(self) -> dict:
        defaults = {"chunks_done": 0, "analysis_calls": 0, "tts_calls": 0,
                    "analysis_seconds": 0.0, "tts_seconds": 0.0,
                    "retries": 0, "failures": 0}
        try:
            saved = json.loads((self.folder / "stats.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        for key in defaults:
            if isinstance(saved.get(key), (int, float)):
                defaults[key] = saved[key]
        return defaults

    def _save_stats(self) -> None:
        with self.lock:
            tmp = self.folder / "stats.json.tmp"
            tmp.write_text(json.dumps(self.stats, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.folder / "stats.json")

    def _record_analysis(self, seconds: float, attempt: int) -> None:
        with self.lock:
            self.stats["analysis_calls"] += 1
            self.stats["analysis_seconds"] += seconds
            self.stats["retries"] += max(0, attempt - 1)
            if self.mode == "subtitles":
                self.stats["chunks_done"] += 1
        self._save_stats()

    def _record_tts(self, seconds: float, tts_calls: int) -> None:
        with self.lock:
            self.stats["tts_calls"] += tts_calls
            self.stats["tts_seconds"] += seconds
            self.stats["chunks_done"] += 1
        self._save_stats()

    def _record_failure(self) -> None:
        with self.lock:
            self.stats["failures"] += 1
        self._save_stats()

    def _cues_valid(self, i: int) -> bool:
        return (self.folder / f"chunk_{i:05}.json").exists()

    def _audio_valid(self, i: int) -> bool:
        audio = self.folder / f"chunk_{i:05}.wav"
        return audio.exists() and audio.stat().st_size > 44

    def _chunk_valid(self, i: int) -> bool:
        if not self._cues_valid(i):
            return False
        return self.mode == "subtitles" or self._audio_valid(i)

    def cue_ready(self, i: int) -> bool:
        with self.lock:
            return bool(self.cues_ready[i])

    def _start_worker(self):
        if all(self.ready):
            self.message = ("All Vietnamese subtitle cues are cached."
                            if self.mode == "subtitles"
                            else "All Vietnamese audio chunks are cached.")
            return
        existing = getattr(self, "thread", None)
        if existing is not None and existing.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"dub-{self.id[:8]}")
        self.thread.start()

    def seek(self, seconds: float):
        with self.cv:
            self.playhead = max(0, min(self.count - 1, int(seconds / self.chunk_seconds)))
            self.cv.notify_all()

    def restart_if_failed(self):
        with self.cv:
            if not self.error:
                return
            self.error = ""
            self.message = "Retrying the failed segment..."
            self._start_worker()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        if hasattr(self, "thread") and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        if hasattr(self, "renderer_thread") and self.renderer_thread.is_alive():
            self.renderer_thread.join(timeout=2.0)

    def _window(self) -> int:
        return LOOKAHEAD_LOCAL if translation_provider() == "local" else LOOKAHEAD

    def _next_translation(self) -> int | None:
        # Demand-driven: only translate the next few clips near the viewer.
        for i in range(self.playhead, min(self.count, self.playhead + self._window())):
            if not self.cues_ready[i]:
                return i
        return None

    def _next_render(self) -> int | None:
        for i in range(self.playhead, min(self.count, self.playhead + LOOKAHEAD)):
            if self.cues_ready[i] and not self.ready[i]:
                return i
        return None

    def _run(self):
        if self.mode == "subtitles":
            self._produce()
            return
        self.renderer_thread = threading.Thread(target=self._render, daemon=True,
                                                name=f"tts-{self.id[:8]}")
        self.renderer_thread.start()
        self._produce()

    def _produce(self):
        try:
            client = (None if translation_provider() == "local"
                      else gemini_client(ANALYSIS_TIMEOUT_SECONDS))
            subtitles_only = self.mode == "subtitles"
            while True:
                with self.lock:
                    if self.closed:
                        return
                    index = self._next_translation()
                    self.translate_index = index
                if index is None:
                    with self.cv:
                        if self.closed or self.error:
                            return
                        self.translate_index = None
                        if all(self.cues_ready):
                            if subtitles_only:
                                self.message = "Entire video subtitled and cached."
                                return
                            if all(self.ready):
                                self.message = "Entire video dubbed and cached."
                                return
                            self.cv.wait(timeout=30)
                            continue
                        self.message = ("Buffered subtitles ahead. Waiting until playback advances..."
                                        if subtitles_only else
                                        "Buffered ahead. Waiting until playback advances...")
                        self.cv.wait(timeout=30)
                    continue
                begin = index * self.chunk_seconds
                end = min(self.duration, begin + self.chunk_seconds)
                self._set_message(
                    f"Translating segment {index + 1}/{self.count} ({begin:.0f}-{end:.0f}s)")
                for attempt in range(1, CHUNK_ATTEMPTS + 1):
                    try:
                        started = time.monotonic()
                        cues = analyze_youtube_window(client, self.video_id,
                                                      self.duration, begin, end)
                        elapsed = time.monotonic() - started
                        tmpjson = self.folder / f"chunk_{index:05}.json.tmp"
                        tmpjson.write_text(json.dumps(cues, ensure_ascii=False),
                                           encoding="utf-8")
                        tmpjson.replace(self.folder / f"chunk_{index:05}.json")
                        with self.cv:
                            self.cues_ready[index] = True
                            if subtitles_only:
                                self.ready[index] = True
                            self._record_analysis(elapsed, attempt)
                            self.cv.notify_all()
                        break
                    except Exception as exc:
                        if self.closed:
                            return
                        if attempt >= CHUNK_ATTEMPTS:
                            self._record_failure()
                            raise
                        self._set_message(
                            f"Segment {index + 1} failed (attempt {attempt}/{CHUNK_ATTEMPTS}): "
                            f"{str(exc)[:160]}. Retrying...")
                        time.sleep(CHUNK_RETRY_SECONDS)
                with self.lock:
                    self.message = f"Segment {index + 1}/{self.count} translated; preparing ahead..."
        except Exception as exc:
            with self.cv:
                self.error = str(exc)
                self.message = f"Stream processing error: {exc}"
                self.translate_index = None
                self.cv.notify_all()

    def _render(self):
        try:
            provider = tts_provider()
            tts_client = gemini_client(TTS_TIMEOUT_SECONDS) if provider == "gemini" else None
            while True:
                with self.lock:
                    if self.closed:
                        return
                    if self.error:
                        return
                    index = self._next_render()
                    self.render_index = index
                if index is None:
                    with self.cv:
                        if self.closed or self.error:
                            return
                        self.render_index = None
                        if all(self.ready):
                            self.message = "Entire video dubbed and cached."
                            return
                        if self.translate_index is None:
                            self.message = "Buffered audio ahead. Waiting until playback advances..."
                        self.cv.wait(timeout=30)
                    continue
                begin = index * self.chunk_seconds
                end = min(self.duration, begin + self.chunk_seconds)
                cuefile = self.folder / f"chunk_{index:05}.json"
                for attempt in range(1, CHUNK_ATTEMPTS + 1):
                    try:
                        if self.closed:
                            return
                        cues = json.loads(cuefile.read_text(encoding="utf-8"))
                        started = time.monotonic()
                        speech_calls = render_chunk(tts_client, cues, begin, end,
                                                    self.folder, self._set_message, self.voice)
                        if self.closed:
                            return
                        elapsed = time.monotonic() - started
                        (self.folder / "pending.wav").replace(
                            self.folder / f"chunk_{index:05}.wav")
                        with self.cv:
                            self.ready[index] = True
                            self._record_tts(elapsed, speech_calls)
                            self.cv.notify_all()
                        break
                    except Exception as exc:
                        if self.closed:
                            return
                        if attempt >= CHUNK_ATTEMPTS:
                            self._record_failure()
                            raise
                        self._set_message(
                            f"Voice {index + 1} failed (attempt {attempt}/{CHUNK_ATTEMPTS}): "
                            f"{str(exc)[:160]}. Retrying...")
                        time.sleep(CHUNK_RETRY_SECONDS)
                with self.lock:
                    self.message = f"Chunk {index + 1}/{self.count} ready; translating ahead..."
        except Exception as exc:
            with self.cv:
                self.error = str(exc)
                self.message = f"Voice rendering error: {exc}"
                self.render_index = None
                self.cv.notify_all()

    def _set_message(self, message: str):
        with self.lock:
            self.message = message

    def snapshot(self) -> dict:
        with self.lock:
            current = self.playhead
            from_start = 0
            while from_start < self.count and self.ready[from_start]:
                from_start += 1
            from_playhead = current
            while from_playhead < self.count and self.ready[from_playhead]:
                from_playhead += 1
            ranges = []
            i = 0
            while i < self.count:
                if self.ready[i]:
                    j = i + 1
                    while j < self.count and self.ready[j]:
                        j += 1
                    ranges.append({"start": i * self.chunk_seconds,
                                   "end": min(self.duration, j * self.chunk_seconds)})
                    i = j
                else:
                    i += 1
            models = current_models()
            if translation_provider() == "local":
                models["analysis"] = "local: captions + ctranslate2"
            return {
                "job_id": self.id, "video_id": self.video_id,
                "mode": self.mode,
                "duration": self.duration, "chunk_seconds": self.chunk_seconds,
                "playhead": current * self.chunk_seconds,
                "total_chunks": self.count,
                "ready_chunks": [i for i, value in enumerate(self.ready) if value],
                "cues_ready_chunks": [i for i, value in enumerate(self.cues_ready) if value],
                "completed_chunks": sum(self.ready),
                "playable_until": min(self.duration, from_start * self.chunk_seconds),
                "playhead_buffered_until": min(self.duration, from_playhead * self.chunk_seconds),
                "ranges": ranges,
                "processing_index": self.processing_index,
                "stage": ("error" if self.error else "complete" if all(self.ready)
                          else "buffered" if self.processing_index is None else "processing"),
                "message": self.message,
                "error": self.error,
                "translation_provider": translation_provider(),
                "models": models,
                "model_order": {
                    "analysis": configured_models("analysis"),
                    "tts": configured_models("tts"),
                },
                "tts_provider": "" if self.mode == "subtitles" else self._provider(),
                "tts_voice": ("" if self.mode == "subtitles" else
                              self.voice if self._provider() == "edge"
                              else os.getenv("GEMINI_VOICE", "Puck")),
                "stats": self._stats_snapshot(),
            }

    def _stats_snapshot(self) -> dict:
        stats = dict(self.stats)
        done = stats["chunks_done"]
        total = stats["analysis_seconds"] + stats["tts_seconds"]
        stats["avg_chunk_seconds"] = round(total / done, 2) if done else None
        stats["avg_analysis_seconds"] = (
            round(stats["analysis_seconds"] / done, 2) if done else None)
        stats["avg_tts_seconds"] = (
            round(stats["tts_seconds"] / done, 2) if done else None)
        return stats

    @staticmethod
    def _provider() -> str:
        return tts_provider()
