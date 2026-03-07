"""Periodic maintenance tasks."""

from src.celery_app import celery_app
from src.services.storage import cleanup_expired_files
from src.logger import logger


@celery_app.task(name="cleanup_expired_pdfs")
def cleanup_expired_pdfs_task():
    """Remove PDFs and ZIPs older than the configured retention period."""
    logger.info("Running scheduled PDF cleanup")
    cleanup_expired_files()
