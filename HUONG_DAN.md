# Gemini YouTube Dubbing V2 — Giới thiệu sản phẩm & Hướng dẫn sử dụng

> Tài liệu này dành cho **người dùng cuối**: đọc xong là xem được.
> Cần chi tiết kỹ thuật, cấu hình chuyên sâu và troubleshooting đầy đủ: xem [`README_VI.md`](README_VI.md) (tiếng Việt) hoặc [`README.md`](README.md) (tiếng Anh).

---

## 1. Sản phẩm này là gì

Một extension Chrome + backend chạy trên máy bạn, biến video YouTube nước ngoài thành **tiếng Việt** theo hai cách: nghe giọng lồng tiếng, hoặc chỉ đọc phụ đề Việt mà vẫn giữ nguyên tiếng gốc.

Điểm khác biệt so với các công cụ dịch video khác:

- **Xem chỉ sau ~20–30 giây.** Gemini dịch và tạo giọng theo từng đoạn 20 giây. Có đủ 2 đoạn đầu là bấm play được, không cần chờ dịch cả video.
- **Vừa xem vừa dịch.** Máy chỉ xử lý tối đa 4 đoạn phía trước vị trí bạn đang xem. Tua đến đâu dịch đến đó, dừng xem là ngừng tốn hạn mức.
- **Không tải video, không vượt DRM/quảng cáo.** Backend chỉ gửi URL video công khai cho Gemini rồi nhận lại văn bản/dịch; phần âm thanh Việt phát đè lên bằng Web Audio của trình duyệt.
- **Xem lại không tốn phí lần hai.** Các đoạn đã dịch được cache trên đĩa (`backend/jobs/`); mở lại video cũ hoặc khởi động lại backend vẫn dùng được bản đã dịch.

## 2. Chọn chế độ nào

| | **Lồng tiếng dần** (Progressive Dubbing) | **Chỉ phụ đề** (Subtitles only) |
| --- | --- | --- |
| Âm thanh | Giọng Việt phát đè lên, tiếng gốc hạ xuống 13% (chỉnh được) | Tiếng gốc giữ nguyên 100% |
| Có tạo giọng đọc (TTS)? | Có (Edge TTS mặc định, hoặc Gemini TTS) | Không, không gọi TTS |
| Khi chưa dịch kịp đoạn kế tiếp | Video **tạm dừng** + hiện `Đang chuẩn bị giọng đọc tiếng Việt…`, tự chạy tiếp khi xong | Video **vẫn chạy bình thường**, phụ đề hiện sau khi dịch xong |
| Thời gian chờ ban đầu | ~20–30 giây (Edge TTS) | Nhanh hơn, chỉ cần đoạn đầu dịch xong |
| Tốn hạn mức Gemini | Lời gọi phân tích + (nếu dùng) TTS | Chỉ lời gọi phân tích |
| Phù hợp khi | Muốn nghe thụ động, xem giải trí | Họp/học, cần nghe tiếng gốc, hoặc muốn tiết kiệm quota |

## 3. Chuẩn bị (một lần)

**Yêu cầu:** Chrome 116 trở lên · Python 3.11+ (3.14 vẫn chạy tốt) · khóa Gemini API free tier · FFmpeg chỉ cần nếu dùng Classic mode.

1. Vào <https://aistudio.google.com/apikey>, tạo khóa API (project Free Tier).
2. Cài môi trường backend:

   ```powershell
   cd "<Đường dẫn>\youtube_gemini_progressive_v2\backend"
   .\setup_windows.bat
   ```

3. Mở `backend\.env` (setup tự tạo từ `.env.example`), dán khóa vào dòng `GEMINI_API_KEY=`. **Không dán khóa vào code extension, không chia sẻ file `.env`.**
4. Nạp extension: `chrome://extensions` → bật **Developer mode** → **Load unpacked** → chọn thư mục `youtube_gemini_progressive_v2\extension` → ghim icon **Gemini YouTube Dubbing (Progressive V2)** lên toolbar.

## 4. Xem mỗi ngày (30 giây là xong)

1. Chạy backend: double-click `backend\run_windows.bat` (giữ cửa sổ CMD mở). Kiểm tra `http://127.0.0.1:8765/health` trả `{"ok":true,...}`.
2. Mở một video YouTube **công khai**, có lời nói, mà bạn được phép xử lý.
3. Bấm icon extension:
   - Muốn nghe giọng Việt → **▶ Start Progressive Dubbing**.
   - Muốn giữ tiếng gốc → **💬 Start Vietnamese Subtitles**.
4. Chờ trạng thái báo đủ đoạn, rồi bấm **▶ Play buffered Vietnamese audio** (lồng tiếng) hoặc **▶ Show Vietnamese subtitles** (chỉ phụ đề).
5. Quay lại trang YouTube và bấm **Play** trên chính video (trình duyệt có thể chặn autoplay từ extension).

## 5. Lồng tiếng dần — từng bước

1. Bấm **▶ Start Progressive Dubbing**. Popup hiện `Đã dịch x/y đoạn • Đệm từ vị trí xem đến Ns`.
2. Chờ đủ 2 đoạn đầu (40 giây audio). Thanh timeline trong popup cho thấy đoạn nào đã xong.
3. Bấm **▶ Play buffered Vietnamese audio**, rồi play video trên YouTube.
4. Chỉnh trong bảng **VOICE & SUBTITLES**:
   - **Original sound** (mặc định 13%) — hạ tiếng gốc xuống nhỏ nhưng vẫn nghe được nhạc nền/hiệu ứng.
   - **Vietnamese dub** (mặc định 100%) — âm lượng giọng Việt.
   - **Show Vietnamese subtitles** — bật/tắt dòng phụ đề Việt trên video.
5. Khi xem tới đoạn chưa dịch kịp, video tự dừng và hiện `Đang chuẩn bị giọng đọc tiếng Việt…`; xong là tự chạy tiếp. Nếu Chrome chặn, nhấn Play trên YouTube.
6. Bấm **■ Stop streamed dubbing** để tắt: âm lượng gốc được khôi phục, phụ đề ẩn.

## 6. Chỉ phụ đề — từng bước

1. Bấm **💬 Start Vietnamese Subtitles**.
2. Bấm **▶ Show Vietnamese subtitles** (bật được ngay khi job đang dịch; phụ đề hiện dần theo từng đoạn xong).
3. Play video như bình thường. Tiếng Việt hiện đè dưới dạng phụ đề, bám theo `video.currentTime`.
4. **Bỏ chọn** checkbox **Show Vietnamese subtitles** nếu muốn tạm ẩn chữ; bấm **■ Stop subtitles** để tắt hẳn chế độ.

Chế độ này **không** chỉnh âm lượng, **không** tắt tiếng, **không** tạo file WAV và **không** gọi TTS. Cache riêng, không lẫn với bản lồng tiếng.

> **Dễ lẫn:** có hai chỗ trùng tên "Show Vietnamese subtitles". **Nút** `▶ Show Vietnamese subtitles` (mục PROGRESSIVE) bật chế độ phụ đề; **checkbox** `Show Vietnamese subtitles` (mục VOICE & SUBTITLES) chỉ ẩn/hiện dòng chữ.

## 7. Điều khiển khi đang xem

| Bạn làm | Kết quả |
| --- | --- |
| Tạm dừng video | Giọng Việt dừng theo (chế độ lồng tiếng) |
| Tua trong vùng đã dịch | Phát tiếp gần như ngay lập tức |
| Tua ra vùng chưa dịch | Lồng tiếng: dừng chờ + overlay rồi tự tiếp. Phụ đề: vẫn chạy, chữ hiện sau |
| Đổi tốc độ 1.25x/1.5x | Giọng Việt và phụ đề theo tốc độ mới |
| Khởi động lại backend giữa chừng | Bấm lại đúng nút Start: extension tự tạo lại job cũ, giữ nguyên đoạn đã cache |
| Đổi video khác | Làm lại bước Start cho video đó |

## 8. Cấu hình nhanh theo nhu cầu

Trong `backend\.env` (sửa xong khởi động lại backend):

| Muốn gì | Chỉnh gì |
| --- | --- |
| Chờ khởi động ngắn hơn | `STREAM_CHUNK_SECONDS=10` (nhiều lời gọi hơn) |
| Ít request hơn cho video dài | `STREAM_CHUNK_SECONDS=30`, `STREAM_LOOKAHEAD_CHUNKS=3` |
| Dự phòng model khi quá tải | `GEMINI_MODEL_FALLBACKS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash` |
| Giọng đọc khác | `EDGE_VOICE=vi-VN-HoaiMyNeural` (mặc định `vi-VN-NamMinhNeural`) |
| Giọng Gemini tự nhiên hơn (chậm hơn) | `TTS_PROVIDER=gemini` — chỉ dùng cho đoạn ngắn |
| Xem video dài hơn | `MAX_PROGRESSIVE_SECONDS=43200` (mặc định đã là 12 giờ) |

Cấu hình mặc định trong `.env.example` (`GEMINI_MODEL=gemini-3.5-flash-lite` + `TTS_PROVIDER=edge`) là cấu hình đã đo thực tế và khuyến nghị.

## 9. Chi phí và hạn mức

- **Không phát sinh tiền** nếu project của bạn ở Free Tier và **chưa bật thanh toán**. Vượt hạn mức thì API trả lỗi `429`, không tự trừ phí.
- Mặc định dùng **Edge TTS** (miễn phí, không chính thức) nên **không tốn quota Gemini TTS**.
- Ước lượng lời gọi phân tích: video **15 phút ≈ 46** lời gọi, **30 phút ≈ 90**. Với hạn mức quan sát được của `gemini-3.5-flash-lite` (15 RPM, 500 RPD), một ngày xem vài video 30 phút vẫn trong ngưỡng.
- Gemini TTS trên Free Tier chỉ ~3 RPM / 10 RPD mỗi model — **không đủ để xem liên tục**, và thường mất 60–90 giây cho một đoạn 20 giây.
- Trước phiên dài, chạy chuẩn đoán (không in khóa, mọi lời gọi có timeout):

  ```powershell
  cd backend
  .\.venv\Scripts\python.exe doctor.py
  ```

## 10. Gặp vấn đề

| Hiện tượng | Cách xử lý |
| --- | --- |
| Popup báo backend offline | Chạy `backend\run_windows.bat`, mở `/health` |
| Nút play chưa bấm được | Chưa đủ đoạn đầu; xem dòng trạng thái, hoặc video chưa phải `/watch` |
| Video dừng và hiện overlay | Đoạn kế tiếp chưa dịch xong — tự tiếp tục; tua gần vùng đã đệm để xem ngay |
| `429` / `RESOURCE_EXHAUSTED` | Hết hạn mức ngày. Backend tự thử model dự phòng; giảm bằng `TTS_PROVIDER=edge`, tăng `STREAM_CHUNK_SECONDS`, hoặc chờ reset |
| `503` / timeout | Model quá tải; backend bỏ qua và thử model kế, popup ghi `(dự phòng)` |
| Extension bị tắt sau khi reload | Bật lại **Developer mode** ở `chrome://extensions` rồi bật extension |
| Không có giọng Việt | Kiểm tra popup có hiện đúng provider (`Edge TTS · ...`); nhớ bấm Play trên YouTube |
| Phụ đề không hiện (chế độ phụ đề) | Bấm **▶ Show Vietnamese subtitles** chưa? Đoạn đó dịch xong chưa? Checkbox phụ đề đang bật chưa? |

## 11. Giới hạn thật lòng cần biết

- **Không phải lip-sync.** Timestamp do model ước lượng; giọng Việt có thể đến sớm/muộn hoặc bị nói gấp.
- **Giảm tiếng gốc là giảm cả nhạc nền.** Bản này chưa tách giọng khỏi nhạc.
- **Chưa xuất file trong progressive.** Download MP3/SRT thuộc Classic mode; phụ đề chỉ hiển thị trực tiếp.
- **Edge TTS là dịch vụ không chính thức**, có thể thay đổi hoặc ngừng.
- **Free Tier có thể dùng nội dung bạn gửi để cải thiện sản phẩm** theo chính sách Google — không xử lý video nhạy cảm.
- **Backend không có xác thực**, chỉ lắng nghe `127.0.0.1`; dùng trên máy cá nhân, đừng mở ra mạng công cộng.
- **Chỉ xử lý video bạn có quyền.** Tuân thủ Điều khoản YouTube và luật bản quyền.

## 12. Trạng thái kiểm chứng

Đã chạy thật trên Chrome for Testing 156 với video công khai 15:04: tạo bộ đệm, phát đè tiếng Việt, phụ đề đồng bộ, pause/seek/đổi tốc độ, tự dừng chờ bộ đệm, cache sống qua restart backend. Chế độ chỉ phụ đề đã chạy thật (video ngắn): phụ đề đúng nhịp, âm lượng gốc không đổi, không gọi TTS.

**Chưa có xác nhận bằng tai của chủ dự án** về chất lượng giọng đọc — đây là bước cuối bạn tự cảm nhận.

## 13. Tài liệu liên quan

- [`README_VI.md`](README_VI.md) — cài đặt Windows, di trú từ V1, kiến trúc, troubleshooting đầy đủ.
- [`README.md`](README.md) — bản tiếng Anh.
