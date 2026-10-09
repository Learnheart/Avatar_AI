"""WSManager — owns the single active WebSocket connection to the frontend."""
import asyncio
import uuid
from typing import Optional

from fastapi import WebSocket

from backend.config import AppConfig
from backend.models import MIC_SAMPLE_RATE
from backend.utils.logging import get_logger

logger = get_logger("ws_manager")

_AUDIO_FORMAT = f"pcm_{MIC_SAMPLE_RATE // 1000}khz_mono"


class WSManager:
    """Manages the single active WebSocket connection."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._ws: Optional[WebSocket] = None
        # Tracking, turn and audio tasks all send: one frame at a time.
        self._send_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> str:
        """Accept the socket, replace any previous one, and send `connected`."""
        await websocket.accept()
        await self._replace(websocket)

        session_id = str(uuid.uuid4())
        await self.send({
            "type": "connected",
            "session_id": session_id,
            "config": {
                "tracking_fps": self.config.TRACKING_FPS,
                "audio_format": _AUDIO_FORMAT,
            },
        })
        return session_id

    async def detach(self) -> None:
        """Forget and close the active socket, if any."""
        await self._replace(None)

    def disconnect(self, websocket: WebSocket) -> None:
        """Forget the socket, unless it was already replaced by a newer one."""
        if self._ws is websocket:
            self._ws = None

    async def send(self, message: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async with self._send_lock:
                await ws.send_json(message)
        except Exception as e:
            logger.warning({"msg": "WS send failed", "error": str(e)})
            self.disconnect(ws)
            # Closing ends the endpoint's receive loop, which runs the disconnect handling.
            await self._close(ws)

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    async def _replace(self, websocket: Optional[WebSocket]) -> None:
        previous, self._ws = self._ws, websocket
        if previous is not None:
            await self._close(previous)

    async def _close(self, websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except Exception as e:
            logger.debug({"msg": "Closing WS failed", "error": str(e)})
