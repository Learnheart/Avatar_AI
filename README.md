# Avatar AI (Magic Mirror)

Một trợ lý ảo dạng nhân vật 3D đặt tại quầy, nói chuyện trực tiếp với khách bằng giọng
nói tiếng Việt: khách đứng trước màn hình và hỏi, avatar nghe, hiểu và trả lời thành
tiếng, miệng và đầu chuyển động theo như một người đang trò chuyện.

## Chạy

```bash
.venv/bin/python -m backend.main      # backend
cd frontend && npm run dev            # giao diện avatar
```

Cài đặt lần đầu và cấu hình: xem `SETUP.md`.

## Sản phẩm giải quyết việc gì

Ở quầy giao dịch, sảnh đón khách hay điểm tư vấn, phần lớn câu hỏi của khách là câu hỏi
thông tin lặp lại, trong khi nhân viên có hạn và không trực liên tục. Avatar AI là một
điểm tiếp đón luôn sẵn sàng: khách không cần chạm màn hình, không cần gõ phím, không cần
cài ứng dụng, chỉ cần nói.

Cấu hình mặc định là vai **trợ lý cá nhân trong công việc hàng ngày**: giúp người dùng
tìm hiểu những điều họ thắc mắc và sẵn sàng trò chuyện cùng họ, giọng gần gũi, ngắn gọn
2–3 câu mỗi lượt. Vai và phong cách trả lời đổi được mà không phải sửa phần còn lại của
sản phẩm, nên cùng một nền tảng dùng được cho lễ tân, tư vấn tại quầy hay hướng dẫn
khách tham quan.

## Khách hàng trải nghiệm thế nào

1. **Khách bước tới.** Camera nhận ra có người đứng trước màn hình; avatar quay đầu theo
   hướng của khách.
2. **Khách nói.** Avatar tự nhận biết lúc khách bắt đầu và ngừng nói, không cần bấm nút.
3. **Avatar trả lời.** Câu trả lời được đọc thành tiếng bằng giọng tiếng Việt tự nhiên,
   miệng avatar cử động khớp với âm thanh đang phát.
4. **Khách ngắt lời được.** Nếu khách nói chen vào khi avatar đang trả lời, avatar dừng
   lại và nghe câu mới.

## Khả năng hiện tại

| Khả năng | Mô tả |
|---|---|
| Nhận biết người | Phát hiện có khách trước màn hình và hướng nhìn của khách |
| Nghe tiếng Việt | Chuyển lời nói của khách thành văn bản |
| Trả lời | Sinh câu trả lời tiếng Việt theo vai đã cấu hình |
| Nói tiếng Việt | Đọc câu trả lời bằng giọng tự nhiên, phát ngay khi câu đầu tiên sẵn sàng |
| Biểu cảm | Nhân vật 3D quay đầu theo khách và nhép miệng theo âm thanh |
| Tốc độ | Mục tiêu: khách nghe tiếng trả lời đầu tiên trong vòng 3,5 giây |

## Câu trả lời dựa trên đâu

Hiện tại avatar trả lời bằng **kiến thức chung của mô hình ngôn ngữ**, trong khuôn khổ
vai và phong cách đã cấu hình. Nó chưa tra cứu tài liệu riêng của doanh nghiệp, nên chưa
nên dùng để đưa ra số liệu cụ thể như lãi suất, biểu phí hay điều khoản sản phẩm.

Hai phần đang trong kế hoạch, chưa có trong bản này:

- **Kho kiến thức riêng**: avatar trả lời dựa trên tài liệu do doanh nghiệp cung cấp
  (sản phẩm, quy trình, câu hỏi thường gặp).
- **Ghi nhớ hội thoại**: avatar nhớ những gì khách đã nói trong cùng một lần trò chuyện.
  Hiện mỗi câu hỏi được trả lời độc lập.

## Giới hạn cần biết

- Phục vụ một khách tại một thời điểm, bằng tiếng Việt.
- Không nhận diện danh tính khách và không lưu hình ảnh hay giọng nói.
- Cần kết nối internet: phần nghe và phần sinh câu trả lời dùng dịch vụ đám mây; phần
  nhận biết người và phần đọc thành tiếng chạy ngay trên máy đặt tại quầy.
- Đang ở giai đoạn MVP: đã chạy trọn một lượt hỏi–đáp thật, chưa triển khai cho khách
  hàng thật.

## Tài liệu

- Kiến trúc: `docs/ARCHITECTURE.html`
- Ngưỡng chất lượng: `docs/quality-gates.md`
- Quy tắc dự án và cấu trúc mã nguồn: `CLAUDE.md`
