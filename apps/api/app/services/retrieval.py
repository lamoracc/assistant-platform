from dataclasses import dataclass
import re
import string
from time import perf_counter
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.document import Document, DocumentChunk
from app.services.embeddings import embed_texts
from app.services.qdrant_store import get_qdrant_client
from app.services.reranker import get_reranker
from app.services.retrieval_ranking import (
    RetrievedChunk,
    query_context_terms,
    query_phrases,
    query_terms,
    rank_candidates,
)
from app.services.text_sanitizer import sanitize_text

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
    total_start = perf_counter()
    timings: dict[str, float] = {
        "vector_ms": 0.0,
        "keyword_ms": 0.0,
        "ranking_ms": 0.0,
        "dedupe_ms": 0.0,
        "reranker_ms": 0.0,
        "answer_builder_ms": 0.0,
        "total_ms": 0.0,
    }
    query = sanitize_text(question)
    final_limit = top_k or settings.retrieval_final_k
    candidate_limit = max(settings.retrieval_candidate_k, final_limit)
    keyword_candidate_limit = max(settings.retrieval_keyword_candidate_k, final_limit)
    threshold = min_score if min_score is not None else settings.retrieval_min_score

    stage_start = perf_counter()
    vector_hits, vector_diagnostics = _vector_search(
        query,
        candidate_limit,
        threshold,
    )
    timings["vector_ms"] = _elapsed_ms(stage_start)

    stage_start = perf_counter()
    keyword_hits = _keyword_search(query, db, keyword_candidate_limit)
    timings["keyword_ms"] = _elapsed_ms(stage_start)

    stage_start = perf_counter()
    merged, duplicate_diagnostics = _merge_hits(vector_hits, keyword_hits)
    timings["merge_ms"] = _elapsed_ms(stage_start)

    filtered = [chunk for chunk in merged if _matches_filters(chunk, filters)]

    stage_start = perf_counter()
    ranked = rank_candidates(query, filtered)
    timings["ranking_ms"] = _elapsed_ms(stage_start)

    stage_start = perf_counter()
    reranker = get_reranker()
    reranked = reranker.rerank(query, ranked)
    if getattr(reranker, "enabled", False):
        timings["reranker_ms"] = _elapsed_ms(stage_start)

    stage_start = perf_counter()
    deduped, near_duplicate_diagnostics = _dedupe_near_duplicates(query, reranked)
    timings["dedupe_ms"] = _elapsed_ms(stage_start)
    chunks = deduped[:final_limit]
    timings["total_ms"] = _elapsed_ms(total_start)

    diagnostics = {
        "collection_name": settings.qdrant_collection_name,
        "retrieval_k": candidate_limit,
        "keyword_retrieval_k": keyword_candidate_limit,
        "final_k": final_limit,
        "top_k": final_limit,
        "vector_candidates": candidate_limit,
        "keyword_candidates": keyword_candidate_limit,
        "min_score": threshold,
        "filters": filters or {},
        "results": (
            vector_diagnostics
            + [_diagnostic_for_hit(hit) for hit in keyword_hits]
            + duplicate_diagnostics
            + near_duplicate_diagnostics
        ),
        "final_results": [_diagnostic_for_hit(hit) for hit in chunks],
        "timings": timings,
    }
    return RetrievalResult(chunks=chunks, diagnostics=diagnostics)


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 3)


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
                    retrieval_stage_score=raw_score,
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
                retrieval_stage_score=0.45,
                keyword_score=0.45,
            )
        )

    return hits


def _merge_hits(
    vector_hits: list[RetrievedChunk],
    keyword_hits: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], list[dict[str, Any]]]:
    merged: dict[str, RetrievedChunk] = {}
    duplicate_diagnostics: list[dict[str, Any]] = []
    for hit in vector_hits + keyword_hits:
        key = _content_fingerprint(hit)
        existing = merged.get(key)
        if not existing:
            merged[key] = hit
            continue

        existing_score = float(existing.score or existing.raw_score or 0.0)
        hit_score = float(hit.score or hit.raw_score or 0.0)
        if hit_score > existing_score:
            merged[key] = hit
            duplicate_diagnostics.append(_duplicate_diagnostic(existing, hit))
        else:
            duplicate_diagnostics.append(_duplicate_diagnostic(hit, existing))

    return list(merged.values()), duplicate_diagnostics


def _content_fingerprint(hit: RetrievedChunk) -> str:
    heading = _normalize_for_fingerprint(hit.heading)
    content = _normalize_for_fingerprint(hit.text)
    if heading:
        return f"{heading}\n{content[:1500]}"
    return content[:1500]


def _normalize_for_fingerprint(value: Any) -> str:
    text = sanitize_text(value).lower()
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = text.translate(str.maketrans({char: " " for char in string.punctuation}))
    return " ".join(text.split())


def _duplicate_diagnostic(
    duplicate: RetrievedChunk,
    kept: RetrievedChunk,
) -> dict[str, Any]:
    diagnostic = _diagnostic_for_hit(duplicate)
    diagnostic.update(
        {
            "score": None,
            "filtered_reason": "duplicate_content",
            "duplicate_of": {
                "source_file": kept.source_file,
                "heading": kept.heading,
                "chunk_index": kept.chunk_index,
            },
        }
    )
    return diagnostic


def _dedupe_near_duplicates(
    query: str,
    chunks: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], list[dict[str, Any]]]:
    if not settings.retrieval_near_duplicate_dedupe:
        return chunks, []

    max_per_group = max(settings.retrieval_max_near_duplicates_per_group, 1)
    groups: list[list[RetrievedChunk]] = []
    group_tokens: list[set[str]] = []

    for chunk in chunks:
        tokens = _content_shingles(_normalize_body_for_near_duplicate(chunk.text))
        if not tokens:
            groups.append([chunk])
            group_tokens.append(tokens)
            continue

        best_group_index = None
        best_similarity = 0.0
        for index, existing_tokens in enumerate(group_tokens):
            similarity = _token_set_similarity(tokens, existing_tokens)
            if similarity > best_similarity:
                best_similarity = similarity
                best_group_index = index

        if (
            best_group_index is not None
            and best_similarity >= settings.retrieval_near_duplicate_threshold
        ):
            groups[best_group_index].append(chunk)
            if len(tokens) > len(group_tokens[best_group_index]):
                group_tokens[best_group_index] = tokens
        else:
            groups.append([chunk])
            group_tokens.append(tokens)

    diagnostics: list[dict[str, Any]] = []
    kept_chunks: list[RetrievedChunk] = []
    context_terms = set(query_context_terms(query, include_generic_terms=True))
    phrase_terms = set(query_phrases(query))

    for group in groups:
        if len(group) <= max_per_group:
            kept_chunks.extend(group)
            continue

        ordered_group = sorted(
            group,
            key=lambda chunk: _near_duplicate_preference_key(
                context_terms,
                phrase_terms,
                chunk,
            ),
            reverse=True,
        )
        kept = ordered_group[:max_per_group]
        removed = ordered_group[max_per_group:]
        kept_chunks.extend(kept)

        for duplicate in removed:
            duplicate_of, similarity = _most_similar_kept_chunk(duplicate, kept)
            diagnostics.append(
                _near_duplicate_diagnostic(
                    duplicate=duplicate,
                    kept=duplicate_of,
                    similarity=similarity,
                )
            )

    kept_ids = {id(chunk) for chunk in kept_chunks}
    return [chunk for chunk in chunks if id(chunk) in kept_ids], diagnostics


def _normalize_body_for_near_duplicate(value: Any) -> str:
    text = sanitize_text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?s)^\s*---\s*\n.*?\n---\s*\n?", "", text)
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _is_markdown_image_only_line(line):
            continue
        if _is_navigation_line(line):
            continue
        if _is_breadcrumb_line(line):
            continue
        lines.append(line)

    normalized = "\n".join(lines).lower()
    normalized = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", normalized)
    normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", normalized)
    normalized = re.sub(r"\[([^\]]+)\]", r"\1", normalized)
    normalized = re.sub(r"https?://\S+", " ", normalized)
    normalized = normalized.translate(
        str.maketrans({char: " " for char in string.punctuation})
    )
    return " ".join(normalized.split())


def _is_markdown_image_only_line(line: str) -> bool:
    return bool(re.fullmatch(r"\s*!\[[^\]]*]\([^)]+\)\s*", line))


def _is_navigation_line(line: str) -> bool:
    normalized = _normalize_for_fingerprint(line)
    if not normalized:
        return True
    navigation_terms = {"previous", "next", "contents", "index", "print"}
    tokens = set(normalized.split())
    return bool(tokens & navigation_terms) and len(tokens) <= 8


def _is_breadcrumb_line(line: str) -> bool:
    markdown_link_count = len(re.findall(r"\[[^\]]+]\([^)]+\)|\[[^\]]+]", line))
    if markdown_link_count < 2 and ">" not in line:
        return False

    visible = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    visible = re.sub(r"\[([^\]]+)\]", r"\1", visible)
    separators = visible.count(">") + visible.count(" / ")
    if separators < 1:
        return False

    words = _normalize_for_fingerprint(visible).split()
    return len(words) <= 20


def _content_shingles(normalized_content: str, size: int = 5) -> set[str]:
    tokens = normalized_content.split()
    if not tokens:
        return set()
    if len(tokens) < size:
        return set(tokens)
    return {" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def _token_set_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    containment = overlap / min(len(left), len(right))
    jaccard = overlap / len(left | right)
    size_ratio = min(len(left), len(right)) / max(len(left), len(right))
    if size_ratio < 0.75:
        return jaccard
    return max(containment, jaccard)


def _near_duplicate_preference_key(
    query_context_terms: set[str],
    query_phrases: set[str],
    chunk: RetrievedChunk,
) -> tuple[int, int, float]:
    context_text = _metadata_context_text(chunk)
    context_matches = sum(1 for term in query_context_terms if term in context_text)
    phrase_matches = sum(1 for phrase in query_phrases if phrase in context_text)
    return (
        context_matches,
        phrase_matches,
        float(chunk.score or chunk.raw_score or 0.0),
    )


def _metadata_context_text(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    ignored_keys = {
        "text",
        "content",
        "source",
        "filename",
        "source_file",
        "document_id",
        "chunk_index",
    }
    values: list[Any] = [
        chunk.metadata.get("title"),
        chunk.metadata.get("breadcrumbs"),
        _body_context_text(chunk.text),
    ]
    for key, value in metadata.items():
        if str(key) in ignored_keys:
            continue
        values.append(value)
    return _normalize_for_fingerprint(" ".join(_flatten_metadata_values(values)))


def _body_context_text(value: Any) -> str:
    """Extract generic context lines embedded in chunk text.

    Some corpora store breadcrumbs/front-matter in the body instead of structured
    metadata. Use only breadcrumb-like or YAML-like context lines, not the full
    body, so near-duplicate selection can prefer the user's requested context
    without turning body content into a primary duplicate preference signal.
    """

    text = sanitize_text(value).replace("\r\n", "\n").replace("\r", "\n")
    context_lines: list[str] = []
    in_front_matter = False
    for raw_line in text.splitlines()[:20]:
        line = raw_line.strip()
        if not line:
            continue
        if line == "---":
            in_front_matter = not in_front_matter
            continue
        if in_front_matter:
            context_lines.append(line)
            continue
        if _is_breadcrumb_line(line):
            context_lines.append(line)
    return " ".join(context_lines)


def _flatten_metadata_values(values: list[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            flattened.extend(_flatten_metadata_values(list(value.values())))
        elif isinstance(value, (list, tuple, set)):
            flattened.extend(_flatten_metadata_values(list(value)))
        else:
            flattened.append(str(value))
    return flattened


def _most_similar_kept_chunk(
    duplicate: RetrievedChunk,
    kept_chunks: list[RetrievedChunk],
) -> tuple[RetrievedChunk, float]:
    duplicate_tokens = _content_shingles(_normalize_body_for_near_duplicate(duplicate.text))
    best = kept_chunks[0]
    best_similarity = 0.0
    for kept in kept_chunks:
        similarity = _token_set_similarity(
            duplicate_tokens,
            _content_shingles(_normalize_body_for_near_duplicate(kept.text)),
        )
        if similarity > best_similarity:
            best = kept
            best_similarity = similarity
    return best, round(best_similarity, 6)


def _near_duplicate_diagnostic(
    duplicate: RetrievedChunk,
    kept: RetrievedChunk,
    similarity: float,
) -> dict[str, Any]:
    diagnostic = _diagnostic_for_hit(duplicate)
    diagnostic.update(
        {
            "score": None,
            "filtered_reason": "near_duplicate_content",
            "duplicate_of": {
                "source_file": kept.source_file,
                "heading": kept.heading,
                "chunk_index": kept.chunk_index,
            },
            "similarity": similarity,
        }
    )
    return diagnostic


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
        "retrieval_stage_score": hit.retrieval_stage_score,
        "keyword_score": hit.keyword_score,
        "metadata_boosts": (hit.ranking_details or {}).get("metadata_boosts", {}),
        "reranker_score": hit.reranker_score,
        "final_score": hit.score,
    }
