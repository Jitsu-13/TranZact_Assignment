"""
Celery Tasks for PDF Generation.

Handles asynchronous bulk PDF generation. Key design:

1. DATA CONSISTENCY: The Django monolith sends a complete data snapshot
   in the request payload. This means all 100 documents in a bulk request
   use the SAME data captured at request time — no stale data from DB
   changes during the 3+ minutes of rendering.

2. PROGRESS TRACKING: Each document updates Celery task state with
   progress info. The SSE endpoint polls this to push updates to clients.

3. RETRY LOGIC: Individual document failures don't abort the entire batch.
   Failed documents are retried up to 3 times, then recorded as failures.

4. MEMORY SAFETY: Documents are processed sequentially within a worker
   (concurrency=2 workers) to keep memory bounded at ~800MB peak.
"""

import time
from src.celery_app import celery_app
from src.services.pdf_generator import generate_pdf
from src.services.hash_registry import register_hash
from src.services.storage import create_zip_archive
from src.logger import logger


@celery_app.task(bind=True, name="generate_single_pdf")
def generate_single_pdf_task(self, doc_type: str, data: dict) -> dict:
    """Async task for single PDF generation (used when queue is needed)."""
    try:
        result = generate_pdf(doc_type, data)
        register_hash(result["file_id"], result["sha256_hash"], {
            "doc_type": doc_type,
            "document_number": data.get("document_number", "unknown"),
        })
        return result
    except Exception as exc:
        logger.error(f"Single PDF generation failed: {exc}")
        raise self.retry(exc=exc, countdown=5, max_retries=2)


@celery_app.task(bind=True, name="generate_bulk_pdfs")
def generate_bulk_pdfs_task(self, documents: list[dict]) -> dict:
    """Process a bulk PDF generation request.

    Args:
        documents: List of dicts, each with 'doc_type' and 'data' keys.
            The data is a SNAPSHOT — captured at request time by Django
            to prevent stale-data issues.

    Returns:
        dict with job summary: completed files, failures, ZIP path.
    """
    total = len(documents)
    completed = []
    failures = []

    for idx, doc in enumerate(documents):
        doc_type = doc["doc_type"]
        data = doc["data"]
        doc_id = data.get("document_number", f"doc_{idx + 1}")

        # Update progress for SSE consumers
        self.update_state(
            state="PROGRESS",
            meta={
                "current": idx + 1,
                "total": total,
                "percent": int(((idx + 1) / total) * 100),
                "current_document": doc_id,
                "completed": len(completed),
                "failures": len(failures),
            },
        )

        # Attempt generation with retry
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                result = generate_pdf(doc_type, data)
                register_hash(result["file_id"], result["sha256_hash"], {
                    "doc_type": doc_type,
                    "document_number": doc_id,
                    "bulk_job_id": self.request.id,
                })
                completed.append({
                    "file_id": result["file_id"],
                    "filename": f"{doc_type}_{doc_id}.pdf",
                    "sha256_hash": result["sha256_hash"],
                    "file_size": result["file_size"],
                })
                logger.info(f"Bulk [{idx + 1}/{total}] {doc_id}: OK")
                break
            except Exception as e:
                logger.warning(
                    f"Bulk [{idx + 1}/{total}] {doc_id}: attempt {attempt} failed: {e}"
                )
                if attempt == max_retries:
                    failures.append({
                        "document_number": doc_id,
                        "error": str(e),
                    })
                else:
                    time.sleep(2)  # Brief pause before retry

    # Create ZIP archive of all successful PDFs
    zip_path = None
    if completed:
        zip_path = create_zip_archive(completed)

    summary = {
        "total": total,
        "completed": len(completed),
        "failed": len(failures),
        "files": completed,
        "failures": failures,
        "zip_path": zip_path,
    }

    logger.info(
        f"Bulk job {self.request.id} complete: "
        f"{len(completed)}/{total} succeeded, {len(failures)} failed"
    )
    return summary
