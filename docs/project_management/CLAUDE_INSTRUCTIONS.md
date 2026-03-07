# Hướng Dẫn Cho Claude Code — Magic Mirror AI Avatar

> Tài liệu này hướng dẫn Claude Code agent làm việc hiệu quả trên dự án.
> Đọc KỸ trước khi thay đổi bất kỳ file nào.

---

## 1. Trước Khi Code — ĐỌC BẮT BUỘC

Trước khi thực hiện bất kỳ thay đổi nào, ĐỌC các file sau theo thứ tự:

1. **`docs/project_management/PROJECT_STATUS.md`** — Trạng thái hiện tại, vấn đề tồn đọng
2. **`docs/project_management/CODING_RULES.md`** — Quy tắc code BẮT BUỘC
3. **`docs/project_management/TASK_BACKLOG.md`** — Công việc cần làm, vấn đề đã biết
4. **`docs/01_prd.md`** — Yêu cầu nghiệp vụ, tình huống biên, máy trạng thái
5. **`docs/02-system-architecture.md`** — Giao thức WS, đặc tả module, sơ đồ tuần tự

---

## 2. Ràng Buộc Kiến Trúc Quan Trọng

### KHÔNG ĐƯỢC thay đổi nếu chưa được phê duyệt rõ ràng:

1. **Định dạng tin nhắn WebSocket** — Frontend và backend phụ thuộc vào cùng định dạng. Thay đổi = phá vỡ tương thích.
2. **Các chuyển trạng thái máy trạng thái** — Chỉ các chuyển đổi trong BR-004 là hợp lệ.
3. **Cấu trúc file** — Không di chuyển, đổi tên file hiện có.
4. **Mẫu cấu hình** — Mọi cấu hình đi qua `AppConfig` trong `config.py`.
5. **Orchestrator là chủ sở hữu trạng thái duy nhất** — Không module nào khác được thay đổi `AvatarState`.

### CÓ THỂ tự do thay đổi:

- Logic nội bộ của bất kỳ module nào (giữ nguyên giao diện công khai)
- CSS/kiểu dáng trong `index.html`
- Nội dung và mức độ log
- Giá trị cấu hình mặc định (trong `config.py`)
- Thông số hoạt ảnh (hệ số nội suy, thời gian)

---

## 3. Các Tác Vụ Thường Gặp — Cách Thực Hiện

### 3.1 Sửa lỗi

1. Đọc file liên quan bằng công cụ Read.
2. Xác định nguyên nhân gốc.
3. Sửa lỗi, đảm bảo không vi phạm CODING_RULES.
4. Cập nhật `TASK_BACKLOG.md` (xóa/cập nhật vấn đề tương ứng).
5. Ghi theo dõi: thêm vào `docs/project_tracking/YYYY-MM-DD.md`.

### 3.2 Thêm tính năng mới

1. Kiểm tra tính năng có trong phạm vi PRD không (`01_prd.md` Phần 4).
2. Nếu NGOÀI PHẠM VI -> thông báo người dùng, không tự ý triển khai.
3. Nếu TRONG PHẠM VI -> đọc FR tương ứng trong `01_prd.md` Phần 6.
4. Triển khai theo CODING_RULES.
5. Nếu cần thêm loại tin nhắn WS -> cập nhật `02-system-architecture.md` Phần 3.
6. Nếu cần thêm cấu hình -> thêm vào `config.py` + `.env.example`.
7. Cập nhật `TASK_BACKLOG.md` và theo dõi tiến độ.

### 3.3 Gỡ lỗi tích hợp Gemini

1. Đọc `backend/modules/gemini_client.py` — hiểu mẫu send_loop/receive_loop.
2. Kiểm tra `.env` có `GEMINI_API_KEY` hợp lệ.
3. Kiểm tra đầu ra log: `{"module": "gemini", ...}`.
4. Các vấn đề thường gặp:
   - Không khớp phiên bản SDK: `google-genai>=1.0.0` có thể thay đổi API.
   - `send_realtime_input()` có thể cần tham số `media_chunks` thay vì tham số vị trí.
   - Hết thời gian phiên: Gemini có thể đóng phiên nếu không có hoạt động.

### 3.4 Gỡ lỗi pipeline âm thanh

Luồng âm thanh Frontend:
```
Mic -> AudioWorklet (audio-processor.js)
     -> Tin nhắn qua Port (Int16 ArrayBuffer)
     -> audio-manager.js: chuyển đổi sang Base64
     -> ws-client.js: gửi {type: "audio_chunk", data: "..."}
```

Luồng âm thanh Backend:
```
WS nhận audio_chunk
  -> orchestrator._process_audio(): giải mã base64
  -> vad.process_audio(): phát hiện giọng nói
  -> gemini_client.send_audio(): truyền tới Gemini
  -> Callback phản hồi Gemini -> orchestrator._on_gemini_response()
  -> WS gửi speech_output
```

Luồng phát lại Frontend:
```
WS nhận speech_output
  -> audio-manager.js: giải mã (PCM hoặc MP3)
  -> AudioBuffer -> hàng đợi phát lại
  -> AnalyserNode -> lip-sync.js: RMS -> blendshape
```

### 3.5 Chạy hệ thống

**Phát triển local:**
```bash
# Terminal 1: Backend
cd /mnt/hdd/projects/avatar_AI
source .venv/bin/activate
PYTHONPATH=/mnt/hdd/projects/avatar_AI uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
cd /mnt/hdd/projects/avatar_AI/frontend
npm run dev

# Terminal 3 (tùy chọn): DroidCam
droidcam-cli -v 192.168.1.15 4747 &
```

**Docker:**
```bash
cd /mnt/hdd/projects/avatar_AI
docker compose up
```

---

## 4. Bảng Tham Chiếu Nhanh Theo File

### Backend — file nào cho mối quan tâm nào

| Mối quan tâm | File chính | File phụ |
|---------------|-----------|----------|
| Điểm cuối API | `main.py` | — |
| Logic trạng thái | `orchestrator.py` | `models.py` |
| Theo dõi khuôn mặt | `modules/vision.py` | `config.py` (giá trị kẹp) |
| Phát hiện giọng nói | `modules/vad.py` | `config.py` (ngưỡng) |
| Hội thoại AI | `modules/gemini_client.py` | `config.py` (mô hình, giọng) |
| Chuyển văn bản thành giọng | `modules/tts.py` | `config.py` (chế độ TTS, khóa) |
| Giá trị cấu hình | `config.py` | `.env`, `.env.example` |
| Mô hình dữ liệu | `models.py` | — |

### Frontend — file nào cho mối quan tâm nào

| Mối quan tâm | File chính | File phụ |
|---------------|-----------|----------|
| Dựng hình 3D | `avatar-renderer.js` | `index.html` |
| Hoạt ảnh theo dõi đầu | `avatar-renderer.js` | — |
| Hoạt ảnh nghỉ | `avatar-renderer.js` | — |
| Đồng bộ môi | `lip-sync.js` | `audio-manager.js` |
| Thu âm mic | `audio-manager.js` | `worklet/audio-processor.js` |
| Phát lại âm thanh | `audio-manager.js` | — |
| WebSocket | `ws-client.js` | — |
| Kết nối module | `main.js` | — |

---

## 5. Theo Dõi & Báo Cáo

### Sau khi hoàn thành công việc:

1. **Thêm vào nhật ký hàng ngày:**
   ```
   date: YYYY-MM-DD
   time: HH:MM
   ======
   {Mô tả công việc đã làm}
   ```
   File: `docs/project_tracking/YYYY-MM-DD.md`

2. **Cập nhật trạng thái task** trong `TASK_BACKLOG.md` nếu task hoàn thành hoặc vấn đề được sửa.

3. **Cập nhật `PROJECT_STATUS.md`** nếu có thay đổi lớn (hoàn thành phase, phát hiện vấn đề mới).

---

## 6. Ghi Chú Về Môi Trường

| Mục | Giá trị |
|-----|---------|
| Thư mục gốc dự án | `/mnt/hdd/projects/avatar_AI` |
| Môi trường ảo Python | `.venv` (kích hoạt: `source .venv/bin/activate`) |
| Phiên bản Python | 3.10+ |
| Phiên bản Node | 20+ |
| GPU | RTX 3070 8GB, CUDA 12.1 |
| Camera | DroidCam tại `/dev/video0`, IP: 192.168.1.15:4747 |
| Mô hình MediaPipe | `backend/models/face_landmarker.task` |
| Mô hình VAD | Đã tải trước tại `~/.cache/torch/hub/snakers4_silero-vad_master` |
| Phụ thuộc Frontend | `npm install --legacy-peer-deps` (xung đột phụ thuộc ngang hàng) |
| Múi giờ | UTC+7 (Việt Nam) |
