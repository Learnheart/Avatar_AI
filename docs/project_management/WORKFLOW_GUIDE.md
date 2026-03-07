# Hướng Dẫn Quy Trình Làm Việc — Claude Code Agent

> Hướng dẫn quy trình làm việc chuẩn khi nhận bất kỳ tác vụ nào trên dự án này.

---

## 1. Quy Trình Tiếp Nhận Tác Vụ

```
Nhận tác vụ từ người dùng
  |
  v
Đọc PROJECT_STATUS.md -> nắm trạng thái dự án
  |
  v
Đọc TASK_BACKLOG.md -> kiểm tra task đã có chưa, có vấn đề chặn không
  |
  v
Đọc CODING_RULES.md -> xem lại quy tắc áp dụng
  |
  v
Đọc file mã nguồn liên quan (dùng công cụ Read)
  |
  v
Thực hiện tác vụ
  |
  v
Cập nhật theo dõi (nhật ký hàng ngày + danh sách công việc + trạng thái)
```

---

## 2. Danh Sách Kiểm Tra Thay Đổi Code

Trước khi hoàn tất bất kỳ thay đổi code nào, kiểm tra:

### Thay đổi Backend:
- [ ] Cấu hình mới -> đã thêm vào `AppConfig` + `.env.example` chưa?
- [ ] Logic trạng thái -> chỉ thay đổi trong `orchestrator.py` chưa?
- [ ] Định dạng tin nhắn WS -> có thay đổi không? Nếu có -> cập nhật tài liệu kiến trúc.
- [ ] Xử lý lỗi -> có try/except quanh các lời gọi bên ngoài không?
- [ ] Ghi log -> dùng `get_logger()`, không dùng `print()` chưa?
- [ ] An toàn luồng -> không truy cập trạng thái chia sẻ từ nhiều luồng chưa?

### Thay đổi Frontend:
- [ ] Truy cập blendshape VRM -> có kiểm tra khả dụng trước khi dùng không?
- [ ] AudioContext -> có xử lý tạm dừng/tiếp tục không?
- [ ] WebSocket -> có xử lý ngắt kết nối uyển chuyển không?
- [ ] Hoạt ảnh -> có kẹp giá trị trong phạm vi an toàn không?

---

## 3. Quy Trình Gỡ Lỗi

### Bước 1: Xác định tầng xảy ra vấn đề

```
Vấn đề ở Frontend?
  -> Mở bảng điều khiển trình duyệt (F12)
  -> Kiểm tra tab Mạng để xem tin nhắn WS
  -> Kiểm tra log trong avatar-renderer.js / audio-manager.js

Vấn đề ở Backend?
  -> Kiểm tra log trong terminal (định dạng JSON)
  -> curl http://localhost:8000/health
  -> Kiểm tra avatar_state, camera_connected, gemini_session_active

Vấn đề mạng?
  -> Kiểm tra trạng thái kết nối WS trong trình duyệt
  -> Kiểm tra cấu hình VITE_WS_URL
  -> Kiểm tra backend cổng 8000 có truy cập được không
```

### Bước 2: Tái hiện lỗi

- Đọc log để xác định thời điểm lỗi xảy ra.
- Kiểm tra máy trạng thái: `avatar_state` là gì khi lỗi xảy ra?
- Kiểm tra phiên Gemini: `gemini_session_active` đang là true hay false?

### Bước 3: Sửa lỗi

- Sửa trong đúng file theo CODING_RULES.
- Kiểm tra lại bằng danh sách kiểm tra nhanh (smoke test).
- Ghi nhận bản sửa vào nhật ký theo dõi hàng ngày.

---

## 4. Danh Sách Kiểm Tra Phát Hành (Khi hoàn thành Phase 4)

Khi tất cả các tác vụ hoàn thành:

- [ ] Mọi mục kiểm tra nhanh (smoke test) ĐẠT
- [ ] VRAM < 2 GB sau 30 phút
- [ ] Không sập trong 30 phút liên tục
- [ ] Độ trễ phản hồi < 5 giây (đo bằng timestamp)
- [ ] Độ trễ theo dõi đầu < 200ms (đo bằng quan sát trực quan + log)
- [ ] Tạo `docs/project_tracking/phase4-evaluation.md`
- [ ] Cập nhật `PROJECT_STATUS.md` -> Phase 4: XONG
- [ ] Cập nhật `TASK_BACKLOG.md` -> TASK-15, 16: XONG

---

## 5. Quy Trình Xử Lý Khẩn Cấp

### Hệ thống sập / Tràn bộ nhớ (OOM):
1. Kiểm tra `nvidia-smi` -> mức sử dụng VRAM
2. Dừng tiến trình: `pkill -f uvicorn`
3. Kiểm tra log cuối cùng để tìm nguyên nhân
4. Khởi động lại hệ thống

### Gemini API thất bại:
1. Kiểm tra khóa API: dùng `curl` gọi endpoint thử
2. Kiểm tra quota trên Google AI Studio
3. Nếu hết quota -> đổi khóa hoặc đợi ngày tiếp theo
4. Hệ thống vẫn chạy (theo dõi đầu + hoạt ảnh nghỉ), chỉ mất giọng nói

### Camera hỏng:
1. `ls /dev/video*` -> kiểm tra thiết bị
2. DroidCam: kiểm tra ứng dụng trên điện thoại còn mở không
3. `droidcam-cli -v 192.168.1.15 4747 &` -> kết nối lại
4. Đổi `CAMERA_INDEX` trong `.env` nếu cần

### WebSocket ngắt kết nối liên tục:
1. Kiểm tra backend còn chạy: `curl /health`
2. Kiểm tra tường lửa/cổng 8000
3. Kiểm tra lỗi trong bảng điều khiển trình duyệt
4. Tải lại cứng trình duyệt (Ctrl+Shift+R)
