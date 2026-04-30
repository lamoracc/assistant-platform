from app.services.language import detect_language
from app.services.text_sanitizer import sanitize_text

PROCEDURE_HINTS = {
    "step",
    "select",
    "click",
    "enter",
    "configure",
    "выберите",
    "нажмите",
    "введите",
}
REFERENCE_HINTS = {"see also", "related", "refer to", "смотрите", "см."}
NAVIGATION_HINTS = {"contents", "index", "previous", "next", "home", "breadcrumb"}


def normalize_text(text: str | None) -> str:
    return sanitize_text(text)


def classify_chunk_type(text: str | None, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    normalized = normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return "empty"
    if _looks_like_image_or_breadcrumb(normalized):
        return "navigation"
    if metadata.get("html_tag") == "table":
        return "table"
    if metadata.get("role") == "navigation":
        return "navigation"
    if len(normalized) < 80 and any(hint in lowered for hint in NAVIGATION_HINTS):
        return "navigation"
    if any(hint in lowered for hint in PROCEDURE_HINTS):
        return "procedure"
    if any(hint in lowered for hint in REFERENCE_HINTS) or "http" in lowered:
        return "reference"
    return "content"


def _looks_like_image_or_breadcrumb(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith("![") or lowered.endswith((".gif)", ".jpg)", ".png)")):
        return True
    return text.count(">") >= 2 and len(text) < 200


def enrich_chunk_metadata(text: str, metadata: dict | None = None) -> dict:
    enriched = dict(metadata or {})
    enriched["language"] = detect_language(text)
    enriched["chunk_type"] = classify_chunk_type(text, enriched)
    return enriched
