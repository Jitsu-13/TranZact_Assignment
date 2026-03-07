"""
Celery Application Configuration.

Uses Redis as both broker and result backend (leveraging existing Redis
from the Django monolith infrastructure). Separate Redis DBs avoid
key collisions with Django's cache.
"""

from celery import Celery
from src.config import Config

celery_app = Celery(
    "pdf_service",
    broker=Config.CELERY_BROKER_URL,
    backend=Config.CELERY_RESULT_BACKEND,
    include=["src.tasks.pdf_tasks", "src.tasks.maintenance_tasks"],
)

celery_app.conf.update(
    # Limit prefetch to 1 so tasks are distributed evenly across workers
    worker_prefetch_multiplier=1,
    # Acknowledge tasks only after completion (prevents lost tasks on crash)
    task_acks_late=True,
    # Reject tasks back to queue if worker is killed mid-task
    task_reject_on_worker_lost=True,
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Result expiry (1 hour — bulk job status lives here)
    result_expires=3600,
    # Task time limits
    task_soft_time_limit=300,  # 5 min soft limit
    task_time_limit=360,       # 6 min hard limit
    # Retry policy for broker connection
    broker_connection_retry_on_startup=True,
    # Periodic tasks (Celery beat)
    beat_schedule={
        "cleanup-expired-pdfs": {
            "task": "cleanup_expired_pdfs",
            "schedule": 3600.0,  # Every hour
        },
    },
)
