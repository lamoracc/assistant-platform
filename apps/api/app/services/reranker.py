from dataclasses import replace
from functools import lru_cache
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class Reranker:
    enabled = False

    def rerank(self, question: str, chunks: list[Any]) -> list[Any]:
        return chunks


class CrossEncoderReranker(Reranker):
    enabled = True

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder

        logger.warning("Loading reranker model name=%s", model_name)
        self.model = CrossEncoder(model_name)

    def rerank(self, question: str, chunks: list[Any]) -> list[Any]:
        if not chunks:
            return []
        top_n = max(settings.reranker_top_n, 0)
        if top_n == 0:
            return chunks

        rerankable = chunks[:top_n]
        remainder = chunks[top_n:]
        pairs = [(question, chunk.text) for chunk in rerankable]
        scores = self.model.predict(pairs, batch_size=settings.reranker_batch_size)
        scored = []
        for chunk, score in zip(rerankable, scores):
            reranker_score = float(score)
            details = dict(chunk.ranking_details or {})
            final_score = float(chunk.score or 0.0) + (
                settings.reranker_weight * reranker_score
            )
            details["reranker_score"] = reranker_score
            details["reranker_weight"] = settings.reranker_weight
            details["final_score"] = final_score
            scored.append(
                replace(
                    chunk,
                    reranker_score=reranker_score,
                    score=final_score,
                    ranking_details=details,
                )
            )
        return sorted(scored, key=lambda chunk: chunk.score, reverse=True) + remainder


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    if settings.reranker_model_name:
        try:
            return CrossEncoderReranker(settings.reranker_model_name)
        except Exception:
            logger.exception(
                "Failed to load reranker model %s; falling back to generic ranking",
                settings.reranker_model_name,
            )
    return Reranker()
