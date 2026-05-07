import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.services.retrieval_ranking import RetrievedChunk, rank_candidates


def chunk(
    text: str,
    *,
    title: str,
    heading: str,
    source_file: str,
    score: float = 0.4,
    breadcrumbs: list[str] | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        document=source_file,
        source_file=source_file,
        heading=heading,
        chunk_index=0,
        score=score,
        raw_score=score,
        retrieval_stage_score=score,
        metadata={
            "title": title,
            "heading": heading,
            "breadcrumbs": breadcrumbs or [],
            "chunk_type": "content",
            "text": text,
        },
        document_id=source_file,
    )


class MetadataAwareRankingTests(unittest.TestCase):
    def test_package_codes_not_displaced_by_other_codes(self) -> None:
        target = chunk(
            "Create package codes for reusable bundled services.",
            title="Package Codes",
            heading="Package Codes",
            source_file="package-codes.md",
        )
        source_codes = chunk(
            "Maintain source codes for lead attribution and channel tracking.",
            title="Source Codes",
            heading="Source Codes",
            source_file="source-codes.md",
            score=0.55,
        )
        reservation_codes = chunk(
            "Configure reservation codes used by booking workflows.",
            title="Reservation Codes",
            heading="Reservation Codes",
            source_file="reservation-codes.md",
            score=0.55,
        )

        ranked = rank_candidates("package codes", [source_codes, reservation_codes, target])

        self.assertEqual(ranked[0].source_file, "package-codes.md")
        self.assertIn("query_phrase_in_metadata", ranked[0].ranking_reason)

    def test_body_only_phrase_does_not_outrank_exact_heading_match(self) -> None:
        target = chunk(
            "Create and maintain reusable bundled-service codes.",
            title="Package Codes",
            heading="Package Codes",
            source_file="package-codes.md",
            score=0.4,
        )
        body_only = chunk(
            (
                "Use the package codes field during export mapping to align "
                "external records with downstream configuration."
            ),
            title="Export Mapping",
            heading="Export Mapping",
            source_file="export-mapping.md",
            score=0.8,
        )

        ranked = rank_candidates("package codes", [body_only, target])

        self.assertEqual(ranked[0].source_file, "package-codes.md")
        self.assertIn("query_phrase_in_metadata", ranked[0].ranking_reason)
        self.assertNotIn("query_phrase_in_metadata", ranked[1].ranking_reason)

    def test_generic_acronym_context_term_boosts_metadata_match(self) -> None:
        contextual = chunk(
            "Create and maintain reusable bundled-service codes.",
            title="Package Codes",
            heading="Package Codes",
            source_file="package-codes-context.md",
            score=0.4,
            breadcrumbs=["Product Family", "PMS"],
        )
        other_context = chunk(
            "Create and maintain reusable bundled-service codes.",
            title="Package Codes",
            heading="Package Codes",
            source_file="package-codes-other-context.md",
            score=0.45,
            breadcrumbs=["Product Family", "ORS", "OCIS"],
        )

        ranked = rank_candidates(
            "How do I configure package codes in PMS?",
            [other_context, contextual],
        )

        self.assertEqual(ranked[0].source_file, "package-codes-context.md")
        self.assertIn("query_context_in_metadata", ranked[0].ranking_reason)

    def test_context_terms_do_not_act_as_topical_metadata_terms(self) -> None:
        context_only = chunk(
            "General product configuration and links.",
            title="PMS Administration",
            heading="PMS Administration",
            source_file="pms-admin.md",
            score=0.65,
        )
        topical = chunk(
            "Create and maintain reusable bundled-service codes.",
            title="Package Codes",
            heading="Package Codes",
            source_file="package-codes.md",
            score=0.45,
        )

        ranked = rank_candidates(
            "How do I configure package codes in PMS?",
            [context_only, topical],
        )

        self.assertEqual(ranked[0].source_file, "package-codes.md")
        ranked_context_only = next(
            item for item in ranked if item.source_file == "pms-admin.md"
        )
        self.assertNotIn(
            "strong_terms_in_metadata",
            ranked_context_only.ranking_reason or [],
        )

    def test_rate_code_query_prefers_rate_code_documents(self) -> None:
        target = chunk(
            "A rate code controls pricing rules, availability, and selling behavior.",
            title="Rate Code Setup",
            heading="Rate Code",
            source_file="rate-code.md",
        )
        generic_rates = chunk(
            "Use rates to define pricing for products and services.",
            title="Rates",
            heading="Rates",
            source_file="rates.md",
            score=0.6,
        )

        ranked = rank_candidates("rate code setup", [generic_rates, target])

        self.assertEqual(ranked[0].source_file, "rate-code.md")

    def test_reservation_reinstate_permission_prefers_exact_permission(self) -> None:
        target = chunk(
            "This permission allows a user to reinstate a previously cancelled reservation.",
            title="Reservation Reinstate Permission",
            heading="Reservation Reinstate Permission",
            source_file="reservation-reinstate-permission.md",
        )
        broad = chunk(
            "Reservation permissions control many booking and cancellation actions.",
            title="Reservation Permissions",
            heading="Permissions",
            source_file="reservation-permissions.md",
            score=0.6,
        )

        ranked = rank_candidates("reservation reinstate permission", [broad, target])

        self.assertEqual(ranked[0].source_file, "reservation-reinstate-permission.md")

    def test_housekeeping_task_sheets_prefers_task_assignment_docs(self) -> None:
        target = chunk(
            "Task sheets assign rooms and work items to housekeeping attendants.",
            title="Housekeeping Task Sheets",
            heading="Task Assignment",
            source_file="housekeeping-task-sheets.md",
            breadcrumbs=["Operations", "Housekeeping"],
        )
        generic = chunk(
            "Housekeeping settings configure room status, service rules, and inspections.",
            title="Housekeeping Settings",
            heading="Housekeeping",
            source_file="housekeeping-settings.md",
            score=0.6,
        )

        ranked = rank_candidates("housekeeping task sheets", [generic, target])

        self.assertEqual(ranked[0].source_file, "housekeeping-task-sheets.md")

    def test_russian_query_keeps_retrieved_english_candidate(self) -> None:
        target = chunk(
            "Task sheets assign rooms and work items to housekeeping attendants.",
            title="Housekeeping Task Sheets",
            heading="Task Assignment",
            source_file="housekeeping-task-sheets.md",
            score=0.7,
        )

        ranked = rank_candidates(
            "\u041a\u0430\u043a \u043d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c "
            "task sheets \u0434\u043b\u044f housekeeping?",
            [target],
        )

        self.assertEqual(ranked[0].source_file, "housekeeping-task-sheets.md")
        self.assertTrue(ranked[0].ranking_details)


if __name__ == "__main__":
    unittest.main()
