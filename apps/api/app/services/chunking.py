import re
from dataclasses import dataclass

from app.services.normalization import enrich_chunk_metadata
from app.services.text_sanitizer import sanitize_text


@dataclass(frozen=True)
class TextBlock:
    text: str
    heading: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class TextChunk:
    content: str
    heading: str | None
    metadata: dict


HEADING_STYLE_NAMES = {"title", "heading 1", "heading 2", "heading 3"}
MAX_CHUNK_CHARS = 1800
MIN_CHUNK_CHARS = 300


def looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False
    if stripped.endswith("."):
        return False
    if re.match(r"^\d+(\.\d+)*\s+\S+", stripped):
        return True
    words = stripped.split()
    if len(words) <= 10 and stripped[:1].isupper():
        uppercase_ratio = sum(1 for char in stripped if char.isupper()) / max(
            len([char for char in stripped if char.isalpha()]), 1
        )
        return uppercase_ratio > 0.6 or stripped.istitle()
    return False


def normalize_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    normalized: list[TextBlock] = []
    current_heading: str | None = None

    for block in blocks:
        text = sanitize_text(block.text)
        text = " ".join(text.split())
        if not text:
            continue

        metadata = block.metadata or {}
        style = str(metadata.get("style", "")).lower()
        block_heading = sanitize_text(block.heading)
        is_heading = style in HEADING_STYLE_NAMES or (
            bool(block_heading) and block_heading == text
        )
        is_heading = is_heading or looks_like_heading(text)

        if is_heading:
            current_heading = block_heading or text
            continue

        normalized.append(
            TextBlock(text=text, heading=current_heading, metadata=metadata)
        )

    return normalized


def chunk_by_headings_and_paragraphs(blocks: list[TextBlock]) -> list[TextChunk]:
    normalized = normalize_blocks(blocks)
    chunks: list[TextChunk] = []
    buffer: list[str] = []
    buffer_heading: str | None = None
    buffer_metadata: dict = {}

    def flush() -> None:
        nonlocal buffer, buffer_heading, buffer_metadata
        if not buffer:
            return
        content = "\n\n".join(buffer).strip()
        if content:
            metadata = enrich_chunk_metadata(content, buffer_metadata)
            chunks.append(
                TextChunk(
                    content=content,
                    heading=buffer_heading,
                    metadata=metadata,
                )
            )
        buffer = []
        buffer_heading = None
        buffer_metadata = {}

    for block in normalized:
        next_heading = block.heading
        paragraph = sanitize_text(block.text)
        paragraph_metadata = block.metadata or {}
        current_size = sum(len(item) for item in buffer)
        heading_changed = buffer and next_heading != buffer_heading
        too_large = buffer and current_size + len(paragraph) > MAX_CHUNK_CHARS

        if heading_changed or (too_large and current_size >= MIN_CHUNK_CHARS):
            flush()

        if len(paragraph) > MAX_CHUNK_CHARS:
            flush()
            for start in range(0, len(paragraph), MAX_CHUNK_CHARS):
                piece = paragraph[start : start + MAX_CHUNK_CHARS].strip()
                if piece:
                    metadata = enrich_chunk_metadata(piece, paragraph_metadata)
                    chunks.append(
                        TextChunk(
                            content=piece,
                            heading=next_heading,
                            metadata=metadata,
                        )
                    )
            continue

        buffer.append(paragraph)
        buffer_heading = next_heading
        buffer_metadata.update(paragraph_metadata)

    flush()
    return chunks
