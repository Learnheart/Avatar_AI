# Danh Sách Công Việc — Magic Mirror AI Avatar

> **Cập nhật lần cuối:** 2026-03-07

---

## 1. Các Task Đã Hoàn Thành (Phase 1-3)

| Task | Module | Trạng thái | Ngày |
|------|--------|------------|------|
| TASK-01 | Dựng khung dự án | XONG | 2026-03-03 |
| TASK-02 | Máy chủ WS | XONG | 2026-03-03 |
| TASK-03 | Module thị giác | XONG | 2026-03-03 |
| TASK-04 | Module VAD | XONG | 2026-03-03 |
| TASK-05 | Bộ điều phối | XONG | 2026-03-03 |
| TASK-06 | Gemini Client | XONG | 2026-03-03 |
| TASK-07 | Lớp trừu tượng TTS | XONG | 2026-03-03 |
| TASK-08 | Tích hợp Pipeline | XONG | 2026-03-03 |
| TASK-09 | Three.js + VRM | XONG | 2026-03-03 |
| TASK-10 | WS Client | XONG | 2026-03-03 |
| TASK-11 | Hoạt ảnh theo dõi đầu | XONG | 2026-03-03 |
| TASK-12 | Hoạt ảnh nghỉ | XONG | 2026-03-03 |
| TASK-13 | Quản lý âm thanh | XONG | 2026-03-03 |
| TASK-14 | Đồng bộ môi | XONG | 2026-03-03 |

---

## 2. Các Task Còn Lại — Phase 4 (Tích hợp E2E)

### TASK-15: Kiểm Thử Tích Hợp Đầu Cuối

**Mức ưu tiên:** P0 — QUAN TRỌNG
**Phụ thuộc:** TASK-01~14 (tất cả đã xong), mô hình VRM, camera, Gemini API key
**Khối lượng ước tính:** 3 giờ

**Điều kiện tiên quyết:**
- [ ] Tải mô hình VRM -> `frontend/public/models/avatar.vrm`
- [ ] Xác nhận `.env` có `GEMINI_API_KEY` hợp lệ
- [ ] Camera đã kết nối (DroidCam hoặc USB)
- [ ] Cài NVIDIA Container Toolkit (nếu dùng Docker)

**Danh sách kiểm tra:**
- [ ] `docker compose up` hoặc khởi động local -> backend + frontend chạy không lỗi
- [ ] `curl /health` -> `status: ok`, `camera_connected: true`
- [ ] Frontend tải -> avatar VRM hiển thị toàn màn hình, 60 FPS
- [ ] WebSocket kết nối -> trạng thái "ONLINE"
- [ ] Đứng trước camera -> avatar quay đầu theo (< 200ms)
- [ ] Nói vào mic -> backend log trạng thái TRACKING -> LISTENING
- [ ] Dừng nói -> Gemini trả lời, âm thanh phát qua loa, đồng bộ môi hoạt động
- [ ] Kiểm tra ngắt lời: nói khi avatar đang trả lời -> avatar dừng, chuyển LISTENING
- [ ] Không nói 30 giây -> phiên Gemini đóng (log xác nhận)
- [ ] Rời khỏi camera 3 giây -> avatar quay về trung tâm, trạng thái -> IDLE
- [ ] Chạy liên tục 30 phút -> không sập, VRAM < 2 GB

**Các vấn đề có thể gặp phải:**
1. Chữ ký hàm Gemini SDK `send_realtime_input()` khác -> cần điều chỉnh `gemini_client.py`
2. Không khớp định dạng âm thanh (tần số lấy mẫu, mã hóa) giữa phản hồi Gemini và bộ giải mã frontend
3. Đường dẫn thiết bị camera khác giữa Docker và local
4. Vấn đề CORS hoặc nội dung hỗn hợp khi frontend gọi WS

### TASK-16: Đánh Giá Chất Lượng TTS

**Mức ưu tiên:** P1
**Phụ thuộc:** TASK-15 (E2E phải chạy được trước)
**Khối lượng ước tính:** 2 giờ

**Tiêu chí đánh giá (từ PRD BR-009):**
- [ ] Gemini TTS tiếng Việt: thanh điệu 6 thanh >= 90% đúng
- [ ] Gemini TTS tiếng Việt: nghe tự nhiên, không giọng "ngoại quốc"
- [ ] Nếu thất bại 1 trong 2 -> chuyển `TTS_MODE=azure` hoặc `viettel`
- [ ] Kiểm thử Azure TTS: đổi `.env`, khởi động lại, kiểm tra đầu ra âm thanh
- [ ] Kiểm thử Viettel TTS: đổi `.env`, khởi động lại, kiểm tra đầu ra âm thanh

---

## 3. Vấn Đề Đã Biết — Cần Sửa

### PHẢI SỬA (trước/trong khi kiểm thử E2E)

| ID | Vấn đề | File | Mô tả cách sửa | Trạng thái |
|----|--------|------|-----------------|------------|
| ISS-01 | Chữ ký `send_realtime_input()` của Gemini chưa xác nhận | `gemini_client.py` | Đã xác nhận hoạt động đúng. Đổi model sang `gemini-2.5-flash-native-audio-latest` | **ĐÃ SỬA** (2026-03-07) |
| ISS-03 | `_is_zero_rotation()` nhận diện sai | `orchestrator.py`, `vision.py`, `models.py` | Thêm `TrackingResult` dataclass, dùng `face_detected` flag | **ĐÃ SỬA** (2026-03-07) |
| ISS-08 | Model `gemini-2.0-flash-live-001` không còn tồn tại | `config.py`, `.env` | Đổi sang `gemini-2.5-flash-native-audio-latest` | **ĐÃ SỬA** (2026-03-07) |
| ISS-09 | Double VAD processing trong SPEAKING state | `orchestrator.py` | Di chuyển `return` ra ngoài block `if speech_start` | **ĐÃ SỬA** (2026-03-07) |

### NÊN SỬA (cải thiện độ tin cậy)

| ID | Vấn đề | File | Mô tả cách sửa |
|----|--------|------|-----------------|
| ISS-04 | Vision Queue bỏ sót frame | `orchestrator.py` | Đo hiệu năng, cân nhắc mẫu chỉ-lấy-frame-mới-nhất |
| ISS-05 | AudioContext 16kHz bị ép tần số | `audio-manager.js` | Kiểm thử trên Chromium kiosk, thêm lấy mẫu lại nếu cần |

### CÓ THÌ TỐT (sau POC)

| ID | Vấn đề | File | Mô tả cách sửa |
|----|--------|------|-----------------|
| ISS-06 | NVIDIA Container Toolkit | Hệ thống host | Đã ghi trong hướng dẫn cài đặt |
| ISS-07 | Silero VAD cần mạng lần đầu | `vad.py` | Đã tải trước trên máy phát triển |

---

## 4. Cải Tiến Tương Lai (V2 — Ngoài Phạm Vi POC)

| ID | Tính năng | Mô tả | Tham chiếu |
|----|-----------|-------|------------|
| V2-01 | Nhận thức ngữ cảnh thị giác | Gemini nhận diện biểu cảm, trang phục | PRD V2-01 |
| V2-02 | Nhận diện khách VIP | InsightFace local, danh sách VIP cứng | PRD V2-02 |
| V2-03 | Bộ lọc One Euro | Làm mượt theo dõi đầu, giảm rung | PRD OOS-02 |
| V2-04 | Ánh xạ viseme (5 nguyên âm) | Blendshape A/I/U/E/O | PRD OOS-05 |
| V2-05 | Khử nhiễu | RNNoise hoặc tương đương | PRD OOS-01 |
| V2-06 | Theo dõi nhiều người | Theo dõi nhiều khuôn mặt, chuyển tiêu điểm | PRD OOS-06 |
| V2-07 | Bảng điều khiển giám sát | Chỉ số sức khỏe hệ thống, phân tích phiên | PRD OOS-07 |
| V2-08 | Hoạt động offline | STT + TTS nội bộ khi mất mạng | PRD OOS-03 |

---

## 5. Nhật Ký Quyết Định

| Ngày | Quyết định | Lý do |
|------|-----------|-------|
| 2026-03-03 | Dùng MediaPipe Tasks API thay vì `mp.solutions` cũ | `mp.solutions` đã bị xóa trong mediapipe >= 0.10.32 |
| 2026-03-03 | Build driver DroidCam từ mã nguồn GitHub | Bản zip đóng gói không tương thích với kernel 6.17 |
| 2026-03-03 | Dùng `npm install --legacy-peer-deps` | Xung đột phụ thuộc ngang hàng giữa các gói three.js |
| 2026-03-03 | Phase 3 làm song song với Phase 1-2 | Tiết kiệm thời gian, frontend không phụ thuộc backend nhiều |
| 2026-03-07 | Đổi model Gemini sang `gemini-2.5-flash-native-audio-latest` | Model cũ `gemini-2.0-flash-live-001` không còn hỗ trợ Live API |
| 2026-03-07 | Thêm `TrackingResult` thay vì `_is_zero_rotation()` | Logic cũ bị lỗi nghiêm trọng: nhầm nhìn thẳng = mất mặt |
| 2026-03-07 | Sửa double VAD processing (ISS-09) | Audio bị xử lý 2 lần qua VAD khi SPEAKING → hỏng trạng thái iterator |
| 2026-03-07 | Cài pytest + tạo bộ test tích hợp (25 tests) | Xác nhận pipeline backend hoạt động đúng mà không cần phần cứng |
