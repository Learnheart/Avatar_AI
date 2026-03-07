# Magic Mirror AI Avatar — Setup Guide

## Yeu cau he thong

| Thanh phan | Yeu cau |
|---|---|
| OS | Linux (tested Ubuntu 24.04, kernel 6.17) |
| Python | >= 3.12 (tested 3.13 via conda) |
| Node.js | >= 18 |
| GPU | NVIDIA GPU voi CUDA (tested RTX 3070, CUDA 11.8) |
| Camera | Webcam USB, hoac phone app ho tro MJPEG qua HTTP |

## 1. Clone & cau truc thu muc

```
avatar_AI/
  backend/             # FastAPI + Python modules
    models/            # face_landmarker.task (MediaPipe model)
    modules/           # vision, vad, gemini_client, tts
  frontend/            # Vite + Three.js + VRM
    public/models/     # avatar.vrm (VRM 3D model)
    public/worklet/    # audio-processor.js (AudioWorklet)
    src/               # main.js, ws-client, avatar-renderer, audio-manager, lip-sync
  tests/               # pytest tests
  .env                 # Config (tao tu .env.example)
```

## 2. Cai dat Python dependencies

Dung conda (khuyen nghi):

```bash
# Tao environment (hoac dung conda base)
conda create -n avatar python=3.12 -y
conda activate avatar

# PyTorch voi CUDA
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118

# Core dependencies
pip install \
  fastapi==0.128.0 \
  uvicorn[standard]==0.40.0 \
  python-dotenv==1.1.0 \
  google-genai==1.65.0 \
  mediapipe==0.10.32 \
  opencv-python==4.13.0.92 \
  numpy \
  httpx \
  websockets

# Test dependencies (optional)
pip install pytest pytest-asyncio httpx
```

## 3. Download models

### MediaPipe Face Landmarker

```bash
# Download vao backend/models/
mkdir -p backend/models
wget -O backend/models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
```

### VRM Avatar Model

Download file `.vrm` tu [VRoid Hub](https://hub.vroid.com/) va dat vao:

```
frontend/public/models/avatar.vrm
```

## 4. Cai dat Frontend

```bash
cd frontend
npm install --legacy-peer-deps
cd ..
```

## 5. Cau hinh .env

```bash
cp .env.example .env
```

Sua file `.env`:

```ini
# BAT BUOC — Gemini API key tu Google AI Studio
GEMINI_API_KEY=your_api_key_here

# Camera — chon 1 trong 2:
CAMERA_INDEX=0                                          # USB webcam (device index)
CAMERA_URL=http://admin:admin@192.168.1.15:8081/video   # IP camera (MJPEG URL)
CAMERA_ROTATE=0                                         # 0, 90, -90, 180

# Cac config khac de default la duoc
```

Lay Gemini API key tai: https://aistudio.google.com/apikey

## 6. Chay

### Terminal 1 — Backend

```bash
cd /path/to/avatar_AI
conda activate avatar
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### Terminal 2 — Frontend

```bash
cd /path/to/avatar_AI/frontend
npm run dev
```

### Truy cap

| URL | Mo ta |
|---|---|
| http://localhost:3000 | Frontend (avatar 3D + voice) |
| http://localhost:8000/health | Backend health check |
| ws://localhost:8000/ws | WebSocket endpoint (frontend tu dong ket noi) |

## 7. Kiem tra nhanh

### Backend health

```bash
curl http://localhost:8000/health
# {"status":"ok","avatar_state":"tracking","camera_connected":true,...}
```

### Camera test

```bash
python tests/quicktest_camera.py
# Hoac chi dinh camera:
python tests/quicktest_camera.py http://ip:port/video -90
python tests/quicktest_camera.py 0
```

### Chay tests

```bash
python -m pytest tests/ -v
```

## 8. Camera setup

### USB Webcam
Dat `CAMERA_INDEX=0` trong `.env`. Khong can `CAMERA_URL`.

### Phone lam camera (IP Camera)
1. Cai app **IP Camera Lite** (iOS) hoac **IP Webcam** (Android)
2. Mo app, bat camera stream
3. Dat `CAMERA_URL=http://[user:pass@]ip:port/video` trong `.env`
4. Neu phone dat ngang: `CAMERA_ROTATE=90` hoac `CAMERA_ROTATE=-90`
5. Test: `python tests/quicktest_camera.py`

## 9. Luong hoat dong

```
[Camera] → Vision (MediaPipe face tracking)
                ↓
[Mic/Browser] → AudioWorklet → WebSocket → VAD (Silero)
                                              ↓
                                         Gemini Live API ←→ Voice conversation
                                              ↓
                                    Audio response → WebSocket → Browser playback
                                              ↓
                           Avatar 3D (VRM) ← head tracking + lip sync
```

1. Nguoi dung dung truoc camera → avatar bat dau tracking khuon mat
2. Nguoi dung noi → VAD phat hien giong noi → gui audio den Gemini
3. Gemini tra loi bang giong noi → avatar noi + lip sync
4. Nguoi dung co the ngat loi (barge-in) bat cu luc nao

## Troubleshooting

| Van de | Giai phap |
|---|---|
| `GEMINI_API_KEY is required` | Thieu API key trong `.env` |
| Camera khong mo | Kiem tra `CAMERA_URL` hoac `CAMERA_INDEX`, chay `quicktest_camera.py` |
| MediaPipe loi | Dam bao `backend/models/face_landmarker.task` ton tai |
| Frontend loi khi npm install | Dung flag `--legacy-peer-deps` |
| Khong co avatar | Can file `frontend/public/models/avatar.vrm` |
| CUDA khong nhan | Kiem tra `python -c "import torch; print(torch.cuda.is_available())"` |
