import logging
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.document import IngestionJob
from app.services.ingestion import IngestionCanceled, IngestionError, ingest_help_folder

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "canceled"}
ACTIVE_STATUSES = {"pending", "running"}


def create_ingestion_job(
    db: Session,
    *,
    source_path: str,
    source_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> IngestionJob:
    job = IngestionJob(
        source_path=source_path,
        source_name=source_name,
        status="pending",
        job_metadata=metadata or {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_ingestion_jobs(
    db: Session,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[IngestionJob]:
    statement = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)
    if status:
        statement = (
            select(IngestionJob)
            .where(IngestionJob.status == status)
            .order_by(IngestionJob.created_at.desc())
            .limit(limit)
        )
    return list(db.scalars(statement).all())


def get_ingestion_job(db: Session, job_id: uuid.UUID) -> IngestionJob | None:
    return db.get(IngestionJob, job_id)


def request_cancel_ingestion_job(
    db: Session,
    job_id: uuid.UUID,
) -> IngestionJob | None:
    job = db.get(IngestionJob, job_id)
    if not job:
        return None
    if job.status in TERMINAL_STATUSES:
        return job
    job.cancel_requested = True
    job.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    return job


def retry_ingestion_job(db: Session, job_id: uuid.UUID) -> IngestionJob | None:
    job = db.get(IngestionJob, job_id)
    if not job:
        return None
    if job.status not in {"failed", "canceled"}:
        return job
    job.status = "pending"
    job.cancel_requested = False
    job.error_summary = None
    job.error_details = None
    job.current_file = None
    job.total_files = 0
    job.processed_files = 0
    job.failed_files = 0
    job.started_at = None
    job.finished_at = None
    job.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    return job


def claim_next_pending_job(db: Session) -> IngestionJob | None:
    job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.status == "pending")
        .order_by(IngestionJob.created_at.asc())
        .limit(1)
    )
    if not job:
        return None
    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    return job


def run_ingestion_job(job_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            logger.warning("Ingestion job not found: %s", job_id)
            return

        if job.status == "pending":
            job.status = "running"
            job.started_at = datetime.now(UTC)
            db.commit()
            db.refresh(job)

        if job.status != "running":
            logger.info("Skipping ingestion job %s with status=%s", job.id, job.status)
            return

        def progress_callback(event: dict[str, Any]) -> None:
            with SessionLocal() as progress_db:
                _apply_progress_event(progress_db, job.id, event)

        def cancel_check() -> bool:
            db.expire(job)
            db.refresh(job)
            return bool(job.cancel_requested)

        try:
            stats = ingest_help_folder(
                db=db,
                root_path=job.source_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            db.refresh(job)
            if job.cancel_requested or stats.get("canceled"):
                job.status = "canceled"
                job.error_summary = "Cancellation requested"
            else:
                job.status = "completed"
                job.error_summary = None
                job.error_details = None
            job.current_file = None
            job.finished_at = datetime.now(UTC)
            job.updated_at = datetime.now(UTC)
            job.job_metadata = {**(job.job_metadata or {}), "last_stats": stats}
            db.commit()
        except IngestionCanceled as exc:
            db.rollback()
            _finish_job(
                db,
                job.id,
                status="canceled",
                error_summary=str(exc),
                error_details=None,
            )
        except Exception as exc:
            db.rollback()
            logger.exception("Ingestion job failed: %s", job.id)
            _finish_job(
                db,
                job.id,
                status="failed",
                error_summary=str(exc),
                error_details=traceback.format_exc(),
            )


def _apply_progress_event(
    db: Session,
    job_id: uuid.UUID,
    event: dict[str, Any],
) -> None:
    job = db.get(IngestionJob, job_id)
    if not job:
        return
    if "total_files" in event:
        job.total_files = int(event.get("total_files") or 0)
    if "processed_files" in event:
        job.processed_files = int(event.get("processed_files") or 0)
    if "failed_files" in event:
        job.failed_files = int(event.get("failed_files") or 0)
    if "current_file" in event:
        job.current_file = event.get("current_file")
    if event.get("error_summary"):
        job.error_summary = str(event["error_summary"])[:1024]
    if event.get("error_details"):
        job.error_details = str(event["error_details"])
    if event.get("metadata"):
        job.job_metadata = {**(job.job_metadata or {}), **event["metadata"]}
    job.updated_at = datetime.now(UTC)
    db.commit()


def _finish_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    status: str,
    error_summary: str | None,
    error_details: str | None,
) -> None:
    job = db.get(IngestionJob, job_id)
    if not job:
        return
    job.status = status
    job.current_file = None
    job.error_summary = error_summary[:1024] if error_summary else None
    job.error_details = error_details
    job.finished_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    db.commit()
