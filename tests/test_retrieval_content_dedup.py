import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.services.retrieval import _merge_hits
    from app.services.retrieval_ranking import RetrievedChunk
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


def chunk(
    text: str,
    *,
    source_file: str,
    heading: str | None = "Shared Heading",
    chunk_index: int = 0,
    score: float = 0.5,
    document_id: str | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading=heading,
        chunk_index=chunk_index,
        score=score,
        raw_score=score,
        metadata={"text": text, "heading": heading},
        retrieval_type="vector",
        document_id=document_id or source_file,
    )


class RetrievalContentDedupTests(unittest.TestCase):
    def test_same_content_in_different_files_collapses(self) -> None:
        lower = chunk(
            "Use the [Create](create.html) button to create the item.",
            source_file="source-a.md",
            score=0.4,
        )
        higher = chunk(
            "Use the Create button to create the item!",
            source_file="source-b.md",
            score=0.8,
        )

        merged, diagnostics = _merge_hits([lower, higher], [])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_file, "source-b.md")
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["filtered_reason"], "duplicate_content")
        self.assertEqual(
            diagnostics[0]["duplicate_of"],
            {
                "source_file": "source-b.md",
                "heading": "Shared Heading",
                "chunk_index": 0,
            },
        )

    def test_same_filename_pattern_with_different_content_does_not_collapse(self) -> None:
        first = chunk(
            "Create an invoice from the billing screen.",
            source_file="guide.md",
            chunk_index=0,
        )
        second = chunk(
            "Configure notification recipients in settings.",
            source_file="guide_1.md",
            chunk_index=0,
        )

        merged, diagnostics = _merge_hits([first, second], [])

        self.assertEqual(len(merged), 2)
        self.assertEqual(diagnostics, [])

    def test_same_heading_with_different_content_does_not_collapse(self) -> None:
        first = chunk(
            "Create a record by selecting New and saving the form.",
            source_file="workflow-a.md",
            heading="Create",
            chunk_index=0,
        )
        second = chunk(
            "Create a report by choosing the date range and output format.",
            source_file="workflow-b.md",
            heading="Create",
            chunk_index=0,
        )

        merged, diagnostics = _merge_hits([first, second], [])

        self.assertEqual(len(merged), 2)
        self.assertEqual(diagnostics, [])

    def test_different_chunks_from_same_document_are_preserved(self) -> None:
        first = chunk(
            "Open the profile tab and update the mailing address.",
            source_file="user-guide.md",
            chunk_index=0,
            document_id="doc-1",
        )
        second = chunk(
            "Open the billing tab and update the payment method.",
            source_file="user-guide.md",
            chunk_index=1,
            document_id="doc-1",
        )

        merged, diagnostics = _merge_hits([first, second], [])

        self.assertEqual(len(merged), 2)
        self.assertEqual({item.chunk_index for item in merged}, {0, 1})
        self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()
