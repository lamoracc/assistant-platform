from typing import Any
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.chunking import chunk_by_headings_and_paragraphs
from app.services.document_extractors import extract_document
from app.services.file_router import content_type_for_path, route_file
from app.services.qdrant_store import (
    get_qdrant_collection_status,
    smoke_test_qdrant_upsert,
)
from app.services.retrieval import retrieve_chunks

router = APIRouter(prefix="/debug", tags=["debug"])


class DebugFileRequest(BaseModel):
    path: str = Field(..., min_length=1)


class DebugSearchRequest(BaseModel):
    question: str = Field(..., min_length=1)
    filters: dict[str, Any] | None = None


@router.get("/qdrant")
def qdrant_status() -> dict[str, Any]:
    return get_qdrant_collection_status()


@router.post("/qdrant/smoke-test")
def qdrant_smoke_test() -> dict[str, Any]:
    return smoke_test_qdrant_upsert()


@router.post("/import-folder")
def debug_import_folder(request: DebugFileRequest) -> dict[str, Any]:
    root = Path(request.path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {request.path}")

    route_counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    total_files = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        total_files += 1
        routed = route_file(path)
        route_counts[routed.route] = route_counts.get(routed.route, 0) + 1
        samples.setdefault(routed.route, [])
        if len(samples[routed.route]) < 5:
            samples[routed.route].append(path.relative_to(root).as_posix())

    return {
        "root_path": str(root),
        "exists": True,
        "is_dir": True,
        "total_files": total_files,
        "route_counts": route_counts,
        "samples": samples,
    }


@router.post("/extract-file")
def debug_extract_file(request: DebugFileRequest) -> dict[str, Any]:
    path = _validated_path(request.path)
    routed = route_file(path)
    if routed.route in {"image", "ignored", "unsupported"}:
        return {
            "path": str(path),
            "route": routed.route,
            "content_type": routed.content_type,
            "metadata": {},
            "text_preview": "",
            "block_count": 0,
        }

    extracted = extract_document(
        content=path.read_bytes(),
        filename=path.name,
        content_type=routed.content_type,
    )
    return {
        "path": str(path),
        "route": routed.route,
        "content_type": routed.content_type,
        "metadata": extracted.metadata,
        "text_preview": extracted.text[:2000],
        "block_count": len(extracted.blocks),
    }


@router.post("/chunks")
def debug_chunks(request: DebugFileRequest) -> dict[str, Any]:
    path = _validated_path(request.path)
    extracted = extract_document(
        content=path.read_bytes(),
        filename=path.name,
        content_type=content_type_for_path(path),
    )
    chunks = chunk_by_headings_and_paragraphs(extracted.blocks)
    return {
        "path": str(path),
        "chunk_count": len(chunks),
        "chunks": [
            {
                "heading": chunk.heading,
                "chunk_type": chunk.metadata.get("chunk_type"),
                "language": chunk.metadata.get("language"),
                "char_count": len(chunk.content),
                "preview": chunk.content[:500],
            }
            for chunk in chunks
        ],
    }


@router.post("/search")
def debug_search(
    request: DebugSearchRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = retrieve_chunks(request.question, db, filters=request.filters)
    return {
        "sources": [
            {
                "document": chunk.document,
                "source_file": chunk.source_file,
                "heading": chunk.heading,
                "chunk_index": chunk.chunk_index,
                "score": chunk.score,
                "retrieval_type": chunk.retrieval_type,
                "chunk_type": chunk.metadata.get("chunk_type"),
                "language": chunk.metadata.get("language"),
                "ranking_reason": chunk.ranking_reason or [],
            }
            for chunk in result.chunks
        ],
        "diagnostics": result.diagnostics,
    }


def _validated_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {raw_path}")
    return path
