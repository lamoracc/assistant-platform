from dataclasses import dataclass, replace
import re
import string
from typing import Any

from app.services.text_sanitizer import sanitize_text

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "for",
    "how",
    "i",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
}
PREFERRED_CHUNK_TYPES = {"procedure", "content", "table"}
LOW_VALUE_CHUNK_TYPES = {"navigation", "reference", "empty"}


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    document: str
    source_file: str
    heading: str | None
    chunk_index: int
    score: float
    metadata: dict[str, Any]
    raw_score: float | None = None
    retrieval_type: str = "vector"
    document_id: str | None = None
    ranking_reason: list[str] | None = None
    retrieval_stage_score: float | None = None
    keyword_score: float | None = None
    reranker_score: float | None = None
    ranking_details: dict[str, Any] | None = None


def rank_candidates(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    phrase = _normalize_for_match(question)
    terms = query_terms(question)
    phrases = query_phrases(question)
    ranked = [_with_generic_score(chunk, phrase, terms, phrases) for chunk in chunks]
    return sorted(ranked, key=lambda chunk: chunk.score, reverse=True)


def query_terms(question: str) -> list[str]:
    terms = []
    for raw in sanitize_text(question).lower().replace("/", " ").split():
        term = "".join(char for char in raw if char.isalnum() or char in {"_", "-"})
        if len(term) >= 3 and term not in STOPWORDS:
            terms.append(term)
    return list(dict.fromkeys(terms))


def query_phrases(question: str) -> list[str]:
    terms = query_terms(question)
    phrases: list[str] = []
    for size in range(min(4, len(terms)), 1, -1):
        for index in range(0, len(terms) - size + 1):
            phrases.append(" ".join(terms[index : index + size]))
    return list(dict.fromkeys(phrases))


def _with_generic_score(
    chunk: RetrievedChunk,
    phrase: str,
    terms: list[str],
    phrases: list[str],
) -> RetrievedChunk:
    title = _normalize_for_match(chunk.metadata.get("title"))
    heading = _normalize_for_match(chunk.heading)
    breadcrumbs = _normalize_for_match(chunk.metadata.get("breadcrumbs"))
    body = _normalize_for_match(chunk.text)
    chunk_type = sanitize_text(chunk.metadata.get("chunk_type")).lower()
    reasons: list[str] = []
    boosts: dict[str, float] = {
        "exact_phrase_metadata": 0.0,
        "exact_phrase_body": 0.0,
        "query_phrase_metadata": 0.0,
        "query_phrase_body": 0.0,
        "all_terms_metadata": 0.0,
        "all_terms_body": 0.0,
        "term_metadata": 0.0,
        "term_body": 0.0,
        "chunk_type": 0.0,
        "length": 0.0,
        "hybrid": 0.0,
    }

    base = float(chunk.raw_score or chunk.score or 0.0)
    if chunk.retrieval_type == "keyword":
        base = max(base, 0.45)

    score = base
    metadata_text = " ".join([title, heading, breadcrumbs])

    if phrase and phrase in metadata_text:
        boosts["exact_phrase_metadata"] = 0.55
        reasons.append("exact_phrase_in_metadata")
    elif phrase and phrase in body:
        boosts["exact_phrase_body"] = 0.25
        reasons.append("exact_phrase_in_content")

    metadata_phrase_matches = [item for item in phrases if item in metadata_text]
    body_phrase_matches = [item for item in phrases if item in body]
    if metadata_phrase_matches:
        boosts["query_phrase_metadata"] = min(0.40, 0.16 * len(metadata_phrase_matches))
        reasons.append("query_phrase_in_metadata")
    elif body_phrase_matches:
        boosts["query_phrase_body"] = min(0.18, 0.07 * len(body_phrase_matches))
        reasons.append("query_phrase_in_content")

    if terms and all(term in metadata_text for term in terms):
        boosts["all_terms_metadata"] = 0.30
        reasons.append("all_strong_terms_in_metadata")
    elif terms and all(term in body for term in terms):
        boosts["all_terms_body"] = 0.14
        reasons.append("all_strong_terms_in_content")
    elif terms:
        matched_title_terms = sum(1 for term in terms if term in metadata_text)
        matched_body_terms = sum(1 for term in terms if term in body)
        if matched_title_terms:
            boosts["term_metadata"] = 0.06 * matched_title_terms
            reasons.append("strong_terms_in_metadata")
        if matched_body_terms:
            boosts["term_body"] = 0.02 * matched_body_terms
            reasons.append("strong_terms_in_content")

    if chunk_type in PREFERRED_CHUNK_TYPES:
        boosts["chunk_type"] = 0.08
        reasons.append(f"preferred_chunk_type:{chunk_type}")
    elif chunk_type in LOW_VALUE_CHUNK_TYPES:
        boosts["chunk_type"] = -0.20
        reasons.append(f"low_value_chunk_type:{chunk_type}")

    length = len(sanitize_text(chunk.text))
    if 120 <= length <= 1800:
        boosts["length"] = 0.06
        reasons.append("focused_chunk_length")
    elif length > 3500:
        boosts["length"] = -0.10
        reasons.append("long_noisy_chunk")
    elif length < 60:
        boosts["length"] = -0.08
        reasons.append("very_short_chunk")

    if chunk.retrieval_type == "hybrid":
        boosts["hybrid"] = 0.08
        reasons.append("matched_vector_and_keyword")

    score += sum(boosts.values())
    final_score = round(score, 6)
    return replace(
        chunk,
        score=final_score,
        ranking_reason=reasons,
        ranking_details={
            "retrieval_stage_score": chunk.retrieval_stage_score,
            "keyword_score": chunk.keyword_score,
            "metadata_boosts": boosts,
            "reranker_score": chunk.reranker_score,
            "final_score": final_score,
        },
    )


def _normalize_for_match(value: Any) -> str:
    text = sanitize_text(_stringify_metadata_value(value)).lower()
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = text.translate(str.maketrans({char: " " for char in string.punctuation}))
    return " ".join(text.split())


def _stringify_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return " ".join(
            item
            for item in (_stringify_metadata_value(item) for item in value.values())
            if item
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(
            item for item in (_stringify_metadata_value(item) for item in value) if item
        )
    return str(value)
