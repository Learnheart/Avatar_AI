TÀI LIỆU ĐẶC TẢ KỸ THUẬT: MAGIC MIRROR AI AVATAR (WEBGL POC)

1. Tổng quan Kiến trúc Hệ thống (System Architecture)
   Hệ thống sử dụng mô hình Client-Server bất đồng bộ qua WebSocket. Máy chủ Ubuntu (Hardware backend) sẽ gánh vác toàn bộ tác vụ tính toán AI nặng, trong khi Trình duyệt Web (Client frontend) đảm nhiệm kết xuất đồ họa 3D trực tiếp bằng tài nguyên của màn hình Kiosk.

Phân bổ tài nguyên VRAM (RTX 3070 - 8GB):
Việc quản lý VRAM phải được kiểm soát nghiêm ngặt theo giới hạn sau để tránh lỗi Out of Memory (OOM):

Vision Engine (MediaPipe + InsightFace): Cố định ~1.5GB - 2GB VRAM.

Voice Engine (Local STT/TTS nếu có): Cố định ~3GB - 4GB VRAM.

Logic / Buffer: ~1GB VRAM.

LLM Engine: Bắt buộc sử dụng Cloud API (Gemini API) để bảo toàn VRAM cục bộ và đảm bảo tốc độ sinh text dạng stream tốc độ cao.

2. Đặc tả Module Backend (Server Ubuntu)
   Môi trường: Python 3.10+, Docker & NVIDIA Container Toolkit.

Web Framework: FastAPI (quản lý luồng WebSocket và API endpoint).

2.1. Module A: Vision & Tracking Engine
Chức năng: Trích xuất đặc trưng khuôn mặt và tọa độ 3D theo thời gian thực từ luồng camera của Kiosk gửi lên (hoặc camera kết nối trực tiếp với server nếu đặt gần).

Công nghệ:

OpenCV: Xử lý mảng hình ảnh (frames).

InsightFace (model: buffalo_l): Nhận diện khuôn mặt khách hàng VIP, trích xuất vector 512-D so khớp với cơ sở dữ liệu Vector DB (như FAISS hoặc ChromaDB dạng in-memory).

MediaPipe Face Mesh: Tính toán ma trận xoay góc đầu (Pitch, Yaw, Roll) để tạo dữ liệu Inverse Kinematics (IK) cho Avatar.

Tần số xuất dữ liệu: Tối thiểu 30 FPS.

2.2. Module B: AI & Voice Engine
Chức năng: Lắng nghe, chuyển đổi giọng nói, gọi LLM API và trả về âm thanh kèm tín hiệu cho Frontend.

Công nghệ:

VAD (Voice Activity Detection): Sử dụng Silero VAD để phát hiện điểm bắt đầu/kết thúc câu nói của khách hàng độ trễ thấp (<50ms).

STT (Speech-to-Text): Sử dụng Faster-Whisper (model: small hoặc base) chạy trên RTX 3070 để dịch âm thanh thành văn bản theo thời gian thực.

LLM (Brain): Gọi API google-genai (Gemini model). Bắt buộc thiết lập System Prompt đóng vai Chuyên viên tài chính VIP và bật chế độ stream=True.

TTS (Text-to-Speech): Sử dụng API của các nhà cung cấp uy tín (Azure, Viettel AI) để có giọng đọc tự nhiên, hoặc mô hình cục bộ nhẹ. File âm thanh trả về được mã hóa dạng Base64 hoặc trả về URL qua WebSocket.

3. Đặc tả Module Frontend (Web Client)
   Môi trường: Trình duyệt Chromium/Edge chạy chế độ Kiosk (Full-screen, ẩn UI).

Công nghệ Cốt lõi: Three.js (WebGL Render Engine).

3.1. Render 3D & Avatar Control
Thư viện: Bắt buộc sử dụng @pixiv/three-vrm. Đây là chuẩn công nghiệp mở (VRM Consortium) chuyên dụng cho Web Avatar.

Tính năng cốt lõi cần lập trình:

Head Tracking (IK): Parse chuỗi JSON nhận từ Server (chứa Pitch, Yaw, Roll), áp dụng toán học Quaternion (THREE.Quaternion) để xoay các node xương head và neck của mô hình VRM. Cần sử dụng hàm MathUtils.clamp() để khóa giới hạn góc quay, tránh việc Avatar gãy cổ.

Blink & Idle Animation: Tự động tạo một chu kỳ chớp mắt (blink) ngẫu nhiên mỗi 3-5 giây và một nhịp thở nhẹ (breathing) sử dụng hàm Math.sin() áp dụng lên xương ngực (chest).

3.2. Real-time Lip-sync (Khớp khẩu hình miệng)
Bỏ qua các package đồ sộ của Unity. Với WebGL, Agent code cần triển khai theo phương pháp Audio Analyzer:

Nhận file audio (Base64/URL) từ Server qua WebSocket.

Đưa vào Web Audio API (AudioContext).

Tạo một AnalyserNode để trích xuất biên độ âm thanh (tần số) mỗi frame requestAnimationFrame.

Map biên độ này vào tham số blendShapeProxy của mô hình VRM (thường là shape "A", "I", "U", "E", "O" hoặc một Blendshape há miệng chung). Môi Avatar sẽ mấp máy khớp xác suất cao với độ lớn của âm thanh.

4. Giao thức Giao tiếp (WebSocket JSON Schema)
   Agent code cần tuân thủ cấu trúc dữ liệu giao tiếp hai chiều sau đây.

Payload từ Server gửi xuống Web Client (Mỗi frame ~30Hz):

JSON
{
"type": "tracking_update",
"timestamp": 1709400000.123,
"client": {
"status": "detected",
"vip_profile": { "is_vip": true, "name": "Nguyen Van A" }
},
"avatar_control": {
"head_rotation": { "pitch": 0.12, "yaw": -0.05, "roll": 0.01 },
"is_speaking": false
}
}
Payload từ Server gửi xuống khi có câu trả lời (Audio Trigger):

JSON
{
"type": "audio_response",
"timestamp": 1709400001.000,
"text_content": "Chào mừng anh Nguyễn Văn A quay trở lại.",
"audio_data": "data:audio/mp3;base64,//NExAAAAANIAAAAAExBTUUzLjEwMKqqqqqq..."
} 5. Hướng dẫn Triển khai (Dành cho Agent Code)
Bước 1: Khởi tạo Backend. Tạo thư mục backend, thiết lập requirements.txt với fastapi, uvicorn, websockets, opencv-python, mediapipe, faster-whisper, google-genai. Viết file server.py mở cổng ws://localhost:8000/ws.

Bước 2: Chuẩn bị 3D Model. Tải một mô hình .vrm mẫu (từ VRoid Hub) đặt vào thư mục frontend/public/models/.

Bước 3: Khởi tạo Frontend. Tạo file index.html và app.js. Nhúng three.js và three-vrm qua CDN hoặc Vite. Viết logic kết nối WebSocket đến port 8000 và render mô hình lên thẻ <canvas>.

Bước 4: Tích hợp logic. Viết hàm nội suy (Lerp) trong vòng lặp render của Three.js để làm mượt các góc quay nhận được từ JSON, triệt tiêu hiện tượng giật cục (jitter) do sai số của camera AI.
