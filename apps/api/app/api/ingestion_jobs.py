import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import IngestionJob
from app.services.ingestion_jobs import (
    create_ingestion_job,
    get_ingestion_job,
    list_ingestion_jobs,
    request_cancel_ingestion_job,
    retry_ingestion_job,
)

router = APIRouter(prefix="/ingestion-jobs", tags=["ingestion-jobs"])


class IngestionJobCreateRequest(BaseModel):
    source_path: str = Field(..., min_length=1)
    source_name: str | None = None
    metadata: dict[str, Any] | None = None


class IngestionJobResponse(BaseModel):
    id: uuid.UUID
    source_path: str
    source_name: str | None
    status: str
    total_files: int
    processed_files: int
    failed_files: int
    current_file: str | None
    error_summary: str | None
    error_details: str | None
    cancel_requested: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@router.post("", response_model=IngestionJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    request: IngestionJobCreateRequest,
    db: Session = Depends(get_db),
) -> IngestionJobResponse:
    job = create_ingestion_job(
        db,
        source_path=request.source_path,
        source_name=request.source_name,
        metadata=request.metadata,
    )
    return _job_response(job)


@router.get("", response_model=list[IngestionJobResponse])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[IngestionJobResponse]:
    return [
        _job_response(job)
        for job in list_ingestion_jobs(db, status=status_filter, limit=limit)
    ]


@router.get("/{job_id}", response_model=IngestionJobResponse)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionJobResponse:
    job = get_ingestion_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return _job_response(job)


@router.post("/{job_id}/cancel", response_model=IngestionJobResponse)
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionJobResponse:
    job = request_cancel_ingestion_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return _job_response(job)


@router.post("/{job_id}/retry", response_model=IngestionJobResponse)
def retry_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionJobResponse:
    job = retry_ingestion_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return _job_response(job)


def _job_response(job: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        id=job.id,
        source_path=job.source_path,
        source_name=job.source_name,
        status=job.status,
        total_files=job.total_files,
        processed_files=job.processed_files,
        failed_files=job.failed_files,
        current_file=job.current_file,
        error_summary=job.error_summary,
        error_details=job.error_details,
        cancel_requested=job.cancel_requested,
        metadata=job.job_metadata or {},
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
