import sys
import unittest
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "apps" / "api"))

try:
    from app.models.document import IngestionJob
    from app.services import ingestion_jobs
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API dependencies are not installed: {exc}") from exc


class FakeDb:
    def __init__(self, job: IngestionJob | None = None) -> None:
        self.job = job
        self.added: list[object] = []
        self.commits = 0

    def add(self, item: object) -> None:
        self.added.append(item)
        if isinstance(item, IngestionJob):
            self.job = item
            if self.job.id is None:
                self.job.id = uuid.uuid4()

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, item: object) -> None:
        return None

    def get(self, model, item_id):
        if self.job and self.job.id == item_id:
            return self.job
        return None


class IngestionJobServiceTests(unittest.TestCase):
    def test_create_job_defaults_to_pending(self) -> None:
        db = FakeDb()

        job = ingestion_jobs.create_ingestion_job(
            db,
            source_path="/data/docs",
            source_name="docs",
            metadata={"source_type": "documentation"},
        )

        self.assertEqual(job.status, "pending")
        self.assertEqual(job.source_path, "/data/docs")
        self.assertEqual(job.source_name, "docs")
        self.assertEqual(job.job_metadata["source_type"], "documentation")
        self.assertEqual(db.commits, 1)

    def test_cancel_sets_flag_without_terminal_status_change(self) -> None:
        job = IngestionJob(id=uuid.uuid4(), source_path="/data/docs", status="running")
        db = FakeDb(job)

        updated = ingestion_jobs.request_cancel_ingestion_job(db, job.id)

        self.assertIsNotNone(updated)
        self.assertTrue(updated.cancel_requested)
        self.assertEqual(updated.status, "running")
        self.assertEqual(db.commits, 1)

    def test_retry_resets_failed_job_to_pending(self) -> None:
        job = IngestionJob(
            id=uuid.uuid4(),
            source_path="/data/docs",
            status="failed",
            cancel_requested=True,
            processed_files=5,
            failed_files=1,
            error_summary="boom",
            error_details="traceback",
        )
        db = FakeDb(job)

        updated = ingestion_jobs.retry_ingestion_job(db, job.id)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "pending")
        self.assertFalse(updated.cancel_requested)
        self.assertEqual(updated.processed_files, 0)
        self.assertEqual(updated.failed_files, 0)
        self.assertIsNone(updated.error_summary)
        self.assertIsNone(updated.error_details)


if __name__ == "__main__":
    unittest.main()
