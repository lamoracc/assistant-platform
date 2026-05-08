import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.services import retrieval
    from app.services.retrieval import retrieve_chunks
    from app.services.retrieval_ranking import RetrievedChunk
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


def chunk(source_file: str = "source.md", score: float = 0.8) -> RetrievedChunk:
    text = "Configure source records and review relevant setup details."
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading="Source",
        chunk_index=0,
        score=score,
        raw_score=score,
        metadata={"title": "Source", "text": text, "chunk_type": "content"},
        retrieval_type="vector",
        document_id=source_file,
        retrieval_stage_score=score,
    )


class DisabledReranker:
    enabled = False

    def rerank(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return chunks


class TimedDisabledReranker:
    enabled = False

    def rerank(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        sum(range(10000))
        return chunks


class RetrievalTimingTests(unittest.TestCase):
    def test_retrieval_diagnostics_include_numeric_timings(self) -> None:
        with (
            patch.object(
                retrieval,
                "_vector_search",
                return_value=([chunk("vector.md")], [{"document": "vector.md"}]),
            ),
            patch.object(retrieval, "_keyword_search", return_value=[]),
            patch.object(retrieval, "get_reranker", return_value=DisabledReranker()),
        ):
            result = retrieve_chunks("configure source", db=None)

        timings = result.diagnostics["timings"]
        required = {
            "vector_ms",
            "keyword_ms",
            "merge_ms",
            "ranking_ms",
            "dedupe_ms",
            "reranker_ms",
            "answer_builder_ms",
            "total_ms",
        }
        self.assertTrue(required.issubset(timings))
        for key in required:
            self.assertIsInstance(timings[key], float)
            self.assertGreaterEqual(timings[key], 0.0)
        self.assertLess(timings["reranker_ms"], 1.0)

    def test_reranker_ms_is_zero_when_reranker_disabled(self) -> None:
        with (
            patch.object(
                retrieval,
                "_vector_search",
                return_value=([chunk("vector.md")], [{"document": "vector.md"}]),
            ),
            patch.object(retrieval, "_keyword_search", return_value=[]),
            patch.object(
                retrieval,
                "get_reranker",
                return_value=TimedDisabledReranker(),
            ),
        ):
            result = retrieve_chunks("configure source", db=None)

        self.assertEqual(result.diagnostics["timings"]["reranker_ms"], 0.0)

    def test_total_ms_covers_major_retrieval_stages(self) -> None:
        with (
            patch.object(
                retrieval,
                "_vector_search",
                return_value=([chunk("vector.md")], [{"document": "vector.md"}]),
            ),
            patch.object(retrieval, "_keyword_search", return_value=[]),
            patch.object(retrieval, "get_reranker", return_value=DisabledReranker()),
        ):
            result = retrieve_chunks("configure source", db=None)

        timings = result.diagnostics["timings"]
        major_sum = sum(
            timings[key]
            for key in (
                "vector_ms",
                "keyword_ms",
                "merge_ms",
                "ranking_ms",
                "dedupe_ms",
                "reranker_ms",
            )
        )
        self.assertGreaterEqual(timings["total_ms"], major_sum - 1.0)


if __name__ == "__main__":
    unittest.main()
