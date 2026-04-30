import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if not value or value.lower() in {"none", "null"}:
        return None
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://assistant:assistant@localhost:5432/assistant",
    )
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_collection_name: str = os.getenv(
        "QDRANT_COLLECTION_NAME", "document_chunks"
    )
    embedding_model_name: str = os.getenv(
        "EMBEDDING_MODEL_NAME", "BAAI/bge-m3"
    )
    embedding_dimensions: int = _int_env("EMBEDDING_DIMENSIONS", 1024)
    embedding_batch_size: int = _int_env("EMBEDDING_BATCH_SIZE", 32)
    upload_max_bytes: int = _int_env("UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
    retrieval_top_k: int = _int_env("RETRIEVAL_TOP_K", 8)
    retrieval_min_score: float = _float_env("RETRIEVAL_MIN_SCORE", 0.55)
    max_context_chars: int = _int_env("MAX_CONTEXT_CHARS", 12000)
    llm_provider_url: str | None = _optional_env("LLM_PROVIDER_URL")
    llm_model_name: str = os.getenv("LLM_MODEL_NAME", "qwen")


settings = Settings()
