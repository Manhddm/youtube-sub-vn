# Gemini YouTube Dubbing — Progressive V2 (Chrome MV3)

**New in V2:** a demand-driven, YouTube-like buffer. Gemini prepares ~20s
clips independently, Chrome starts after the first two clips, and audio is
scheduled with Web Audio while future clips are generated. Seeking reprioritizes
translation. Prepared WAV chunks are cached across restarts.

**Subtitles-only mode:** the same progressive analysis pipeline can overlay
Vietnamese captions while leaving the original audio completely untouched — no
TTS calls, no WAV files, no volume/mute changes, and no pausing when a caption
chunk is still being prepared. Start it with **Start Vietnamese Subtitles** in
the popup.

**Vietnamese voice picker:** the popup's VOICE & SUBTITLES panel offers four
free Edge TTS presets — Nam Minh (standard), Hoài My (standard), Nam Minh
(deep, `pitch=-10Hz`) and Hoài My (bright, `pitch=+8Hz`). The spec is validated
by the backend, threaded into every speech request, and included in the cache
key, so each voice gets its own cached chunks.

**Read `README_VI.md` first for Windows setup, migration from V1, instructions,
limitations and troubleshooting in Vietnamese.** For a product introduction and
end-user walkthrough (Vietnamese), see `HUONG_DAN.md`.

**Status (2026-10-31):** the V2 progressive flow was verified end-to-end on a
15:04 public video with Chrome for Testing 156: initial buffering, Vietnamese
audio overlay, synced captions, pause, seeks (buffered and unbuffered), 1.5x
playback, automatic pause/resume on buffer exhaustion, stop/restore, and cache
reuse after a backend restart. Edge TTS measured ~13.1s per 20s chunk (7.1s
analysis + 5.9s TTS), faster than realtime. The subtitles-only mode was verified
live on the short public video `jNQXAC9IVRw`: caption timing/seek/toggle worked,
the original volume stayed at 1 with `muted=false`, and no TTS was called
(`tts_calls=0`; `/chunks/0/audio` returns 409). The voice picker was verified on
the same video with live Edge TTS: all four presets rendered, `pitch=-10Hz`
produced a different (same-length, different-MD5) file for identical text, two
voices produced two distinct job ids and caches, and subtitles mode ignored the
voice. **The project owner still needs
to listen/watch on their own Chrome to confirm perceived audio quality**;
automated checks cannot judge that. Gemini throughput and free-tier quotas can
still cause buffering. Do not expect unlimited-free streaming.

The original whole-video processing pipeline remains in the "Classic" section.

---

# Gemini YouTube Dubbing - Chrome MV3 starter (Windows)

A **working reference MVP** for an owner-authorized YouTube video:

1. **Fast mode:** send a public YouTube URL straight to Gemini 3.8 Flash for timestamped speech transcription and Vietnamese translation. No waiting for the video to play through.
2. **Fallback mode:** capture tab audio via `chrome.tabCapture`, upload to the backend at **127.0.0.1**, and let Gemini analyze short audio chunks.
3. Gemini or Edge TTS generates Vietnamese speech; FFmpeg aligns speech with silence and creates `dub.mp3` and `subtitles.vi.srt` (classic mode).
4. Chrome plays dub audio alongside the original video with seek/pause synchronization.

No model weights, Whisper or Ollama are required. A free-tier Gemini API key IS required, and Gemini requires Internet access. Speech defaults to the unofficial, keyless **Edge TTS** (fast, no Gemini TTS quota, Vietnamese voices selectable in the popup); set `TTS_PROVIDER=gemini` for the Gemini voice pipeline instead.

## 1. Install prerequisites on Windows

- Chrome 116 or later.
- Python 3.11+ from https://www.python.org/downloads/ (enable Add Python to PATH).
- FFmpeg (including ffprobe): https://ffmpeg.org/download.html (Windows builds at https://www.gyan.dev/ffmpeg/builds/).
- Install FFmpeg and add its `bin` directory to your Windows PATH, then open a fresh CMD.
- Confirm in CMD: `py -3 --version`, `ffmpeg -version`, `ffprobe -version`.

## 2. Configure Gemini API

- Open https://aistudio.google.com/apikey, create an API key in an eligible Free-Tier project.
- Double click `backend/setup_windows.bat` to create a virtual environment and install packages.
- Open `backend/.env` in Notepad (setup copies `.env.example` if it did not exist).
- Replace `PASTE_YOUR_API_KEY_HERE` with your own secret API key. **Never place your key in the extension code or share it.**
- Recommended for continuous watching (already in `.env.example`): `GEMINI_MODEL=gemini-3.5-flash-lite` with fallbacks, and `TTS_PROVIDER=edge` for speed/no Gemini TTS quota. For a more natural but slower Gemini voice set `TTS_PROVIDER=gemini`; expect 60-90s per 20s chunk on the free tier.
- The backend retries overloaded/missing models in `GEMINI_MODEL_FALLBACKS` and caches the model that worked. Per-chunk retry is controlled by `STREAM_CHUNK_ATTEMPTS` / `STREAM_CHUNK_RETRY_SECONDS`.

### Local translation (default, no API key, no quota)

Translation no longer needs Gemini: `TRANSLATION_PROVIDER=local` (default) uses the video's English captions when YouTube publishes them, falls back to `yt-dlp` + `faster-whisper` (`LOCAL_WHISPER_MODEL=base.en`, VAD) for videos without captions, and translates English to Vietnamese with a CTranslate2 int8 model (`LOCAL_MT_MODEL=Helsinki-NLP/opus-mt-en-vi`) that is converted from Hugging Face on the first run. Everything is cached under `backend/.model_cache/` (models, transcripts, downloaded audio), so a 20 second window is usually ready in well under a second. Because local translation is free, `STREAM_LOOKAHEAD_LOCAL_CHUNKS=12` translates up to 240 seconds around the playhead while only `STREAM_LOOKAHEAD_CHUNKS=4` chunks are rendered to audio. Set `TRANSLATION_PROVIDER=gemini` to return to the URL-clip path (requires `GEMINI_API_KEY` and quota). Speech synthesis stays on Edge TTS by default and is now batched in one event loop with an `asyncio.Semaphore` instead of a thread per line.

The project uses Gemini Interactions and the modern Python SDK `google-genai >= 2.25.0`.
Free Tier availability and rate limits depend on your own project/account. For the latest limits check https://ai.google.dev/gemini-api/docs/rate-limits and https://ai.google.dev/gemini-api/docs/pricing .

## 3. Start the backend

Double click `backend/run_windows.bat`. Keep the CMD window open. Open:

    http://127.0.0.1:8765/health

Expected JSON: `{"ok":true,"model":"gemini-3.5-flash-lite","translation":"local","tts":"edge"}` (the fields reflect `TRANSLATION_PROVIDER` and `TTS_PROVIDER`).

The server listens only on 127.0.0.1, never on the external network.

Before a long video, run the diagnostic (no API key is printed; every live call has a timeout):

```powershell
cd backend
.\.venv\Scripts\python.exe doctor.py
# key + model list only, no video quota:
.\.venv\Scripts\python.exe doctor.py --no-live
```

## 4. Install the extension

- Open `chrome://extensions` and keep **Developer mode** ON (Chrome 156 may disable unpacked extensions when it is off).
- Click **Load unpacked** and select this project's `extension` folder (NOT the backend folder).
- Pin **Gemini YouTube Dubbing (Progressive V2)** to the Chrome toolbar.

## 5. Progressive mode quick start (primary)

1. Open a **public** YouTube `/watch?v=...` video you own or are allowed to process; 10-15 minutes is a good first long test. Refresh the tab after installing/reloading the extension.
2. Open the popup and click **Start Progressive Dubbing**. Leave the backend CMD window open.
3. Wait for **Play buffered Vietnamese audio** to enable (the first two 20s chunks are ready; with Edge TTS this is typically 20-30s).
4. Click it, return to YouTube, and press Play. Pause, seek, change speed, choose a voice and adjust volumes in the popup; the dub follows `video.currentTime`.
5. If playback reaches an unprepared chunk, the video pauses with `Đang chuẩn bị giọng đọc tiếng Việt…` and resumes automatically when it is ready. Seeking far reprioritizes work at the new position. If the backend restarts, clicking Start again reuses the cache under the same job id.
6. The popup shows the active model and `Edge TTS · vi-VN-NamMinhNeural` (or the Gemini model) plus `(dự phòng)` if a fallback model is active. Call/timing telemetry is available as `stats` in `/stream/jobs/<id>` and in `backend/jobs/<id>/stats.json`.

### Subtitles-only mode (original audio unchanged)

1. Open the same kind of public YouTube video.
2. In the popup click **Start Vietnamese Subtitles** (secondary button).
3. **Show Vietnamese subtitles** is enabled while the job is running; click it (captions appear as chunks finish translating), return to YouTube, and press Play.
4. Vietnamese captions follow `video.currentTime`; pause/seek/speed changes work as usual. Missing caption chunks do **not** pause the video — captions appear once translated.
5. Click **Stop subtitles** to hide them. The video's volume and mute state are never modified.

This mode skips TTS entirely: no WAV files, no `tts_calls`, no audio scheduling, and a separate cache key (`subtitles`) so it never mixes with dubbed chunks. `/stream/jobs/<id>/chunks/<i>/audio` returns 409 for subtitle jobs.

### Voice selection (Edge TTS)

The popup's **Giọng đọc** dropdown holds four free Vietnamese presets:

| Popup label | Value sent to the backend |
| --- | --- |
| Nam Minh (Giọng Nam chuẩn) | `vi-VN-NamMinhNeural` |
| Hoài My (Giọng Nữ chuẩn) | `vi-VN-HoaiMyNeural` |
| Nam Minh (Giọng Nam trầm) | `vi-VN-NamMinhNeural\|pitch=-10Hz` |
| Hoài My (Giọng Nữ cao) | `vi-VN-HoaiMyNeural\|pitch=+8Hz` |

- `parse_voice_spec()` in `backend/engine.py` validates the spec — `pitch` only accepts `±Hz`, `rate` only accepts `±%` — and the endpoints return `400` otherwise. The same parser is used for `EDGE_VOICE`, so `.env` may carry a preset as the default.
- `voice` is accepted by `POST /stream/jobs`, `POST /jobs/from-youtube` and the classic `POST /jobs` form, is stored in `chrome.storage`, and is reported back as `tts_voice` in the job snapshot.
- The voice is part of `cache_key()`, so each preset gets its own cached chunks; switching voices cannot play the wrong dub and does not invalidate what the other voice already produced. With the default voice, existing caches are unchanged.
- Changing the voice applies on the next **Start**, because speech is rendered per chunk. Subtitles mode greys the dropdown out and reports an empty `tts_voice`; `TTS_PROVIDER=gemini` ignores it and uses `GEMINI_VOICE`.

### Classic mode (backup)

1. Click the extension and select **Gemini Direct (public video)**. The page pauses; the backend sends the URL straight to Gemini without downloading or recording the video.
2. Leave the backend CMD window open. You may close the popup during processing. Re-open to check status.
3. When **ready**, click **Play Vietnamese dub**. Return to YouTube, rewind to 0 and press the YouTube Play button. Audio sync follows video pause and seeks.
4. Adjust volume and download the MP3/SRT.
5. If Gemini cannot access the public video, choose **Fallback: record full video** instead. For fallback, wait for ads to end, disable Autoplay, let the video play through from 0 at 1x speed, and do not pause/seek during recording. The extension automatically submits the audio at the end or when you click **Finish recording & process**.

Generated artifacts are stored at `backend/jobs/<job_id>/`.

## 6. Typical troubleshooting

| Symptom | How to resolve |
| --- | --- |
| Local backend offline | Run `backend/run_windows.bat`; visit `/health` |
| Cannot import google.genai | Run `backend/setup_windows.bat` again; the requirements ask for the current SDK |
| No FFmpeg | Add the FFmpeg `bin` folder to PATH and restart CMD |
| Missing GEMINI_API_KEY | Put a real key inside `backend/.env` and restart backend |
| `429` or `RESOURCE_EXHAUSTED` | Gemini Free Tier quota reached. The backend tries `GEMINI_MODEL_FALLBACKS`, but cannot guarantee continuous playback. Switch `TTS_PROVIDER=edge`, increase `STREAM_CHUNK_SECONDS`, or wait for quota reset |
| `503` / timeout | Overloaded/slow model. The backend skips it, tries the next candidate, and caches the model that worked; the popup marks `(dự phòng)` |
| `404` model not found | Update `GEMINI_MODEL` / `GEMINI_MODEL_FALLBACKS` using `doctor.py` output |
| `400 INVALID_ARGUMENT` | Input/key error; the backend stops instead of retrying other models. Read the popup/backend message |
| Extension disappears after reload | Turn **Developer mode** ON in `chrome://extensions` and re-enable it (Chrome 156 may disable unpacked extensions) |
| Progressive playback paused with an overlay | The next chunk is not ready. It resumes automatically; seek near prepared ranges or wait |
| Subtitles do not appear | Confirm you clicked **Show Vietnamese subtitles**, and that the chunk at the current position has been translated (popup shows `Đã dịch x/y đoạn phụ đề`). Unlike dubbing, captions never pause the video |
| `NoAudioReceived` from Edge TTS | The unofficial service returns empty streams in bursts, for the default voice too — it is not a bad preset. `synth_edge` already retries each cue `EDGE_TTS_ATTEMPTS` times (default 5, 0.5s×attempt backoff). If it still fails, start again in a few minutes or set `TTS_PROVIDER=gemini` |
| `400 Invalid voice parameter` | `pitch` must be `±Hz` and `rate` must be `±%`; e.g. `pitch=+8%` is rejected |
| Changed voice but still hear the old one | The voice applies when the chunk is rendered: click **Start** again. Each voice has its own job id and cache |
| No dubbed audio in subtitles mode | Expected: subtitles mode never generates TTS. Use **Start Progressive Dubbing** for Vietnamese speech |
| Backend restarted mid-video | Click Start Progressive again; the extension recreates the same cached job and keeps prepared chunks |
| Audio pauses after clicking Play Vietnamese dub | Press Play in the YouTube page; browsers may block autoplay from extension code |
| Wrong video selected | Return to the same YouTube video in the same tab |
| Direct URL rejected | Gemini currently accepts only public videos; try the fallback tab-capture mode for a video you are authorized to process |
| No recording after ads | Let ads finish and start a new capture; this demo does not bypass ads or DRM |
| Dub is not aligned with speech | Gemini timestamps are approximate; try smaller `STREAM_CHUNK_SECONDS`, manually revise transcript or build an alignment pass |

## 7. Caveats / future work

- This is **pre-processing**, not real-time speech-to-speech. Progressive mode avoids waiting for the whole video; classic fallback capture takes roughly the original video's playback time.
- Progressive mode has no 10-minute cap (default `MAX_PROGRESSIVE_SECONDS=43200`), but Gemini free-tier quota still applies. Classic mode keeps `MAX_VIDEO_SECONDS=600`.
- Measured with Edge TTS: ~13.1s per 20s chunk (analysis + TTS), so a 30-minute video needs ~90 analysis calls and ~405 Edge calls and is feasible when the network/model is stable. Gemini TTS is much slower (60-90s/chunk) and not suitable for continuous free-tier viewing.
- Free Tier has **limited requests/tokens** and may use submitted content to improve Google's products. Direct YouTube input is a preview feature for **public** videos only, with a separate daily free-tier video limit. Do not upload sensitive recordings.
- Edge TTS is an **unofficial** online service and may change or stop working. Its empty-stream `NoAudioReceived` is intermittent (measured on the default voice as well), so each cue is retried `EDGE_TTS_ATTEMPTS` times before the chunk retry logic takes over.
- The four popup presets are **Edge TTS** voices only. They are free-form strings validated for shape, not against Edge's live voice list; any other `xx-YY-ZzzNeural` name is accepted and fails at synthesis if it does not exist.
- This version does not preserve background music as a separate track: reducing original volume also reduces music and effects.
- Gemini timestamps are estimates. TTS groups neighboring cues and may accelerate/clip a line to fit its allotted time. This MVP is not lip-sync quality.
- Subtitles-only mode has not been live-tested across many chunks on a long video yet (only the short `jNQXAC9IVRw` run); chunking/cache/seek share the dubbing pipeline and are covered by mocked tests.
- Subtitles-only mode does not export a complete SRT; cues are shown live and stored per chunk as JSON in `backend/jobs/<job_id>/`.
- Only a single job processes at a time; in-memory job state resets when restarting the backend, but cached WAV/JSON chunks are reused and the extension recreates the job.
- The local API is **not authenticated**, so it is for a private development machine only, not deployment on the public Internet. Add authentication and restrictive CORS before publishing.
- Do not process videos without the rights or permission to do so. Check YouTube's Terms of Service and applicable copyright rules.
- Automated tests cover mocked Gemini, subtitles-only (no TTS/WAV, separate cache, audio endpoint 409), voice specs (parsing, edge-tts arguments, empty-stream retry, per-voice cache and API validation), fallback/retry, restart recovery, and a real Chrome run verified buffering/playback/sync/seek/rate plus a live subtitles-only run and live voice-preset render. **Subjective audio quality still requires the project owner to listen on their own machine.**

## 8. Project tree

```
HUONG_DAN.md           # Vietnamese product intro + end-user walkthrough
README_VI.md           # Vietnamese setup, architecture, troubleshooting
backend/
  .env.example         # API key/config template
  setup_windows.bat    # creates venv and installs packages
  run_windows.bat      # starts local server
  doctor.py            # Gemini auth/model/TTS/video diagnostics with timeouts
  requirements.txt
  app.py               # local HTTP jobs and outputs incl. URL mode
  engine.py            # Gemini/FFmpeg processing, model fallback watchdog
  progressive.py       # 20s chunk worker, cache, stats, seek prioritization
extension/
  manifest.json        # Chrome MV3
  background.js        # capture/job state, stream recovery, model status
  offscreen.html/js    # MediaRecorder and local upload
  content.js           # YouTube video control / Web Audio scheduling / captions
  popup.html/css/js    # control panel
tests/
  smoke_test.py        # no-key mocked end-to-end processing test
  progressive_test.py  # chunking, buffer, seek, cache, retry
  subtitles_test.py    # subtitles-only: cue JSON, no TTS/WAV, cache/API
  voice_selector_test.py # voice specs, edge-tts args, retry, per-voice cache/API
  model_fallback_test.py # 503/404/timeout/400 fallback + PCM wrapping
  restart_test.py      # cache/job recovery after backend restart
```

## Official documentation

- https://developer.chrome.com/docs/extensions/reference/api/tabCapture
- https://developer.chrome.com/docs/extensions/how-to/web-platform/screen-capture
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/audio
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://ai.google.dev/gemini-api/docs/files
- https://ai.google.dev/gemini-api/docs/pricing
