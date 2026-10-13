"""
HTTP routes of the persona, used by the "Tính cách của avatar" block of the profile page.

  GET    /persona   → {"fields": {...}}, the fields that are set
  PUT    /persona   {"fields": {name: text}} → {"fields": {...}} as stored; it replaces
                    the whole persona, and an empty text leaves its field out
  DELETE /persona   → 204

Every route is scoped to `current_user_id()`: a guest gets 401. The persona and the users
are read from `app.state`, set at startup by `backend/main.py`.
"""
import asyncio
from contextlib import contextmanager
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from backend.config import AppConfig
from backend.core.contracts import PersonaError
from backend.features.accounts.identity import current_user_id
from backend.features.persona.persona import InvalidPersona, PersonaService
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger

logger = get_logger("persona.api")


class NewPersona(BaseModel):
    """Body of PUT: the whole persona. Any other field of the body is refused."""
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, str]


@contextmanager
def _answering() -> Iterator[None]:
    """Turn the failures of the persona into their HTTP answers."""
    try:
        yield
    except InvalidPersona as e:
        raise HTTPException(422, str(e)) from e
    except PersonaError as e:
        logger.error({"msg": "Persona request failed", "error": str(e)})
        raise HTTPException(502, "The persona is not available") from e


def create_persona_router(config: AppConfig) -> APIRouter:
    router = APIRouter(prefix="/persona", tags=["persona"])

    def persona(request: Request) -> PersonaService:
        return request.app.state.persona

    async def user(request: Request) -> str:
        """The id of the logged-in user; a guest has no persona to set."""
        try:
            user_id = await asyncio.to_thread(current_user_id, config, request.app.state.users)
        except DatabaseError as e:
            logger.error({"msg": "User lookup failed", "error": str(e)})
            raise HTTPException(502, "The persona is not available") from e
        if user_id is None:
            raise HTTPException(401, "Login required")
        return user_id

    @router.get("")
    async def get_persona(request: Request, user_id: str = Depends(user)) -> dict:
        with _answering():
            return {"fields": await persona(request).fields(user_id)}

    @router.put("")
    async def save_persona(
        request: Request, new: NewPersona, user_id: str = Depends(user)
    ) -> dict:
        with _answering():
            return {"fields": await persona(request).save(user_id, new.fields)}

    @router.delete("", status_code=204)
    async def delete_persona(request: Request, user_id: str = Depends(user)) -> Response:
        with _answering():
            await persona(request).clear(user_id)
        return Response(status_code=204)

    return router
