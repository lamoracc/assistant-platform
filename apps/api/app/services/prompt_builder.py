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

    context = build_context(chunks, max_chars=2000)
    return (
        "No LLM provider is configured, so this is a retrieval-only response. "
        "The most relevant documentation excerpts are:\n\n"
        f"{context}"
    )
