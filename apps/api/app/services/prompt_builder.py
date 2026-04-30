from app.core.config import settings
from app.services.language import is_russian
from app.services.retrieval import RetrievedChunk
from app.services.text_sanitizer import sanitize_text

OPERA_SYSTEM_PROMPT = """You are an OPERA PMS support assistant.
Answer questions using only the retrieved OPERA documentation context.
Be precise, operational, and cite the relevant source files or headings.
If the context is insufficient, say what is missing instead of guessing."""


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
    language_instruction = (
        "Answer in Russian. Preserve original OPERA UI terms, menu names, field "
        "labels, and button names in English."
        if is_russian(question)
        else "Answer in the same language as the user when possible."
    )
    user_prompt = (
        "Use the following retrieved OPERA PMS documentation context to answer.\n\n"
        f"{language_instruction}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {sanitize_text(question)}"
    )
    return [
        {"role": "system", "content": OPERA_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_retrieval_only_answer(
    question: str,
    chunks: list[RetrievedChunk],
) -> str:
    if not chunks:
        return (
            "I could not find relevant OPERA PMS documentation chunks for that "
            "question."
        )

    context = build_context(chunks, max_chars=2000)
    return (
        "No LLM provider is configured, so this is a retrieval-only response. "
        "The most relevant OPERA PMS documentation excerpts are:\n\n"
        f"{context}"
    )
