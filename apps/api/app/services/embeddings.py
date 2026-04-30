from functools import lru_cache
import logging
import time

from sentence_transformers import SentenceTransformer

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    logger.warning(
        "Loading embedding model name=%s device=auto",
        settings.embedding_model_name,
    )
    started_at = time.monotonic()
    model = SentenceTransformer(settings.embedding_model_name)
    device = getattr(model, "device", "unknown")
    logger.warning(
        "Embedding model loaded name=%s device=%s elapsed_seconds=%.2f",
        settings.embedding_model_name,
        device,
        time.monotonic() - started_at,
    )
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    model = get_embedding_model()
    logger.warning(
        "Embedding encode start text_count=%s batch_size=%s",
        len(texts),
        settings.embedding_batch_size,
    )
    started_at = time.monotonic()
    embeddings = model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
    )
    elapsed_seconds = time.monotonic() - started_at
    logger.warning(
        "Embedding encode finished text_count=%s elapsed_seconds=%.2f",
        len(texts),
        elapsed_seconds,
    )
    return embeddings.tolist()
