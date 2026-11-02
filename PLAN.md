# Kế hoạch hoàn thiện — Gemini YouTube Dubbing V2

## 1. Mục tiêu

Đưa dự án từ trạng thái "khung kỹ thuật + test mock" sang trạng thái **thực sự xem được trên YouTube**:

- Mở một video YouTube công khai có lời nói.
- Bấm **Start Progressive Dubbing**.
- Nghe giọng Việt do Gemini tạo, phát đè lên video, đồng bộ khi pause/seek/đổi tốc độ.
- Có phụ đề tiếng Việt và trạng thái bộ đệm rõ ràng.
- Khi Gemini quá tải hoặc model lỗi, backend tự chuyển model dự phòng và retry có kiểm soát.

## 2. Quyết định đã chốt

| Vấn đề | Quyết định |
| --- | --- |
| Đích cuối | Xem qua extension Chrome trên trang YouTube; không upload video mới lên YouTube trong phạm vi này |
| Khi Gemini lỗi | Tự động đổi model + retry; vẫn giữ Progressive, không chuyển sang ghi âm tab |
| Video nghiệm thu | 10–30 phút; trước hết nghiệm thu chắc với video 10–15 phút, sau đó thử 30 phút nếu quota cho phép |
| Chế độ bổ sung | **Chỉ phụ đề**: dùng chung pipeline phân tích/chia đoạn, bỏ TTS/WAV, giữ nguyên tiếng gốc (T9) |
| Giọng đọc | 4 preset Edge TTS miễn phí chọn trong popup, thông số `voice|pitch=±Hz|rate=±%`, cache riêng theo giọng (T10) |

## 3. Định nghĩa "hoàn thiện" (DoD)

Trên máy Windows của chủ dự án, với Chrome đã load extension V2:

1. Backend chạy ổn định tại `127.0.0.1:8765`; `/health` trả `ok: true`.
2. Bác sĩ chẩn đoán (doctor) xác nhận có ít nhất một model phân tích video và một model TTS hoạt động.
3. Với video công khai 10–15 phút có lời nói:
   - Start Progressive tạo được 2 chunk đầu (40 giây audio) trong thời gian chấp nhận được.
   - Bấm Play, tiếng Việt phát đè lên video; pause/seek/đổi tốc độ không làm mất tiếng.
   - Khi hết bộ đệm, video tạm dừng và tự tiếp tục sau khi chunk sẵn sàng; có thông báo rõ.
   - Phụ đề tiếng Việt hiển thị đúng vùng thời gian; âm lượng gốc/giọng đọc chỉnh được.
   - Không có lỗi 503/409 không được xử lý; nếu Gemini lỗi, UI báo model đã thử và lý do.
4. Restart backend giữa chừng: extension tự tạo lại job từ cache, không phải bấm lại từ đầu.
5. Video 30 phút: chạy được nếu quota đủ; nếu không, ghi rõ giới hạn thực tế và phương án.
6. README_VI cập nhật đúng cách chạy, fallback model, quota và cách nghiệm thu.

## 4. Hiện trạng có bằng chứng

- Khung V2 đã có: backend FastAPI, extension MV3, chia WAV 20 giây, Web Audio scheduling, cache trên đĩa.
- `tests/smoke_test.py`: PASS (mock Gemini).
- `tests/progressive_test.py`: PASS (đã sửa assert offset dạng chuỗi).
- Khóa Gemini hợp lệ; `GET /v1beta/models` trả 200, 50 model.
- TTS thật `gemini-3.8-flash-tts`: tạo được WAV 24 kHz mono (~3 giây audio, ~6 giây phản hồi).
- Phân tích video YouTube thật: `gemini-3.5-flash-lite` OK (~5 giây); `3.8-flash` timeout, `3.6/3.5-flash` 503 tại thời điểm kiểm tra; `2.5-flash` 404.
- Timeout của SDK `google-genai 2.34` không đáng tin với request video chậm (lúc chặn được, lúc treo vô hạn) → T3 thêm watchdog hạn cứng.
- Đã chạy end-to-end thật trên Chrome for Testing 156 + extension unpacked, video 15:04 `UF8uR6Z6KLc`: tạo chunk, phát đè, phụ đề, pause, seek, rate, tự dừng chờ bộ đệm và tự phát tiếp.
- Đã kiểm chứng Chrome UI/playback/sync trên video dài; cache sống qua restart backend (job `ff1769db...`).
- Đo Edge TTS: trung bình 13.08s/chunk 20s (analysis 7.13s + TTS 5.95s), nhanh hơn thời gian phát.
- Còn thiếu: chủ dự án tự nghe/xem xác nhận chất lượng âm thanh trên máy của mình.
- Gọi audio analysis live từng bị treo quá 5 phút khi không có timeout rõ ràng.

## 5. Các task

### T1 — Chuẩn hóa setup và test baseline

**Mục tiêu:** fresh setup chạy được; test hiện có phản ánh đúng hợp đồng SDK.

- Thêm `backend/.env.example` với đầy đủ biến và placeholder, không chứa khóa thật.
- Sửa `tests/progressive_test.py` để assert `start_offset`/`end_offset` là chuỗi.
- Rà `setup_windows.bat` để báo lỗi rõ nếu thiếu template.
- Chạy `tests/progressive_test.py` và `tests/smoke_test.py`.

**DoD:** cả hai test PASS; setup từ thư mục sạch tạo được `.env`; không có secret bị commit.

### T2 — Doctor chẩn đoán Gemini

**Mục tiêu:** biết ngay model nào đang dùng được, thay vì đoán.

- Mở rộng `backend/check_gemini_auth.py` hoặc thêm `backend/doctor.py`.
- Kiểm tra: khóa hợp lệ, danh sách model, TTS ngắn, phân tích một clip YouTube ngắn (tùy chọn `--live-video`).
- Mọi lời gọi có timeout; không in khóa; in khuyến nghị cấu hình `.env`.
- Thử lần lượt các model ứng viên cho analysis và TTS.

**DoD:** chạy doctor trên máy chủ dự án cho kết quả rõ: model analysis hoạt động (hoặc lỗi cụ thể), model TTS hoạt động, thời gian phản hồi.

### T3 — Fallback model + retry + timeout

**Mục tiêu:** Gemini 503/429 không làm chết job; tự chuyển model và báo trạng thái.

- Thêm cấu hình danh sách model dự phòng cho analysis và TTS, ví dụ:
  - `GEMINI_MODEL=gemini-3.5-flash-lite` (model đang hoạt động tại 2026-10-31)
  - `GEMINI_MODEL_FALLBACKS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash`
  - `GEMINI_TTS_MODEL=gemini-3.8-flash-tts`
  - `GEMINI_TTS_MODEL_FALLBACKS=gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts`
- Phân loại lỗi: retry/fallback với 429, 503, timeout; fallback khi 404 model ngừng hỗ trợ; dừng ngay với 400/401/403.
- Timeout: đặt budget ở client (`HttpOptions.timeout`) và chặn bằng watchdog hạn cứng trong `call_model`; không dùng timeout per-call vì SDK 2.34 không thực thi đáng tin.
- Cache model đang hoạt động trong phiên để chunk sau dùng ngay model đã thành công.
- Lưu model đã dùng vào snapshot job và hiển thị trong popup.
- Thêm test mock cho thứ tự fallback, 503 rồi thành công, 404 rồi thành công, 400 không fallback, timeout, treo vô hạn, retry một lượt khi tất cả bị throttle.

**DoD:** test mock PASS; doctor chạy qua fallback thật; không còn treo quá lâu khi model không phản hồi.

### T4 — Khôi phục job sau restart + trạng thái UI

**Mục tiêu:** restart backend giữa video không làm mất phiên xem.

- Extension tự POST lại `/stream/jobs` khi gặp 404 "Stream not in memory", dùng `videoId` và duration đã lưu.
- Backend giữ cache WAV/JSON và tạo lại `ProgressiveJob` đúng cache key.
- Popup hiển thị model đang dùng, fallback, số chunk sẵn sàng và lỗi bằng tiếng Việt.
- Kiểm tra cache hit sau restart.

**DoD:** video đang xem dở, restart backend, popup tự phục hồi và tiếp tục dùng chunk đã cache.

### T5 — Nghiệm thu Chrome thật (video 10–15 phút)

**Mục tiêu:** chứng minh người dùng thực sự xem được trên YouTube.

- Tắt extension V1, load V2, mở video 10–15 phút có lời nói và được phép xử lý.
- Kiểm tra: buffer đầu, play, pause, seek xa, seek gần, đổi tốc độ, phụ đề, âm lượng, autoplay.
- Ghi lại lỗi console, trạng thái job, thời gian chờ, số lần phải dừng chờ.
- Sửa các lỗi phát hiện được; đặc biệt kiểm tra mixed content của Classic mode (nếu còn dùng).

**DoD:** người dùng xác nhận nghe/xem được; checklist T5 có bằng chứng; không có lỗi console nghiêm trọng.

### T6 — Tối ưu video 10–30 phút và quota

**Mục tiêu:** biết giới hạn thật và cấu hình phù hợp.

- Đo số lời gọi analysis/TTS mỗi chunk, thời gian tạo chunk, số lần lỗi quota.
- Thử cấu hình chunk 20s/30s và lookahead tương ứng; chọn profile cho video dài.
- Thêm giới hạn/telemetry số lời gọi để tránh đốt quota âm thầm.
- Nếu Free Tier không đủ: đề xuất rõ (video ngắn hơn, Edge TTS cho khối lượng lớn, hoặc paid tier) để chủ dự án quyết định.
- Thử video 30 phút nếu quota cho phép.

**DoD:** video 10–15 phút xem liên tục với số lần buffer được ghi lại; kết luận rõ 30 phút có khả thi hay không.

### T7 — Tài liệu và nghiệm thu cuối

**Mục tiêu:** người dùng khác (hoặc chính chủ dự án sau này) làm lại được.

- Cập nhật `README_VI.md`/`README.md`: setup, doctor, fallback model, quota, giới hạn video dài, troubleshooting 503.
- Ghi lại kết quả nghiệm thu thực tế và cấu hình đã dùng.
- Chạy lại toàn bộ test + doctor + một phiên YouTube ngắn.

**DoD:** tài liệu khớp thực tế; chủ dự án xác nhận "xem được trên YouTube"; các test/doctor đều xanh.

### T8 — Tùy chọn (ngoài phạm vi hiện tại)

Xuất MP4 lồng tiếng và upload lên YouTube bằng YouTube Data API/OAuth. Chỉ làm nếu chủ dự án mở rộng phạm vi; cần nguồn video hợp lệ và quyền tải lên.

### T9 — Chế độ chỉ phụ đề

**Mục tiêu:** xem phụ đề tiếng Việt trên YouTube mà vẫn giữ nguyên tiếng gốc; không gọi TTS, không tạo WAV, không đổi âm lượng.

- Backend: `mode=dub|subtitles` trong `/stream/jobs`; job phụ đề chỉ ghi cue JSON, bỏ qua TTS; khóa cache riêng có `subtitles`; endpoint audio trả 409.
- Extension: popup có nút **Start Vietnamese Subtitles**; content tải cue JSON và hiện phụ đề theo `video.currentTime`, không tạo AudioContext/không chỉnh volume, không tạm dừng video khi thiếu đoạn.
- Telemetry: `tts_calls=0`, `tts_seconds=0`, `tts_provider`/`tts_voice` rỗng trong snapshot.
- Test: `tests/subtitles_test.py`; chạy live trên Chrome thật.

**DoD:** phụ đề hiển thị/seek/ẩn-hiện đúng, tiếng gốc không đổi, không có lời gọi TTS; test tự động PASS.

### T10 — Bộ chọn giọng đọc tiếng Việt (Edge TTS)

**Mục tiêu:** đổi giọng Việt (nam/nữ, trầm/cao) ngay trong popup, không đụng cấu hình `.env` mỗi lần đổi.

- Backend: `parse_voice_spec()` tách `voice|pitch=±Hz|rate=±%`, chặn thông số sai; `synth_edge(text, path, voice_spec)` chuyển cả ba vào edge-tts và thử lại `EDGE_TTS_ATTEMPTS` lần khi stream rỗng; `voice` là field của `/stream/jobs`, `/jobs/from-youtube`, `/jobs` (form); `cache_key()` đưa voice vào khóa; snapshot trả `tts_voice`.
- Extension: dropdown **Giọng đọc** 4 preset trong bảng VOICE & SUBTITLES, lưu `voice` vào state, gửi khi start/recreate job, xám đi ở chế độ phụ đề.
- Test: `tests/voice_selector_test.py` (parser, tham số edge-tts, retry, cache theo giọng, API 400/202).

**DoD:** 4 preset render được audio thật; cùng văn bản, pitch tạo file khác nhau; đổi giọng không dùng nhầm cache cũ; toàn bộ test PASS. **Chủ dự án nghe để chốt giọng yêu thích.**

## 6. Rủi ro và điểm cần quyết định

1. **Gemini 503 hiện tại là yếu tố bên ngoài.** Fallback giúp giảm rủi ro nhưng không đảm bảo mọi model luôn sẵn sàng.
2. **Quota Free Tier cho video 10–30 phút.** Số lời gọi analysis + TTS có thể vượt hạn mức; T6 sẽ đo và đề xuất phương án.
3. **Chất lượng đồng bộ.** Timestamp của model là ước tính; mục tiêu là "xem được, tua/pause ổn", không phải lip-sync.
4. **Classic mode.** Có rủi ro mixed content khi tải audio HTTP từ trang HTTPS; Progressive là đường chính.
5. **Không tải media từ YouTube.** Dự án chỉ dùng URL công khai và tab capture khi được phép; không thêm tải video trái phép.

## 7. Quy trình review

- T1–T3: làm một task mỗi lượt, dừng để chủ dự án review.
- T4–T7: chủ dự án yêu cầu chạy liên tục; chỉ dừng báo cáo khi xong T7.
- Kết thúc mỗi task: ghi bằng chứng vào bảng trạng thái.
- Không commit/push cho đến khi có yêu cầu rõ ràng.

## 8. Trạng thái task

| Task | Trạng thái | Bằng chứng |
| --- | --- | --- |
| T1 | DONE | `.env.example` added; `progressive_test.py` PASS; `smoke_test.py` PASS; setup copy test: `ENV_CREATED`, `ENV_TEMPLATE_OK` |
| T2 | DONE | `doctor.py`: auth 200/50 models; TTS `gemini-3.8-flash-tts` OK; video `gemini-3.5-flash-lite` OK (1 segment); 3.8/3.7 timeout, 3.6/3.5 503 |
| T3 | DONE | `tests/model_fallback_test.py` 8/8 PASS; live `call_model`: 3.8-flash bị bỏ sau 8s, 3.5-flash-lite OK 5.1s (tổng 13.1s); doctor 10s/probe, lite OK 5.5s; snapshot có `models`, popup hiển thị model thật |
| T4 | DONE | `tests/restart_test.py` PASS: restart giả lập → POST lại ra cùng job_id, chunk 0 trong cache vẫn phục vụ; extension `recreateStreamJob` + `streamSnapshotForContent`; `content.js` nhận job/chunk mới; popup có nhãn tiếng Việt + `(dự phòng)`; `node --check` 3 file JS OK |
| T5 | DONE (tự động) | Chrome for Testing 156 + extension unpacked, video 15:04 `UF8uR6Z6KLc`: start → 7 chunk cache; play đè (video `muted=false`, volume 0.13); phụ đề khớp tại 31.5s/38.5s/62.7s; pause 2s đứng yên 36.28s rồi tiếp; seek 60.5 → 62.69 đúng chunk 3; seek 85 tự phục hồi; seek 200 dừng ~5s + overlay rồi tự phát; rate 1.5x tiến 4.8s/3s, về 1x phụ đề tiếp; stop khôi phục volume 1 + ẩn phụ đề; popup hiện `gemini-3.5-flash-lite` + `Edge TTS · vi-VN-NamMinhNeural`; không lỗi extension. **Chờ chủ dự án xác nhận nghe trên máy thật** |
| T6 | DONE | Edge: 17 chunk cache; đo 2 chunk mới: avg **13.08s/chunk 20s** (analysis 7.13s + TTS 5.95s), 9 TTS call/2 chunk, 0 retry; telemetry `stats` + `stats.json` (analysis_calls/tts_calls/giây/retries/failures) và trong snapshot; 20s + lookahead 4 = tối đa 80s đệm; 15 phút ≈ 46 analysis call, 30 phút ≈ 90 analysis + ~405 Edge call; kết luận: 30 phút khả thi với Edge, không khả thi với Gemini TTS (~60–90s/chunk) và dễ chạm quota Free Tier |
| T7 | DONE (chờ xác nhận) | `README_VI.md`/`README.md`: setup, doctor, fallback, quota, provider, troubleshooting, kết quả nghiệm thu, giới hạn; 4 bộ test PASS (`smoke` 2, `progressive` 4, `model_fallback` 9, `restart` 3); doctor live: auth 200/50 model, Edge TTS OK 2.0s, video `gemini-3.5-flash-lite` OK 6.2s; phiên Chrome cuối: phụ đề 20.99s/26.56s, start gần biên 19.4s vẫn qua 20s liên tục; **còn chờ chủ dự án tự nghe/xem xác nhận** |
| T9 | DONE (live ngắn) | Backend `mode=subtitles` chỉ ghi cue JSON, cache riêng, audio 409; extension/popup nút phụ đề, không đổi volume/mute; `tests/subtitles_test.py` PASS; live `jNQXAC9IVRw`: job `98fdaed…`, 3 cue, phụ đề ~4.0s + seek + ẩn/hiện, `tts_calls=0`, volume 1/muted false trước-sau; **chưa live nhiều đoạn trên video dài** |
| Bổ sung | DONE | `HUONG_DAN.md`: giới thiệu sản phẩm + hướng dẫn người dùng cuối (2 chế độ, cấu hình nhanh, chi phí/hạn mức, FAQ); template trả về tên `backend/.env.example` khớp `setup_windows.bat` (kiểm tra `TEMPLATE_OK`, `.example.env` đã hết); README_VI/README link tới hướng dẫn mới |
| T10 | DONE (chờ nghe thử) | `parse_voice_spec` + `synth_edge(voice_spec)` + `EDGE_TTS_ATTEMPTS=5`; `voice` trong `/stream/jobs`, `/jobs/from-youtube`, `/jobs`, `cache_key`, `ProgressiveJob`, `render_chunk`, `build_dub`, `snapshot.tts_voice`; popup `<select id="voiceSelect">` 4 preset + background `INIT.voice`/`saveSettings`/3 chỗ POST. `tests/voice_selector_test.py` 6/6 PASS; cả bộ test PASS (voice 6, progressive 4, subtitles 2, model_fallback 9, restart 3, smoke 2); `node --check` OK. Live `jNQXAC9IVRw` (edge): 4 preset có audio; `pitch=-10Hz` khác MD5 cùng độ dài; job `8a2730…` vs `a579b8…` khác nhau; `retries=0, failures=0` với 5 lần thử (3 lần thử trước đó phải redo chunk `retries=1`); phụ đề bỏ qua giọng (`0ecb19…`: `tts_voice=''`, `tts_calls=0`); **còn chờ chủ dự án nghe chọn giọng** |
