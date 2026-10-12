"""
HTTP routes of the memory, used by the "Hồ sơ" page of the frontend.

  GET    /memory/items        → {"items": [...]}
  POST   /memory/items        {"category", "text"} → 201 + the item
  PATCH  /memory/items/{id}   {"text"?, "category"?} → the item, or 404
  DELETE /memory/items/{id}   → 204, or 404

Every route is scoped to `current_user_id()`: a guest gets 401, and an item of another
user is a 404. The memory and the users are read from `app.state`, set at startup by
`backend/main.py`.
"""
import asyncio
from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from backend.config import AppConfig
from backend.core.contracts import MemoryServiceError
from backend.features.accounts.identity import current_user_id
from backend.features.memory.memory import InvalidMemoryItem, MemoryService
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger

logger = get_logger("memory.api")


class NewItem(BaseModel):
    """Body of POST: what the user typed about themselves. Any other field is refused."""
    model_config = ConfigDict(extra="forbid")

    category: str
    text: str


class ItemChanges(BaseModel):
    """Body of PATCH: the fields to change. Any other field is refused."""
    model_config = ConfigDict(extra="forbid")

    text: Optional[str] = None
    category: Optional[str] = None


@contextmanager
def _answering() -> Iterator[None]:
    """Turn the failures of the memory into their HTTP answers."""
    try:
        yield
    except InvalidMemoryItem as e:
        raise HTTPException(422, str(e)) from e
    except MemoryServiceError as e:
        logger.error({"msg": "Memory request failed", "error": str(e)})
        raise HTTPException(502, "The memory is not available") from e


def create_memory_router(config: AppConfig) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])

    def memory(request: Request) -> MemoryService:
        return request.app.state.memory

    async def user(request: Request) -> str:
        """The id of the logged-in user; a guest has no memory."""
        try:
            user_id = await asyncio.to_thread(current_user_id, config, request.app.state.users)
        except DatabaseError as e:
            logger.error({"msg": "User lookup failed", "error": str(e)})
            raise HTTPException(502, "The memory is not available") from e
        if user_id is None:
            raise HTTPException(401, "Login required")
        return user_id

    @router.get("/items")
    async def list_items(request: Request, user_id: str = Depends(user)) -> dict:
        with _answering():
            items = await memory(request).list_items(user_id)
        return {"items": [item.to_dict() for item in items]}

    @router.post("/items", status_code=201)
    async def add_item(request: Request, new: NewItem, user_id: str = Depends(user)) -> dict:
        with _answering():
            item = await memory(request).add_item(user_id, new.category, new.text)
        return item.to_dict()

    @router.patch("/items/{item_id}")
    async def change_item(
        request: Request, item_id: str, changes: ItemChanges, user_id: str = Depends(user)
    ) -> dict:
        if changes.text is None and changes.category is None:
            raise HTTPException(422, "Nothing to change: give a text or a category")
        with _answering():
            item = await memory(request).update_item(
                user_id, item_id, changes.text, changes.category
            )
        if item is None:
            raise HTTPException(404, "No such memory item")
        return item.to_dict()

    @router.delete("/items/{item_id}", status_code=204)
    async def delete_item(
        request: Request, item_id: str, user_id: str = Depends(user)
    ) -> Response:
        with _answering():
            deleted = await memory(request).delete_item(user_id, item_id)
        if not deleted:
            raise HTTPException(404, "No such memory item")
        return Response(status_code=204)

    return router
