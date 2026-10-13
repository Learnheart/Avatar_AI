"""
FastAPI application entry point. Run with `python -m backend.main` (HOST, PORT and
LOG_LEVEL come from the config).

Endpoints:
  GET  /health     — health check with the system status
  WS   /ws         — the single WebSocket endpoint for all realtime communication
  /knowledge/...   — upload, list and delete the documents of the knowledge base
                     (`backend/features/knowledge/api.py`)
  /memory/...      — what the avatar remembers about the logged-in user
                     (`backend/features/memory/api.py`)
  /persona         — the personality the logged-in user gives the avatar
                     (`backend/features/persona/api.py`)

Startup: validate config → telemetry (+ capture) → open the application database (its
         missing tables are created) → open the knowledge base → build modules
         → start orchestrator → in the background, read the conversations an earlier
         run left unread into the memory
Shutdown: stop the orchestrator, which releases every module; close the knowledge base
          and the application database
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
from backend.core.contracts import LLM, Knowledge, Memory, Persona
from backend.core.conversation.engine import ConversationEngine
from backend.features.accounts.identity import current_user_id
from backend.features.accounts.store import UsersStore
from backend.features.knowledge.api import create_knowledge_router
from backend.features.knowledge.knowledge_base import KnowledgeBase
from backend.features.knowledge.store import SqliteVecStore
from backend.features.memory.api import create_memory_router
from backend.features.memory.consolidation import MemoryConsolidation
from backend.features.memory.imports import MemoryImports
from backend.features.memory.memory import MemoryService
from backend.features.memory.store import MemoryStore
from backend.features.persona.api import create_persona_router
from backend.features.persona.persona import PersonaService
from backend.features.persona.store import PersonaStore
from backend.providers.catalog import create_embedder, create_llm, create_stt, create_tts
from backend.core.orchestrator import Orchestrator
from backend.core.perception.camera import Camera
from backend.core.perception.tracking import HeadTracker
from backend.core.perception.vad import VADModule
from backend.utils import telemetry
from backend.utils.capture import CaptureSpans, CaptureStore
from backend.utils.database import Database, DatabaseError
from backend.utils.logging import configure_logging, get_logger
from backend.utils.object_store import ObjectStore

logger = get_logger("main")


def build_knowledge(config: AppConfig) -> KnowledgeBase:
    """The knowledge base on its configured embedding and store. Nothing is opened yet."""
    return KnowledgeBase(config, create_embedder(config), SqliteVecStore(config.KNOWLEDGE_DB_PATH))


def build_memory(config: AppConfig, database: Database, llm: LLM) -> MemoryService:
    """The memory on the application database; the conversations of a logged-in user
    are read into proposed items with the LLM it is given."""
    store = MemoryStore(database)
    memory = MemoryService(config, store)
    memory.gather_with(
        MemoryConsolidation(config, store, llm, forget_profile=memory.forget_profile)
    )
    return memory


def build_imports(
    config: AppConfig, database: Database, llm: LLM, memory: MemoryService
) -> MemoryImports:
    """The imports on the application database and the object store, read with the LLM
    they are given; an import changes the profile the memory has cached."""
    return MemoryImports(
        config,
        MemoryStore(database),
        ObjectStore(config.OBJECT_STORE_DIR),
        llm,
        forget_profile=memory.forget_profile,
    )


def build_persona(config: AppConfig, database: Database) -> PersonaService:
    """The persona on the application database."""
    return PersonaService(config, PersonaStore(database))


def build_orchestrator(
    config: AppConfig,
    knowledge: Knowledge,
    memory: Memory,
    llm: LLM,
    capture: Optional[CaptureStore] = None,
    persona: Optional[Persona] = None,
) -> Orchestrator:
    """Wire the real modules together, on the LLM the app built."""
    engine = ConversationEngine(
        config,
        stt=create_stt(config),
        llm=llm,
        tts=create_tts(config),
        knowledge=knowledge,
        memory=memory,
        persona=persona,
    )
    return Orchestrator(
        config,
        vad=VADModule(config),
        engine=engine,
        camera=Camera(config),
        tracker=HeadTracker(config),
        memory=memory,
        capture=capture,
    )


def create_app(
    config: AppConfig,
    orchestrator: Optional[Orchestrator] = None,
    knowledge: Optional[KnowledgeBase] = None,
    memory: Optional[MemoryService] = None,
) -> FastAPI:
    """Build the app. Without an orchestrator, a knowledge base or a memory, the real
    ones are built at startup."""

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

        database = Database(config.DATABASE_URL)
        try:
            await asyncio.to_thread(database.start)
        except DatabaseError as e:
            logger.error({"msg": "The application database cannot be opened", "error": str(e)})
            raise
        app.state.users = UsersStore(database)
        # One LLM for the turns, the imports and the reading of closed conversations:
        # with a backup backend, a primary the turns found dead is not asked again in
        # the background.
        llm = create_llm(config)
        app.state.memory = memory or build_memory(config, database, llm)
        app.state.imports = build_imports(config, database, llm, app.state.memory)
        app.state.persona = build_persona(config, database)
        try:
            app.state.knowledge = knowledge or build_knowledge(config)
            await app.state.knowledge.start()
            try:
                app.state.orchestrator = orchestrator or build_orchestrator(
                    config, app.state.knowledge, app.state.memory, llm, capture,
                    persona=app.state.persona,
                )
                app.state.started_at = time.monotonic()
                await app.state.orchestrator.start()
                # In the background, so that the start does not wait for the LLM: what
                # an earlier run left unread of the conversations.
                gathering = asyncio.create_task(
                    app.state.memory.consolidate_unfinished(), name="memory-startup"
                )
                logger.info({"msg": "System ready"})

                try:
                    yield
                finally:
                    gathering.cancel()
                    await asyncio.gather(gathering, return_exceptions=True)

                logger.info({"msg": "Avatar AI shutting down"})
                await app.state.orchestrator.stop()
            finally:
                await app.state.knowledge.close()
        finally:
            await asyncio.to_thread(database.close)
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
    app.include_router(create_memory_router(config))
    app.include_router(create_persona_router(config))

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

    async def connecting_user_id() -> Optional[str]:
        """Who is behind the new connection; None for a guest. A failing database does
        not refuse the connection: the person talks as a guest, without the memory."""
        try:
            return await asyncio.to_thread(current_user_id, config, app.state.users)
        except DatabaseError as e:
            logger.error({"msg": "User lookup failed, connecting as a guest", "error": str(e)})
            return None

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        orch: Orchestrator = app.state.orchestrator
        session_id = await orch.handle_connect(websocket, await connecting_user_id())
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
