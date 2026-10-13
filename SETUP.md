# Avatar AI (Magic Mirror) — Setup

A kiosk avatar: camera presence + head tracking, and a separated speech pipeline
(Silero VAD → Groq Whisper STT → Groq LLM → ZeroTTS) driving a VRM avatar in the browser.

- Install, run and check commands: **`CLAUDE.md` §5** (the single source — not repeated here).
- Architecture: `docs/ARCHITECTURE.html`. Source layout: `CLAUDE.md` §4.

## What you need

| Item | Notes |
|---|---|
| Python 3.10+ and `uv` | Backend virtualenv in `.venv` |
| Node.js 18+ | Frontend (Vite + Three.js) |
| Groq API key | `groq_api_key` in `.env` (lowercase name); used for STT and LLM |
| Network access to Hugging Face | Only for the first backend start, which downloads the ZeroTTS weights (about 910 MB) into the Hugging Face cache |
| Webcam | Optional — without one the app runs with no head tracking |
| VRM model | `frontend/public/models/avatar.vrm` (or `VITE_VRM_PATH`); without it the page runs with audio only |

## Configuration

- Backend: copy `.env.example` to `.env` and fill in `groq_api_key`. Every other key has
  a working default and is described by the comment above it in `.env.example`.
- Camera: `CAMERA_INDEX` for a USB webcam, or `CAMERA_URL` for an IP camera stream;
  `CAMERA_ROTATE` (90, -90, 180) for a camera mounted sideways.
- Frontend: `frontend/.env.example` lists the `VITE_*` values (`VITE_WS_URL` must point
  at the backend's `HOST`/`PORT`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `groq_api_key is required` at startup | The key is missing from `.env` |
| Startup exits with `ZeroTTS could not load ...` | The weights are not cached and Hugging Face is unreachable, or `ZEROTTS_REPO` is wrong |
| `ZEROTTS_VOICE ... cannot be used` | The voice name does not exist in the loaded repo |
| Avatar answers only with the fallback sentence | The Groq LLM call failed — check the backend log and the network |
| `camera_connected: false` in `/health` | Check `CAMERA_INDEX` / `CAMERA_URL`; the camera is retried every `CAMERA_RETRY_SEC` |
| No avatar on the page | The VRM file is missing |
| No sound | The browser needs microphone permission and one user gesture before audio plays |
