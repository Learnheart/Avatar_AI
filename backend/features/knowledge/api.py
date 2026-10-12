"""
HTTP routes of the knowledge base, used by the "Tài liệu" panel of the frontend.

  GET    /knowledge/documents        → {"documents": [...]}
  POST   /knowledge/documents        multipart field `file` → 201 + the document
  DELETE /knowledge/documents/{id}   → 204, or 404

The knowledge base is read from `app.state.knowledge`, set at startup by `backend/main.py`.
"""
from fastapi import APIRouter, HTTPException, Request, Response, UploadFile

from backend.config import AppConfig
from backend.core.contracts import KnowledgeError
from backend.features.knowledge.knowledge_base import EmptyDocument, KnowledgeBase
from backend.features.knowledge.loaders import UnreadableDocument, UnsupportedDocument
from backend.utils.logging import get_logger

logger = get_logger("knowledge.api")

_MB = 1024 * 1024


def create_knowledge_router(config: AppConfig) -> APIRouter:
    router = APIRouter(prefix="/knowledge", tags=["knowledge"])

    def knowledge(request: Request) -> KnowledgeBase:
        return request.app.state.knowledge

    @router.get("/documents")
    async def list_documents(request: Request) -> dict:
        documents = await knowledge(request).list_documents()
        return {"documents": [document.to_dict() for document in documents]}

    @router.post("/documents", status_code=201)
    async def upload_document(request: Request, file: UploadFile) -> dict:
        limit = int(config.KNOWLEDGE_MAX_FILE_MB * _MB)
        # One byte past the limit is enough to know the file is too large.
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File larger than {config.KNOWLEDGE_MAX_FILE_MB:g} MB")
        try:
            document = await knowledge(request).add_document(file.filename or "", data)
        except UnsupportedDocument as e:
            raise HTTPException(415, str(e)) from e
        except (UnreadableDocument, EmptyDocument) as e:
            raise HTTPException(422, str(e)) from e
        except KnowledgeError as e:
            logger.error({"msg": "Document upload failed", "error": str(e)})
            raise HTTPException(502, "The document could not be indexed") from e
        return document.to_dict()

    @router.delete("/documents/{document_id}", status_code=204)
    async def delete_document(request: Request, document_id: str) -> Response:
        if not await knowledge(request).delete_document(document_id):
            raise HTTPException(404, "No such document")
        return Response(status_code=204)

    return router
