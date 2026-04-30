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
    chunk_type: str = "content",
    score: float = 0.4,
    document_id: str = "doc",
    chunk_index: int = 0,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        document=f"{title}.md",
        source_file=f"{title}.md",
        heading=heading,
        chunk_index=chunk_index,
        score=score,
        raw_score=score,
        retrieval_type="keyword",
        document_id=document_id,
        metadata={
            "title": title,
            "chunk_type": chunk_type,
            "text": text,
            "heading": heading,
        },
    )


class GenericRerankingTests(unittest.TestCase):
    def assert_domain_query_prefers_relevant_chunk(
        self,
        question: str,
        title: str,
        heading: str,
        body: str,
    ) -> None:
        relevant = chunk(
            body,
            title=title,
            heading=heading,
            chunk_type="procedure",
            document_id=f"{title}-relevant",
            chunk_index=0,
        )
        noisy = chunk(
            "Previous | Next | Contents | Index",
            title="Navigation",
            heading="Contents",
            chunk_type="navigation",
            score=0.5,
            document_id=f"{title}-navigation",
            chunk_index=1,
        )
        broad = chunk(
            ("General reference information. " * 250).strip(),
            title="General Reference",
            heading="Reference",
            chunk_type="reference",
            score=0.45,
            document_id=f"{title}-reference",
            chunk_index=2,
        )

        ranked = rank_candidates(question, [noisy, broad, relevant])

        self.assertEqual(ranked[0].document_id, relevant.document_id)
        self.assertTrue(ranked[0].ranking_reason)
        self.assertIn("preferred_chunk_type:procedure", ranked[0].ranking_reason)

    def test_packages_domain(self) -> None:
        self.assert_domain_query_prefers_relevant_chunk(
            "configure package codes",
            "Package Configuration",
            "Configure Package Codes",
            "Select Configuration and create Package Codes for OPERA PMS packages.",
        )

    def test_reservations_domain(self) -> None:
        self.assert_domain_query_prefers_relevant_chunk(
            "create reservation",
            "Reservations",
            "Create Reservation",
            "Select New Reservation, enter guest stay details, and save the reservation.",
        )

    def test_profiles_domain(self) -> None:
        self.assert_domain_query_prefers_relevant_chunk(
            "update guest profile",
            "Profiles",
            "Update Guest Profile",
            "Open the guest Profile, edit address and membership details, then save.",
        )

    def test_cashiering_domain(self) -> None:
        self.assert_domain_query_prefers_relevant_chunk(
            "post payment cashiering",
            "Cashiering",
            "Post Payment",
            "In Cashiering, select Payment, enter the payment method, and post.",
        )

    def test_reports_domain(self) -> None:
        self.assert_domain_query_prefers_relevant_chunk(
            "run production report",
            "Reports",
            "Run Production Report",
            "Open Reports, select the production report, choose parameters, and run.",
        )


if __name__ == "__main__":
    unittest.main()
