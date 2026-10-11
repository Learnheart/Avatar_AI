"""
WSManager — owns the single active WebSocket connection to the frontend.

Every message the backend sends is built by one of the `*_message` functions below: they
are the server → client half of the wire contract with `frontend/src/`.
"""
import asyncio
import base64
import time
import uuid
from typing import Optional

from fastapi import WebSocket

from backend.config import AppConfig
from backend.models import MIC_SAMPLE_RATE, AvatarState, HeadRotation
from backend.utils.logging import get_logger

logger = get_logger("ws_manager")

_AUDIO_FORMAT = f"pcm_{MIC_SAMPLE_RATE // 1000}khz_mono"


# ── Server → client messages ──────────────────────────────────────────────

def connected_message(session_id: str, tracking_fps: int) -> dict:
    return {
        "type": "connected",
        "session_id": session_id,
        "config": {"tracking_fps": tracking_fps, "audio_format": _AUDIO_FORMAT},
    }


def state_message(state: AvatarState) -> dict:
    return {"type": "state", "state": state.value}


def speech_message(text: str, pcm: bytes, sample_rate: int, *, is_final: bool) -> dict:
    return {
        "type": "speech_output",
        "text_content": text,
        "audio_data": base64.b64encode(pcm).decode("ascii"),
        "audio_mime_type": f"audio/pcm;rate={sample_rate}",
        "is_final": is_final,
    }


def interrupt_message() -> dict:
    """Stop the audio that is playing and drop what is queued."""
    return {"type": "interrupt"}


def error_message(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}


def tracking_message(present: bool, head_rotation: HeadRotation, is_speaking: bool) -> dict:
    return {
        "type": "tracking_update",
        "timestamp": time.time(),
        "avatar_control": {
            "present": present,
            "head_rotation": head_rotation.to_dict(),
            "is_speaking": is_speaking,
        },
    }


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
        await self.send(connected_message(session_id, self.config.TRACKING_FPS))
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
