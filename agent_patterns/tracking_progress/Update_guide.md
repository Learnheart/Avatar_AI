# Hướng dẫn cập nhật tiến trình OCR

## Mục đích
Folder  `docs/update_progress/` lưu trữ các bản cập nhật tình trạng hệ thống theo ngày.

## Quy tắc đặt tên file
- Mỗi ngày một file riêng, tên file theo format: `YYYY-MM-DD.md`
- Ví dụ: `2026-02-21.md`
- Múi giờ: **UTC+7 (Việt Nam)**

## Format nội dung
Mỗi lần cập nhật trong ngày sẽ **append** vào cuối file với format sau:

```
date: YYYY-MM-DD
time: HH:MM
======
Nội dung cập nhật ở đây.

```

### Ví dụ

```markdown
date: 2026-02-21
time: 09:30
======
Deploy phiên bản mới lên staging. API endpoint /ocr/process đã hoạt động ổn định.

date: 2026-02-21
time: 14:15
======
Fix lỗi timeout khi xử lý file PDF > 50 trang. Tăng timeout từ 30s lên 120s.

date: 2026-02-21
time: 17:00
======
Hoàn thành test toàn bộ flow. Sẵn sàng cho production.
```

## Lưu ý
- Nếu trong ngày có nhiều lần cập nhật, **append tiếp vào cuối file**, không tạo file mới.
- Giữ nội dung ngắn gọn, tập trung vào những thay đổi chính.
- Phân cách giữa các lần cập nhật bằng dòng `======`.
