from typing import Any


class Reranker:
    def rerank(self, question: str, chunks: list[Any]) -> list[Any]:
        return chunks


def get_reranker() -> Reranker:
    return Reranker()
