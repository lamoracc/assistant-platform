import re
import unicodedata

_CONTROL_CHARS = {
    "\t",
    "\n",
    "\r",
}


def sanitize_text(value: str | None) -> str:
    if not value:
        return ""

    without_nuls = value.replace("\x00", "")
    cleaned = "".join(
        char
        if char in _CONTROL_CHARS or unicodedata.category(char)[0] != "C"
        else " "
        for char in without_nuls
    )
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r" *\r?\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
