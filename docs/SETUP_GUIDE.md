# Setup Guide — Magic Mirror AI Avatar

> **Phiên bản:** POC v0.1
> **Mục đích:** Hướng dẫn đầy đủ để kích hoạt toàn bộ hệ thống từ đầu.

---

## Mục lục

1. [Yêu cầu phần cứng](#1-yêu-cầu-phần-cứng)
2. [Yêu cầu phần mềm hệ thống](#2-yêu-cầu-phần-mềm-hệ-thống)
3. [Yêu cầu tài khoản & API key](#3-yêu-cầu-tài-khoản--api-key)
4. [Yêu cầu asset 3D](#4-yêu-cầu-asset-3d)
5. [Cấu hình môi trường (.env)](#5-cấu-hình-môi-trường-env)
6. [Cấu hình camera — DroidCam (WiFi Phone Camera)](#6-cấu-hình-camera--droidcam-wifi-phone-camera)
7. [Thứ tự khởi động — Local (Development)](#7-thứ-tự-khởi-động--local-development)
8. [Thứ tự khởi động — Docker (Khuyến nghị)](#8-thứ-tự-khởi-động--docker-khuyến-nghị)
9. [Kiểm tra hệ thống sau khi chạy](#9-kiểm-tra-hệ-thống-sau-khi-chạy)
10. [Mở giao diện Kiosk](#10-mở-giao-diện-kiosk)
11. [Xử lý sự cố thường gặp](#11-xử-lý-sự-cố-thường-gặp)

---

## 1. Yêu Cầu Phần Cứng

| Thành phần | Tối thiểu | Ghi chú |
|------------|-----------|---------|
| **GPU** | NVIDIA RTX 3070 8GB | VRAM budget: ~1 GB dùng, ~7 GB dư |
| **RAM** | 8 GB | Python process dùng < 2 GB |
| **CPU** | 4 cores | VAD + orchestrator chạy trên CPU |
| **Camera** | 720p @ 30 FPS | USB hoặc điện thoại qua DroidCam WiFi |
| **Microphone** | Bất kỳ | Kết nối qua browser (getUserMedia) |
| **Màn hình** | 1080p+ | Fullscreen kiosk mode |
| **Mạng** | Internet ổn định | Kết nối Gemini Live API |

> Hệ thống mặc định dùng `/dev/video0`. Nếu có nhiều camera, đổi `CAMERA_INDEX` trong `.env`.
> Hỗ trợ cả **USB camera** và **DroidCam** (điện thoại qua WiFi) — xem mục 6.

---

## 2. Yêu Cầu Phần Mềm Hệ Thống

### 2a. Nếu chạy bằng Docker (Khuyến nghị)

```bash
# 1. Docker Engine >= 24.0
curl -fsSL https://get.docker.com | sh

# 2. Docker Compose plugin >= 2.24
docker compose version   # Phải >= 2.24

# 3. NVIDIA Container Toolkit (để GPU passthrough hoạt động)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Kiểm tra
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### 2b. Nếu chạy Local (Development)

```bash
# Python 3.11+
python3 --version   # Phải >= 3.10

# pip
pip install --upgrade pip

# Node.js 20+
node --version      # Phải >= 18
npm --version

# NVIDIA Driver + CUDA (cho GPU)
nvidia-smi          # Kiểm tra driver đang hoạt động
# CUDA >= 11.8 (PyTorch requirement)

# Các lib system cần cho OpenCV + MediaPipe
sudo apt-get install -y \
  libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 libgomp1
```

---

## 3. Yêu Cầu Tài Khoản & API Key

### 3a. Gemini Live API (BẮT BUỘC)

1. Truy cập [Google AI Studio](https://aistudio.google.com/apikey)
2. Tạo API key mới
3. Đảm bảo account đã được **enable Gemini Live API** (model `gemini-2.0-flash-live-001`)
4. Copy key → điền vào `.env`: `GEMINI_API_KEY=...`

> 💡 Kiểm tra API key còn hạn và có quota trước khi demo.

### 3b. Azure TTS (Tùy chọn — chỉ nếu `TTS_MODE=azure`)

1. Tạo resource **Speech Service** trên Azure Portal
2. Copy **Key 1** → `AZURE_TTS_KEY=...`
3. Copy **Region** (vd: `southeastasia`) → `AZURE_TTS_REGION=...`
4. Voice tiếng Việt khuyến nghị: `vi-VN-HoaiMyNeural`

### 3c. Viettel AI TTS (Tùy chọn — chỉ nếu `TTS_MODE=viettel`)

1. Đăng ký tại [Viettel AI](https://viettelai.vn)
2. Lấy API key → `VIETTEL_TTS_KEY=...`
3. Endpoint URL → `VIETTEL_TTS_URL=...`

---

## 4. Yêu Cầu Asset 3D

### VRM Model (BẮT BUỘC cho frontend)

File VRM cần đặt tại: **`frontend/public/models/avatar.vrm`**

**Cách lấy model:**

```bash
# Option A: Download từ VRoid Hub (miễn phí)
# 1. Truy cập https://hub.vroid.com
# 2. Tìm model có license "Available for use in all types of work"
# 3. Download file .vrm
# 4. Đổi tên thành avatar.vrm, đặt vào đúng path

cp ~/Downloads/your-model.vrm frontend/public/models/avatar.vrm

# Option B: Tạo model bằng VRoid Studio (miễn phí)
# https://vroid.com/en/studio
# Export → .vrm → đặt vào path trên
```

**Kiểm tra blendshapes cần có:**

| Blendshape | Dùng cho | Fallback nếu thiếu |
|------------|----------|--------------------|
| `blinkLeft` | Chớp mắt trái | Disable blink |
| `blinkRight` | Chớp mắt phải | Disable blink |
| `aa` | Lip-sync miệng | Disable lip-sync |

> ⚠️ Nếu VRM thiếu blendshapes, hệ thống vẫn chạy được nhưng avatar không chớp mắt / không mấp máy miệng.

---

## 5. Cấu Hình Môi Trường (.env)

```bash
# Copy template
cp .env.example .env

# Mở file và điền các giá trị
nano .env   # hoặc dùng editor khác
```

**Các field BẮT BUỘC phải điền:**

```bash
GEMINI_API_KEY=your_actual_api_key_here    # ← BẮT BUỘC
```

**Các field quan trọng cần kiểm tra:**

```bash
CAMERA_INDEX=0          # Đổi nếu camera không phải /dev/video0
TTS_MODE=gemini         # Giữ "gemini" cho POC — đơn giản nhất
GEMINI_VOICE=Kore       # Giọng Kore hoặc Charon, Aoede, Fenrir, Puck
```

**Kiểm tra `.env` trước khi chạy:**

```bash
# Đảm bảo không có giá trị trống bắt buộc
grep "GEMINI_API_KEY=" .env
# Kết quả phải có giá trị thực, không phải "your_key_here"
```

> 🔒 **Bảo mật:** File `.env` đã được gitignore. Không bao giờ commit file này lên git.

---

## 6. Cấu Hình Camera — DroidCam (WiFi Phone Camera)

> **Dùng khi:** Không có USB camera vật lý, hoặc muốn dùng camera điện thoại chất lượng cao.
> IP điện thoại hiện tại: `192.168.1.15`

### 6a. Cài đặt lần đầu (một lần duy nhất)

```bash
# 1. Cài build deps
sudo apt-get install -y git dkms libv4l-dev libudev-dev libusb-1.0-0-dev

# 2. Clone source mới nhất (tương thích kernel 6.x)
cd /tmp && git clone --depth=1 https://github.com/dev47apps/droidcam.git droidcam-src
cd droidcam-src

# 3. Tạo thư mục cần thiết (Ubuntu 24.04 có thể thiếu)
sudo mkdir -p /usr/local/bin

# 4. Cài client (droidcam + droidcam-cli)
sudo bash install-client

# 5. Build và cài video driver (v4l2loopback-dc)
sudo bash install-video

# 6. Load module ngay (không cần reboot)
sudo modprobe v4l2loopback_dc

# 7. Kiểm tra thiết bị xuất hiện
ls /dev/video*   # Phải thấy /dev/video0
```

> **Lưu ý kernel 6.x:** Driver bundled trong bản zip cũ không tương thích. Phải clone từ GitHub như trên.
> Driver sẽ tự load khi boot nhờ `/etc/modules-load.d/droidcam.conf`.

### 6b. Kết nối mỗi khi dùng

**Trên điện thoại:**
1. Cài app **DroidCam** (Google Play Store)
2. Mở app — đảm bảo điện thoại cùng mạng WiFi với máy tính
3. App hiển thị IP (phải là `192.168.1.15`) và port `4747`

**Trên máy tính:**
```bash
# Kết nối (chỉ video, không cần audio)
droidcam-cli -v 192.168.1.15 4747 &

# Kiểm tra kết nối
v4l2-ctl --device=/dev/video0 --info
# Phải thấy: Driver name: Droidcam
```

### 6c. Script tiện lợi — khởi động DroidCam

```bash
# Tạo alias để dùng nhanh (thêm vào ~/.bashrc)
alias droidcam-start='droidcam-cli -v 192.168.1.15 4747 &'
alias droidcam-stop='pkill -f droidcam-cli'
```

### 6d. Cấu hình .env khi dùng DroidCam

```bash
# DroidCam tạo /dev/video0 → không cần đổi gì nếu không có camera khác
CAMERA_INDEX=0

# Nếu có USB camera khác cũng cắm vào, DroidCam có thể là video1 hoặc video2
# Kiểm tra: v4l2-ctl --list-devices
```

### 6e. Tích hợp với Docker

DroidCam chạy ở host, Docker backend cần mount device:

```yaml
# docker-compose.yml (đã có sẵn)
devices:
  - /dev/video0:/dev/video0
```

> Phải chạy `droidcam-cli` ở **host** trước khi `docker compose up backend`.

---

## 8. Thứ Tự Khởi Động — Docker (Khuyến Nghị)

### Bước 1: Chuẩn bị

```bash
# Di chuyển vào thư mục dự án
cd /mnt/hdd/projects/avatar_AI

# Đảm bảo .env đã cấu hình
cat .env | grep GEMINI_API_KEY   # Phải có giá trị

# Đảm bảo VRM model đã có
ls -lh frontend/public/models/avatar.vrm

# Nếu dùng DroidCam: kết nối trước khi chạy Docker
droidcam-cli -v 192.168.1.15 4747 &
ls /dev/video0   # Phải tồn tại
```

### Bước 2: Build images

```bash
docker compose build
# Lần đầu mất ~5-10 phút (download base images + pip install)
# Lần sau nhanh hơn (layer cache)
```

### Bước 3: Đảm bảo user trong group video

```bash
# Chạy một lần, sau đó logout/login lại
sudo usermod -aG video $USER
```

### Bước 4: Khởi động backend trước

```bash
docker compose up backend
# Chờ đến khi thấy log:
# {"msg": "System ready", ...}
# {"msg": "Vision module started", ...}
# {"msg": "Silero VAD loaded", ...}
```

> Silero VAD lần đầu sẽ download model (~30MB) — cần internet, mất 1-2 phút.

### Bước 5: Khởi động frontend

```bash
# Trong terminal mới
docker compose up frontend
# Chờ đến khi thấy:
# Local:   http://localhost:3000/
```

### Bước 6: Chạy cả hai cùng lúc (sau khi đã test riêng)

```bash
docker compose up -d
# Xem logs
docker compose logs -f
```

---

## 7. Thứ Tự Khởi Động — Local (Development)

> **Trạng thái hiện tại (2026-03-03):** `.venv` và `frontend/node_modules` đã cài xong.
> Silero VAD đã pre-download tại `~/.cache/torch/hub/`. Chỉ cần chạy từ Bước 3.

### Bước 1: Chuẩn bị Python environment (lần đầu)

```bash
cd /mnt/hdd/projects/avatar_AI

# Tạo virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Cài PyTorch với CUDA 12.1
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# Cài toàn bộ dependencies
pip install -r backend/requirements.txt

# Pre-download Silero VAD để tránh delay khi start
python3 -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)"
```

### Bước 2: Cài frontend dependencies (lần đầu)

```bash
cd /mnt/hdd/projects/avatar_AI/frontend
npm install --legacy-peer-deps   # flag cần cho peer dep conflicts
cd ..
```

### Bước 3: Kết nối camera

```bash
# Option A — DroidCam (điện thoại qua WiFi):
droidcam-cli -v 192.168.1.15 4747 &
# Xem mục 6 để biết chi tiết cài đặt DroidCam

# Option B — USB camera:
ls /dev/video*   # Cắm USB và kiểm tra thiết bị xuất hiện
```

### Bước 4: Kiểm tra .env

```bash
grep "GEMINI_API_KEY=" .env   # Phải có giá trị thực (không phải placeholder)
```

### Bước 5: Khởi động backend

```bash
# Từ thư mục root của project (PYTHONPATH quan trọng)
cd /mnt/hdd/projects/avatar_AI
source .venv/bin/activate

PYTHONPATH=/mnt/hdd/projects/avatar_AI \
  uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload

# Chờ log: {"msg": "System ready"}
```

### Bước 6: Khởi động frontend

```bash
# Trong terminal mới
cd /mnt/hdd/projects/avatar_AI/frontend
npm run dev

# Chờ: Local:   http://localhost:3000/
```

---

## 9. Kiểm Tra Hệ Thống Sau Khi Chạy

### 9a. Health check backend

```bash
curl http://localhost:8000/health
```

**Kết quả mong đợi:**
```json
{
  "status": "ok",
  "uptime_seconds": 15.3,
  "vram_used_gb": 0.9,
  "avatar_state": "idle",
  "camera_connected": true,
  "gemini_session_active": false
}
```

| Field | Giá trị bình thường | Cần kiểm tra nếu |
|-------|---------------------|------------------|
| `status` | `"ok"` | Bất kỳ giá trị khác |
| `camera_connected` | `true` | `false` → camera chưa kết nối |
| `vram_used_gb` | < 2.0 | > 6.0 → risk OOM |
| `avatar_state` | `"idle"` hoặc `"tracking"` | `"idle"` mãi → camera không detect face |

### 9b. Smoke test checklist (Milestone 1)

```
□  docker compose up (hoặc local) → backend start không lỗi
□  curl /health → status: ok, camera_connected: true
□  Mở http://localhost:3000 → avatar load (không có loading error)
□  WebSocket kết nối → status indicator chuyển thành "● ONLINE"
□  Đứng trước camera → avatar quay đầu theo (~< 200ms)
□  Nói vào mic → state chuyển LISTENING (kiểm tra backend log)
□  Dừng nói → Gemini trả lời, avatar mấp máy miệng
```

### 9c. Monitor VRAM

```bash
# Trong terminal riêng, chạy liên tục
watch -n 2 nvidia-smi

# Giá trị an toàn: < 2 GB
# Cảnh báo: > 4 GB
# Nguy hiểm: > 6 GB → risk crash
```

### 9d. Xem logs real-time

```bash
# Docker
docker compose logs -f backend

# Local
# Logs xuất ra stdout theo format JSON, ví dụ:
# {"ts": "2026-03-03T...", "level": "INFO", "module": "orchestrator", "msg": "State: idle → tracking"}
```

---

## 10. Mở Giao Diện Kiosk

### Chế độ development (testing)

```
http://localhost:3000
```

### Chế độ kiosk (production demo)

```bash
# Chromium fullscreen kiosk mode — QUAN TRỌNG: cần flag autoplay
chromium-browser \
  --kiosk \
  --enable-webgl \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --disable-infobars \
  --no-first-run \
  http://localhost:3000

# Lưu ý: --autoplay-policy=no-user-gesture-required là BẮT BUỘC
# Không có flag này → AudioContext bị suspend → không phát được audio
```

---

## 11. Xử Lý Sự Cố Thường Gặp

### ❌ DroidCam driver không build được (kernel mới)

```bash
# Lỗi: "setup_timer undeclared" hoặc "vidioc_queryctrl no member"
# Nguyên nhân: bản zip bundled dùng API kernel cũ

# Fix: Clone source từ GitHub (đã fix API mới)
cd /tmp && git clone --depth=1 https://github.com/dev47apps/droidcam.git droidcam-src
sudo mkdir -p /usr/local/bin
cd droidcam-src
sudo bash install-client
sudo bash install-video
sudo modprobe v4l2loopback_dc
```

### ❌ DroidCam kết nối bị reset ("Connection reset!")

```bash
# Kiểm tra port 4747 trên điện thoại
nc -zv 192.168.1.15 4747   # Phải "succeeded"

# Nếu port không mở → mở app DroidCam trên điện thoại trước
# Nếu port mở nhưng vẫn reset → thử kết nối lại sau 2-3 giây
droidcam-cli -v 192.168.1.15 4747 &
```

### ❌ Camera không kết nối (`camera_connected: false`)

```bash
# Kiểm tra camera có nhận không
ls /dev/video*
v4l2-ctl --list-devices   # Nếu có v4l-utils

# Thử camera index khác
# → Đổi CAMERA_INDEX=1 (hoặc 2) trong .env

# Docker: đảm bảo device mount đúng
# docker-compose.yml: devices: [/dev/video0:/dev/video0]
```

### ❌ Silero VAD không load (`torch.hub.load` timeout)

```bash
# Pre-download thủ công
python3 -c "
import torch
model, _ = torch.hub.load('snakers4/silero-vad', 'silero_vad',
                           force_reload=True, trust_repo=True)
print('VAD loaded OK')
"

# Hoặc cài pip package trực tiếp
pip install silero-vad
```

### ❌ Gemini API lỗi 403 / key không hợp lệ

```bash
# Test API key
curl -H "Content-Type: application/json" \
  "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_API_KEY"
# Phải trả về list models, không phải error
```

### ❌ WebSocket không kết nối (status "● OFFLINE")

```bash
# Kiểm tra backend đang chạy
curl http://localhost:8000/health

# Kiểm tra VITE_WS_URL
# docker-compose.yml: VITE_WS_URL=ws://localhost:8000/ws
# Nếu frontend chạy trên máy khác, đổi localhost thành IP của backend
```

### ❌ Avatar không load (VRM error)

```bash
# Kiểm tra file tồn tại
ls -lh frontend/public/models/avatar.vrm
# Phải có file, không phải symlink hỏng

# Kiểm tra console browser (F12)
# Lỗi thường gặp: "Failed to load resource: net::ERR_FILE_NOT_FOUND"
```

### ❌ Audio không phát (lip-sync không hoạt động)

```bash
# Mở browser console (F12)
# Tìm: "[Audio] Mic permission denied" → cấp quyền microphone cho browser
# Tìm: "AudioContext suspended" → cần flag --autoplay-policy khi dùng Chromium kiosk
```

### ❌ Docker: NVIDIA GPU không nhận

```bash
# Kiểm tra nvidia-container-toolkit
nvidia-ctk --version

# Test GPU trong container
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi

# Nếu lỗi: "could not select device driver"
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

---

## Phụ Lục: Các Giá Trị Mặc Định Quan Trọng

| Config | Mặc định | Ý nghĩa |
|--------|----------|---------|
| `TRACKING_FPS` | 30 | Tần suất gửi tracking_update qua WS |
| `VAD_THRESHOLD` | 0.5 | Độ nhạy VAD (0.0-1.0, thấp hơn = nhạy hơn) |
| `NO_FACE_TIMEOUT_SEC` | 3.0 | Giây không thấy mặt → về IDLE |
| `GEMINI_IDLE_TIMEOUT_SEC` | 30.0 | Giây im lặng → đóng Gemini session |
| `SNAPSHOT_INTERVAL_SEC` | 2.5 | Tần suất gửi ảnh camera cho Gemini |
| `HEAD_YAW_MAX` | 0.78 rad | Giới hạn quay ngang (~45°) |
