import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    ingestion_embed_batch_chunks: int = _int_env("INGESTION_EMBED_BATCH_CHUNKS", 256)
    upload_max_bytes: int = _int_env("UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
    retrieval_top_k: int = _int_env("RETRIEVAL_TOP_K", 8)
    retrieval_candidate_k: int = _int_env("RETRIEVAL_CANDIDATE_K", 80)
    retrieval_final_k: int = _int_env(
        "RETRIEVAL_FINAL_K", _int_env("RETRIEVAL_TOP_K", 8)
    )
    retrieval_keyword_candidate_k: int = _int_env("RETRIEVAL_KEYWORD_CANDIDATE_K", 80)
    retrieval_min_score: float = _float_env("RETRIEVAL_MIN_SCORE", 0.55)
    retrieval_near_duplicate_dedupe: bool = _bool_env(
        "RETRIEVAL_NEAR_DUPLICATE_DEDUPE", True
    )
    retrieval_near_duplicate_threshold: float = _float_env(
        "RETRIEVAL_NEAR_DUPLICATE_THRESHOLD", 0.92
    )
    retrieval_max_near_duplicates_per_group: int = _int_env(
        "RETRIEVAL_MAX_NEAR_DUPLICATES_PER_GROUP", 1
    )
    max_context_chars: int = _int_env("MAX_CONTEXT_CHARS", 12000)
    assistant_system_prompt: str = _optional_env("ASSISTANT_SYSTEM_PROMPT") or (
            "You are a company work assistant. Answer questions using only the "
            "retrieved documentation context. Be precise, operational, and cite "
            "the relevant source files or headings. If the context is "
            "insufficient, say what is missing instead of guessing."
    )
    preserve_source_terms_instruction: str = _optional_env(
        "PRESERVE_SOURCE_TERMS_INSTRUCTION"
    ) or (
            "Preserve original product names, UI terms, menu names, field labels, "
            "and button names when they appear in the sources."
    )
    llm_provider_url: str | None = _optional_env("LLM_PROVIDER_URL")
    llm_model_name: str = os.getenv("LLM_MODEL_NAME", "qwen")
    reranker_model_name: str | None = _optional_env("RERANKER_MODEL_NAME")
    reranker_batch_size: int = _int_env("RERANKER_BATCH_SIZE", 16)
    reranker_top_n: int = _int_env("RERANKER_TOP_N", 24)
    reranker_weight: float = _float_env("RERANKER_WEIGHT", 0.15)
    ingestion_worker_poll_seconds: float = _float_env(
        "INGESTION_WORKER_POLL_SECONDS", 5.0
    )


settings = Settings()
