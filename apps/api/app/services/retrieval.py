from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.document import Document, DocumentChunk
from app.services.embeddings import embed_texts
from app.services.qdrant_store import get_qdrant_client
from app.services.reranker import get_reranker
from app.services.retrieval_ranking import RetrievedChunk, query_terms, rank_candidates
from app.services.text_sanitizer import sanitize_text

VECTOR_CANDIDATE_LIMIT = 40
KEYWORD_CANDIDATE_LIMIT = 40


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    diagnostics: dict[str, Any]


def retrieve_chunks(
    question: str,
    db: Session,
    top_k: int | None = None,
    min_score: float | None = None,
    filters: dict[str, Any] | None = None,
) -> RetrievalResult:
    query = sanitize_text(question)
    final_limit = top_k or settings.retrieval_top_k
    threshold = min_score if min_score is not None else settings.retrieval_min_score

    vector_hits, vector_diagnostics = _vector_search(
        query,
        VECTOR_CANDIDATE_LIMIT,
        threshold,
    )
    keyword_hits = _keyword_search(query, db, KEYWORD_CANDIDATE_LIMIT)
    merged = _merge_hits(vector_hits, keyword_hits)
    filtered = [chunk for chunk in merged if _matches_filters(chunk, filters)]
    ranked = rank_candidates(query, filtered)
    reranked = get_reranker().rerank(query, ranked)
    chunks = reranked[:final_limit]

    diagnostics = {
        "collection_name": settings.qdrant_collection_name,
        "top_k": final_limit,
        "vector_candidates": VECTOR_CANDIDATE_LIMIT,
        "keyword_candidates": KEYWORD_CANDIDATE_LIMIT,
        "min_score": threshold,
        "filters": filters or {},
        "results": vector_diagnostics + [_diagnostic_for_hit(hit) for hit in keyword_hits],
        "final_results": [_diagnostic_for_hit(hit) for hit in chunks],
    }
    return RetrievalResult(chunks=chunks, diagnostics=diagnostics)


def _matches_filters(chunk: RetrievedChunk, filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = chunk.metadata.get(key)
        if key == "source_file":
            actual = chunk.source_file
        if key == "document":
            actual = chunk.document
        if actual != expected:
            return False
    return True


def _vector_search(
    question: str,
    candidate_limit: int,
    min_score: float,
) -> tuple[list[RetrievedChunk], list[dict[str, Any]]]:
    query_vector = embed_texts([question])[0]
    client = get_qdrant_client()
    results = client.search(
        collection_name=settings.qdrant_collection_name,
        query_vector=query_vector,
        limit=candidate_limit,
        with_payload=True,
    )

    chunks: list[RetrievedChunk] = []
    diagnostics: list[dict[str, Any]] = []
    for result in results:
        payload = result.payload or {}
        raw_score = float(result.score)
        filename = str(payload.get("filename", "unknown"))
        source_file = str(payload.get("source", filename))
        heading = payload.get("heading")
        chunk_index = int(payload.get("chunk_index", 0))
        document_id = str(payload.get("document_id", source_file))
        filtered_reason = None
        if raw_score < min_score:
            filtered_reason = "below_min_score"
        else:
            chunks.append(
                RetrievedChunk(
                    text=str(payload.get("text", "")),
                    document=filename,
                    source_file=source_file,
                    heading=heading,
                    chunk_index=chunk_index,
                    score=raw_score,
                    raw_score=raw_score,
                    metadata=payload,
                    retrieval_type="vector",
                    document_id=document_id,
                )
            )

        diagnostics.append(
            {
                "document": filename,
                "source_file": source_file,
                "heading": heading,
                "chunk_index": chunk_index,
                "document_id": document_id,
                "raw_score": raw_score,
                "score": raw_score if filtered_reason is None else None,
                "retrieval_type": "vector",
                "filtered_reason": filtered_reason,
                "ranking_reason": [],
            }
        )

    return chunks, diagnostics


def _keyword_search(
    question: str,
    db: Session,
    candidate_limit: int,
) -> list[RetrievedChunk]:
    terms = query_terms(question)
    if not terms:
        return []

    conditions = []
    for term in terms:
        pattern = f"%{term}%"
        conditions.extend(
            [
                DocumentChunk.content.ilike(pattern),
                DocumentChunk.heading.ilike(pattern),
                Document.filename.ilike(pattern),
                Document.source_path.ilike(pattern),
                cast(Document.doc_metadata["title"], String).ilike(pattern),
            ]
        )

    rows = (
        db.execute(
            select(DocumentChunk)
            .join(DocumentChunk.document)
            .options(joinedload(DocumentChunk.document))
            .where(or_(*conditions))
            .limit(candidate_limit)
        )
        .scalars()
        .all()
    )

    hits: list[RetrievedChunk] = []
    for chunk in rows:
        document = chunk.document
        metadata = {
            **(chunk.chunk_metadata or {}),
            "document_id": str(document.id),
            "filename": document.filename,
            "source": document.source_path or document.filename,
            "title": (document.doc_metadata or {}).get("title"),
            "heading": chunk.heading,
            "chunk_index": chunk.chunk_index,
            "text": chunk.content,
        }
        hits.append(
            RetrievedChunk(
                text=chunk.content,
                document=document.filename,
                source_file=document.source_path or document.filename,
                heading=chunk.heading,
                chunk_index=chunk.chunk_index,
                score=0.45,
                raw_score=0.45,
                metadata=metadata,
                retrieval_type="keyword",
                document_id=str(document.id),
            )
        )

    return hits


def _merge_hits(
    vector_hits: list[RetrievedChunk],
    keyword_hits: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    merged: dict[tuple[str, int], RetrievedChunk] = {}
    for hit in vector_hits + keyword_hits:
        key = (hit.document_id or hit.source_file, hit.chunk_index)
        existing = merged.get(key)
        if not existing:
            merged[key] = hit
            continue
        retrieval_type = "hybrid" if existing.retrieval_type != hit.retrieval_type else existing.retrieval_type
        score = max(existing.score, hit.score)
        raw_score = max(existing.raw_score or 0.0, hit.raw_score or 0.0)
        metadata = {**existing.metadata, **hit.metadata}
        text = existing.text if len(existing.text) >= len(hit.text) else hit.text
        merged[key] = replace(
            existing,
            text=text,
            score=score,
            raw_score=raw_score,
            metadata=metadata,
            retrieval_type=retrieval_type,
        )
    return list(merged.values())


def _diagnostic_for_hit(hit: RetrievedChunk) -> dict[str, Any]:
    return {
        "document": hit.document,
        "source_file": hit.source_file,
        "heading": hit.heading,
        "chunk_index": hit.chunk_index,
        "document_id": hit.document_id,
        "raw_score": hit.raw_score,
        "score": hit.score,
        "retrieval_type": hit.retrieval_type,
        "filtered_reason": None,
        "ranking_reason": hit.ranking_reason or [],
    }

