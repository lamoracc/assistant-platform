import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.api import chat
    from app.services.prompt_builder import build_retrieval_only_answer
    from app.services.retrieval_ranking import RetrievedChunk
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


def chunk(
    text: str,
    *,
    source_file: str = "source_codes.md",
    heading: str = "Source Codes",
    score: float = 0.8,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading=heading,
        chunk_index=0,
        score=score,
        raw_score=score,
        metadata={"title": heading, "text": text},
        retrieval_type="vector",
        document_id=source_file,
        ranking_reason=["query_phrase_in_metadata"],
    )


@dataclass
class FakeRetrieval:
    chunks: list[RetrievedChunk]
    diagnostics: dict


class FakeProvider:
    def generate(self, messages: list[dict[str, str]]) -> str | None:
        return None


SOURCE_CODES_TEXT = (
    "[Home] > [Configuration] > Source Codes\n\n"
    "Source codes are used to track where reservations and business come from, "
    "such as mail, telephone, fax, central reservations, or travel agency. "
    "They are attached to reservation records and grouped into source groups "
    "for reporting. Additional configuration controls how these values appear "
    "in search and reporting workflows."
)


class ChatResponseFormattingTests(unittest.TestCase):
    def test_debug_false_does_not_include_diagnostics(self) -> None:
        fake_retrieval = FakeRetrieval(
            chunks=[chunk(SOURCE_CODES_TEXT)],
            diagnostics={"results": [{"document": "source_codes.md"}]},
        )

        with (
            patch.object(chat, "retrieve_chunks", return_value=fake_retrieval),
            patch.object(chat, "get_llm_provider", return_value=FakeProvider()),
        ):
            response = chat.query_chat(
                chat.ChatQueryRequest(question="What are source codes?", debug=False),
                db=None,
            )

        self.assertIsNone(response.diagnostics)
        self.assertIsNone(response.sources[0].ranking_reason)

    def test_debug_true_still_includes_diagnostics(self) -> None:
        fake_retrieval = FakeRetrieval(
            chunks=[chunk(SOURCE_CODES_TEXT)],
            diagnostics={"results": [{"document": "source_codes.md"}]},
        )

        with (
            patch.object(chat, "retrieve_chunks", return_value=fake_retrieval),
            patch.object(chat, "get_llm_provider", return_value=FakeProvider()),
        ):
            response = chat.query_chat(
                chat.ChatQueryRequest(question="What are source codes?", debug=True),
                db=None,
            )

        self.assertEqual(response.diagnostics, fake_retrieval.diagnostics)
        self.assertEqual(response.sources[0].ranking_reason, ["query_phrase_in_metadata"])

    def test_retrieval_only_answer_is_compact_and_structured(self) -> None:
        answer = build_retrieval_only_answer(
            "What are source codes?",
            [chunk(SOURCE_CODES_TEXT)],
        )

        self.assertIn("Short answer:", answer)
        self.assertIn("Relevant facts:", answer)
        self.assertIn("Top sources:", answer)
        self.assertIn("source_codes.md — Source Codes", answer)
        self.assertNotIn("[Home] > [Configuration]", answer)
        self.assertLess(len(answer), 1200)

    def test_source_excerpts_are_truncated(self) -> None:
        long_text = (
            "Transaction codes define how financial activity is categorized "
            "and posted to accounts with configuration details for ledgers, "
            "taxes, routing, adjustments, cashiering, reporting, permissions, "
            "exports, validation, and operational workflows " * 12
        )

        answer = build_retrieval_only_answer(
            "How do I configure transaction codes?",
            [chunk(long_text, source_file="transaction_codes.md", heading="Transaction Codes")],
        )

        self.assertIn("...", answer)
        self.assertLess(len(answer), 1400)

    def test_duplicate_source_content_is_not_repeated_in_answer(self) -> None:
        duplicate_text = (
            "Market codes classify business for reporting and analysis. "
            "They can be grouped for reporting workflows."
        )
        near_duplicate_text = (
            "Market codes classify business for reporting and analysis. "
            "They are grouped for reporting workflows."
        )

        answer = build_retrieval_only_answer(
            "How do I configure market codes?",
            [
                chunk(duplicate_text, source_file="market_codes.md", heading="Market Codes"),
                chunk(near_duplicate_text, source_file="market_codes_1.md", heading="Market Codes"),
            ],
        )

        self.assertIn("1. market_codes.md — Market Codes", answer)
        self.assertNotIn("2. market_codes_1.md — Market Codes", answer)
        self.assertEqual(answer.count("Market codes classify business"), 2)

    def test_plain_breadcrumb_lines_are_not_used_as_facts(self) -> None:
        text = (
            "Welcome > Configuration Topics > Rate Management Topics > Market Codes\n\n"
            "Market codes classify business for reporting and analysis."
        )

        answer = build_retrieval_only_answer(
            "How do I configure market codes?",
            [chunk(text, source_file="market_codes.md", heading="Market Codes")],
        )

        self.assertNotIn("Welcome > Configuration Topics", answer)
        self.assertIn("Market codes classify business", answer)

    def test_answer_facts_use_primary_source_only(self) -> None:
        package_text = (
            "Package setup involves defining package elements, or codes, and "
            "collecting these elements into groups, or packages. Each package "
            "code is set up separately before the package group is attached to "
            "a rate code or reservation."
        )
        source_text = (
            "Just like market codes, source codes are attached to reservation "
            "records in order to track how reservations come to the property. "
            "All source codes can be distributed to external systems."
        )

        answer = build_retrieval_only_answer(
            "How do I configure package codes in PMS?",
            [
                chunk(package_text, source_file="package_codes.md", heading="Package Codes"),
                chunk(source_text, source_file="source_codes.md", heading="Source Codes"),
            ],
        )

        self.assertIn("Package setup involves defining package elements", answer)
        self.assertIn("package_codes.md — Package Codes", answer)
        self.assertIn("source_codes.md — Source Codes", answer)
        self.assertNotIn("source codes are attached", answer.lower())
        self.assertNotIn("reservation records", answer.lower())


if __name__ == "__main__":
    unittest.main()
