# Gemini YouTube Dubbing — Progressive V2 (Chrome MV3)

**New in V2:** a demand-driven, YouTube-like buffer. Gemini prepares ~20s
clips independently, Chrome starts after the first two clips, and audio is
scheduled with Web Audio while future clips are generated. Seeking reprioritizes
translation. Prepared WAV chunks are cached across restarts.

**Read `README_VI.md` first for Windows setup, migration from V1, instructions,
limitations and troubleshooting in Vietnamese.**

This is experimental: live Gemini API / YouTube clipping and Chrome UI playback
have not been verified with an actual API key. Smoothness still depends on Gemini
throughput and free-tier quotas. Do not expect unlimited-free streaming.

The original whole-video processing pipeline remains in the "Classic" section.

---

# Gemini YouTube Dubbing - Chrome MV3 starter (Windows)

A **working reference MVP** for an owner-authorized YouTube video:

1. **Fast mode:** send a public YouTube URL straight to Gemini 3.8 Flash for timestamped speech transcription and Vietnamese translation. No waiting for the video to play through.
2. **Fallback mode:** capture tab audio via `chrome.tabCapture`, upload to the backend at **127.0.0.1**, and let Gemini analyze short audio chunks.
3. Gemini 3.8 Flash TTS generates Vietnamese speech; FFmpeg aligns speech with silence and creates `dub.mp3` and `subtitles.vi.srt`.
4. Chrome plays dub audio alongside the original video with seek/pause synchronization.

No model weights, Whisper or Ollama are required. A free-tier Gemini API key IS required, and Gemini requires Internet access. The default TTS also uses Gemini; an optional unofficial Edge TTS fallback is available.

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
- Leave `GEMINI_MODEL=gemini-3.8-flash` and `TTS_PROVIDER=gemini` for the full-Gemini test.
- Optional if speech generation hits quota: change `TTS_PROVIDER=edge` and restart the backend. This uses an unofficial online Microsoft voice service and might be unstable.

The project uses Gemini Interactions and the modern Python SDK `google-genai >= 2.25.0`.
Free Tier availability and rate limits depend on your own project/account. For the latest limits check https://ai.google.dev/gemini-api/docs/rate-limits and https://ai.google.dev/gemini-api/docs/pricing .

## 3. Start the backend

Double click `backend/run_windows.bat`. Keep the CMD window open. Open:

    http://127.0.0.1:8765/health

Expected JSON: `{"ok":true,"model":"gemini-3.8-flash","tts":"gemini"}`.

The server listens only on 127.0.0.1, never on the external network.

## 4. Install the extension

- Open `chrome://extensions`.
- Enable **Developer mode**.
- Click **Load unpacked** and select this project's `extension` folder (NOT the backend folder).
- Pin **Gemini YouTube Dubbing MVP** to the Chrome toolbar.

## 5. First test (recommended 20-45 seconds)

1. Open a **public** YouTube `/watch?v=...` video you own or are allowed to process. Start with a 20-45 second video. Refresh that tab once if you installed the extension after opening it.
2. Click the extension and select **Gemini Direct (public video)**. The page pauses; the backend sends the URL straight to Gemini without downloading or recording the video.
3. Leave the backend CMD window open. You may close the popup during processing. Re-open to check status.
4. When **ready**, click **Play Vietnamese dub**. Return to YouTube, rewind to 0 and press the YouTube Play button. Audio sync follows video pause and seeks.
5. Adjust volume and download the MP3/SRT.
6. If Gemini cannot access the public video, choose **Fallback: record full video** instead. For fallback, wait for ads to end, disable Autoplay, let the video play through from 0 at 1x speed, and do not pause/seek during recording. The extension automatically submits the audio at the end or when you click **Finish recording & process**.

Generated artifacts are stored at `backend/jobs/<job_id>/`.

## 6. Typical troubleshooting

| Symptom | How to resolve |
| --- | --- |
| Local backend offline | Run `backend/run_windows.bat`; visit `/health` |
| Cannot import google.genai | Run `backend/setup_windows.bat` again; the requirements ask for the current SDK |
| No FFmpeg | Add the FFmpeg `bin` folder to PATH and restart CMD |
| Missing GEMINI_API_KEY | Put a real key inside `backend/.env` and restart backend |
| `429` or `RESOURCE_EXHAUSTED` | Gemini Free Tier quota reached. Shorten video, retry later, or switch `TTS_PROVIDER=edge` |
| Audio pauses after clicking Play Vietnamese dub | Press Play in the YouTube page; browsers may block autoplay from extension code |
| Wrong video selected | Return to the same YouTube video in the same tab |
| Direct URL rejected | Gemini currently accepts only public videos; try the fallback tab-capture mode for a video you are authorized to process |
| No recording after ads | Let ads finish and start a new capture; this demo does not bypass ads or DRM |
| Dub is not aligned with speech | Gemini timestamps are approximate; try smaller `CHUNK_SECONDS`, manually revise transcript or build an alignment pass |

## 7. Caveats / future work

- This is **pre-processing**, not real-time speech-to-speech. Gemini Direct avoids real-time capture for public videos, but fallback capture takes roughly the original video's playback time.
- The demo has a **10-minute cap** (`MAX_VIDEO_SECONDS=600`) and should initially be tested with a 20-45 second video to avoid exhausting free quota.
- Free Tier has **limited requests/tokens** and may use submitted content to improve Google's products. Direct YouTube input is a preview feature for **public** videos only, with a separate daily free-tier video limit. Do not upload sensitive recordings.
- This version does not preserve background music as a separate track: reducing original volume also reduces music and effects.
- Gemini timestamps are estimates. TTS groups neighboring cues and may accelerate/clip a line to fit its allotted time. This MVP is not lip-sync quality.
- Only a single job processes at a time; in-memory job state resets when restarting the backend, but old output files remain on disk.
- The local API is **not authenticated**, so it is for a private development machine only, not deployment on the public Internet. Add authentication and restrictive CORS before publishing.
- Do not process videos without the rights or permission to do so. Check YouTube's Terms of Service and applicable copyright rules.
- This code has static checks and a mocked-audio smoke test; a live Gemini API call and Chrome UI test require your key and Windows browser.

## 8. Project tree

```
backend/
  .env.example         # API key/config template
  setup_windows.bat    # creates venv and installs packages
  run_windows.bat      # starts local server
  requirements.txt
  app.py               # local HTTP jobs and outputs incl. URL mode
  engine.py            # Gemini/FFmpeg processing
extension/
  manifest.json        # Chrome MV3
  background.js        # capture/job state
  offscreen.html/js    # MediaRecorder and local upload
  content.js           # YouTube video control / synced MP3 / captions
  popup.html/css/js    # control panel
 tests/
  smoke_test.py        # no-key mocked end-to-end processing test
```

## Official documentation

- https://developer.chrome.com/docs/extensions/reference/api/tabCapture
- https://developer.chrome.com/docs/extensions/how-to/web-platform/screen-capture
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/audio
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://ai.google.dev/gemini-api/docs/files
- https://ai.google.dev/gemini-api/docs/pricing
