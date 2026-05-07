import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.models.document import Document
    from app.services import ingestion
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


class ScalarResult:
    def all(self) -> list:
        return []


class FakeDb:
    def __init__(self, existing: Document | None = None) -> None:
        self.existing = existing
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, statement) -> Document | None:
        return self.existing

    def add(self, item: object) -> None:
        self.added.append(item)

    def flush(self) -> None:
        for item in self.added:
            if isinstance(item, Document) and item.id is None:
                item.id = uuid.uuid4()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def refresh(self, item: object) -> None:
        return None

    def scalars(self, statement) -> ScalarResult:
        return ScalarResult()


class FolderIngestionBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = ingestion.settings
        self.original_embed_texts = ingestion.embed_texts
        self.original_upsert = ingestion.upsert_document_chunks

    def tearDown(self) -> None:
        ingestion.settings = self.original_settings
        ingestion.embed_texts = self.original_embed_texts
        ingestion.upsert_document_chunks = self.original_upsert

    def test_folder_import_embeds_chunks_across_documents_in_one_batch(self) -> None:
        embed_calls: list[list[str]] = []
        upsert_counts: list[int] = []
        ingestion.settings = replace(
            ingestion.settings,
            ingestion_embed_batch_chunks=3,
            embedding_dimensions=4,
        )

        def fake_embed_texts(texts: list[str]) -> list[list[float]]:
            embed_calls.append(texts)
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def fake_upsert(points: list[object]) -> None:
            upsert_counts.append(len(points))

        ingestion.embed_texts = fake_embed_texts
        ingestion.upsert_document_chunks = fake_upsert

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(3):
                (root / f"doc-{index}.md").write_text(
                    f"# Topic {index}\n\nUseful content for document {index}.",
                    encoding="utf-8",
                )

            stats = ingestion.ingest_help_folder(FakeDb(), str(root))

        self.assertEqual(len(embed_calls), 1)
        self.assertEqual(len(embed_calls[0]), 3)
        self.assertEqual(upsert_counts, [3])
        self.assertEqual(stats["documents_ingested"], 3)
        self.assertEqual(stats["total_chunks"], 3)
        self.assertEqual(stats["batch_flushes"], 1)

    def test_folder_import_skips_duplicate_without_reembedding(self) -> None:
        existing = Document(
            id=uuid.uuid4(),
            filename="existing.md",
            source_path="existing.md",
            file_hash="hash",
            content_type="text/markdown",
            source_type="text",
            chunk_count=1,
        )
        embed_calls: list[list[str]] = []
        ingestion.embed_texts = lambda texts: embed_calls.append(texts) or []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "existing.md").write_text("# Existing\n\nAlready indexed.", encoding="utf-8")
            stats = ingestion.ingest_help_folder(FakeDb(existing=existing), str(root))

        self.assertEqual(embed_calls, [])
        self.assertEqual(stats["documents_skipped"], 1)
        self.assertEqual(stats["skipped_duplicate"], 1)
        self.assertEqual(stats["documents_ingested"], 0)

    def test_empty_document_does_not_rollback_pending_batch(self) -> None:
        embed_calls: list[list[str]] = []
        ingestion.settings = replace(
            ingestion.settings,
            ingestion_embed_batch_chunks=2,
            embedding_dimensions=4,
        )
        ingestion.embed_texts = lambda texts: embed_calls.append(texts) or [
            [1.0, 0.0, 0.0, 0.0] for _ in texts
        ]
        ingestion.upsert_document_chunks = lambda points: None

        db = FakeDb()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "first.md").write_text("# First\n\nUseful content.", encoding="utf-8")
            (root / "empty.md").write_text("---\ntitle: Empty\n---\n\n![](icon.gif)", encoding="utf-8")
            (root / "second.md").write_text("# Second\n\nMore useful content.", encoding="utf-8")

            stats = ingestion.ingest_help_folder(db, str(root))

        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(len(embed_calls), 1)
        self.assertEqual(len(embed_calls[0]), 2)
        self.assertEqual(stats["documents_ingested"], 2)
        self.assertEqual(stats["skipped_empty"], 1)


if __name__ == "__main__":
    unittest.main()
