"""Progressively translates public YouTube clips into timestamp-aligned WAV chunks.

Gemini is invoked on small YouTube URL clip windows, not on the full video.
The local cache is resumable across backend restarts. This is an experimental
implementation: actual model access and YouTube clipping must be tested with
an eligible Gemini API key. No YouTube media downloading or scraping is used.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pydub import AudioSegment

from engine import (
    PROMPT, JSON_RESPONSE_FORMAT, _run, fit_to_slot, gemini_client, group_cues,
    parse_segments, request_retry, synth_edge, synth_gemini,
)

CHUNK_SECONDS = max(10, min(60, int(os.getenv("STREAM_CHUNK_SECONDS", "20"))))
MAX_SECONDS = max(60, int(os.getenv("MAX_PROGRESSIVE_SECONDS", "43200")))
LOOKAHEAD = max(2, min(10, int(os.getenv("STREAM_LOOKAHEAD_CHUNKS", "4"))))


def cache_key(video_id: str, duration: float) -> str:
    key = "|".join((
        video_id, f"{duration:.2f}", str(CHUNK_SECONDS),
        os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        os.getenv("TTS_PROVIDER", "gemini"),
        os.getenv("GEMINI_VOICE", "Puck"), os.getenv("EDGE_VOICE", ""),
        "progressive-2",
    ))
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def analyze_youtube_window(client: Any, video_id: str, duration: float,
                           begin: float, end: float) -> list[dict]:
    """Ask Gemini to process a small clipped URL; output ABSOLUTE cues."""
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
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://www.youtube.com/watch?v={video_id}"
    def call():
        return client.interactions.create(
            model=model,
            input=[
                {"type": "video", "uri": url,
                 # Interactions API expects duration STRINGS with an 's' suffix.
                 "processing": {"type": "static", "start_offset": f"{clip_begin}s",
                                "end_offset": f"{clip_end}s"}},
                {"type": "text", "text": prompt},
            ],
            response_format=JSON_RESPONSE_FORMAT,
        )
    response = request_retry(call, f"Gemini clip {begin:.0f}-{end:.0f}s", attempts=3)
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


def render_chunk(client: Any, cues: list[dict], begin: float, end: float,
                 folder: Path, report) -> None:
    """Render sample-accurate silent+speech WAV for this segment, no MP3 gap."""
    duration_ms = max(1, round((end - begin) * 1000))
    out = AudioSegment.silent(duration=duration_ms, frame_rate=24000)
    out = out.set_channels(1).set_sample_width(2)
    groups = group_cues(sorted(cues, key=lambda x: x["start"]))
    provider = os.getenv("TTS_PROVIDER", "gemini").strip().lower()
    if provider not in ("gemini", "edge"):
        raise RuntimeError("TTS_PROVIDER must be gemini or edge")
    for n, cue in enumerate(groups):
        position = max(0, round((cue["start"] - begin) * 1000))
        next_start = (round((groups[n + 1]["start"] - begin) * 1000)
                      if n + 1 < len(groups) else duration_ms)
        available = min(duration_ms - position, next_start - position - 25)
        if available < 150:
            continue
        report(f"Generating speech {n + 1}/{len(groups)} for {begin:.0f}-{end:.0f}s")
        with tempfile.TemporaryDirectory(dir=folder) as temp:
            d = Path(temp)
            wav = d / ("voice.wav" if provider == "gemini" else "voice.mp3")
            if provider == "gemini":
                synth_gemini(client, cue["vi"], wav)
            else:
                synth_edge(cue["vi"], wav)
            piece = fit_to_slot(wav, available, d).set_sample_width(2)
            out = out.overlay(piece, position=position)
    out.export(folder / "pending.wav", format="wav")


class ProgressiveJob:
    def __init__(self, video_id: str, duration: float, folder: Path):
        self.video_id = video_id
        self.duration = duration
        self.id = folder.name
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.chunk_seconds = CHUNK_SECONDS
        self.count = math.ceil(duration / self.chunk_seconds)
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.playhead = 0
        self.processing_index: int | None = None
        self.error = ""
        self.closed = False
        self.message = "Preparing first Vietnamese audio buffer..."
        self.ready = [self._chunk_valid(i) for i in range(self.count)]
        self._start_worker()

    def _chunk_valid(self, i: int) -> bool:
        audio = self.folder / f"chunk_{i:05}.wav"
        cues = self.folder / f"chunk_{i:05}.json"
        return audio.exists() and audio.stat().st_size > 44 and cues.exists()

    def _start_worker(self):
        if all(self.ready):
            self.message = "All Vietnamese audio chunks are cached."
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
            self.message = "Retrying failed Gemini segment..."
            self._start_worker()

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()

    def _next(self) -> int | None:
        # Demand-driven: only spend API quota on the next few clips near where
        # the viewer currently is. Do not process a 6h video unnecessarily.
        for i in range(self.playhead, min(self.count, self.playhead + LOOKAHEAD)):
            if not self.ready[i]:
                return i
        return None

    def _run(self):
        try:
            client = gemini_client()
            while True:
                with self.lock:
                    if self.closed:
                        return
                    index = self._next()
                    self.processing_index = index
                if index is None:
                    with self.cv:
                        if self.closed:
                            return
                        self.processing_index = None
                        if all(self.ready):
                            self.message = "Entire video dubbed and cached."
                            return
                        self.message = "Buffered ahead. Waiting until playback advances..."
                        self.cv.wait(timeout=30)
                    continue
                begin = index * self.chunk_seconds
                end = min(self.duration, begin + self.chunk_seconds)
                with self.lock:
                    self.message = f"Gemini translating segment {index + 1}/{self.count} ({begin:.0f}-{end:.0f}s)"
                cues = analyze_youtube_window(client, self.video_id, self.duration, begin, end)
                partial = self.folder / "pending.wav"
                render_chunk(client, cues, begin, end, self.folder,
                             lambda msg: self._set_message(msg))
                # Publish audio and cues only after BOTH are completely written.
                cuefile = self.folder / f"chunk_{index:05}.json"
                tmpjson = self.folder / f"chunk_{index:05}.json.tmp"
                tmpjson.write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
                partial.replace(self.folder / f"chunk_{index:05}.wav")
                tmpjson.replace(cuefile)
                with self.lock:
                    self.ready[index] = True
                    self.message = f"Chunk {index + 1}/{self.count} ready; translating ahead..."
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.message = f"Stream processing error: {exc}"
                self.processing_index = None

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
            return {
                "job_id": self.id, "video_id": self.video_id,
                "duration": self.duration, "chunk_seconds": self.chunk_seconds,
                "playhead": current * self.chunk_seconds,
                "total_chunks": self.count,
                "ready_chunks": [i for i, value in enumerate(self.ready) if value],
                "completed_chunks": sum(self.ready),
                "playable_until": min(self.duration, from_start * self.chunk_seconds),
                "playhead_buffered_until": min(self.duration, from_playhead * self.chunk_seconds),
                "ranges": ranges,
                "processing_index": self.processing_index,
                "stage": ("error" if self.error else "complete" if all(self.ready)
                          else "buffered" if self.processing_index is None else "processing"),
                "message": self.message,
                "error": self.error,
            }
