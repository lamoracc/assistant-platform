import logging
import time

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.services.ingestion_jobs import claim_next_pending_job, run_ingestion_job
from app.services.qdrant_store import init_qdrant_collection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    init_db()
    init_qdrant_collection()
    logger.info(
        "ingestion worker started poll_seconds=%s",
        settings.ingestion_worker_poll_seconds,
    )

    while True:
        with SessionLocal() as db:
            job = claim_next_pending_job(db)

        if not job:
            time.sleep(settings.ingestion_worker_poll_seconds)
            continue

        logger.info("claimed ingestion job id=%s source_path=%s", job.id, job.source_path)
        run_ingestion_job(job.id)


if __name__ == "__main__":
    main()
