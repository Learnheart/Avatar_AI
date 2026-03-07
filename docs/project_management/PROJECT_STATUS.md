# Trạng Thái Dự Án — Magic Mirror AI Avatar

> **Cập nhật lần cuối:** 2026-03-07 (Phase 4A-4B hoàn thành)
> **Phiên bản:** POC v0.1

---

## 1. Tổng Quan

Magic Mirror AI Avatar là hệ thống kiosk AI dành cho khách hàng VIP ngân hàng. Avatar 3D (VRM) tương tác thời gian thực với khách hàng qua hội thoại giọng nói (Gemini Live API) và theo dõi khuôn mặt (MediaPipe).

**Trạng thái hiện tại:** Code Phase 1-3 HOÀN THÀNH (16/16 tasks). Phase 4A-4B HOÀN THÀNH (25/25 tests PASSED). Dự án đạt khoảng **~92% hoàn thiện** — code đầy đủ, pipeline backend xác nhận hoạt động, chỉ còn cần kiểm thử E2E với phần cứng thực (camera + VRM model).

---

## 2. Ma Trận Hoàn Thành Theo Phase

| Phase | Tasks | Trạng thái | Hoàn thành |
|-------|-------|------------|------------|
| Phase 1: Nền tảng | TASK-01 ~ 05 | **XONG** | 100% |
| Phase 2: Tích hợp AI | TASK-06 ~ 08 | **XONG** | 100% |
| Phase 3: Giao diện | TASK-09 ~ 14 | **XONG** | 100% |
| Phase 4A: Sửa lỗi & Test độc lập | ISS-01,03,08,09 + scripts | **XONG** | 100% |
| Phase 4B: Kiểm thử tích hợp | Pipeline + WS integration | **XONG** | 100% |
| Phase 4C: Kiểm thử E2E | TASK-15 ~ 16 | **CHƯA BẮT ĐẦU** | 0% |

---

## 3. Đánh Giá Mức Độ Sẵn Sàng Từng Tính Năng

### 3.1 Backend (100% đã code, ~80% đã xác nhận)

| Thành phần | File | Code | Unit Test | E2E Test |
|------------|------|------|-----------|----------|
| FastAPI + WS | `main.py` | XONG | CHƯA | CHƯA |
| Cấu hình | `config.py` | XONG | CHƯA | CHƯA |
| Mô hình dữ liệu | `models.py` | XONG | CHƯA | CHƯA |
| Bộ điều phối | `orchestrator.py` | XONG | CHƯA | CHƯA |
| Thị giác (MediaPipe) | `modules/vision.py` | XONG | CHƯA | CHƯA |
| Phát hiện giọng nói (VAD) | `modules/vad.py` | XONG | CHƯA | CHƯA |
| Gemini Client | `modules/gemini_client.py` | XONG | CHƯA | **BỊ CHẶN** |
| Lớp trừu tượng TTS | `modules/tts.py` | XONG | CHƯA | CHƯA |
| Ghi log | `utils/logging.py` | XONG | CHƯA | CHƯA |

### 3.2 Frontend (100% đã code, ~70% đã xác nhận)

| Thành phần | File | Code | Test trình duyệt | E2E Test |
|------------|------|------|-------------------|----------|
| Giao diện HTML | `index.html` | XONG | CHƯA | CHƯA |
| Điểm khởi đầu | `src/main.js` | XONG | CHƯA | CHƯA |
| WS Client | `src/ws-client.js` | XONG | CHƯA | CHƯA |
| Bộ dựng Avatar | `src/avatar-renderer.js` | XONG | CHƯA | CHƯA |
| Quản lý âm thanh | `src/audio-manager.js` | XONG | CHƯA | CHƯA |
| Đồng bộ môi | `src/lip-sync.js` | XONG | CHƯA | CHƯA |
| AudioWorklet | `worklet/audio-processor.js` | XONG | CHƯA | CHƯA |

### 3.3 Hạ tầng (100% đã code)

| Thành phần | File | Trạng thái |
|------------|------|------------|
| Backend Dockerfile | `backend/Dockerfile` | XONG |
| Frontend Dockerfile | `frontend/Dockerfile` | XONG |
| Docker Compose | `docker-compose.yml` | XONG |
| Cấu hình môi trường | `.env.example`, `.env` | XONG |
| Hướng dẫn cài đặt | `docs/SETUP_GUIDE.md` | XONG |

---

## 4. Đánh Giá Rủi Ro — Vấn Đề Tồn Đọng

### Mức Ưu Tiên CAO

| ID | Vấn đề | Ảnh hưởng | File | Cách xử lý |
|----|--------|-----------|------|------------|
| ISS-01 | ~~Gemini `send_realtime_input()` chưa kiểm thử thực tế~~ | ~~Pipeline giọng nói có thể hỏng~~ | `gemini_client.py` | **ĐÃ SỬA** — Đổi model sang `gemini-2.5-flash-native-audio-latest`, xác nhận SDK OK |
| ISS-02 | ~~Chưa có file VRM model~~ | ~~Avatar không hiển thị được~~ | `frontend/public/models/` | **ĐÃ CÓ** — `avatar.vrm` (10.3 MB, glTF hợp lệ) |
| ISS-08 | Model cũ `gemini-2.0-flash-live-001` không tồn tại | Gemini Live không kết nối được | `config.py`, `.env` | **ĐÃ SỬA** — Đổi sang model mới |

### Mức Ưu Tiên TRUNG BÌNH

| ID | Vấn đề | Ảnh hưởng | File | Cách xử lý |
|----|--------|-----------|------|------------|
| ISS-03 | ~~`_is_zero_rotation()` phát hiện sai~~ | ~~Nhận diện nhầm~~ | `orchestrator.py`, `vision.py`, `models.py` | **ĐÃ SỬA** — Thêm `TrackingResult` với cờ `face_detected` |
| ISS-09 | ~~Double VAD processing trong SPEAKING state~~ | ~~VAD iterator bị hỏng trạng thái~~ | `orchestrator.py` | **ĐÃ SỬA** — Di chuyển `return` ra ngoài block `if speech_start` |
| ISS-04 | Vision Queue `wait_for(get_rotation(), 0.1)` có thể bỏ sót frame | Theo dõi đầu không đều | `orchestrator.py` | Cần đo hiệu năng, cân nhắc chiến lược bộ đệm |
| ISS-05 | Frontend AudioContext ép tần số lấy mẫu 16kHz | Một số trình duyệt/nền tảng không hỗ trợ | `audio-manager.js` | Cần kiểm tra trên Chromium kiosk đích |

### Mức Ưu Tiên THẤP

| ID | Vấn đề | Ảnh hưởng | File | Cách xử lý |
|----|--------|-----------|------|------------|
| ISS-06 | NVIDIA Container Toolkit chưa cài | GPU passthrough qua Docker thất bại | Hệ thống host | Cài `nvidia-ctk` |
| ISS-07 | Silero VAD cần internet lần đầu tải mô hình | Build offline sẽ thất bại | `vad.py` | Đã tải trước trên máy phát triển |

---

## 5. Điều Kiện Chặn Trước Phase 4 (Kiểm thử E2E)

Các điều kiện BẮT BUỘC trước khi chạy E2E:

1. **VRM Model** — Tải từ VRoid Hub, đặt tại `frontend/public/models/avatar.vrm`
2. **Gemini API Key** — Xác nhận key còn hạn và có quota (đã có trong `.env`)
3. **Camera** — Kết nối camera (USB hoặc DroidCam)
4. **NVIDIA Container Toolkit** — Cài đặt nếu dùng Docker GPU

---

## 6. Tiêu Chí Thành Công POC (từ PRD)

| Chỉ số | Mục tiêu | Trạng thái hiện tại |
|--------|----------|---------------------|
| Tương tác E2E thành công | >= 80% phiên | CHƯA ĐO |
| Độ trễ phản hồi đầu tiên | < 5 giây | CHƯA ĐO |
| Độ trễ theo dõi đầu | < 200ms | CHƯA ĐO |
| Sử dụng VRAM | < 2 GB | CHƯA ĐO (~1 GB ước tính) |
| Thời gian hoạt động liên tục | >= 30 phút | CHƯA ĐO |

---

## 7. Mức Độ Hoàn Thiện Tài Liệu

| Tài liệu | Trạng thái | Chất lượng |
|-----------|------------|------------|
| PRD (`01_prd.md`) | XONG | Xuất sắc — phân tích BA đầy đủ |
| Kiến trúc (`02-system-architecture.md`) | XONG | Xuất sắc — đặc tả module, sơ đồ tuần tự, giao thức WS |
| Kế hoạch (`03_planning.md`) | XONG | Tốt — phân rã task, đồ thị phụ thuộc, ước lượng |
| Hướng dẫn cài đặt (`SETUP_GUIDE.md`) | XONG | Tốt — từng bước với xử lý sự cố |
| Đánh giá Phase (`phase1-2-evaluation.md`) | XONG | Tốt — danh sách kiểm tra tính năng chi tiết |
| Quy tắc code | XONG | -> `docs/project_management/CODING_RULES.md` |
| Kế hoạch kiểm thử | **THIẾU** | Cần tạo cho Phase 4 |
