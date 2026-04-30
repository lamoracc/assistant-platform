from app.services.text_sanitizer import sanitize_text


def detect_language(text: str | None) -> str:
    sample = sanitize_text(text)[:4000]
    if not sample:
        return "unknown"

    try:
        from langdetect import detect

        return detect(sample)
    except Exception:
        cyrillic = sum(1 for char in sample if "\u0400" <= char <= "\u04ff")
        latin = sum(1 for char in sample if "a" <= char.lower() <= "z")
        if cyrillic > latin:
            return "ru"
        if latin:
            return "en"
        return "unknown"


def is_russian(text: str | None) -> bool:
    return detect_language(text) == "ru"
