import re
import string
from typing import Any

from app.core.config import settings
from app.services.retrieval import RetrievedChunk
from app.services.text_sanitizer import sanitize_text


def build_context(
    chunks: list[RetrievedChunk],
    max_chars: int | None = None,
) -> str:
    budget = max_chars or settings.max_context_chars
    parts: list[str] = []
    used = 0

    for chunk in chunks:
        header = (
            f"Source: {chunk.source_file}\n"
            f"Heading: {chunk.heading or 'N/A'}\n"
            f"Chunk: {chunk.chunk_index}\n"
        )
        body = sanitize_text(chunk.text)
        block = f"{header}{body}".strip()
        if not block:
            continue

        remaining = budget - used
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining].rsplit(" ", 1)[0].strip()
        if block:
            parts.append(block)
            used += len(block) + 2

    return "\n\n---\n\n".join(parts)


def build_chat_prompt(question: str, chunks: list[RetrievedChunk]) -> list[dict[str, str]]:
    context = build_context(chunks)
    language_instruction = "Answer in the same language as the user when possible."
    user_prompt = (
        "Use the following retrieved documentation context to answer.\n\n"
        f"{language_instruction}\n\n"
        f"{settings.preserve_source_terms_instruction}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {sanitize_text(question)}"
    )
    return [
        {"role": "system", "content": settings.assistant_system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_retrieval_only_answer(
    question: str,
    chunks: list[RetrievedChunk],
) -> str:
    if not chunks:
        return (
            "I could not find relevant documentation chunks for that question."
        )

    answer_chunks = _unique_answer_chunks(chunks, limit=3)
    facts = _extract_relevant_facts(question, answer_chunks, limit=5)
    if not facts:
        facts = [_compact_excerpt(chunk.text) for chunk in answer_chunks]

    short_answer = " ".join(facts[:2]).strip()
    if len(short_answer) > 420:
        short_answer = _truncate_at_word(short_answer, 420)

    lines = [
        "Retrieval-only answer (no LLM provider is configured).",
        "",
        "Short answer:",
        short_answer or "The retrieved sources contain relevant information, but no compact excerpt could be extracted.",
        "",
        "Relevant facts:",
    ]
    lines.extend(f"- {fact}" for fact in facts[:5] if fact)
    lines.extend(["", "Top sources:"])
    lines.extend(
        f"{index}. {chunk.source_file} — {chunk.heading or chunk.document}"
        for index, chunk in enumerate(answer_chunks, start=1)
    )
    return "\n".join(lines).strip()


def _unique_answer_chunks(
    chunks: list[RetrievedChunk],
    *,
    limit: int,
) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    seen: list[str] = []
    for chunk in chunks:
        fingerprint = _answer_content_fingerprint(chunk.text)
        if fingerprint and any(_answer_fingerprints_match(fingerprint, item) for item in seen):
            continue
        if fingerprint:
            seen.append(fingerprint)
        selected.append(chunk)
        if len(selected) >= limit:
            break
    return selected


def _extract_relevant_facts(
    question: str,
    chunks: list[RetrievedChunk],
    *,
    limit: int,
) -> list[str]:
    query_terms = _query_terms(question)
    candidates: list[tuple[int, int, str]] = []
    order = 0
    for chunk in chunks:
        for sentence in _candidate_sentences(chunk.text):
            normalized = _normalize_for_answer(sentence)
            if not normalized:
                continue
            matches = sum(1 for term in query_terms if term in normalized)
            if query_terms and matches == 0:
                continue
            candidates.append((matches, -order, _truncate_at_word(sentence, 240)))
            order += 1

    if not candidates:
        return []

    facts: list[str] = []
    seen: set[str] = set()
    for _matches, _order, sentence in sorted(candidates, reverse=True):
        fingerprint = _answer_content_fingerprint(sentence)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        facts.append(sentence)
        if len(facts) >= limit:
            break
    return facts


def _candidate_sentences(value: Any) -> list[str]:
    text = _strip_answer_noise(sanitize_text(value))
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences: list[str] = []
    for part in parts:
        sentence = " ".join(part.split()).strip()
        if len(sentence) < 35:
            continue
        sentences.append(sentence)
    return sentences


def _compact_excerpt(value: Any) -> str:
    sentences = _candidate_sentences(value)
    if sentences:
        return _truncate_at_word(sentences[0], 240)
    return _truncate_at_word(_strip_answer_noise(sanitize_text(value)), 240)


def _strip_answer_noise(text: str) -> str:
    text = re.sub(r"(?s)^\s*---\s*\n.*?\n---\s*\n?", "", text)
    cleaned_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"\s*!\[[^\]]*]\([^)]+\)\s*", line):
            continue
        if _looks_like_breadcrumb(line):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    return " ".join(text.split())


def _looks_like_breadcrumb(line: str) -> bool:
    link_count = len(re.findall(r"\[[^\]]+]\([^)]+\)|\[[^\]]+]", line))
    if link_count < 2 and ">" not in line:
        return False
    visible = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    visible = re.sub(r"\[([^\]]+)\]", r"\1", visible)
    return len(_normalize_for_answer(visible).split()) <= 50


def _query_terms(question: str) -> list[str]:
    terms: list[str] = []
    for raw in sanitize_text(question).lower().replace("/", " ").split():
        term = "".join(char for char in raw if char.isalnum() or char in {"_", "-"})
        if len(term) >= 3:
            terms.append(term)
    return list(dict.fromkeys(terms))


def _answer_content_fingerprint(value: Any) -> str:
    normalized = _normalize_for_answer(_strip_answer_noise(sanitize_text(value)))
    return normalized[:700]


def _answer_fingerprints_match(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    containment = overlap / min(len(left_tokens), len(right_tokens))
    jaccard = overlap / len(left_tokens | right_tokens)
    return containment >= 0.88 or jaccard >= 0.82


def _normalize_for_answer(value: Any) -> str:
    text = sanitize_text(value).lower()
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = text.translate(str.maketrans({char: " " for char in string.punctuation}))
    return " ".join(text.split())


def _truncate_at_word(text: str, limit: int) -> str:
    text = " ".join(sanitize_text(text).split())
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0].strip()
    return f"{truncated}..."
