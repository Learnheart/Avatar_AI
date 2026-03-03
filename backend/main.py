"""
FastAPI Application Entry Point.

Endpoints:
  GET  /health     — Health check với system status
  WS   /ws         — Single WebSocket endpoint cho toàn bộ realtime communication

Startup: init modules → start orchestrator
Shutdown: cleanup all modules
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.config import AppConfig
from backend.orchestrator import Orchestrator
from backend.utils.logging import get_logger

logger = get_logger("main")

config = AppConfig()
orchestrator = Orchestrator(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info({"msg": "Avatar AI starting up", "host": config.HOST, "port": config.PORT})
    try:
        config.validate()
    except ValueError as e:
        logger.error({"msg": "Config validation failed", "error": str(e)})
        raise

    await orchestrator.start()
    logger.info({"msg": "System ready"})

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info({"msg": "Avatar AI shutting down"})
    await orchestrator.stop()


app = FastAPI(title="Magic Mirror AI Avatar", version="0.1.0", lifespan=lifespan)

_start_time = time.time()


@app.get("/health")
async def health():
    state = orchestrator.session_state
    uptime = round(time.time() - _start_time, 1)

    # Try reading VRAM usage
    vram_gb = _get_vram_gb()

    return JSONResponse({
        "status": "ok",
        "uptime_seconds": uptime,
        "vram_used_gb": vram_gb,
        "avatar_state": state.avatar_state.value,
        "camera_connected": orchestrator.camera_connected,
        "gemini_session_active": state.gemini_session_active,
    })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session_id = await orchestrator.ws_manager.connect(websocket)
    logger.info({"msg": "WebSocket connected", "session_id": session_id})

    try:
        while True:
            data = await websocket.receive_json()
            await orchestrator.handle_client_message(data)
    except WebSocketDisconnect:
        logger.info({"msg": "WebSocket disconnected", "session_id": session_id})
    except Exception as e:
        logger.error({"msg": "WebSocket error", "error": str(e)})
    finally:
        await orchestrator.ws_manager.disconnect()


def _get_vram_gb() -> float:
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            mb = float(result.stdout.strip())
            return round(mb / 1024, 2)
    except Exception:
        pass
    return 0.0
