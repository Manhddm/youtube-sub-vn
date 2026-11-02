"""Local English-to-Vietnamese speech pipeline (no API key, no quota).

Fast path: the English captions YouTube already publishes for a video are
fetched once and cached on disk; a 20 second window is then sliced purely in
memory. Fallback: yt-dlp downloads the audio stream and faster-whisper
transcribes only the requested window. Translation runs on CTranslate2 with an
int8-quantized Marian model that is converted from Hugging Face on first use.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.getenv("LOCAL_CACHE_DIR") or (BACKEND_DIR / ".model_cache"))
TRANSCRIPT_DIR = CACHE_DIR / "transcripts"
AUDIO_DIR = CACHE_DIR / "audio"
CT2_DIR = CACHE_DIR / "ct2"
WHISPER_DIR = CACHE_DIR / "whisper"

CAPTION_LANGUAGES = ("en", "en-US", "en-GB")
DEFAULT_MT_MODELS = ("Helsinki-NLP/opus-mt-en-vi", "vinai/vinai-translate-en2vi-v2")
NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|>>|<[^>]*>|&gt;&gt;")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
MEMO_LIMIT = 8192
MAX_TOKENS = 480


def translation_provider() -> str:
    value = (os.getenv("TRANSLATION_PROVIDER") or "local").strip().lower()
    return value if value in ("local", "gemini") else "local"


def _int_env(name: str, default: int, low: int = 1, high: int = 4096) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def clean_text(text: str) -> str:
    value = html.unescape(text or "")
    value = NOISE.sub(" ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clip_bounds(begin: float, end: float, duration: float) -> tuple[float, float]:
    return (max(0.0, math.floor(begin - 1)), min(math.ceil(duration), math.ceil(end + 1)))


def midpoint(cue: dict) -> float:
    return (float(cue["start"]) + float(cue["end"])) / 2


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LocalPipeline:
    """Thread-safe singleton: one ASR model and one MT model per process."""

    def __init__(self) -> None:
        self._load_lock = threading.RLock()
        self._mt_lock = threading.Lock()
        self._translator = None
        self._tokenizer = None
        self._mt_model = ""
        self._whisper = None
        self._whisper_name = ""
        self._memo: dict[str, str] = {}
        self._memory: dict[str, list[dict]] = {}
        self._memory_lock = threading.Lock()
        self._stream_urls: dict[str, tuple[str, float]] = {}
        self._stream_url_lock = threading.Lock()

    def mt_model(self) -> str:
        return (os.getenv("LOCAL_MT_MODEL") or "").strip() or DEFAULT_MT_MODELS[0]

    def whisper_model(self) -> str:
        return (os.getenv("LOCAL_WHISPER_MODEL") or "base.en").strip()

    def captions(self, video_id: str) -> list[dict] | None:
        """Existing English captions, cached in memory and on disk."""
        with self._memory_lock:
            cached = self._memory.get(video_id)
        if cached:
            return cached
        path = TRANSCRIPT_DIR / f"{video_id}.en.json"
        saved = _read_json(path)
        if isinstance(saved, list) and saved:
            with self._memory_lock:
                self._memory[video_id] = saved
            return saved
        fetched = self._fetch_captions(video_id)
        if fetched:
            _write_json(path, fetched)
            with self._memory_lock:
                self._memory[video_id] = fetched
        return fetched

    @staticmethod
    def _fetch_captions(video_id: str) -> list[dict] | None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except Exception:
            return None
        rows = None
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=list(CAPTION_LANGUAGES))
            rows = [(float(s.start), float(s.start) + float(s.duration), str(s.text))
                    for s in fetched]
        except AttributeError:
            try:
                legacy = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=list(CAPTION_LANGUAGES))
                rows = [(float(e["start"]), float(e["start"]) + float(e.get("duration", 0)),
                         str(e["text"])) for e in legacy]
            except Exception:
                return None
        except Exception:
            return None
        if not rows:
            return None
        cues: list[dict] = []
        for start, end, text in rows:
            cleaned = clean_text(text)
            if not cleaned:
                continue
            cues.append({"start": round(start, 3), "end": round(max(end, start + 0.4), 3),
                         "text": cleaned})
        return cues or None

    def audio_file(self, video_id: str) -> Path:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        for candidate in sorted(AUDIO_DIR.glob(f"{video_id}.*")):
            if candidate.suffix in (".part", ".ytdl", ".tmp"):
                continue
            if candidate.stat().st_size > 0:
                return candidate
        try:
            import yt_dlp
        except Exception as exc:
            raise RuntimeError(f"yt-dlp is required for local audio fallback: {exc}") from exc
        options = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(AUDIO_DIR / f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 20,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([f"https://www.youtube.com/watch?v={video_id}"])
        for candidate in sorted(AUDIO_DIR.glob(f"{video_id}.*")):
            if candidate.suffix in (".part", ".ytdl", ".tmp"):
                continue
            if candidate.stat().st_size > 0:
                return candidate
        raise RuntimeError("yt-dlp produced no audio stream for this video")

    def _whisper_model(self):
        name = self.whisper_model()
        with self._load_lock:
            if self._whisper is not None and self._whisper_name == name:
                return self._whisper
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                raise RuntimeError(f"faster-whisper is required for ASR fallback: {exc}") from exc
            WHISPER_DIR.mkdir(parents=True, exist_ok=True)
            device = (os.getenv("LOCAL_WHISPER_DEVICE") or "cpu").strip().lower()
            compute = (os.getenv("LOCAL_WHISPER_COMPUTE_TYPE") or "int8").strip()
            self._whisper = WhisperModel(
                name, device=device, compute_type=compute,
                download_root=str(WHISPER_DIR),
                cpu_threads=_int_env("LOCAL_WHISPER_THREADS", os.cpu_count() or 4),
            )
            self._whisper_name = name
            return self._whisper

    def transcribe(self, path: Path, offset: float = 0.0) -> list[dict]:
        model = self._whisper_model()
        segments, _info = model.transcribe(
            str(path), language="en", vad_filter=True, beam_size=1,
            condition_on_previous_text=False,
        )
        cues: list[dict] = []
        for segment in segments:
            text = clean_text(getattr(segment, "text", ""))
            if not text or not re.search(r"[A-Za-z0-9]", text):
                continue
            start = max(0.0, float(segment.start) + offset)
            end = max(start + 0.4, float(segment.end) + offset)
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        return cues

    def _slice_audio(self, source: Path, begin: float, end: float, target: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg must be installed and available in PATH")
        span = max(0.5, end - begin)
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-loglevel", "error",
             "-ss", f"{begin:.3f}", "-t", f"{span:.3f}", "-i", str(source),
             "-vn", "-ac", "1", "-ar", "16000", str(target)],
            capture_output=True, text=True,
        )
        if done.returncode:
            raise RuntimeError(f"FFmpeg failed to slice audio: {done.stderr[-500:]}")

    def _get_stream_url(self, video_id: str) -> str:
        now = time.time()
        with self._stream_url_lock:
            cached = self._stream_urls.get(video_id)
            if cached:
                url, expiry = cached
                if now < expiry:
                    return url
        try:
            import yt_dlp
        except Exception as exc:
            raise RuntimeError(f"yt-dlp is required for local audio fallback: {exc}") from exc
        url = f"https://www.youtube.com/watch?v={video_id}"
        options = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 20,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        stream_url = info.get("url") if isinstance(info, dict) else None
        if not stream_url:
            raise RuntimeError("Could not extract direct audio stream URL")
        with self._stream_url_lock:
            self._stream_urls[video_id] = (stream_url, now + 1800)
        return stream_url

    def extract_window_audio(self, video_id: str, begin: float, end: float, target: Path) -> None:
        stream_url = self._get_stream_url(video_id)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg must be installed and available in PATH")
        span = max(0.5, end - begin)
        target.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-loglevel", "error",
             "-ss", f"{begin:.3f}", "-t", f"{span:.3f}", "-i", stream_url,
             "-vn", "-ac", "1", "-ar", "16000", str(target)],
            capture_output=True, text=True,
        )
        if done.returncode:
            raise RuntimeError(f"FFmpeg failed to slice audio: {done.stderr[-500:]}")

    def window_transcript(self, video_id: str, duration: float,
                          begin: float, end: float) -> list[dict]:
        """English cues for [begin, end); captions first, ASR fallback."""
        clip_begin, clip_end = clip_bounds(begin, end, duration)
        full = self.captions(video_id)
        if full:
            return [cue for cue in full if clip_begin <= midpoint(cue) <= clip_end]
        key = TRANSCRIPT_DIR / f"{video_id}.asr.{int(clip_begin)}-{int(clip_end)}.json"
        saved = _read_json(key)
        if isinstance(saved, list):
            return saved
        local_audio = None
        if AUDIO_DIR.exists():
            for candidate in sorted(AUDIO_DIR.glob(f"{video_id}.*")):
                if candidate.suffix not in (".part", ".ytdl", ".tmp") and candidate.stat().st_size > 0:
                    local_audio = candidate
                    break
        if local_audio and clip_begin <= 0 and clip_end >= duration:
            cues = self.transcribe(local_audio, 0.0)
        else:
            with tempfile.TemporaryDirectory(dir=str(CACHE_DIR)) as temp:
                clip = Path(temp) / "clip.wav"
                if local_audio:
                    self._slice_audio(local_audio, clip_begin, clip_end, clip)
                else:
                    self.extract_window_audio(video_id, clip_begin, clip_end, clip)
                cues = self.transcribe(clip, clip_begin)
        _write_json(key, cues)
        return cues

    def full_transcript(self, video_id: str, duration: float) -> list[dict]:
        full = self.captions(video_id)
        if full:
            return full
        key = TRANSCRIPT_DIR / f"{video_id}.asr.full.json"
        saved = _read_json(key)
        if isinstance(saved, list) and saved:
            return saved
        source = self.audio_file(video_id)
        cues = self.transcribe(source, 0.0)
        _write_json(key, cues)
        return cues

    def _convert_model(self, name: str) -> Path:
        folder = CT2_DIR / SAFE_NAME.sub("_", name)
        marker = folder / "tokenizer.name"
        if (folder / "model.bin").exists() and marker.exists():
            if not (folder / "tokenizer_config.json").exists():
                try:
                    from transformers import AutoTokenizer
                    AutoTokenizer.from_pretrained(name).save_pretrained(str(folder))
                except Exception:
                    pass
            return folder
        from ctranslate2.converters import TransformersConverter
        from transformers import AutoTokenizer
        CT2_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(folder, ignore_errors=True)
        converter = TransformersConverter(name, low_cpu_mem_usage=True)
        converter.convert(str(folder), quantization="int8", force=True)
        AutoTokenizer.from_pretrained(name).save_pretrained(str(folder))
        marker.write_text(name, encoding="utf-8")
        return folder

    def _ensure_mt(self) -> None:
        if self._translator is not None:
            return
        import ctranslate2
        from transformers import AutoTokenizer
        candidates = [self.mt_model()]
        candidates += [model for model in DEFAULT_MT_MODELS if model not in candidates]
        errors: list[str] = []
        for name in candidates:
            try:
                folder = self._convert_model(name)
                try:
                    tokenizer = AutoTokenizer.from_pretrained(str(folder), local_files_only=True)
                except Exception:
                    tokenizer = AutoTokenizer.from_pretrained(name)
                    tokenizer.save_pretrained(str(folder))
                translator = ctranslate2.Translator(
                    str(folder), device="cpu", compute_type="int8",
                    inter_threads=1,
                    intra_threads=_int_env("LOCAL_MT_THREADS", (os.cpu_count() or 4) // 2),
                )
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:200]}")
                continue
            self._tokenizer, self._translator, self._mt_model = tokenizer, translator, name
            return
        raise RuntimeError("Local translation model is unavailable (" + "; ".join(errors[-2:]) + ")")

    def translate(self, texts: list[str]) -> list[str]:
        wanted = [clean_text(text) for text in texts]
        if not any(wanted):
            return ["" for _ in wanted]
        with self._mt_lock:
            self._ensure_mt()
            output = [self._memo.get(text, "") if text else "" for text in wanted]
            missing = sorted({text for text, vi in zip(wanted, output) if text and not vi})
            if missing:
                batches: list[list[str]] = []
                for text in missing:
                    tokens = self._tokenizer.convert_ids_to_tokens(
                        self._tokenizer(text)["input_ids"])
                    if len(tokens) > MAX_TOKENS:
                        tokens = tokens[:MAX_TOKENS - 1] + [tokens[-1]]
                    batches.append(tokens)
                results = self._translator.translate_batch(
                    batches, beam_size=_int_env("LOCAL_MT_BEAM", 1, 1, 8),
                    batch_type="examples", max_batch_size=_int_env("LOCAL_MT_BATCH", 64),
                    max_decoding_length=_int_env("LOCAL_MT_MAX_LENGTH", 512),
                )
                for text, result in zip(missing, results):
                    hypothesis = result.hypotheses[0] if result.hypotheses else []
                    ids = self._tokenizer.convert_tokens_to_ids(hypothesis)
                    self._memo[text] = self._tokenizer.decode(
                        ids, skip_special_tokens=True).strip()
                if len(self._memo) > MEMO_LIMIT:
                    for key in list(self._memo)[: MEMO_LIMIT // 2]:
                        self._memo.pop(key, None)
            return [self._memo.get(text, "") if text else "" for text in wanted]

    def cues_for_window(self, video_id: str, duration: float, begin: float,
                        end: float) -> list[dict]:
        """Absolute cues whose midpoint falls inside [begin, end)."""
        english = self.window_transcript(video_id, duration, begin, end)
        if not english:
            return []
        vietnamese = self.translate([cue["text"] for cue in english])
        cues: list[dict] = []
        for cue, vi in zip(english, vietnamese):
            vi = (vi or "").strip()
            if not vi or not begin <= midpoint(cue) < end:
                continue
            start = max(begin, min(end, float(cue["start"])))
            stop = max(start + 0.1, min(end, float(cue["end"])))
            if start >= end:
                continue
            cues.append({"start": round(start, 3), "end": round(stop, 3),
                         "original": cue["text"], "vi": vi})
        return sorted(cues, key=lambda item: item["start"])

    def cues_for_video(self, video_id: str, duration: float) -> list[dict]:
        english = self.full_transcript(video_id, duration)
        if not english:
            return []
        vietnamese = self.translate([cue["text"] for cue in english])
        cues: list[dict] = []
        for cue, vi in zip(english, vietnamese):
            vi = (vi or "").strip()
            if not vi:
                continue
            start = max(0.0, min(duration, float(cue["start"])))
            stop = max(start + 0.1, min(duration, float(cue["end"])))
            if start >= duration:
                continue
            cues.append({"start": round(start, 3), "end": round(stop, 3),
                         "original": cue["text"], "vi": vi})
        return sorted(cues, key=lambda item: item["start"])


_PIPELINE: LocalPipeline | None = None
_PIPELINE_LOCK = threading.Lock()


def get_pipeline() -> LocalPipeline:
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            _PIPELINE = LocalPipeline()
        return _PIPELINE


def cached_captions(video_id: str) -> list[dict] | None:
    """Captions already on disk or in memory; never touches the network."""
    pipeline = get_pipeline()
    with pipeline._memory_lock:
        cached = pipeline._memory.get(video_id)
    if cached:
        return cached
    saved = _read_json(TRANSCRIPT_DIR / f"{video_id}.en.json")
    if isinstance(saved, list) and saved:
        with pipeline._memory_lock:
            pipeline._memory[video_id] = saved
        return saved
    return None
