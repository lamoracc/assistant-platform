import mimetypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoutedFile:
    path: Path
    extension: str
    content_type: str
    route: str


HTML_EXTENSIONS = {".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
WORD_EXTENSIONS = {".doc", ".docx"}
TEXT_EXTENSIONS = {".md", ".txt"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
IGNORED_EXTENSIONS = {".css", ".js"}


def route_file(path: Path, content_type: str | None = None) -> RoutedFile:
    extension = path.suffix.lower()
    guessed_type = content_type or mimetypes.guess_type(path.name)[0] or ""

    if extension in HTML_EXTENSIONS or guessed_type in {"text/html", "application/xhtml+xml"}:
        route = "html"
    elif extension in PDF_EXTENSIONS or guessed_type == "application/pdf":
        route = "pdf"
    elif extension in WORD_EXTENSIONS or guessed_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        route = "word"
    elif extension in TEXT_EXTENSIONS or guessed_type.startswith("text/"):
        route = "text"
    elif extension in IMAGE_EXTENSIONS or guessed_type.startswith("image/"):
        route = "image"
    elif extension in IGNORED_EXTENSIONS:
        route = "ignored"
    else:
        route = "unsupported"

    return RoutedFile(
        path=path,
        extension=extension,
        content_type=guessed_type or "application/octet-stream",
        route=route,
    )


def content_type_for_path(path: Path) -> str:
    return route_file(path).content_type
