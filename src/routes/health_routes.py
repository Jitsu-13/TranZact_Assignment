"""Health check and system status endpoints."""

import time

from flask import Blueprint, jsonify

from src.services.storage import get_storage_stats
from src.logger import logger

health_bp = Blueprint("health", __name__)

_start_time = time.time()


@health_bp.route("/api/v1/health", methods=["GET"])
def health_check():
    """Basic health check endpoint."""
    checks = {"api": "healthy"}

    # Check Redis connectivity
    try:
        import redis
        from src.config import Config
        r = redis.from_url(Config.REDIS_URL)
        r.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {e}"

    storage = get_storage_stats()
    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "degraded"

    return jsonify({
        "status": overall,
        "uptime_seconds": int(time.time() - _start_time),
        "checks": checks,
        "storage": storage,
    })
