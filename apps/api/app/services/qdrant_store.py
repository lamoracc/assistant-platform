import logging
import time
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def init_qdrant_collection() -> None:
    client = get_qdrant_client()
    for attempt in range(1, 11):
        try:
            if client.collection_exists(settings.qdrant_collection_name):
                return

            client.create_collection(
                collection_name=settings.qdrant_collection_name,
                vectors_config=models.VectorParams(
                    size=settings.embedding_dimensions,
                    distance=models.Distance.COSINE,
                ),
            )
            return
        except Exception:
            if attempt == 10:
                raise
            time.sleep(1)


def upsert_document_chunks(points: list[models.PointStruct]) -> None:
    if not points:
        logger.info("Qdrant upsert skipped: no points")
        return

    safe_points = [_coerce_point(point) for point in points]
    first_point = safe_points[0]
    first_vector = first_point.vector
    vector_dimension = len(first_vector) if isinstance(first_vector, list) else None

    logger.info(
        "Qdrant upsert starting collection=%s points=%s first_point_id=%s vector_dimension=%s",
        settings.qdrant_collection_name,
        len(safe_points),
        first_point.id,
        vector_dimension,
    )

    client = get_qdrant_client()
    result = client.upsert(
        collection_name=settings.qdrant_collection_name,
        points=safe_points,
        wait=True,
    )
    logger.info(
        "Qdrant upsert result collection=%s result=%s",
        settings.qdrant_collection_name,
        result,
    )


def get_qdrant_collection_status() -> dict[str, Any]:
    client = get_qdrant_client()
    info = client.get_collection(settings.qdrant_collection_name)
    vectors_config = info.config.params.vectors
    vector_size = getattr(vectors_config, "size", None)
    if isinstance(vectors_config, dict):
        vector_size = {
            name: getattr(config, "size", None)
            for name, config in vectors_config.items()
        }

    return {
        "collection_name": settings.qdrant_collection_name,
        "vector_size": vector_size,
        "points_count": info.points_count,
    }


def smoke_test_qdrant_upsert() -> dict[str, Any]:
    point_id = str(uuid.uuid4())
    vector = [0.0] * settings.embedding_dimensions
    vector[0] = 1.0
    point = models.PointStruct(
        id=point_id,
        vector=vector,
        payload={"debug": True, "source": "qdrant_smoke_test"},
    )
    upsert_document_chunks([point])
    return {
        "collection_name": settings.qdrant_collection_name,
        "point_id": point_id,
        "vector_dimension": len(vector),
        "status": "upserted",
    }


def _coerce_point(point: models.PointStruct) -> models.PointStruct:
    return models.PointStruct(
        id=point.id,
        vector=_coerce_vector(point.vector),
        payload=point.payload,
    )


def _coerce_vector(vector) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()

    if isinstance(vector, tuple):
        vector = list(vector)

    if not isinstance(vector, list):
        raise TypeError(f"Qdrant vector must be a list, got {type(vector)!r}")

    return [float(value) for value in vector]
