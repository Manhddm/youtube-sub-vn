"""localhost-only API for the Gemini YouTube dubbing extension."""
from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Progressive mode uses its own chunk processor; older full-file routes remain usable.
from pydantic import BaseModel

from engine import process_recording, process_youtube_url

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
from progressive import ProgressiveJob, cache_key, MAX_SECONDS as MAX_STREAM_SECS
JOBS = ROOT / "jobs"
JOBS.mkdir(exist_ok=True)
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
MAX_SECS = int(os.getenv("MAX_VIDEO_SECONDS", "600"))
lock = threading.Lock()
items = {}
executor = ThreadPoolExecutor(max_workers=1)
stream_lock = threading.Lock()
stream_jobs = {}
app = FastAPI(title="Gemini YouTube Dubbing MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://[a-p]{32}",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def progress(job_id: str, stage: str, amount: float, message: str):
    with lock:
        items[job_id].update(stage=stage, progress=round(amount, 3), message=message)


def work(job_id: str, file_path: Path, folder: Path):
    try:
        info = process_recording(
            file_path, folder,
            lambda stage, pct, msg: progress(job_id, stage, pct, msg),
        )
        with lock:
            items[job_id].update(info)
    except Exception as e:
        progress(job_id, "error", 0, str(e))


class YouTubeJob(BaseModel):
    video_id: str
    declared_duration: float


class StreamJobRequest(YouTubeJob):
    mode: str = "dub"


def work_public_video(job_id: str, video_id: str, duration: float, folder: Path):
    try:
        info = process_youtube_url(
            video_id, duration, folder,
            lambda stage, pct, msg: progress(job_id, stage, pct, msg),
        )
        with lock:
            items[job_id].update(info)
    except Exception as exc:
        progress(job_id, "error", 0, str(exc))


@app.post("/jobs/from-youtube", status_code=202)
def start_youtube_job(request: YouTubeJob):
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", request.video_id):
        raise HTTPException(400, "Invalid video ID")
    if not (1 <= request.declared_duration <= MAX_SECS + 1):
        raise HTTPException(400, f"Video must be shorter than {MAX_SECS} seconds")
    folder = JOBS / uuid.uuid4().hex
    folder.mkdir(parents=True)
    job_id = folder.name
    with lock:
        items[job_id] = {
            "job_id": job_id, "video_id": request.video_id,
            "stage": "queued", "progress": 0,
            "message": "Gemini will analyze the public video URL", "segments": 0,
        }
    executor.submit(work_public_video, job_id, request.video_id, request.declared_duration, folder)
    return {"job_id": job_id}


@app.get("/health")
def health():
    return {"ok": True, "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            "tts": os.getenv("TTS_PROVIDER", "gemini")}


@app.post("/jobs", status_code=202)
async def start_job(
    audio: UploadFile = File(...),
    video_id: str = Form(...),
    declared_duration: float = Form(...),
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
        raise HTTPException(400, "Invalid YouTube video ID")
    if not (1 <= declared_duration <= MAX_SECS + 1):
        raise HTTPException(400, f"Video must be shorter than {MAX_SECS} seconds")
    folder = JOBS / uuid.uuid4().hex
    folder.mkdir(parents=True)
    input_file = folder / "capture.webm"
    size = 0
    try:
        with input_file.open("wb") as target:
            while data := await audio.read(1024 * 1024):
                size += len(data)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Recording exceeds max upload size")
                target.write(data)
    finally:
        await audio.close()
    if size < 2048:
        raise HTTPException(400, "Recorded audio is empty")
    job_id = folder.name
    with lock:
        items[job_id] = {
            "job_id": job_id, "video_id": video_id, "stage": "queued", "progress": 0,
            "message": "Queued for Gemini analysis", "segments": 0,
        }
    executor.submit(work, job_id, input_file, folder)
    return {"job_id": job_id}


def require_job(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Unknown job")
    with lock:
        job = items.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found (restart creates a fresh session)")
        return dict(job)


@app.get("/jobs/{job_id}")
def status(job_id: str):
    return require_job(job_id)


def get_artifact(job_id: str, filename: str) -> Path:
    job = require_job(job_id)
    if job["stage"] != "ready":
        raise HTTPException(409, "Job has not finished yet")
    output = JOBS / job_id / filename
    if not output.is_file():
        raise HTTPException(404, "Artifact missing")
    return output


@app.get("/jobs/{job_id}/audio")
def dub_audio(job_id: str):
    return FileResponse(get_artifact(job_id, "dub.mp3"), media_type="audio/mpeg", filename="dub.mp3")


@app.get("/jobs/{job_id}/transcript")
def transcript(job_id: str):
    return FileResponse(get_artifact(job_id, "transcript.json"), media_type="application/json")


@app.get("/jobs/{job_id}/subtitles")
def subtitles(job_id: str):
    return FileResponse(get_artifact(job_id, "subtitles.vi.srt"),
                        media_type="text/plain; charset=utf-8", filename="subtitles.vi.srt")


# ==================== PROGRESSIVE BUFFERED DUBBING =====================
# Unlike the original /jobs endpoint, these routes never require the whole
# translated MP3 before the first playback. Each job streams fixed-time WAV
# chunks, while Gemini keeps preparing future clips in a background thread.
@app.post("/stream/jobs", status_code=202)
def start_stream_job(request: StreamJobRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", request.video_id):
        raise HTTPException(400, "Invalid YouTube video ID")
    if not (1 <= request.declared_duration <= MAX_STREAM_SECS):
        raise HTTPException(400, f"Progressive mode supports videos up to {MAX_STREAM_SECS} seconds")
    mode = request.mode.strip().lower()
    if mode not in ("dub", "subtitles"):
        raise HTTPException(400, "mode must be dub or subtitles")
    job_id = cache_key(request.video_id, request.declared_duration, mode)
    with stream_lock:
        job = stream_jobs.get(job_id)
        if job is None:
            folder = JOBS / job_id
            job = ProgressiveJob(request.video_id, request.declared_duration,
                                 folder, mode)
            stream_jobs[job_id] = job
        job.seek(0)
        job.restart_if_failed()
    return {"job_id": job_id, "chunk_seconds": job.chunk_seconds, "mode": mode}


def require_stream(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Invalid stream job")
    with stream_lock:
        item = stream_jobs.get(job_id)
    if item is None:
        raise HTTPException(404, "Stream not in memory; recreate it through POST /stream/jobs (cache persists)")
    return item


@app.get("/stream/jobs/{job_id}")
def stream_status(job_id: str):
    return require_stream(job_id).snapshot()


class StreamSeek(BaseModel):
    seconds: float


@app.post("/stream/jobs/{job_id}/seek")
def stream_seek(job_id: str, request: StreamSeek):
    job = require_stream(job_id)
    if not 0 <= request.seconds <= job.duration:
        raise HTTPException(400, "Invalid seek timestamp")
    job.seek(request.seconds)
    return job.snapshot()


@app.get("/stream/jobs/{job_id}/chunks/{index}/audio")
def stream_chunk_audio(job_id: str, index: int):
    job = require_stream(job_id)
    if job.mode == "subtitles":
        raise HTTPException(409, "This job is subtitles-only; no dubbed audio exists")
    if index < 0 or index >= job.count:
        raise HTTPException(404, "Unknown chunk")
    if index not in job.snapshot()["ready_chunks"]:
        raise HTTPException(409, "Chunk not ready yet")
    path = job.folder / f"chunk_{index:05}.wav"
    return FileResponse(path, media_type="audio/wav", filename=f"chunk_{index:05}.wav")


@app.get("/stream/jobs/{job_id}/chunks/{index}/cues")
def stream_chunk_cues(job_id: str, index: int):
    job = require_stream(job_id)
    if index < 0 or index >= job.count:
        raise HTTPException(404, "Unknown chunk")
    if index not in job.snapshot()["ready_chunks"]:
        raise HTTPException(409, "Chunk not ready yet")
    return FileResponse(job.folder / f"chunk_{index:05}.json", media_type="application/json")
