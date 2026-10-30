# Gemini YouTube Dubbing V2 — phát theo bộ đệm như YouTube

Bản nâng cấp độc lập của `youtube_gemini_dubbing_mvp`. **Không ghi đè thư mục V1.**

V2 dịch và tổng hợp giọng Việt **theo từng đoạn nhỏ** từ URL YouTube công khai, rồi phát WAV bằng Web Audio API theo `video.currentTime`. Nó không đợi dịch hết video mới cho xem.

> **Đây là phiên bản thử nghiệm, không bảo đảm không gián đoạn**: các lời gọi Gemini có thể chậm hơn tốc độ xem hoặc bị giới hạn quota, khi đó trình phát **tạm dừng để chờ bộ đệm**. Chưa kiểm thử thực tế trên tài khoản Gemini hoặc Chrome ở máy Windows của bạn; những test đi kèm dùng Gemini giả lập.

## Thay đổi so với bản trước

| Tính năng | V1 | V2 Progressive |
| --- | --- | --- |
| Cách xử lý | Toàn video, MP3 duy nhất | Clip 20 giây, WAV độc lập |
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

Mở `backend/.env` của V2 và bổ sung các dòng sau nếu chưa có:

```env
STREAM_CHUNK_SECONDS=20
STREAM_LOOKAHEAD_CHUNKS=4
MAX_PROGRESSIVE_SECONDS=43200
```

Giữ nguyên các biến Gemini và giọng đọc từ V1. API key vẫn **chỉ ở backend**.

Python 3.14: file `requirements.txt` V2 đã thêm `audioop-lts` tự động để tránh lỗi `No module named 'pyaudioop'`.

Chạy server:

```powershell
.\run_windows.bat
```

Mở `http://127.0.0.1:8765/health` và kiểm tra `ok: true`.

## 2. Cài Extension V2

1. Vào `chrome://extensions` và **tắt V1** để hai extension không cùng can thiệp audio YouTube.
2. Bật **Developer mode** → **Load unpacked** → chọn thư mục `youtube_gemini_progressive_v2\extension`.
3. Mở lại tab YouTube hoặc tải lại bằng F5.
4. Trong popup, bấm **Start Progressive Dubbing**.
5. Trạng thái sẽ cho biết số đoạn đã dịch. Khi nút **Play buffered Vietnamese audio** được bật, bấm nút đó.
6. Quay lại YouTube và bấm Play. Bạn có thể tạm dừng, tua, thay đổi tốc độ phát và chỉnh âm lượng.

V2 mặc định cần có 2 đoạn đầu (40 giây audio đã tổng hợp) trước khi cho phát. **Đây không có nghĩa bạn phải chờ 40 giây thực tế**: Gemini có thể cần lâu hơn (hoặc ít hơn) để tạo 40 giây lồng tiếng.

Khi sắp hết bộ đệm, nếu Gemini chưa tạo xong đoạn kế tiếp, V2 tự tạm dừng video và hiện `Đang chuẩn bị giọng đọc tiếng Việt…`; khi sẵn sàng sẽ thử tiếp tục. Nếu Chrome chặn phát tự động, nhấn Play trên YouTube.

## 3. Cách chỉnh dung lượng bộ đệm

File `backend/.env`:

```env
STREAM_CHUNK_SECONDS=20
STREAM_LOOKAHEAD_CHUNKS=4
```

- Đoạn **10s**: đoạn đầu có thể ra sớm hơn, nhưng tốn nhiều lời gọi Gemini/TTS, dễ chạm giới hạn Free Tier.
- Đoạn **20s**: cân bằng độ trễ đầu và số request; khuyến nghị.
- Đoạn **30–40s**: ít request, cache đủ dài nhưng thời gian chờ khởi động có thể tăng.
- Lookahead **4**: chuẩn bị tối đa 80 giây gần vị trí đang phát; sẽ dừng gọi Gemini nếu đã đệm đủ.

Sau khi chỉnh `.env`, khởi động lại backend. Cache sử dụng khóa gồm video, model, voice và số giây/đoạn để tránh dùng nhầm bản cũ.

## 4. Lỗi thường gặp

- `429 RESOURCE_EXHAUSTED`: hạn mức Free Tier/Gemini không đủ. V2 sẽ hiện lỗi, không thể đảm bảo phát liên tục. Có thể giảm số cuộc gọi TTS bằng cách dùng giọng Edge TTS **không chính thức** (`TTS_PROVIDER=edge`) nếu bạn chấp nhận dịch vụ bên ngoài và chính sách của nó.
- `404 model not found`: kiểm tra tên model và project/quyền truy cập.
- `clip processing`/`INVALID_ARGUMENT`: trên tài khoản/video của bạn, Gemini có thể chưa chấp nhận cấu hình `start_offset`/`end_offset` kèm YouTube URL. Thử chế độ cũ hoặc audio được phép xử lý.
- Nút play chưa sáng: chưa đủ hai đoạn đầu, xem thông báo trạng thái.
- Tua xa: bình thường sẽ dừng video để ưu tiên dịch khu vực mới.
- Không có âm thanh: đảm bảo cửa sổ CMD còn mở, Chrome phát video bằng nút Play, đúng video và cùng tab.
- Audio lệch tiếng gốc: timestamp từ mô hình là ước tính; đồng bộ theo timeline đoạn WAV không đồng nghĩa lip-sync. Nâng cấp tiếp theo cần forced alignment và căn chỉnh độ dài lời Việt.
- Video dài: bản V2 bỏ giới hạn 10 phút trong chế độ progressive, nhưng **không có nguồn API vô hạn**. Mặc định giới hạn 12 giờ để tránh gửi sai dữ liệu; có thể chỉnh bằng `.env`.

## 5. Kiến trúc kỹ thuật

```text
Chrome popup
  └── START_PROGRESSIVE
       └── localhost POST /stream/jobs
            └── StreamJob worker (demand-driven)
                 ├── Gemini video URL + [start_offset,end_offset]
                 ├── JSON Vietnamese cues
                 ├── Gemini TTS (hoặc Edge TTS dự phòng)
                 └── WAV mono/24kHz đúng độ dài chunk + JSON cues → disk cache

Chrome content.js (YouTube)
  ├── hỏi /stream/jobs/:id qua background proxy
  ├── tải WAV các chunk đã sẵn sàng
  ├── AudioContext.decodeAudioData + BufferSource scheduling
  ├── video.currentTime là clock chủ
  ├── tạm dừng nếu buffer rỗng, thử tiếp tục khi nạp xong
  └── POST /seek để đổi thứ tự ưu tiên dịch khi người dùng tua
```

Mã nguồn nằm ở `backend/progressive.py`, các endpoint mới ở cuối `backend/app.py`, bộ phát ở `extension/content.js`, service worker ở `extension/background.js`.

## 6. Kiểm tra không cần API key

Ở thư mục gốc project, sau khi cài dependency:

```powershell
.\backend\.venv\Scripts\python.exe tests\progressive_test.py
.\backend\.venv\Scripts\python.exe tests\smoke_test.py
```

Test kiểm tra cách chia WAV 20s, bộ đệm đầu 80s, ưu tiên seek, cache; thử endpoint HTTP. **Các bài test không chứng minh Gemini API thực tế hay Chrome playback hoạt động chính xác.**

## 7. Giới hạn và bảo mật

- Backend chỉ lắng nghe `127.0.0.1`; không triển khai ra mạng công khai. `/stream` chưa có hệ thống xác thực đa người dùng.
- Gemini API key không đặt trong mã JS; giữ file `.env` bí mật.
- Gemini Free Tier có quota; nội dung có thể được dùng để cải thiện dịch vụ theo chính sách của Google.
- Gemini YouTube URL là tính năng preview; khả năng nhận clip từ URL cần kiểm tra bằng key của bạn.
- Điều chỉnh `Original sound` giảm cả nhạc nền. V2 chưa có AI tách riêng giọng gốc để giữ nhạc và hiệu ứng.
- TTS có thể bị ép tốc độ để nằm trong timestamp, đôi khi gây gấp hoặc bị cắt câu. Phần này cần kỹ thuật forced alignment và dịch ngắn gọn hơn để đạt chất lượng thương mại.
- Chưa có chế độ lưu toàn bộ MP3/SRT đã ghép trong progressive: tùy chọn Download MP3/SRT trong popup thuộc **Classic mode**.

Nguồn tài liệu:
- https://ai.google.dev/gemini-api/docs/video-understanding
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://ai.google.dev/gemini-api/docs/pricing
- https://developer.chrome.com/docs/extensions/reference/api/tabCapture
