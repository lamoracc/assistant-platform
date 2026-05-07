import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.services import retrieval
    from app.services.retrieval import _dedupe_near_duplicates, _merge_hits
    from app.services.retrieval_ranking import RetrievedChunk
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


BODY = (
    "Create a record by opening the form, selecting New, entering the required "
    "details, validating the values, and saving the completed record. Review "
    "the confirmation message and verify that the new record appears in the "
    "search results before continuing with the next workflow step."
)


def chunk(
    text: str,
    *,
    source_file: str,
    heading: str | None = "Create Record",
    chunk_index: int = 0,
    score: float = 0.5,
    metadata: dict | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading=heading,
        chunk_index=chunk_index,
        score=score,
        raw_score=score,
        metadata=metadata or {"text": text, "heading": heading},
        retrieval_type="vector",
        document_id=f"{source_file}:{chunk_index}",
    )


class RetrievalNearDuplicateDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = retrieval.settings
        retrieval.settings = replace(
            retrieval.settings,
            retrieval_near_duplicate_dedupe=True,
            retrieval_near_duplicate_threshold=0.92,
            retrieval_max_near_duplicates_per_group=1,
        )

    def tearDown(self) -> None:
        retrieval.settings = self.original_settings

    def test_near_identical_docs_with_different_breadcrumbs_collapse(self) -> None:
        first = chunk(
            f"---\ntopic_id: a\n---\n\n[Home] > [Area A]\n\n{BODY}",
            source_file="guide-a.md",
            score=0.7,
            metadata={"breadcrumbs": ["Home", "Area A"], "title": "Create Record"},
        )
        second = chunk(
            f"---\ntopic_id: b\nrelative_path: other\n---\n\n[Home] > [Area B]\n\n{BODY} Extra note.",
            source_file="guide-b.md",
            score=0.6,
            metadata={"breadcrumbs": ["Home", "Area B"], "title": "Create Record"},
        )

        deduped, diagnostics = _dedupe_near_duplicates("create record", [first, second])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_file, "guide-a.md")
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["filtered_reason"], "near_duplicate_content")
        self.assertGreaterEqual(diagnostics[0]["similarity"], 0.92)

    def test_query_context_term_prefers_matching_metadata_version(self) -> None:
        general = chunk(
            f"{BODY} Extra note.",
            source_file="general.md",
            score=0.9,
            metadata={
                "title": "Create Record",
                "breadcrumbs": ["Home", "General"],
                "section_group": "General",
            },
        )
        contextual = chunk(
            BODY,
            source_file="context.md",
            score=0.5,
            metadata={
                "title": "Create Record",
                "breadcrumbs": ["Home", "Alpha"],
                "section_group": "Alpha",
            },
        )

        deduped, diagnostics = _dedupe_near_duplicates(
            "create record alpha", [general, contextual]
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_file, "context.md")
        self.assertEqual(diagnostics[0]["duplicate_of"]["source_file"], "context.md")

    def test_acronym_context_term_prefers_matching_metadata_version(self) -> None:
        first_context = chunk(
            BODY,
            source_file="first-context.md",
            score=0.9,
            metadata={
                "title": "Package Codes",
                "breadcrumbs": ["Product Family", "ORS", "OCIS"],
            },
        )
        second_context = chunk(
            f"{BODY} Extra note.",
            source_file="second-context.md",
            score=0.5,
            metadata={
                "title": "Package Codes",
                "breadcrumbs": ["Product Family", "PMS"],
            },
        )

        deduped, diagnostics = _dedupe_near_duplicates(
            "configure package codes in PMS",
            [first_context, second_context],
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_file, "second-context.md")
        self.assertEqual(
            diagnostics[0]["duplicate_of"]["source_file"],
            "second-context.md",
        )

    def test_embedded_breadcrumb_context_prefers_matching_version(self) -> None:
        first_context = chunk(
            f"[Home] > [Packages - ORS/OCIS]\n\n{BODY}",
            source_file="first-context.md",
            score=0.9,
            metadata={"title": "Package Codes"},
        )
        second_context = chunk(
            f"[Home] > [Packages - PMS]\n\n{BODY} Extra note.",
            source_file="second-context.md",
            score=0.5,
            metadata={"title": "Package Codes"},
        )

        deduped, diagnostics = _dedupe_near_duplicates(
            "configure package codes in PMS",
            [first_context, second_context],
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_file, "second-context.md")
        self.assertEqual(
            diagnostics[0]["duplicate_of"]["source_file"],
            "second-context.md",
        )

    def test_same_heading_with_different_body_content_is_preserved(self) -> None:
        first = chunk(
            "Create a record by filling out required customer information and saving.",
            source_file="first.md",
            heading="Create",
        )
        second = chunk(
            "Create a dashboard by choosing widgets, layout, filters, and sharing.",
            source_file="second.md",
            heading="Create",
        )

        deduped, diagnostics = _dedupe_near_duplicates("create", [first, second])

        self.assertEqual(len(deduped), 2)
        self.assertEqual(diagnostics, [])

    def test_similar_filenames_with_different_content_are_preserved(self) -> None:
        first = chunk(
            "Configure notification delivery channels and escalation timing.",
            source_file="admin-guide.md",
        )
        second = chunk(
            "Review billing adjustments and export the monthly finance report.",
            source_file="admin-guide-copy.md",
        )

        deduped, diagnostics = _dedupe_near_duplicates("admin guide", [first, second])

        self.assertEqual(len(deduped), 2)
        self.assertEqual(diagnostics, [])

    def test_exact_duplicate_content_still_works_before_near_duplicate_content(self) -> None:
        first = chunk("Use the [Create](create.html) button.", source_file="a.md", score=0.4)
        second = chunk("Use the Create button!", source_file="b.md", score=0.8)

        exact_deduped, exact_diagnostics = _merge_hits([first, second], [])
        near_deduped, near_diagnostics = _dedupe_near_duplicates(
            "create", exact_deduped
        )

        self.assertEqual(len(exact_deduped), 1)
        self.assertEqual(exact_diagnostics[0]["filtered_reason"], "duplicate_content")
        self.assertEqual(len(near_deduped), 1)
        self.assertEqual(near_diagnostics, [])


if __name__ == "__main__":
    unittest.main()
