import hashlib
import logging
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urldefrag, urlparse

from qdrant_client.http import models as qdrant_models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentChunk, DocumentLink, ImageAsset
from app.services.chunking import chunk_by_headings_and_paragraphs
from app.services.document_extractors import extract_document
from app.services.embeddings import embed_texts
from app.services.file_router import content_type_for_path, route_file
from app.services.qdrant_store import upsert_document_chunks
from app.services.text_sanitizer import sanitize_text

logger = logging.getLogger(__name__)

TINY_IMAGE_MAX_SIZE = 32
ICON_NAME_TOKENS = {"icon", "logo", "spacer", "bullet", "btn", "button", "pixel"}
NON_INDEXED_CHUNK_TYPES = {"empty", "navigation"}


class IngestionError(ValueError):
    pass


class EmptyDocumentError(IngestionError):
    pass


def ingest_document(
    db: Session,
    filename: str,
    content_type: str,
    content: bytes,
    source_path: str | None = None,
    file_hash: str | None = None,
) -> Document:
    try:
        logger.warning(
            "ingest_document start filename=%s source_path=%s content_type=%s bytes=%s",
            filename,
            source_path,
            content_type,
            len(content),
        )
        file_hash = file_hash or hash_bytes(content)
        existing = db.scalar(select(Document).where(Document.file_hash == file_hash))
        if existing:
            logger.info("Skipping duplicate document %s", source_path or filename)
            reindex_document_chunks(existing)
            return existing

        extracted = extract_document(content, filename, content_type)
        logger.warning(
            "ingest_document after extract_document filename=%s block_count=%s text_length=%s",
            filename,
            len(extracted.blocks),
            len(extracted.text),
        )
        chunks = [
            chunk
            for chunk in chunk_by_headings_and_paragraphs(extracted.blocks)
            if chunk.metadata.get("chunk_type") not in NON_INDEXED_CHUNK_TYPES
        ]
        logger.warning(
            "ingest_document after chunking filename=%s chunk_count=%s",
            filename,
            len(chunks),
        )
        if not chunks:
            raise EmptyDocumentError("No extractable text was found in the document.")

        document = Document(
            filename=filename,
            source_path=source_path,
            file_hash=file_hash,
            content_type=content_type,
            source_type=_source_type(filename, content_type),
            status="processing",
            text_length=sum(len(chunk.content) for chunk in chunks),
            chunk_count=len(chunks),
            doc_metadata=extracted.metadata,
        )
        db.add(document)
        db.flush()

        texts_to_embed = [chunk.content for chunk in chunks]
        logger.warning(
            "ingest_document before embed_texts filename=%s text_count=%s",
            filename,
            len(texts_to_embed),
        )
        embeddings = embed_texts(texts_to_embed)
        embedding_dimension = len(embeddings[0]) if embeddings else 0
        logger.warning(
            "ingest_document after embed_texts filename=%s embedding_count=%s embedding_dimension=%s",
            filename,
            len(embeddings),
            embedding_dimension,
        )
        qdrant_points: list[qdrant_models.PointStruct] = []

        for chunk_index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            vector_id = str(uuid.uuid5(document.id, f"{filename}:{chunk_index}"))
            chunk_content = sanitize_text(chunk.content)
            chunk_heading = sanitize_text(chunk.heading)
            chunk_metadata = _sanitize_metadata(chunk.metadata)
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    vector_id=vector_id,
                    chunk_index=chunk_index,
                    heading=chunk_heading,
                    content=chunk_content,
                    char_count=len(chunk_content),
                    chunk_metadata=chunk_metadata,
                )
            )
            qdrant_points.append(
                qdrant_models.PointStruct(
                    id=vector_id,
                    vector=embedding,
                    payload={
                        "document_id": str(document.id),
                        "filename": filename,
                        "content_type": content_type,
                        "chunk_index": chunk_index,
                        "heading": chunk_heading,
                        "text": chunk_content,
                        "language": chunk_metadata.get("language"),
                        "chunk_type": chunk_metadata.get("chunk_type"),
                        **chunk_metadata,
                    },
                )
            )

        try:
            logger.warning(
                "ingest_document before Qdrant upsert filename=%s point_count=%s",
                filename,
                len(qdrant_points),
            )
            upsert_document_chunks(qdrant_points)
            logger.warning(
                "ingest_document after Qdrant upsert filename=%s point_count=%s",
                filename,
                len(qdrant_points),
            )
        except Exception as exc:
            raise IngestionError(f"Qdrant upsert failed: {exc}") from exc

        _store_document_links(db, document, extracted.metadata)
        _store_referenced_images(db, document, extracted.metadata)
        document.status = "ingested"
        logger.warning(
            "ingest_document before DB commit filename=%s document_id=%s",
            filename,
            document.id,
        )
        db.commit()
        logger.warning(
            "ingest_document after DB commit filename=%s document_id=%s",
            filename,
            document.id,
        )
        db.refresh(document)
        return document
    except IngestionError:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise IngestionError(str(exc)) from exc
    except Exception:
        db.rollback()
        raise


def _source_type(filename: str, content_type: str) -> str:
    routed = route_file(Path(filename), content_type.split(";")[0])
    return routed.route if routed.route not in {"unsupported", "ignored"} else "document"


def ingest_help_folder(db: Session, root_path: str) -> dict[str, int | str]:
    started_at = time.monotonic()
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise IngestionError(f"Import path is not a directory: {root_path}")

    files = sorted(item for item in root.rglob("*") if item.is_file())
    route_counts: dict[str, int] = {}
    for file_path in files:
        route = route_file(file_path).route
        route_counts[route] = route_counts.get(route, 0) + 1

    logger.warning(
        "Starting documentation folder import root=%s total_files=%s route_counts=%s",
        root,
        len(files),
        route_counts,
    )
    stats = {
        "root_path": str(root),
        "total_files": len(files),
        "route_counts": route_counts,
        "processed": 0,
        "indexed": 0,
        "skipped_empty": 0,
        "failed": 0,
        "documents_ingested": 0,
        "documents_skipped": 0,
        "images_stored": 0,
        "images_skipped": 0,
        "ignored": 0,
    }
    path_to_document_id: dict[str, uuid.UUID] = {}

    for path in files:
        routed = route_file(path)
        relative_path = path.relative_to(root).as_posix()

        if routed.route == "ignored":
            logger.info("Ignoring support asset: %s", relative_path)
            stats["ignored"] += 1
            continue

        try:
            if routed.route in {"html", "pdf", "word", "text"}:
                stats["processed"] += 1
                if stats["processed"] == 1 or stats["processed"] % 100 == 0:
                    logger.warning(
                        "Import progress root=%s processed=%s indexed=%s skipped_empty=%s failed=%s current=%s",
                        root,
                        stats["processed"],
                        stats["indexed"],
                        stats["skipped_empty"],
                        stats["failed"],
                        relative_path,
                    )
                logger.info("Ingesting document: %s", relative_path)
                content = path.read_bytes()
                file_hash = hash_bytes(content)
                existing = db.scalar(select(Document).where(Document.file_hash == file_hash))
                if existing:
                    logger.info(
                        "Skipping duplicate document and reindexing Qdrant chunks: %s",
                        relative_path,
                    )
                    reindex_document_chunks(existing)
                    path_to_document_id[relative_path] = existing.id
                    stats["documents_skipped"] += 1
                    stats["indexed"] += 1
                    continue

                document = ingest_document(
                    db=db,
                    filename=relative_path,
                    content_type=routed.content_type,
                    content=content,
                    source_path=relative_path,
                    file_hash=file_hash,
                )
                path_to_document_id[relative_path] = document.id
                stats["images_stored"] += store_referenced_image_assets(
                    db, document, root
                )
                stats["documents_ingested"] += 1
                stats["indexed"] += 1
                continue

            if routed.route == "image":
                stats["processed"] += 1
                logger.info("Storing standalone image asset: %s", relative_path)
                asset = store_image_asset(db, path, root)
                if asset:
                    stats["images_stored"] += 1
                else:
                    stats["images_skipped"] += 1
                continue

            logger.info("Ignoring unsupported file: %s", relative_path)
            stats["ignored"] += 1
        except EmptyDocumentError:
            logger.info("Skipping empty document: %s", relative_path)
            db.rollback()
            stats["skipped_empty"] += 1
        except Exception:
            logger.exception("Failed to ingest %s", relative_path)
            db.rollback()
            stats["failed"] += 1

    resolved = resolve_document_links(db, path_to_document_id)
    elapsed_seconds = round(time.monotonic() - started_at, 2)
    stats["links_resolved"] = resolved
    stats["elapsed_seconds"] = elapsed_seconds
    logger.warning("Finished documentation folder import stats=%s", stats)
    return stats


def store_image_asset(
    db: Session,
    path: Path,
    root: Path,
    referenced_by_document: Document | None = None,
    referenced_src: str | None = None,
) -> ImageAsset | None:
    relative_path = path.relative_to(root).as_posix()
    width, height = _image_dimensions(path)
    if _should_skip_image(path.name, width, height):
        logger.info("Skipping tiny UI image asset: %s", relative_path)
        return None

    content = path.read_bytes()
    file_hash = hash_bytes(content)
    existing = db.scalar(
        select(ImageAsset).where(
            ImageAsset.file_hash == file_hash,
            ImageAsset.path == relative_path,
            ImageAsset.referenced_by_document_id
            == (referenced_by_document.id if referenced_by_document else None),
        )
    )
    if existing:
        return existing

    asset = ImageAsset(
        referenced_by_document_id=referenced_by_document.id
        if referenced_by_document
        else None,
        path=relative_path,
        filename=path.name,
        file_hash=file_hash,
        width=width,
        height=height,
        content_type=_content_type_for_path(path),
        asset_metadata={"referenced_src": referenced_src} if referenced_src else {},
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def store_referenced_image_assets(
    db: Session,
    document: Document,
    root: Path,
) -> int:
    stored = 0
    for image_ref in document.doc_metadata.get("image_refs", []):
        src = str(image_ref.get("src", ""))
        image_path = _resolve_local_asset_path(root, document.source_path, src)
        if not image_path or not image_path.exists() or not image_path.is_file():
            continue
        asset = store_image_asset(
            db=db,
            path=image_path,
            root=root,
            referenced_by_document=document,
            referenced_src=src,
        )
        if asset:
            stored += 1
    return stored


def resolve_document_links(
    db: Session,
    path_to_document_id: dict[str, uuid.UUID],
) -> int:
    resolved = 0
    links = db.scalars(
        select(DocumentLink).where(DocumentLink.target_document_id.is_(None))
    ).all()
    for link in links:
        if not link.target_path:
            continue
        target_id = path_to_document_id.get(link.target_path)
        if target_id:
            link.target_document_id = target_id
            resolved += 1
    db.commit()
    return resolved


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def reindex_document_chunks(document: Document) -> int:
    chunks = sorted(document.chunks, key=lambda chunk: chunk.chunk_index)
    if not chunks:
        logger.info("No existing chunks to reindex for document %s", document.id)
        return 0

    texts = [sanitize_text(chunk.content) for chunk in chunks]
    embeddings = embed_texts(texts)
    qdrant_points: list[qdrant_models.PointStruct] = []

    for chunk, embedding, text in zip(chunks, embeddings, texts):
        heading = sanitize_text(chunk.heading)
        metadata = _sanitize_metadata(chunk.chunk_metadata)
        qdrant_points.append(
            qdrant_models.PointStruct(
                id=chunk.vector_id,
                vector=embedding,
                payload={
                    "document_id": str(document.id),
                    "filename": document.filename,
                    "content_type": document.content_type,
                    "chunk_index": chunk.chunk_index,
                    "heading": heading,
                    "text": text,
                    **metadata,
                },
            )
        )

    try:
        upsert_document_chunks(qdrant_points)
    except Exception as exc:
        raise IngestionError(f"Qdrant reindex failed: {exc}") from exc

    return len(qdrant_points)


def _sanitize_metadata(value):
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_text(str(key)): _sanitize_metadata(item)
            for key, item in value.items()
        }
    return value


def _store_document_links(db: Session, document: Document, metadata: dict) -> None:
    for link in metadata.get("links", []):
        href = str(link.get("href", ""))
        db.add(
            DocumentLink(
                source_document_id=document.id,
                link_text=link.get("text"),
                href=href,
                target_path=_local_target_path(document.source_path, href),
                link_metadata=link,
            )
        )


def _store_referenced_images(db: Session, document: Document, metadata: dict) -> None:
    image_refs = metadata.get("image_refs", [])
    if image_refs:
        document.doc_metadata = {**document.doc_metadata, "image_refs": image_refs}


def _local_target_path(source_path: str | None, href: str) -> str | None:
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        return None
    clean_href = urldefrag(href).url
    if not clean_href:
        return None
    if source_path:
        base = PurePosixPath(source_path).parent
        if clean_href.startswith(f"{base.as_posix()}/"):
            return clean_href
        if clean_href.startswith("/"):
            return clean_href.lstrip("/")
        return str(base.joinpath(clean_href))
    return clean_href


def _resolve_local_asset_path(
    root: Path,
    source_path: str | None,
    href: str,
) -> Path | None:
    target_path = _local_target_path(source_path, href)
    if not target_path:
        return None
    candidate = (root / target_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _content_type_for_path(path: Path) -> str:
    return content_type_for_path(path)


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        logger.info("Could not read image dimensions: %s", path)
        return None, None


def _should_skip_image(filename: str, width: int | None, height: int | None) -> bool:
    lowered = filename.lower()
    if any(token in lowered for token in ICON_NAME_TOKENS):
        return True
    if width is not None and height is not None:
        return width <= TINY_IMAGE_MAX_SIZE and height <= TINY_IMAGE_MAX_SIZE
    return False
