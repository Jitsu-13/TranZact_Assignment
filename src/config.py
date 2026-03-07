import os
from dotenv import load_dotenv

load_dotenv()

# Project root directory (one level up from src/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # PDF Storage — absolute path based on project root
    PDF_STORAGE_DIR = os.getenv("PDF_STORAGE_DIR", os.path.join(BASE_DIR, "generated_pdfs"))
    PDF_RETENTION_HOURS = int(os.getenv("PDF_RETENTION_HOURS", "24"))

    # Playwright browser pool
    BROWSER_POOL_SIZE = int(os.getenv("PLAYWRIGHT_BROWSER_POOL_SIZE", "3"))
    RENDER_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_RENDER_TIMEOUT_MS", "30000"))

    # Large document chunking threshold (line items)
    CHUNK_THRESHOLD = int(os.getenv("CHUNK_THRESHOLD", "200"))
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "100"))

    # Celery
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
    CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "2"))

    # Rate Limiting
    RATE_LIMIT_SINGLE = os.getenv("RATE_LIMIT_SINGLE", "100/minute")
    RATE_LIMIT_BULK = os.getenv("RATE_LIMIT_BULK", "10/minute")

    # Server
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "5000"))

    # Max bulk documents per request
    MAX_BULK_DOCUMENTS = 100
