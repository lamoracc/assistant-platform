from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.file_router import route_file
from app.services.ingestion import IngestionError, ingest_document, ingest_help_folder

router = APIRouter(prefix="/documents", tags=["documents"])

SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
    "application/xhtml+xml",
    "text/markdown",
    "text/plain",
}
SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".html", ".htm", ".md", ".txt"}


class FolderImportRequest(BaseModel):
    path: str = Field(..., min_length=1)


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    filename = file.filename or "uploaded-document"
    content_type = (file.content_type or "application/octet-stream").split(";")[0]
    routed = route_file(path=Path(filename), content_type=content_type)
    has_supported_extension = routed.extension in SUPPORTED_EXTENSIONS
    has_supported_content_type = routed.content_type in SUPPORTED_CONTENT_TYPES

    if not has_supported_content_type and not has_supported_extension:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF, DOC, DOCX, HTML, HTM, MD, and TXT uploads are supported.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds {settings.upload_max_bytes} bytes.",
        )

    try:
        document = ingest_document(
            db=db,
            filename=filename,
            content_type=content_type,
            content=content,
        )
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "document_id": str(document.id),
        "filename": document.filename,
        "content_type": document.content_type,
        "chunk_count": document.chunk_count,
        "status": document.status,
    }


@router.post("/import-folder", status_code=status.HTTP_202_ACCEPTED)
def import_help_folder(
    request: FolderImportRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        return ingest_help_folder(db=db, root_path=request.path)
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
