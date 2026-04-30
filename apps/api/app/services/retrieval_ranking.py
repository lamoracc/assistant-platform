from dataclasses import dataclass, replace
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


def rank_candidates(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    phrase = sanitize_text(question).lower()
    terms = query_terms(question)
    ranked = [_with_generic_score(chunk, phrase, terms) for chunk in chunks]
    return sorted(ranked, key=lambda chunk: chunk.score, reverse=True)


def query_terms(question: str) -> list[str]:
    terms = []
    for raw in sanitize_text(question).lower().replace("/", " ").split():
        term = "".join(char for char in raw if char.isalnum() or char in {"_", "-"})
        if len(term) >= 3 and term not in STOPWORDS:
            terms.append(term)
    return list(dict.fromkeys(terms))


def _with_generic_score(
    chunk: RetrievedChunk,
    phrase: str,
    terms: list[str],
) -> RetrievedChunk:
    title = sanitize_text(chunk.metadata.get("title")).lower()
    heading = sanitize_text(chunk.heading).lower()
    body = sanitize_text(chunk.text).lower()
    source = sanitize_text(chunk.source_file).lower()
    chunk_type = sanitize_text(chunk.metadata.get("chunk_type")).lower()
    reasons: list[str] = []

    base = float(chunk.raw_score or chunk.score or 0.0)
    if chunk.retrieval_type == "keyword":
        base = max(base, 0.45)

    score = base
    title_heading_source = " ".join([title, heading, source])

    if phrase and phrase in title_heading_source:
        score += 0.35
        reasons.append("exact_phrase_in_title_or_heading")
    elif phrase and phrase in body:
        score += 0.20
        reasons.append("exact_phrase_in_content")

    if terms and all(term in title_heading_source for term in terms):
        score += 0.28
        reasons.append("all_strong_terms_in_title_or_heading")
    elif terms and all(term in body for term in terms):
        score += 0.16
        reasons.append("all_strong_terms_in_content")
    elif terms:
        matched_title_terms = sum(1 for term in terms if term in title_heading_source)
        matched_body_terms = sum(1 for term in terms if term in body)
        if matched_title_terms:
            score += 0.05 * matched_title_terms
            reasons.append("strong_terms_in_title_or_heading")
        if matched_body_terms:
            score += 0.025 * matched_body_terms
            reasons.append("strong_terms_in_content")

    if chunk_type in PREFERRED_CHUNK_TYPES:
        score += 0.08
        reasons.append(f"preferred_chunk_type:{chunk_type}")
    elif chunk_type in LOW_VALUE_CHUNK_TYPES:
        score -= 0.20
        reasons.append(f"low_value_chunk_type:{chunk_type}")

    length = len(sanitize_text(chunk.text))
    if 120 <= length <= 1800:
        score += 0.06
        reasons.append("focused_chunk_length")
    elif length > 3500:
        score -= 0.10
        reasons.append("long_noisy_chunk")
    elif length < 60:
        score -= 0.08
        reasons.append("very_short_chunk")

    if chunk.retrieval_type == "hybrid":
        score += 0.08
        reasons.append("matched_vector_and_keyword")

    return replace(chunk, score=round(score, 6), ranking_reason=reasons)
