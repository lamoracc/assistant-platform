import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.services.prompt_builder import build_retrieval_only_answer
    from app.services.retrieval_ranking import RetrievedChunk, rank_candidates
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


CASES_PATH = Path(__file__).with_name("golden_retrieval_cases.json")


def load_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def chunk_from_case(candidate: dict[str, Any]) -> RetrievedChunk:
    source_file = candidate["source_file"]
    heading = candidate.get("heading")
    text = candidate["text"]
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading=heading,
        chunk_index=int(candidate.get("chunk_index", 0)),
        score=float(candidate.get("score", 0.4)),
        raw_score=float(candidate.get("score", 0.4)),
        retrieval_stage_score=float(candidate.get("score", 0.4)),
        metadata={
            "title": candidate.get("title"),
            "heading": heading,
            "breadcrumbs": candidate.get("breadcrumbs", []),
            "chunk_type": candidate.get("chunk_type", "content"),
            "text": text,
        },
        retrieval_type=candidate.get("retrieval_type", "vector"),
        document_id=source_file,
    )


class GoldenRetrievalTests(unittest.TestCase):
    def test_golden_retrieval_cases_rank_expected_source_first(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["id"]):
                ranked = rank_candidates(
                    case["question"],
                    [chunk_from_case(item) for item in case["candidates"]],
                )

                top_source = ranked[0].source_file
                if expected := case.get("expected_top_source"):
                    self.assertEqual(top_source, expected)
                if expected_contains := case.get("expected_top_source_contains"):
                    self.assertIn(expected_contains, top_source)
                for forbidden in case.get("forbidden_top_source_contains", []):
                    self.assertNotIn(forbidden, top_source)

    def test_golden_retrieval_only_answers_stay_focused(self) -> None:
        for case in load_cases():
            with self.subTest(case=case["id"]):
                ranked = rank_candidates(
                    case["question"],
                    [chunk_from_case(item) for item in case["candidates"]],
                )
                answer = build_retrieval_only_answer(case["question"], ranked)
                normalized_answer = answer.lower()

                for forbidden in case.get("forbidden_answer_terms", []):
                    self.assertNotIn(forbidden.lower(), normalized_answer)

                top_source = ranked[0].source_file
                self.assertIn(f"1. {top_source}", answer)


if __name__ == "__main__":
    unittest.main()
