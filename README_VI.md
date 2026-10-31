# Gemini YouTube Dubbing V2 — phát theo bộ đệm như YouTube

**Mới dùng lần đầu?** Đọc [`HUONG_DAN.md`](HUONG_DAN.md) trước: giới thiệu sản phẩm và hướng dẫn sử dụng cho người dùng cuối. Tài liệu này tập trung vào cài đặt Windows, cấu hình và troubleshooting kỹ thuật.

Bản nâng cấp độc lập của `youtube_gemini_dubbing_mvp`. **Không ghi đè thư mục V1.**

V2 có hai chế độ progressive:

- **Lồng tiếng:** dịch và tổng hợp giọng Việt **theo từng đoạn nhỏ** từ URL YouTube công khai, rồi phát WAV bằng Web Audio API theo `video.currentTime`. Không đợi dịch hết video mới cho xem.
- **Chỉ phụ đề:** dịch theo từng đoạn và hiện phụ đề tiếng Việt đè lên video; **không gọi TTS, không tạo/ghi đè âm thanh**, tiếng gốc giữ nguyên.

> **Trạng thái kiểm chứng (2026-10-31):** Bản V2 đã chạy end-to-end thật với một video 15 phút trên Chrome for Testing 156: tạo bộ đệm, phát đè tiếng Việt, phụ đề đồng bộ, pause/seek/đổi tốc độ, tự dừng chờ bộ đệm và tự phát tiếp, cache sống qua restart backend. Chế độ chỉ phụ đề đã chạy thật trên video ngắn `jNQXAC9IVRw`: phụ đề hiển thị/seek/ẩn-hiện đúng, âm lượng gốc giữ nguyên, không gọi TTS. **Chủ dự án vẫn cần tự nghe/xem trên máy của mình để xác nhận chất lượng âm thanh**; các kiểm thử tự động không đánh giá được giọng đọc có dễ nghe hay không. Gemini có thể chậm hơn tốc độ xem hoặc bị giới hạn quota; khi đó trình phát **tạm dừng để chờ bộ đệm**.

## Thay đổi so với bản trước

| Tính năng | V1 | V2 Progressive |
| --- | --- | --- |
| Cách xử lý | Toàn video, MP3 duy nhất | Clip 20 giây, WAV độc lập |
| Chế độ xem | Lồng tiếng | Lồng tiếng hoặc **chỉ phụ đề** (giữ nguyên tiếng gốc) |
| Thời điểm bắt đầu nghe | Sau khi toàn bộ file hoàn thành | Sau khi 2 clip đầu đã sẵn sàng |
| Dịch khi đang xem | Không | Có; chuẩn bị trước tối đa 4 clip gần vị trí xem |
| Tua xa | Cần file dài hoàn chỉnh | Ưu tiên xử lý đoạn được tua đến |
| Dừng xem | Đã tiêu quota cho cả video | Không tiếp tục dịch quá 4 clip đi trước |
| Xem lại | Chỉ job đang chạy | Cache WAV/JSON local trên đĩa, tái sử dụng sau restart |
| Video dài | Giới hạn 10 phút | Mặc định tối đa 12 giờ (tùy chỉnh); **hạn mức Gemini vẫn giới hạn** |

**Điều kiện:** Video YouTube công khai, có thời lượng hữu hạn, quyền truy cập URL từ Gemini. URL video + clip thời gian dựa theo tính năng Gemini Video Understanding preview. Nếu preview thay đổi hoặc clip không hỗ trợ cho URL trên tài khoản của bạn, mode Progressive sẽ báo lỗi. Chỉ sử dụng video bạn được phép xử lý.

## 1. Chạy bản V2 trên Windows (đang có bản V1)

**Trước tiên tắt backend cũ:** ở CMD đang chạy `run_windows.bat`, nhấn `Ctrl + C`, xác nhận nếu được hỏi. Cả hai cùng dùng cổng 8765 nên không chạy đồng thời.

Giải nén ZIP vào:

    D:\MANH\Projects\youtube_gemini_progressive_v2

Mở PowerShell và chạy:

```powershell
cd "D:\MANH\Projects\youtube_gemini_progressive_v2\backend"
.\setup_windows.bat
```

Sau khi setup hoàn tất, **sao chép cấu hình `.env` cũ** sang V2 (không cần nhập lại khóa API):

```powershell
Copy-Item "D:\MANH\Projects\youtube_gemini_dubbing_mvp\backend\.env" ".\.env" -Force
```

Mở `backend/.env` của V2 và bổ sung các dòng sau nếu chưa có (hoặc chép từ `backend/.env.example`):

```env
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_MODEL_FALLBACKS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash

# Khuyến nghị cho xem liên tục: Edge TTS nhanh hơn thời gian phát và không tốn quota Gemini.
TTS_PROVIDER=edge
EDGE_VOICE=vi-VN-NamMinhNeural
# Nếu cần giọng Gemini tự nhiên hơn, đổi TTS_PROVIDER=gemini; chấp nhận chậm hơn video.
GEMINI_TTS_MODEL=gemini-3.8-flash-tts
GEMINI_TTS_MODEL_FALLBACKS=gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts
GEMINI_VOICE=Puck

STREAM_CHUNK_SECONDS=20
STREAM_LOOKAHEAD_CHUNKS=4
STREAM_TTS_PARALLEL=4
STREAM_CHUNK_ATTEMPTS=3
STREAM_CHUNK_RETRY_SECONDS=5
MAX_PROGRESSIVE_SECONDS=43200
GEMINI_ANALYSIS_TIMEOUT_SECONDS=30
GEMINI_TTS_TIMEOUT_SECONDS=20
```

`GEMINI_MODEL` và danh sách fallback được thử lần lượt khi model quá tải, mất kết nối hoặc timeout; model đang hoạt động được cache cho các đoạn sau. `GEMINI_MODEL_FALLBACKS` có thể để trống nếu chỉ muốn dùng một model.

Python 3.14: file `requirements.txt` V2 đã thêm `audioop-lts` tự động để tránh lỗi `No module named 'pyaudioop'`.

Chạy server:

```powershell
.\run_windows.bat
```

Mở `http://127.0.0.1:8765/health` và kiểm tra `ok: true`; trường `tts` cho biết provider đang dùng (`edge` hoặc `gemini`).

Chạy bác sĩ chẩn đoán trước khi xem video dài (không in API key, mọi lời gọi đều có timeout):

```powershell
.\.venv\Scripts\python.exe doctor.py
# Chỉ kiểm tra khóa + danh sách model, không tốn quota video:
.\.venv\Scripts\python.exe doctor.py --no-live
# Kiểm tra một video công khai ngắn cụ thể:
.\.venv\Scripts\python.exe doctor.py --video-id jNQXAC9IVRw --clip-seconds 10
```

Doctor in ra model phân tích/TTS nào đang hoạt động, thời gian phản hồi và khuyến nghị cấu hình `.env`. Nếu tất cả model analysis đều 503/429, thử lại sau hoặc đổi danh sách fallback.

## 2. Cài Extension V2

1. Vào `chrome://extensions` và **tắt V1** để hai extension không cùng can thiệp audio YouTube.
2. Bật **Developer mode** → **Load unpacked** → chọn thư mục `youtube_gemini_progressive_v2\extension`.
3. Mở lại tab YouTube hoặc tải lại bằng F5.
4. Trong popup, bấm **Start Progressive Dubbing**.
5. Trạng thái sẽ cho biết số đoạn đã dịch. Khi nút **Play buffered Vietnamese audio** được bật, bấm nút đó.
6. Quay lại YouTube và bấm Play. Bạn có thể tạm dừng, tua, thay đổi tốc độ phát và chỉnh âm lượng.

V2 mặc định cần có 2 đoạn đầu (40 giây audio đã tổng hợp) trước khi cho phát. **Đây không có nghĩa bạn phải chờ 40 giây thực tế**: Gemini có thể cần lâu hơn (hoặc ít hơn) để tạo 40 giây lồng tiếng.

Khi sắp hết bộ đệm, nếu Gemini chưa tạo xong đoạn kế tiếp, V2 tự tạm dừng video và hiện `Đang chuẩn bị giọng đọc tiếng Việt…`; khi sẵn sàng sẽ thử tiếp tục. Nếu Chrome chặn phát tự động, nhấn Play trên YouTube.

### 2.1. Chế độ chỉ phụ đề (giữ nguyên tiếng gốc)

> Hướng dẫn cho người dùng cuối nằm ở [`HUONG_DAN.md`](HUONG_DAN.md). Phần dưới đây là bản tóm tắt kỹ thuật.

Dùng khi bạn chỉ muốn đọc phụ đề tiếng Việt và vẫn nghe âm thanh gốc:

1. Mở video YouTube công khai như trên.
2. Trong popup, bấm **Start Vietnamese Subtitles** (nút thứ cấp, dưới nút lồng tiếng).
3. Nút **Show Vietnamese subtitles** bật khi job đang dịch; bấm được ngay, phụ đề sẽ hiện dần khi từng đoạn dịch xong.
4. Bấm **Show Vietnamese subtitles**, quay lại YouTube và phát video. Phụ đề tiếng Việt hiện theo `video.currentTime`.
5. Tua/pause/đổi tốc độ bình thường; phụ đề tự theo vị trí mới và ưu tiên dịch đoạn được tua đến, giống chế độ lồng tiếng.
6. Bấm **Stop subtitles** để ẩn phụ đề. Âm lượng video không bị thay đổi trong suốt quá trình.

Chế độ này không gọi TTS, không tạo file WAV, không chỉnh âm lượng hay tắt tiếng video; cache riêng (khóa có `subtitles`) nên không lẫn với cache lồng tiếng. Nếu chưa có đủ đoạn phụ đề tại vị trí đang xem, video vẫn phát bình thường và phụ đề sẽ hiện khi đoạn đó dịch xong (không tạm dừng như chế độ lồng tiếng).

## 3. Cách chỉnh dung lượng bộ đệm và tốc độ dịch

File `backend/.env`:

```env
STREAM_CHUNK_SECONDS=20
STREAM_LOOKAHEAD_CHUNKS=4
STREAM_TTS_PARALLEL=4
```

- Đoạn **10s**: đoạn đầu có thể ra sớm hơn, nhưng tốn nhiều lời gọi Gemini/TTS, dễ chạm giới hạn Free Tier.
- Đoạn **20s**: cân bằng độ trễ đầu và số request; khuyến nghị.
- Đoạn **30–40s**: ít request, cache đủ dài nhưng thời gian chờ khởi động có thể tăng.
- Lookahead **4**: chuẩn bị tối đa 80 giây gần vị trí đang phát; sẽ dừng gọi Gemini nếu đã đệm đủ. Đây là cơ chế chính giới hạn quota: tua đi đâu mới dịch tới đó, không dịch toàn bộ video.
- `STREAM_TTS_PARALLEL=4`: tổng hợp tối đa 4 câu trong một đoạn song song. Giảm xuống 1 nếu máy yếu hoặc bị nhà cung cấp TTS giới hạn.
- `STREAM_CHUNK_ATTEMPTS=3`, `STREAM_CHUNK_RETRY_SECONDS=5`: một đoạn lỗi tạm thời (503/timeout) được thử lại trước khi báo lỗi toàn job.

**Số liệu đo thực tế ngày 2026-10-31** (video 15:04, Edge TTS, `gemini-3.5-flash-lite`):

| Chỉ số | Kết quả |
| --- | --- |
| Thời gian một đoạn 20s | trung bình **13.08s** (analysis 7.13s + Edge TTS 5.95s) |
| Số lời gọi TTS | 9 lời gọi cho 2 đoạn (~4.5 câu/đoạn) |
| Retry | 0 trong mẫu đo |
| Bộ đệm ban đầu | 2 đoạn = 40s audio, khoảng 20–30s thực tế |
| 15 phút video | 46 lời gọi analysis; Edge không tốn quota Gemini |
| 30 phút video | ~90 lời gọi analysis + ~405 lời gọi Edge; khả thi nếu mạng và model ổn định |

Gemini TTS cho giọng tự nhiên hơn nhưng thường mất **60–90s cho một đoạn 20s** trên Free Tier, tức chậm hơn thời gian xem và dễ chạm quota. Vì vậy cấu hình khuyến nghị cho video 10–30 phút là **Edge TTS**; dùng Gemini TTS khi bạn chấp nhận video phải dừng chờ hoặc chỉ xem đoạn ngắn.

Sau khi chỉnh `.env`, khởi động lại backend. Cache sử dụng khóa gồm video, model, provider, voice, số giây/đoạn và chế độ (lồng tiếng/phụ đề) để tránh dùng nhầm bản cũ; đổi cấu hình sẽ tạo job/cache mới.

### Kết quả nghiệm thu ngày 2026-10-31

Cấu hình dùng để nghiệm thu: Chrome for Testing 156.0.8078.80 (load unpacked), video công khai **15:04** `UF8uR6Z6KLc`, `GEMINI_MODEL=gemini-3.5-flash-lite`, `TTS_PROVIDER=edge`, `EDGE_VOICE=vi-VN-NamMinhNeural`, chunk 20s, lookahead 4.

| Hạng mục | Kết quả |
| --- | --- |
| Tạo bộ đệm ban đầu | Đạt; popup báo đủ 2 đoạn đầu và bật nút phát |
| Phát đè tiếng Việt | Đạt; video gốc giữ âm lượng 13%, dub phát qua Web Audio |
| Phụ đề tiếng Việt | Đạt; hiện đúng câu tại ~31.5s, 38.5s, 62.7s; ẩn/hiện theo cài đặt |
| Pause / resume | Đạt; dừng 2 giây vẫn đứng yên, phát tiếp đúng vị trí |
| Seek trong vùng đã đệm | Đạt; tua 60.5s → phát tiếp với phụ đề đúng đoạn |
| Seek ra ngoài vùng đệm | Đạt; video tự dừng + overlay, đoạn mới xong thì tự phát tiếp (thử tại 200s và 300s) |
| Đổi tốc độ 1.5x | Đạt; video tiến ~4.8s trong 3s, phụ đề tiếp tục, sau đó trả về 1x |
| Stop | Đạt; khôi phục âm lượng gốc, ẩn phụ đề |
| Restart backend | Đạt; extension tự POST lại, dùng đúng `job_id` cũ và 17 đoạn đã cache |
| Lỗi extension trong console | Không phát hiện lỗi JS của extension; trình duyệt test có lúc bị YouTube stall do môi trường, không lặp lại trên phiên sau |

**Chưa nghiệm thu:** cảm nhận chủ quan về giọng đọc/âm lượng và xác nhận của chủ dự án trên Chrome cá nhân. Đây là bước cuối cùng bạn cần tự làm.

### Kết quả nghiệm thu chế độ chỉ phụ đề (2026-10-31)

Video công khai ngắn `jNQXAC9IVRw`, Chrome for Testing 156, `gemini-3.5-flash-lite`:

| Hạng mục | Kết quả |
| --- | --- |
| Tạo job phụ đề | Đạt; job `98fdaed…`, mode `subtitles`, 3 cue JSON, không có WAV |
| Không gọi TTS | Đạt; `tts_calls=0`, `tts_provider`/`tts_voice` rỗng; `/chunks/0/audio` trả 409 |
| Hiển thị phụ đề | Đạt; câu tiếng Việt hiện đúng tại ~4.0s, tua đến cue thứ hai đúng nội dung |
| Ẩn/hiện phụ đề | Đạt; bật/tắt lại hiển thị đúng |
| Âm thanh gốc | Đạt; `volume=1`, `muted=false` trước, trong và sau khi bật phụ đề |
| Popup | Đạt; hiện **Start Vietnamese Subtitles**, **Show Vietnamese subtitles**, ẩn dòng TTS, khóa thanh âm lượng |

Chưa chạy live nhiều đoạn trên video dài ở chế độ này; phần chia đoạn/cache/seek dùng chung và đã được `tests/subtitles_test.py` kiểm tra.

## 4. Lỗi thường gặp

- `429 RESOURCE_EXHAUSTED`: hạn mức Free Tier/Gemini không đủ. V2 thử lần lượt các model trong `GEMINI_MODEL_FALLBACKS`, nhưng không thể đảm bảo phát liên tục. Giảm số lời gọi bằng `TTS_PROVIDER=edge`, tăng `STREAM_CHUNK_SECONDS`, hoặc chờ quota reset.
- `503 UNAVAILABLE` / timeout: model quá tải hoặc chậm. Hệ thống tự bỏ model đó và thử model kế tiếp; model đã hoạt động được giữ cho các đoạn sau. Popup hiện tên model kèm `(dự phòng)` khi phải đổi model.
- `404 model not found`: model không còn hỗ trợ; cập nhật `GEMINI_MODEL`/`GEMINI_MODEL_FALLBACKS` theo kết quả `doctor.py`.
- `400 INVALID_ARGUMENT`: lỗi đầu vào/khóa; **không** fallback vô ích. Đọc thông báo trong popup/backend.
- `Speech metadata is not supported for this model`: đã xử lý trong code (không còn gửi annotations). Nếu bạn tự sửa code và gặp lại, bỏ phần `annotations`/`speech_metadata` khỏi payload TTS.
- `clip processing`/`INVALID_ARGUMENT`: trên tài khoản/video của bạn, Gemini có thể chưa chấp nhận cấu hình `start_offset`/`end_offset` kèm YouTube URL. Thử video công khai khác hoặc chế độ cũ.
- Nút play chưa sáng: chưa đủ hai đoạn đầu, xem thông báo trạng thái.
- Tua xa: bình thường sẽ dừng video để ưu tiên dịch khu vực mới, hiện `Đang chuẩn bị giọng đọc tiếng Việt…`, rồi tự phát tiếp khi đoạn mới sẵn sàng.
- Backend khởi động lại giữa chừng: extension tự tạo lại job từ cache (cùng `job_id` nếu giữ nguyên cấu hình) và tiếp tục các đoạn đã có; không cần bấm Start lại.
- Extension biến mất/bị tắt sau khi reload: bật **Developer mode** trong `chrome://extensions` rồi bật lại extension. Chrome 156 có thể tắt extension unpacked nếu Developer mode tắt.
- Không có âm thanh: đảm bảo cửa sổ CMD còn mở, Chrome phát video bằng nút Play, đúng video và cùng tab. Kiểm tra popup hiển thị đúng provider (`Edge TTS · vi-VN-NamMinhNeural` hoặc tên model Gemini).
- Audio lệch tiếng gốc: timestamp từ mô hình là ước tính; đồng bộ theo timeline đoạn WAV không đồng nghĩa lip-sync. Nâng cấp tiếp theo cần forced alignment và căn chỉnh độ dài lời Việt.
- Chế độ phụ đề: nếu bấm **Start Vietnamese Subtitles** mà chưa thấy chữ, kiểm tra đã bấm **Show Vietnamese subtitles** chưa và đoạn tại vị trí đang xem đã dịch xong chưa (popup hiện `Đã dịch x/y đoạn phụ đề`). Chế độ này **không tạm dừng video** khi thiếu phụ đề; video tiếp tục phát bằng tiếng gốc.
- Chế độ phụ đề không có âm thanh lồng tiếng: đúng thiết kế. Dùng **Start Progressive Dubbing** nếu muốn nghe giọng Việt.
- Video dài: bản V2 bỏ giới hạn 10 phút trong chế độ progressive, nhưng **không có nguồn API vô hạn**. Mặc định giới hạn 12 giờ để tránh gửi sai dữ liệu; có thể chỉnh bằng `.env`.

## 5. Kiến trúc kỹ thuật

```text
Chrome popup
  └── START_PROGRESSIVE (mode=dub | subtitles)
       └── localhost POST /stream/jobs
            └── StreamJob worker (demand-driven)
                 ├── Gemini video URL + [start_offset,end_offset]
                 ├── JSON Vietnamese cues
                 ├── [dub] Gemini TTS (hoặc Edge TTS dự phòng)
                 └── [dub] WAV mono/24kHz đúng độ dài chunk + JSON cues → disk cache
                     [subtitles] chỉ JSON cues → disk cache riêng

Chrome content.js (YouTube)
  ├── hỏi /stream/jobs/:id qua background proxy
  ├── [dub] tải WAV các chunk đã sẵn sàng
  ├── [dub] AudioContext.decodeAudioData + BufferSource scheduling
  ├── [subtitles] tải cue JSON, hiện phụ đề theo video.currentTime
  ├── video.currentTime là clock chủ
  ├── [dub] tạm dừng nếu buffer rỗng, thử tiếp tục khi nạp xong
  └── POST /seek để đổi thứ tự ưu tiên dịch khi người dùng tua
```

Mã nguồn nằm ở `backend/progressive.py`, các endpoint mới ở cuối `backend/app.py`, bộ phát ở `extension/content.js`, service worker ở `extension/background.js`.

## 6. Kiểm tra không cần API key

Ở thư mục gốc project, sau khi cài dependency:

```powershell
.\backend\.venv\Scripts\python.exe tests\progressive_test.py
.\backend\.venv\Scripts\python.exe tests\subtitles_test.py
.\backend\.venv\Scripts\python.exe tests\model_fallback_test.py
.\backend\.venv\Scripts\python.exe tests\restart_test.py
.\backend\.venv\Scripts\python.exe tests\smoke_test.py
```

Các test mock kiểm tra: chia WAV 20s, bộ đệm đầu 80s, ưu tiên seek, cache, retry đoạn lỗi; **chế độ chỉ phụ đề** tạo đúng cue JSON, không gọi TTS/không tạo WAV, khóa cache tách biệt, endpoint audio trả 409; fallback model theo 503/404/timeout, dừng khi 400, retry một lượt khi tất cả bị throttle; bọc PCM thô thành WAV; tái tạo job sau restart; endpoint HTTP. **Test mock không thay thế việc nghe thử bằng tai trên máy của bạn.**

Kiểm tra API thật (tốn quota):

```powershell
cd backend
.\.venv\Scripts\python.exe doctor.py
```

## 7. Giới hạn và bảo mật

- Backend chỉ lắng nghe `127.0.0.1`; không triển khai ra mạng công khai. `/stream` chưa có hệ thống xác thực đa người dùng.
- Gemini API key không đặt trong mã JS; giữ file `.env` bí mật. `doctor.py` không bao giờ in khóa.
- Gemini Free Tier có quota; nội dung có thể được dùng để cải thiện dịch vụ theo chính sách của Google.
- Gemini YouTube URL là tính năng preview; khả năng nhận clip từ URL cần kiểm tra bằng key của bạn (`doctor.py --video-id ...`).
- **Chưa có xác nhận nghe thử của chủ dự án** trên Chrome cá nhân; kết quả tự động chỉ chứng minh luồng dữ liệu/đồng bộ, không chứng minh chất lượng giọng đọc.
- Edge TTS là dịch vụ **không chính thức**, có thể đổi/ngừng hoạt động; dùng khi bạn chấp nhận điều đó. Gemini TTS chậm hơn thời gian phát trên Free Tier.
- Điều chỉnh `Original sound` giảm cả nhạc nền. V2 chưa có AI tách riêng giọng gốc để giữ nhạc và hiệu ứng.
- TTS có thể bị ép tốc độ để nằm trong timestamp, đôi khi gây gấp hoặc bị cắt câu. Phần này cần kỹ thuật forced alignment và dịch ngắn gọn hơn để đạt chất lượng thương mại.
- Chế độ chỉ phụ đề chưa có live test nhiều đoạn trên video dài (mới nghiệm thu trên video ngắn `jNQXAC9IVRw`); cơ chế chia đoạn/cache dùng chung với chế độ lồng tiếng đã được kiểm bằng test tự động.
- Chế độ chỉ phụ đề chưa xuất file SRT hoàn chỉnh; phụ đề hiển thị trực tiếp trên trang và lưu cue JSON theo từng đoạn trong `backend/jobs/<job_id>/`.
- Chưa có chế độ lưu toàn bộ MP3/SRT đã ghép trong progressive: tùy chọn Download MP3/SRT trong popup thuộc **Classic mode**.
- Tác vụ chạy trong một tiến trình backend; khởi động lại sẽ dừng worker hiện tại nhưng cache vẫn dùng lại được.

Nguồn tài liệu:
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://ai.google.dev/gemini-api/docs/pricing
- https://developer.chrome.com/docs/extensions/reference/api/tabCapture
