"""
HTTP routes of the memory, used by the "Hồ sơ" page of the frontend.

  GET    /memory/items        → {"items": [...]}
  POST   /memory/items        {"category", "text"} → 201 + the item
  PATCH  /memory/items/{id}   {"text"?, "category"?, "status"?: "active"} → the item,
                              or 404; `status` approves a proposed (pending) item
  DELETE /memory/items/{id}   → 204, or 404; also rejects a proposed item

  GET    /memory/imports               → {"imports": [...]}
  POST   /memory/imports               multipart field `file`, or field `text`
                                       → 201 + {"import": {...}, "items": [...]}
  GET    /memory/imports/{id}/content  → the original bytes, as a download, or 404
  DELETE /memory/imports/{id}?items=keep|delete   → 204, or 404

Every route is scoped to `current_user_id()`: a guest gets 401, and an item or an import
of another user is a 404. The memory, the imports and the users are read from `app.state`, set at
startup by `backend/main.py`.
"""
import asyncio
from contextlib import contextmanager
from typing import Iterator, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from backend.config import AppConfig
from backend.core.contracts import MemoryServiceError
from backend.features.accounts.identity import bearer_token, current_user_id
from backend.features.memory.imports import (
    EmptyImport,
    ImportExtractionFailed,
    ImportTooLarge,
    MemoryImports,
    NothingToRemember,
    max_file_bytes,
)
from backend.features.memory.memory import InvalidMemoryItem, MemoryService
from backend.utils.database import DatabaseError
from backend.utils.loaders import UnreadableDocument, UnsupportedDocument
from backend.utils.logging import get_logger

logger = get_logger("memory.api")


class NewItem(BaseModel):
    """Body of POST: what the user typed about themselves. Any other field is refused."""
    model_config = ConfigDict(extra="forbid")

    category: str
    text: str


class ItemChanges(BaseModel):
    """Body of PATCH: the fields to change. Any other field is refused. `status` takes
    one value: it approves an item proposed after a conversation."""
    model_config = ConfigDict(extra="forbid")

    text: Optional[str] = None
    category: Optional[str] = None
    status: Optional[Literal["active"]] = None


@contextmanager
def _answering() -> Iterator[None]:
    """Turn the failures of the memory into their HTTP answers."""
    try:
        yield
    except ImportTooLarge as e:
        raise HTTPException(413, str(e)) from e
    except UnsupportedDocument as e:
        raise HTTPException(415, str(e)) from e
    except (InvalidMemoryItem, UnreadableDocument, EmptyImport, NothingToRemember) as e:
        raise HTTPException(422, str(e)) from e
    except ImportExtractionFailed as e:
        logger.error({"msg": "Memory import could not be read", "error": str(e)})
        raise HTTPException(502, "The import could not be read") from e
    except MemoryServiceError as e:
        logger.error({"msg": "Memory request failed", "error": str(e)})
        raise HTTPException(502, "The memory is not available") from e


def create_memory_router(config: AppConfig) -> APIRouter:
    router = APIRouter(prefix="/memory", tags=["memory"])

    def memory(request: Request) -> MemoryService:
        return request.app.state.memory

    def imports(request: Request) -> MemoryImports:
        return request.app.state.imports

    async def user(request: Request) -> str:
        """The id of the logged-in user; a guest has no memory."""
        try:
            user_id = await asyncio.to_thread(
                current_user_id, config, request.app.state.users,
                sessions=request.app.state.sessions,
                token=bearer_token(request.headers.get("authorization")),
            )
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
        if changes.text is None and changes.category is None and changes.status is None:
            raise HTTPException(422, "Nothing to change: give a text, a category or a status")
        service = memory(request)
        # `status` takes one value: it approves a proposed item.
        change = service.update_item if changes.status is None else service.approve_item
        with _answering():
            item = await change(user_id, item_id, changes.text, changes.category)
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

    @router.get("/imports")
    async def list_imports(request: Request, user_id: str = Depends(user)) -> dict:
        with _answering():
            listed = await imports(request).list_imports(user_id)
        return {"imports": [record.to_dict(item_count) for record, item_count in listed]}

    @router.post("/imports", status_code=201)
    async def add_import(
        request: Request,
        file: Optional[UploadFile] = File(None),
        text: Optional[str] = Form(None),
        user_id: str = Depends(user),
    ) -> dict:
        if (file is None) == (text is None):
            raise HTTPException(422, "Give a file or a text, one of the two")
        with _answering():
            if file is not None:
                # One byte past the limit is enough for the memory to refuse the file.
                data = await file.read(max_file_bytes(config) + 1)
                record, items = await imports(request).import_file(
                    user_id, file.filename or "", data
                )
            else:
                record, items = await imports(request).import_text(user_id, text)
        return {
            "import": record.to_dict(len(items)),
            "items": [item.to_dict() for item in items],
        }

    @router.get("/imports/{import_id}/content")
    async def import_content(
        request: Request, import_id: str, user_id: str = Depends(user)
    ) -> Response:
        with _answering():
            found = await imports(request).import_content(user_id, import_id)
        if found is None:
            raise HTTPException(404, "No such import")
        record, data = found
        return Response(
            content=data,
            media_type=record.content_type,
            headers={
                # Bytes a user supplied are saved by the browser, never shown by it from
                # the origin of the API, and never read as another type than the stored one.
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.name, safe='')}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.delete("/imports/{import_id}", status_code=204)
    async def delete_import(
        request: Request,
        import_id: str,
        items: Literal["keep", "delete"] = "keep",
        user_id: str = Depends(user),
    ) -> Response:
        with _answering():
            deleted = await imports(request).delete_import(user_id, import_id, items == "delete")
        if not deleted:
            raise HTTPException(404, "No such import")
        return Response(status_code=204)

    return router
