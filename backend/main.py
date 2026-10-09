"""
FastAPI application entry point. Run with `python -m backend.main` (HOST, PORT and
LOG_LEVEL come from the config).

Endpoints:
  GET  /health     — health check with the system status
  WS   /ws         — the single WebSocket endpoint for all realtime communication

Startup: validate config → build modules → start orchestrator
Shutdown: stop the orchestrator, which releases every module
"""
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.config import AppConfig
from backend.conversation.engine import ConversationEngine
from backend.conversation.llm.groq_llm import GroqLLM
from backend.conversation.stt.router import create_stt
from backend.conversation.tts import create_tts
from backend.orchestrator import Orchestrator
from backend.perception.camera import Camera
from backend.perception.tracking import HeadTracker
from backend.perception.vad import VADModule
from backend.utils.logging import configure_logging, get_logger

logger = get_logger("main")


def build_orchestrator(config: AppConfig) -> Orchestrator:
    """Wire the real modules together."""
    engine = ConversationEngine(
        config, stt=create_stt(config), llm=GroqLLM(config), tts=create_tts(config)
    )
    return Orchestrator(
        config,
        vad=VADModule(config),
        engine=engine,
        camera=Camera(config),
        tracker=HeadTracker(config),
    )


def create_app(config: AppConfig, orchestrator: Optional[Orchestrator] = None) -> FastAPI:
    """Build the app. Without an orchestrator, the real one is built at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            config.validate()
        except ValueError as e:
            logger.error({"msg": "Config validation failed", "error": str(e)})
            raise
        configure_logging(config.LOG_LEVEL)
        logger.info({"msg": "Avatar AI starting up"})

        app.state.orchestrator = orchestrator or build_orchestrator(config)
        app.state.started_at = time.monotonic()
        await app.state.orchestrator.start()
        logger.info({"msg": "System ready"})

        yield

        logger.info({"msg": "Avatar AI shutting down"})
        await app.state.orchestrator.stop()

    app = FastAPI(title="Magic Mirror AI Avatar", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        orch: Orchestrator = app.state.orchestrator
        return JSONResponse({
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - app.state.started_at, 1),
            "avatar_state": orch.state.value,
            "camera_connected": orch.camera_connected,
            "ws_connected": orch.ws_manager.is_connected,
        })

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        orch: Orchestrator = app.state.orchestrator
        session_id = await orch.handle_connect(websocket)
        logger.info({"msg": "WebSocket connected", "session_id": session_id})

        try:
            while True:
                await orch.handle_client_message(await websocket.receive_text())
        except WebSocketDisconnect:
            logger.info({"msg": "WebSocket disconnected", "session_id": session_id})
        except Exception as e:
            logger.error({"msg": "WebSocket error", "error": str(e)}, exc_info=True)
        finally:
            await orch.handle_disconnect(websocket)

    return app


config = AppConfig()
app = create_app(config)

if __name__ == "__main__":
    config.validate()  # fail on a bad LOG_LEVEL here, with a clear message
    uvicorn.run(
        app, host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL.lower()
    )
