# MAGIC MIRROR AI AVATAR — ĐẶC TẢ POC

## 1. Tổng quan

Hệ thống Avatar AI tương tác thời gian thực trên màn hình Kiosk. Avatar có khả năng nhìn (nhận diện ngữ cảnh khách hàng qua camera), nghe (nhận giọng nói), suy luận (trả lời như chuyên viên tài chính VIP), và nói (phản hồi bằng giọng nói với khẩu hình miệng đồng bộ).

**Kiến trúc:** Client-Server bất đồng bộ qua WebSocket.

- **Backend** (Ubuntu + RTX 3070 8GB): Chạy vision tracking local, điều phối Gemini Live API.
- **Frontend** (Chromium Kiosk): Render Avatar 3D bằng Three.js/WebGL.

**Nguyên tắc POC:** Chạy được trước, tối ưu sau. Ưu tiên pipeline đơn giản nhất, VRAM thấp nhất, và thiết kế các module có thể swap không cần refactor.

## 2. Pipeline chính

```
[Camera USB] → MediaPipe Face Mesh (local) → Head Tracking data ──→ [Frontend Avatar]
                                                                          ↑
[Mic Kiosk] → Silero VAD (local) → Gemini Live API ──→ Audio output ─────┘
                                        ↑
                                Frame JPEG (mỗi 2-3s)
```

Gemini Live API đảm nhận 3 vai trò: STT (nghe khách nói), LLM (suy luận multimodal), và TTS (phản hồi bằng giọng nói). Pipeline này giải phóng gần như toàn bộ VRAM cho tracking local.

## 3. Phân bổ VRAM

| Module              | VRAM    | Ghi chú                        |
|---------------------|---------|--------------------------------|
| MediaPipe Face Mesh | ~0.3 GB | Head tracking 30 FPS           |
| Silero VAD          | ~0.1 GB | Chạy được cả trên CPU          |
| CUDA runtime        | ~0.5 GB | Driver overhead + buffer       |
| **Tổng**            | **~1 GB** | **Dư ~7 GB cho mở rộng sau này** |

> Chạy `nvidia-smi` trước và sau khi load từng module để xác nhận VRAM thực tế.

## 4. Backend (Server Ubuntu)

**Môi trường:** Python 3.10+, FastAPI, Docker + NVIDIA Container Toolkit.

### 4.1. Vision — MediaPipe Face Mesh (Local)

- Tính toán Pitch, Yaw, Roll từ 468 face landmarks.
- Camera USB kết nối trực tiếp server qua `cv2.VideoCapture(0)`.
- Xuất dữ liệu head rotation tối thiểu 30 FPS qua WebSocket.
- Mỗi 2–3 giây, chụp 1 frame JPEG gửi kèm vào Gemini Live session để cung cấp ngữ cảnh thị giác.

### 4.2. Voice + AI — Gemini Live API

- **Silero VAD (local):** Phát hiện điểm bắt đầu/kết thúc câu nói (<50ms). Kiểm soát mở/đóng session Gemini — chỉ kết nối khi có người nói để tiết kiệm chi phí API.
- **Gemini Live API:** Nhận audio stream + image, trả về audio stream. Model `gemini-2.0-flash-live`, cấu hình `response_modalities: ["AUDIO"]` mặc định.
- **System Prompt:** Đóng vai Chuyên viên tài chính VIP, trả lời tiếng Việt, phong cách chuyên nghiệp trang trọng.

### 4.3. TTS Abstraction Layer

Mặc định pass-through audio từ Gemini. Server gửi xuống frontend luôn kèm cả text và audio:

```json
{
  "type": "speech_output",
  "text_content": "Chào mừng anh quay trở lại.",
  "audio_source": "gemini",
  "audio_data": "<base64 audio>"
}
```

Nếu đánh giá giọng Việt của Gemini không đạt → swap sang TTS chuyên dụng (Azure / Viettel AI) bằng cách chuyển Gemini sang `response_modalities: ["TEXT"]`, đẩy text qua TTS provider, chỉ thay đổi ở backend, frontend không cần sửa.

**Tiêu chí đánh giá giọng Gemini TTS tiếng Việt:**

| Tiêu chí           | Đạt                            | Không đạt → Swap TTS                     |
|---------------------|--------------------------------|-------------------------------------------|
| Thanh điệu 6 thanh | Phát âm đúng ≥ 90%            | Sai thanh điệu gây hiểu nhầm             |
| Độ tự nhiên         | Nghe như người Việt nói        | Nghe rõ ràng là máy / ngoại quốc          |
| Tốc độ & ngắt nghỉ | Phù hợp ngữ cảnh tài chính    | Nói quá nhanh hoặc ngắt vô nghĩa         |
| Ấn tượng demo       | Khách không comment về giọng   | Khách hỏi "sao giọng nghe lạ?"            |

> Fail bất kỳ 1 trong 2 tiêu chí đầu → swap sang TTS chuyên dụng.

## 5. Frontend (Web Client)

**Môi trường:** Chromium/Edge chế độ Kiosk (full-screen, ẩn UI).

### 5.1. Render 3D Avatar

- **Thư viện:** Three.js + `@pixiv/three-vrm` (chuẩn VRM Consortium).
- **Model:** File `.vrm` từ VRoid Hub, đặt tại `frontend/public/models/`.
- **Head Tracking:** Parse JSON (Pitch/Yaw/Roll) từ WebSocket → `THREE.Quaternion` xoay node xương head + neck. Dùng `MathUtils.clamp()` giới hạn góc quay + Lerp nội suy làm mượt giữa các frame.
- **Idle Animation:** Chớp mắt ngẫu nhiên mỗi 3–5 giây, nhịp thở nhẹ bằng `Math.sin()` trên xương chest.

### 5.2. Lip-sync (Amplitude-based)

- Nhận audio từ WebSocket → Web Audio API (`AudioContext`).
- `AnalyserNode` trích xuất biên độ RMS mỗi `requestAnimationFrame`.
- Map biên độ vào 1 blendshape há miệng (ví dụ `aa`) của mô hình VRM.
- POC sử dụng amplitude-based (miệng mở to/nhỏ theo độ lớn âm thanh). Mapping 5 viseme (A/I/U/E/O) riêng biệt là mục tiêu phiên bản sau.

## 6. WebSocket JSON Schema

**Server → Client: Tracking Update (~30Hz liên tục)**

```json
{
  "type": "tracking_update",
  "timestamp": 1709400000.123,
  "avatar_control": {
    "head_rotation": { "pitch": 0.12, "yaw": -0.05, "roll": 0.01 },
    "is_speaking": false
  }
}
```

**Server → Client: Speech Output (event-driven)**

```json
{
  "type": "speech_output",
  "text_content": "Chào mừng anh quay trở lại.",
  "audio_source": "gemini",
  "audio_data": "<base64 audio>"
}
```

**Client → Server: Audio Chunk (khi khách nói)**

```json
{
  "type": "audio_chunk",
  "audio_data": "<base64 PCM 16kHz>"
}
```

## 7. Phân tier tính năng POC

| Tier | Tính năng                                                     | Ghi chú                              |
|------|---------------------------------------------------------------|---------------------------------------|
| P0   | Head tracking (MediaPipe → Avatar quay đầu)                  | Bắt buộc                             |
| P0   | Hội thoại voice (Gemini Live: nghe → suy luận → nói)         | Bắt buộc                             |
| P0   | Lip-sync amplitude-based                                      | Bắt buộc                             |
| P0   | Blink & breathing idle animation                              | Bắt buộc                             |
| P1   | Gemini nhận diện ngữ cảnh thị giác (biểu cảm, trang phục)   | Có thì tốt                           |
| P1   | VIP recognition (InsightFace local nếu VRAM đủ)              | Nice-to-have, có thể hardcode        |
| P2   | Noise suppression, One Euro Filter                            | Bỏ qua ở POC                         |
| P2   | Offline fallback, error handling nâng cao                     | Bỏ qua ở POC                         |

## 8. Tiêu chí thành công

- Avatar quay đầu theo khách real-time (latency < 200ms).
- Khách nói → Avatar trả lời bằng giọng nói với miệng mấp máy (latency câu đầu < 5 giây).
- Hệ thống chạy ổn định ít nhất 30 phút liên tục, VRAM < 2 GB.
- (Bonus) AI nhận biết ngữ cảnh thị giác và chủ động đề cập trong hội thoại.

## 9. Rủi ro đã nhận diện

| Rủi ro                                | Mức độ (POC) | Phương án xử lý                                            |
|----------------------------------------|--------------|-------------------------------------------------------------|
| Giọng Việt Gemini TTS kém             | Trung bình   | Đánh giá theo tiêu chí → swap Azure/Viettel nếu cần        |
| VRAM OOM                              | Thấp         | Chỉ dùng ~1 GB, monitor bằng `nvidia-smi`                  |
| Mất mạng → Avatar câm điếc           | Chấp nhận    | POC chạy môi trường kiểm soát, note cho production          |
| Chi phí API Gemini Live               | Chấp nhận    | VAD kiểm soát mở/đóng session, POC dùng ít                 |
| Frontend audio streaming phức tạp     | Trung bình   | AudioWorklet + queue, pattern đã proven                     |
