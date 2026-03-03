# Hướng Dẫn Tracking Tiến Độ Dự Án

> Guideline đánh giá mức độ đáp ứng requirements của hệ thống so với file yêu cầu.

---

## 1. Nguyên Tắc

- Chỉ đánh giá theo **phase hiện tại** (không đánh giá scope của phase sau).
- Lấy scope từ file `docs/02-system-architecture.md` — file này có thể thay đổi theo nhu cầu.
- Code stub / TODO = **không tính** là hoàn thành.
- Cập nhật tracking mỗi khi hoàn thành nhóm tính năng hoặc kết thúc sprint.

---

## 2. Các Bước Thực Hiện

### Bước 1: Xác định scope phase hiện tại

Mở file requirements, tìm phần mô tả scope của phase đang làm. Liệt kê:

- **Trong scope:** Những gì cần hoàn thành trong phase này.
- **Ngoài scope:** Những gì thuộc phase sau (không đánh giá).

### Bước 2: Liệt kê features của phase

Từ requirements, lấy danh sách features cần làm. Đánh giá từng feature:

| Feature | Yêu cầu | Status | % | Phần còn thiếu |
|---------|---------|--------|---|----------------|
| F-xxx | Mô tả ngắn | DONE / PARTIAL / NOT STARTED | 0-100 | Ghi rõ nếu PARTIAL |

**Quy ước:**
- **DONE** = 100% — Code hoạt động, không còn TODO/stub.
- **PARTIAL** = ghi % cụ thể — Nêu rõ phần đã làm và phần thiếu.
- **NOT STARTED** = 0%.

### Bước 3: Đánh giá hạ tầng

Liệt kê các thành phần hạ tầng mà phase yêu cầu (DB, queue, storage, deploy...):

| Thành phần | Yêu cầu | Status | Ghi chú |
|------------|---------|--------|---------|
| ... | ... | DONE / PARTIAL | ... |

### Bước 4: Đánh giá kiến trúc

Liệt kê các quyết định kiến trúc (Architecture Decisions) áp dụng cho phase hiện tại:

| Quyết định | Yêu cầu | Status | Ghi chú |
|------------|---------|--------|---------|
| ... | ... | DONE / PARTIAL | ... |

### Bước 5: Ghi nhận phần làm vượt scope (nếu có)

Những tính năng đã implement nhưng thuộc phase sau. Ghi nhận để biết tiến độ tổng thể:

| Tính năng | Thuộc Phase | Trạng thái |
|-----------|-------------|------------|
| ... | Phase X | DONE / PARTIAL |

### Bước 6: Liệt kê vấn đề tồn đọng

Các bug, stub, hoặc rủi ro ảnh hưởng đến hệ thống hiện tại. Phân loại theo mức độ:

| Mức độ | Ý nghĩa |
|--------|---------|
| **CRITICAL** | Hệ thống không ổn định nếu không sửa |
| **HIGH** | Ảnh hưởng lớn, nên sửa sớm |
| **MEDIUM** | Cần làm nhưng chưa gấp |

Mỗi vấn đề ghi: **Vấn đề gì → Ảnh hưởng gì → File nào** (nếu biết).

---

## 3. Tổng Kết

Sau khi đánh giá xong, tổng hợp kết quả:

| Hạng mục | Kết quả |
|----------|---------|
| Features | X/Y hoàn thành |
| Hạ tầng | X/Y hoàn thành |
| Kiến trúc | X/Y hoàn thành |
| Vượt scope | N tính năng làm sớm |
| Tồn đọng | N CRITICAL, N HIGH, N MEDIUM |

Kết luận: Phase hiện tại **HOÀN THÀNH / CHƯA HOÀN THÀNH** + điều kiện để chuyển phase tiếp.

---

## 4. Khi Nào Cập Nhật?

- Kết thúc sprint / milestone.
- Hoàn thành một nhóm tính năng.
- Phát hiện vấn đề mới.
- File requirements thay đổi → review lại scope và đánh giá lại.
