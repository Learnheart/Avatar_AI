"""
FastAPI application entry point. Run with `python -m backend.main` (HOST, PORT and
LOG_LEVEL come from the config).

Endpoints:
  GET  /health     — health check with the system status
  WS   /ws         — the single WebSocket endpoint for all realtime communication
  /knowledge/...   — upload, list and delete the documents of the knowledge base
                     (`backend/features/knowledge/api.py`)

Startup: validate config → telemetry (+ capture) → open the knowledge base → build modules
         → start orchestrator
Shutdown: stop the orchestrator, which releases every module; close the knowledge base
"""
import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import AppConfig
from backend.core.contracts import Knowledge
from backend.core.conversation.engine import ConversationEngine
from backend.features.knowledge.api import create_knowledge_router
from backend.features.knowledge.knowledge_base import KnowledgeBase
from backend.features.knowledge.store import SqliteVecStore
from backend.providers.catalog import create_embedder, create_llm, create_stt, create_tts
from backend.core.orchestrator import Orchestrator
from backend.core.perception.camera import Camera
from backend.core.perception.tracking import HeadTracker
from backend.core.perception.vad import VADModule
from backend.utils import telemetry
from backend.utils.capture import CaptureSpans, CaptureStore
from backend.utils.logging import configure_logging, get_logger

logger = get_logger("main")


def build_knowledge(config: AppConfig) -> KnowledgeBase:
    """The knowledge base on its configured embedding and store. Nothing is opened yet."""
    return KnowledgeBase(config, create_embedder(config), SqliteVecStore(config.KNOWLEDGE_DB_PATH))


def build_orchestrator(
    config: AppConfig, knowledge: Knowledge, capture: Optional[CaptureStore] = None
) -> Orchestrator:
    """Wire the real modules together."""
    engine = ConversationEngine(
        config,
        stt=create_stt(config),
        llm=create_llm(config),
        tts=create_tts(config),
        knowledge=knowledge,
    )
    return Orchestrator(
        config,
        vad=VADModule(config),
        engine=engine,
        camera=Camera(config),
        tracker=HeadTracker(config),
        capture=capture,
    )


def create_app(
    config: AppConfig,
    orchestrator: Optional[Orchestrator] = None,
    knowledge: Optional[KnowledgeBase] = None,
) -> FastAPI:
    """Build the app. Without an orchestrator or a knowledge base, the real ones are
    built at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            config.validate()
        except ValueError as e:
            logger.error({"msg": "Config validation failed", "error": str(e)})
            raise
        configure_logging(config.LOG_LEVEL)
        telemetry.setup(config)
        logger.info({"msg": "Avatar AI starting up"})

        capture: Optional[CaptureStore] = None
        if config.CAPTURE_ENABLED:
            capture = CaptureStore(config)
            capture.start()
            telemetry.add_span_processor(CaptureSpans(capture))

        app.state.knowledge = knowledge or build_knowledge(config)
        await app.state.knowledge.start()
        try:
            app.state.orchestrator = orchestrator or build_orchestrator(
                config, app.state.knowledge, capture
            )
            app.state.started_at = time.monotonic()
            await app.state.orchestrator.start()
            logger.info({"msg": "System ready"})

            yield

            logger.info({"msg": "Avatar AI shutting down"})
            await app.state.orchestrator.stop()
        finally:
            await app.state.knowledge.close()
            telemetry.flush()
            if capture:
                await asyncio.to_thread(capture.stop)

    app = FastAPI(title="Magic Mirror AI Avatar", version="0.1.0", lifespan=lifespan)
    # The page is served from another origin (the Vite server) than this API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in config.CORS_ALLOW_ORIGINS.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_knowledge_router(config))

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
