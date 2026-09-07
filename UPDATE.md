# PostureCare — Danh sách cập nhật chức năng

**Nhánh:** `dev-tracking`  
**Ngày:** 2026-09-02  
**Phạm vi:** cập nhật theo tầng chức năng (không liệt kê từng file).

Hệ thống chuyển từ “cảnh báo theo từng lần đọc cảm biến” sang **phiên làm việc có trạng thái**, với điều phối thông báo, báo cáo cuối phiên, và điều khiển LED theo mức nghiêm trọng.

---

## 1. Quản lý phiên làm việc (Session)

- Mở **một phiên theo thiết bị** khi bắt đầu camera; tab thứ hai **gắn vào phiên đang chạy**, không tạo phiên mới.
- Máy trạng thái: `idle` → `calibrating` (STARTING) → `monitoring` → `away` / `break` → `ended`.
- **Stop Stream = Stop Session:** dừng camera đóng phiên, tắt LED, sinh insight cuối phiên.
- Thời gian ân hạn ~20 giây lúc STARTING; mất mặt ~30 giây chuyển `away`; vắng lâu reset thời gian ngồi; idle ~15 phút tự kết thúc phiên.
- Wizard 4 bước trên UI: chọn nguồn camera → xem trước → calibrate tư thế → vào dashboard (kết thúc ân hạn).
- API phiên: snapshot, snooze, ack, DND, bắt đầu/kết thúc nghỉ, ready, timeline, report.

---

## 2. Điều phối thông báo (Notification Governor)

- 4 mức: **normal → notice → alert → escalated**, không bắn LLM mỗi lần vượt ngưỡng.
- Cờ nguy hiểm (ngồi quá gần nghiêm trọng, cúi/ngẩng/nghiêng đầu) khác cờ chỉ nhắc (quay đầu, tối/sáng, chớp mắt ít, ngồi xa).
- Cờ `too_long` **tắt**: gợi ý nghỉ 20 phút đi qua governor, không cạnh tranh cờ song song.
- Hành vi: notice ngắn → LLM khi đủ điều kiện → lặp lại có backoff → leo thang → gợi ý nghỉ.
- Người dùng: **Snooze 10 phút**, **DND**, **ack**, **bắt đầu nghỉ**; trạng thái governor **lưu theo phiên** (restart / tab thứ hai không reset cooldown).
- Câu nói ngắn (`spoken_line`) để dashboard đọc TTS; toast kèm nút hành động.

---

## 3. Dashboard & trải nghiệm người dùng

- Badge trạng thái phiên + chip **DND** trên header; nguồn Sensors / Cam / AI.
- Timeline rủi ro 10 phút của phiên hiện tại; nút **Session Report** mở trang in được.
- Get Advice thủ công tách khỏi auto-governor; thêm chế độ **Explain**.
- Cài đặt **demo ngồi** (mặc định 3 phút) để demo nghỉ ngắn, không bật lại `too_long`.
- Focus Hub đồng bộ nghỉ Pomodoro với trạng thái `break` của phiên.
- Toast / TTS theo mức governor; Focus Hub vẫn có calibrate tư thế 0°.

---

## 4. Đánh giá rủi ro & cấu hình rules

- Ngưỡng tư thế / khoảng cách / ánh sáng / chớp mắt đọc từ `rules.json`; tracking AI lấy cùng nguồn qua `/api/rules`.
- Roll mặc định 15°; khoảng cách mục tiêu 50–70 cm; lux 300–500.
- Detector bỏ qua giá trị cảm biến `null` (mất mặt / nguồn chết) thay vì giả định 0.
- `GET /sensor` chỉ đọc snapshot phiên, **không** chạy side-effect (LED, history, LLM).
- Poller / session manager là nơi duy nhất ghi history, LED, governor.

---

## 5. Lịch sử, insight và báo cáo phiên

- SQLite thêm bảng `sessions` (trạng thái, thời gian phơi nhiễm, `governor_json`).
- Mỗi lần đọc gắn `session_id`; insight **theo phiên**, không theo cửa sổ thời gian trượt tự do.
- Báo cáo in: thống kê phiên, sparkline rủi ro, advice đã lưu — **không gọi LLM lần nữa**.
- LLM: chế độ `advice` / `explain`; prompt yêu cầu Context + Advice + Spoken; fallback heuristic khi thiếu API key.

---

## 6. Computer Vision (`tracking_AI`)

- Đồng bộ Firebase nền: pitch/roll/yaw, khoảng cách mắt–màn, EAR, nhịp chớp, `face_present`, thời gian mất mặt, thời lượng từng cờ.
- Mất mặt → không giả lập góc/khoảng cách; `posture_status = NO_FACE`.
- Calibrate tư thế / khoảng cách hai chiều với Firebase; lưu `calibration.json`.
- Relay MJPEG nội bộ (`:8089/stream`) để dashboard xem feed đã overlay AI.
- Placeholder “CONNECTING…” khi chưa có camera (macOS từ chối quyền không làm crash process).
- Ngưỡng overlay lấy từ dashboard API, rồi `rules.json`, rồi mặc định cứng.

---

## 7. Firmware ESP32 (`firmware/`)

- `/led_state` dạng `{ red, green, blink }`; giữ tương thích node boolean cũ.
- Đỏ / nhấp nháy = cảnh báo (nhấp ~2 Hz trên firmware, không phụ thuộc chu kỳ poll backend).
- Xanh = đang theo dõi, tắt khi có cảnh báo.
- Đọc LED mỗi ~400 ms; cảm biến vẫn upload ~2 s; loop không `delay(2000)` để nhấp nháy mượt.
- Backend tắt LED khi dừng phiên.

Đã **gỡ thư mục trùng** `firmware and tinyML/` — firmware chính thức chỉ còn `firmware/`.

---

## 8. Khởi chạy & môi trường

- `run.ps1` / `run.sh` (root): hai venv riêng — dashboard FastAPI và tracking MediaPipe.
- Tracking **Python 3.10–3.12** (khuyến nghị 3.11); dashboard-only cho phép 3.13.
- Tự tạo venv, cài dependency theo hash `requirements.txt`, copy `.env` / `rules.json` từ example nếu thiếu.
- Gỡ `start_web.sh` / `stop_web.sh`; dùng launcher root hoặc `dashboard/gPBL/run.*`.
- Line ending: `.gitattributes` (`LF` mặc định, `.ps1`/`.bat` giữ `CRLF`).

---

## 9. Bài báo (`paper/`)

Thêm khung LaTeX PRIME/arXiv: Introduction, literature review, research design, bibliography, template.

---

## Tóm tắt hành vi người dùng

| Trước | Sau |
|---|---|
| Mỗi lần đọc cảm biến có thể kích LLM / cảnh báo | Governor theo thời gian giữ cờ + backoff + snooze/DND |
| Không có khái niệm phiên | Một phiên/thiết bị; Stop camera = đóng phiên + báo cáo |
| LED đỏ đơn giản | Đỏ / xanh / nhấp nháy theo mức phiên |
| Insight theo cửa sổ phút | Insight + report theo `session_id` |
| Tracking tự ngưỡng | Tracking dùng cùng `rules.json` với dashboard |
| Hai cây firmware | Một cây `firmware/` |
