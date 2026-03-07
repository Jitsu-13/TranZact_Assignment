"""
Hash Registry for Tamper Evidence.

Stores SHA-256 hashes of generated PDFs to allow verification that a
document has not been modified after generation. Uses Redis for fast
lookups with a TTL matching the PDF retention period.

In a production setup, these hashes would also be persisted to PostgreSQL
for long-term compliance/audit purposes. Redis serves as the hot cache.

Verification flow:
1. PDF generated → SHA-256 hash computed → stored in Redis + DB
2. User requests verification → recompute hash of file on disk
3. Compare with stored hash → match = tamper-free, mismatch = tampered
"""

import hashlib
import json
import time

import redis

from src.config import Config
from src.logger import logger

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
    return _redis


def register_hash(file_id: str, sha256_hash: str, metadata: dict = None):
    """Store the hash of a generated PDF for tamper verification.

    Args:
        file_id: Unique identifier for the PDF.
        sha256_hash: SHA-256 hex digest of the PDF bytes.
        metadata: Optional dict with doc_type, generated_at, etc.
    """
    r = _get_redis()
    record = {
        "sha256": sha256_hash,
        "created_at": time.time(),
        "metadata": json.dumps(metadata or {}),
    }
    ttl_seconds = Config.PDF_RETENTION_HOURS * 3600
    r.hset(f"pdf_hash:{file_id}", mapping=record)
    r.expire(f"pdf_hash:{file_id}", ttl_seconds)
    logger.info(f"Hash registered for {file_id}: {sha256_hash[:16]}...")


def verify_hash(file_id: str, pdf_bytes: bytes) -> dict:
    """Verify a PDF has not been tampered with.

    Args:
        file_id: The file identifier to look up.
        pdf_bytes: The actual bytes of the PDF to verify.

    Returns:
        dict with 'verified' (bool), 'stored_hash', 'computed_hash', 'match'.
    """
    r = _get_redis()
    record = r.hgetall(f"pdf_hash:{file_id}")

    if not record:
        return {
            "verified": False,
            "reason": "No hash record found — file may have expired or was never registered",
        }

    stored_hash = record["sha256"]
    computed_hash = hashlib.sha256(pdf_bytes).hexdigest()

    return {
        "verified": True,
        "match": stored_hash == computed_hash,
        "stored_hash": stored_hash,
        "computed_hash": computed_hash,
        "tampered": stored_hash != computed_hash,
        "registered_at": float(record.get("created_at", 0)),
    }


def get_hash(file_id: str) -> str | None:
    """Retrieve the stored hash for a file_id."""
    r = _get_redis()
    record = r.hgetall(f"pdf_hash:{file_id}")
    return record.get("sha256") if record else None
