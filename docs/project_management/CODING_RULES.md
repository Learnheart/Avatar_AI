# Quy Tắc Code — Magic Mirror AI Avatar

> Tài liệu này là nguồn tham chiếu CHÍNH cho mọi Claude Code agent khi làm việc trên dự án.
> Mọi thay đổi code PHẢI tuân thủ các quy tắc dưới đây.

---

## 1. Quy Tắc Cấu Trúc Dự Án

### 1.1 Bố Cục Thư Mục (KHÔNG ĐƯỢC thay đổi)

```
backend/                    # Backend Python FastAPI
  config.py                 # AppConfig — NGUỒN DUY NHẤT cho cấu hình
  models.py                 # Dataclass & enum dùng chung
  main.py                   # Ứng dụng FastAPI, /ws, /health — ĐIỂM VÀO
  orchestrator.py           # Máy trạng thái — NƠI DUY NHẤT quản lý AvatarState
  modules/                  # Các module tính năng (vision, vad, gemini, tts)
  utils/                    # Tiện ích (logging)

frontend/                   # Giao diện Vite + Three.js
  index.html                # Trang đơn
  src/                      # Các module JS
  public/                   # Tài nguyên tĩnh (mô hình VRM, worklet)
```

### 1.2 Trách Nhiệm Từng File (KHÔNG ĐƯỢC vi phạm)

| Quy tắc | Mô tả |
|----------|-------|
| **Cấu hình chỉ ở `config.py`** | Mọi cấu hình đọc từ biến môi trường PHẢI đi qua `AppConfig`. Không được hardcode cấu hình trong module khác. |
| **Trạng thái chỉ ở `orchestrator.py`** | CHỈ orchestrator được gọi `_transition()` để thay đổi `AvatarState`. Không module nào được tự thay đổi trạng thái. |
| **Mô hình dữ liệu chỉ ở `models.py`** | Mọi dataclass/enum chia sẻ giữa các module PHẢI định nghĩa trong `models.py`. |
| **Ghi log chỉ dùng `utils/logging.py`** | Gọi `get_logger(module_name)` — không dùng `print()` trong code chạy thật. |

---

## 2. Quy Tắc Backend Python

### 2.1 Phong Cách & Quy Ước

| Quy tắc | Ví dụ |
|----------|-------|
| Python >= 3.10 | Gợi ý kiểu (type hints) bắt buộc cho chữ ký hàm |
| Ưu tiên bất đồng bộ | Dùng `async def` cho mọi handler, dùng `asyncio.Queue` giữa các module |
| Đặt tên | `snake_case` cho hàm/biến, `PascalCase` cho lớp, `UPPER_CASE` cho hằng số |
| Thứ tự import | thư viện chuẩn -> bên thứ ba -> nội bộ. Cách nhau bằng dòng trống. |
| Không dùng import tương đối | `from backend.modules.vision import VisionModule`, KHÔNG dùng `from .vision import...` khi chạy với uvicorn |

### 2.2 Xử Lý Lỗi

```python
# ĐÚNG — ghi log lỗi, không làm sập tiến trình
try:
    result = await thao_tac_rui_ro()
except Exception as e:
    logger.error({"msg": "Thao tác thất bại", "error": str(e), "exc": True})
    # Giảm cấp uyển chuyển: trả về None / bỏ qua / thử lại

# SAI — để ngoại lệ lan truyền làm sập toàn bộ
result = await thao_tac_rui_ro()  # Sập nếu thất bại
```

**Nguyên tắc:**
- Backend KHÔNG ĐƯỢC sập vì 1 module lỗi. Vision hỏng -> theo dõi dừng nhưng giọng nói vẫn hoạt động.
- Ghi log ERROR kèm ngữ cảnh (tên module, thao tác, thông báo lỗi).
- Cơ chế thử lại: tối đa 3 lần, thời gian chờ tăng dần (exponential backoff).

### 2.3 Giao Thức WebSocket (KHÔNG ĐƯỢC thay đổi định dạng)

**Tin nhắn Server -> Client:**

```json
// Đã kết nối
{"type": "connected", "session_id": "session-xxx", "config": {"tracking_fps": 30, "audio_format": "pcm_16khz_mono"}}

// Cập nhật theo dõi (~30Hz)
{"type": "tracking_update", "timestamp": 1234567890.123, "avatar_control": {"head_rotation": {"pitch": 0.1, "yaw": -0.2, "roll": 0.05}, "is_speaking": false}}

// Phản hồi giọng nói
{"type": "speech_output", "audio_data": "base64...", "text_content": "Xin chào", "audio_source": "gemini", "audio_mime_type": "audio/pcm;rate=24000", "is_final": false}

// Ngắt lời (barge-in)
{"type": "interrupt"}

// Lỗi
{"type": "error", "message": "Mô tả lỗi"}
```

**Tin nhắn Client -> Server:**

```json
// Đoạn âm thanh
{"type": "audio_chunk", "audio_data": "base64_pcm_16khz_int16"}
```

**Quy tắc:**
- KHÔNG thêm trường mới vào tin nhắn mà không cập nhật `docs/02-system-architecture.md` Phần 3.
- KHÔNG thay đổi tên trường hiện có (thay đổi phá vỡ tương thích với frontend).
- Mọi tin nhắn PHẢI có trường `type`.

### 2.4 Quy Tắc Máy Trạng Thái

```
IDLE -> TRACKING       (phát hiện khuôn mặt)
TRACKING -> LISTENING  (VAD phát hiện bắt đầu nói)
LISTENING -> SPEAKING  (Gemini trả về phản hồi âm thanh)
SPEAKING -> TRACKING   (phát xong âm thanh)
TRACKING -> IDLE       (không thấy mặt > 3 giây)
BẤT KỲ -> IDLE        (lỗi nghiêm trọng)
```

**Quy tắc:**
- CHỈ có các chuyển trạng thái trên là hợp lệ. Không được thêm chuyển trạng thái mới mà không cập nhật PRD (BR-004).
- Chuyển trạng thái PHẢI đi qua `orchestrator._transition()`.
- Mỗi chuyển trạng thái PHẢI ghi log: `State: {cũ} -> {mới}`.

### 2.5 Mô Hình Luồng Xử Lý

| Thành phần | Luồng | Giao tiếp |
|------------|-------|-----------|
| FastAPI + Orchestrator | Vòng lặp sự kiện asyncio chính | `async/await` |
| Thị giác (MediaPipe + cv2) | `threading.Thread` riêng | `asyncio.Queue(maxsize=2)` |
| VAD (Silero) | Luồng chính (gọi đồng bộ) | Trả về trực tiếp |
| Gemini Client | Các tác vụ asyncio (send_loop + receive_loop) | `asyncio.Queue` + callback |

**Quy tắc:**
- KHÔNG tạo luồng mới ngoài luồng thị giác. Dùng tác vụ asyncio cho mọi thứ khác.
- Vision Queue maxsize=2: bỏ frame cũ nếu chưa xử lý kịp (ưu tiên frame mới nhất).

---

## 3. Quy Tắc Frontend JavaScript

### 3.1 Phong Cách & Quy Ước

| Quy tắc | Mô tả |
|----------|-------|
| ES Modules | `import/export` — không dùng CommonJS |
| Không dùng framework | Vanilla JS + Three.js. KHÔNG thêm React/Vue/Angular |
| Đặt tên | `camelCase` cho hàm/biến, `PascalCase` cho lớp, `UPPER_SNAKE` cho hằng số |
| Trách nhiệm đơn nhất | Mỗi file = 1 module với 1 class export |

### 3.2 Quy Tắc Pipeline Âm Thanh

```
Mic -> AudioWorklet (16kHz, Int16 PCM) -> Base64 -> WebSocket -> Backend

Backend -> WebSocket (speech_output) -> Giải mã Base64 -> AudioBuffer -> Hàng đợi phát -> Loa
                                                                            |
                                                                      AnalyserNode -> RMS -> Đồng bộ môi
```

**Quy tắc:**
- Thu âm: PHẢI dùng AudioWorklet (không dùng ScriptProcessorNode — đã ngưng hỗ trợ).
- Tần số lấy mẫu: 16kHz cho thu âm mic, tần số đầu ra tùy vào phản hồi Gemini.
- Hàng đợi bộ đệm: FIFO, tối đa 5 mục. Khi tràn -> bỏ mục cũ nhất (EC-007).
- Ngắt lời: Xóa hàng đợi + đóng AudioContext + tạo lại (không tái sử dụng context sau khi ngắt).

### 3.3 Quy Tắc Hoạt Ảnh Avatar

| Hoạt ảnh | Phương pháp | Độ ưu tiên |
|----------|-------------|------------|
| Theo dõi đầu | Quaternion Slerp, hệ số nội suy 0.15 | Cao nhất |
| Đồng bộ môi | RMS -> blendshape "aa", nội suy bất đối xứng (mở 0.4, đóng 0.08) | Cao |
| Chớp mắt | Ngẫu nhiên 3-5 giây, 100ms nhắm + 50ms giữ + 100ms mở | Thấp |
| Thở | `sin(time * 0.8)`, biên độ 0.003 trên xương ngực trục Y | Thấp nhất |

**Quy tắc:**
- Xoay đầu: 70% xương đầu + 30% xương cổ. Xương cổ là phương án dự phòng nếu VRM không có.
- Giới hạn kẹp: pitch +/-30 độ, yaw +/-45 độ, roll +/-20 độ (khớp với backend BR-002).
- Các hoạt ảnh PHẢI độc lập: đồng bộ môi không ảnh hưởng chớp mắt, chớp mắt không ảnh hưởng thở.
- Nếu VRM thiếu blendshape -> vô hiệu hóa tính năng tương ứng, KHÔNG được sập.

### 3.4 Quy Tắc WebSocket Client

- Tự động kết nối lại: thời gian chờ tăng dần 3s -> 6s -> 12s -> tối đa 30s.
- Khi kết nối lại: KHÔNG gửi lại tin nhắn cũ. Chỉ thiết lập lại kết nối.
- Hiển thị trạng thái: cập nhật phần tử DOM `#status` (đã kết nối/mất kết nối/đang kết nối lại).

---

## 4. Quy Tắc Cấu Hình

### 4.1 Biến Môi Trường

| Loại | Quy tắc |
|------|---------|
| Bí mật (API key) | CHỈ đọc từ file `.env`. KHÔNG BAO GIỜ hardcode trong mã nguồn. |
| Giá trị mặc định | Mọi cấu hình PHẢI có giá trị mặc định trong `AppConfig` (trừ GEMINI_API_KEY). |
| Kiểm tra hợp lệ | `AppConfig.__post_init__()` kiểm tra khi khởi động. Dừng ngay nếu thiếu cấu hình bắt buộc. |

### 4.2 Thêm Cấu Hình Mới

1. Thêm trường vào `AppConfig` trong `config.py` kèm giá trị mặc định.
2. Thêm vào `.env.example` kèm ghi chú giải thích.
3. Cập nhật `docs/SETUP_GUIDE.md` nếu là cấu hình người dùng cần biết.

---

## 5. Quy Tắc Kiểm Thử

### 5.1 Chiến Lược Kiểm Thử (phạm vi POC)

- **KHÔNG cần framework kiểm thử đơn vị** (phạm vi POC).
- Kiểm thử E2E = kiểm tra thủ công theo danh sách.
- Mỗi lần sửa lỗi -> chạy lại danh sách kiểm tra liên quan.

### 5.2 Danh Sách Kiểm Tra Nhanh (Smoke Test)

```
[ ] Backend khởi động không lỗi
[ ] curl /health -> status: ok, camera_connected: true
[ ] Frontend tải -> avatar hiển thị
[ ] WebSocket kết nối -> trạng thái "ONLINE"
[ ] Camera -> avatar quay đầu (< 200ms)
[ ] Nói vào mic -> trạng thái chuyển LISTENING
[ ] Dừng nói -> Gemini trả lời + đồng bộ môi
[ ] Ngắt lời: nói khi avatar đang nói -> avatar dừng lại
[ ] Chạy >= 30 phút không sập
[ ] VRAM < 2 GB suốt phiên
```

---

## 6. Quy Tắc Git & Quản Lý Phiên Bản

### 6.1 Chiến Lược Nhánh

- `main` — code ổn định. Chỉ merge khi đã kiểm thử.
- Nhánh tính năng: `feature/task-XX-mo-ta` hoặc `fix/issue-XX-mo-ta`.

### 6.2 Thông Điệp Commit

```
feat: thêm phương án dự phòng TTS Azure
fix: sửa logic thử lại camera trong module thị giác
refactor: dọn dẹp chuyển trạng thái orchestrator
docs: cập nhật hướng dẫn cài đặt cho DroidCam
```

### 6.3 Các File KHÔNG ĐƯỢC commit

- `.env` (chứa bí mật)
- `frontend/public/models/avatar.vrm` (file nhị phân lớn)
- `__pycache__/`, `node_modules/`, `.venv/`
- `*.pyc`, `.DS_Store`

---

## 7. Quy Tắc Tài Liệu

### 7.1 Khi Nào PHẢI Cập Nhật Tài Liệu

| Thay đổi code | Tài liệu cần cập nhật |
|----------------|----------------------|
| Thêm/sửa định dạng tin nhắn WS | `02-system-architecture.md` Phần 3 |
| Thêm/sửa chuyển trạng thái | `01_prd.md` BR-004, `02-system-architecture.md` Phần 2 |
| Thêm cấu hình mới | `SETUP_GUIDE.md`, `.env.example` |
| Sửa vấn đề trong `PROJECT_STATUS.md` | Cập nhật trạng thái trong `PROJECT_STATUS.md` |
| Hoàn thành task | Cập nhật `TASK_BACKLOG.md` |

### 7.2 Theo Dõi Tiến Độ

- Sau mỗi nhóm task: thêm vào `docs/project_tracking/YYYY-MM-DD.md`
- Định dạng: `date: YYYY-MM-DD\ntime: HH:MM\n======\n{nội dung}\n`
- Múi giờ: UTC+7 (Việt Nam)
- Sau mỗi phase: tạo `docs/project_tracking/phase{N}-evaluation.md`

---

## 8. Ràng Buộc Hiệu Năng

| Tài nguyên | Giới hạn | Cách theo dõi |
|------------|----------|---------------|
| VRAM | < 6 GB (cứng), < 2 GB (mục tiêu) | `nvidia-smi` |
| FPS theo dõi đầu | >= 30 FPS | Log backend |
| Kích thước tin nhắn WS | < 1 KB (tracking_update) | Log backend |
| Độ trễ phản hồi | < 5 giây (giọng nói), < 200ms (theo dõi đầu) | Chênh lệch timestamp |
| Bộ đệm âm thanh | Tối đa 5 đoạn trong hàng đợi | Log frontend |

---

## 9. Quy Tắc Bảo Mật

| Quy tắc | Mô tả |
|----------|-------|
| Không lưu trữ PII | Video/âm thanh/văn bản chỉ xử lý trong bộ nhớ. KHÔNG lưu trữ. |
| Khóa API | Chỉ đọc từ biến môi trường. KHÔNG ghi log giá trị khóa API. |
| Kiểm tra đầu vào | Kiểm tra định dạng audio_chunk trước khi xử lý (base64, kích thước hợp lý). |
| Không dùng eval/exec | KHÔNG dùng `eval()`, `exec()`, hoặc thực thi mã động. |
| Phụ thuộc | Ghim phiên bản trong `requirements.txt` và `package.json`. |
