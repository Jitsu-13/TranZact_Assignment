"""
PDF Generation API Routes.

Endpoints:
    POST /api/v1/pdf/generate         — Generate a single PDF (sync)
    POST /api/v1/pdf/generate/async    — Generate a single PDF (async via queue)
    POST /api/v1/pdf/bulk              — Generate bulk PDFs (async, returns job_id)
    GET  /api/v1/pdf/bulk/<job_id>/status — Poll bulk job status
    GET  /api/v1/pdf/bulk/<job_id>/progress — SSE stream for real-time progress
    GET  /api/v1/pdf/download/<file_id>    — Download a single PDF (supports Range)
    GET  /api/v1/pdf/bulk/<job_id>/download — Download bulk ZIP
    POST /api/v1/pdf/verify/<file_id>      — Verify tamper evidence
    GET  /api/v1/health                    — Health check
"""

import json
import os
import time

from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
from marshmallow import ValidationError

from src.config import Config
from src.logger import logger
from src.schemas import SinglePdfRequestSchema, BulkPdfRequestSchema
from src.services.pdf_generator import generate_pdf
from src.services.hash_registry import register_hash, verify_hash, get_hash
from src.services.storage import get_pdf_path, get_pdf_bytes
from src.tasks.pdf_tasks import generate_single_pdf_task, generate_bulk_pdfs_task
from src.celery_app import celery_app

pdf_bp = Blueprint("pdf", __name__, url_prefix="/api/v1/pdf")

single_schema = SinglePdfRequestSchema()
bulk_schema = BulkPdfRequestSchema()


# ---------- Single PDF Generation (Synchronous) ----------

@pdf_bp.route("/generate", methods=["POST"])
def generate_single():
    """Generate a single PDF synchronously.

    For low-latency single document generation (~2-5s response).
    The Django monolith sends a complete data snapshot to avoid stale data.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Request body must be JSON"}), 400

    try:
        validated = single_schema.load(payload)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    try:
        result = generate_pdf(validated["doc_type"], validated["data"])
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)
        return jsonify({"error": "PDF generation failed", "detail": str(e)}), 500

    # Register hash for tamper evidence (best-effort — don't fail the request if Redis is down)
    try:
        register_hash(result["file_id"], result["sha256_hash"], {
            "doc_type": validated["doc_type"],
            "document_number": validated["data"].get("document_number"),
        })
    except Exception as e:
        logger.warning(f"Hash registration failed (Redis may be down): {e}")

    return jsonify({
        "status": "completed",
        "file_id": result["file_id"],
        "file_size": result["file_size"],
        "sha256_hash": result["sha256_hash"],
        "generation_time_ms": result["generation_time_ms"],
        "download_url": f"/api/v1/pdf/download/{result['file_id']}",
    }), 201


# ---------- Single PDF Generation (Async) ----------

@pdf_bp.route("/generate/async", methods=["POST"])
def generate_single_async():
    """Queue a single PDF for async generation. Returns a task_id to poll."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Request body must be JSON"}), 400

    try:
        validated = single_schema.load(payload)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    task = generate_single_pdf_task.delay(validated["doc_type"], validated["data"])
    return jsonify({
        "status": "queued",
        "task_id": task.id,
        "status_url": f"/api/v1/pdf/task/{task.id}/status",
    }), 202


# ---------- Async Task Status ----------

@pdf_bp.route("/task/<task_id>/status", methods=["GET"])
def get_task_status(task_id):
    """Check status of an async single-PDF task."""
    result = celery_app.AsyncResult(task_id)
    if result.state == "PENDING":
        return jsonify({"status": "pending", "task_id": task_id})
    elif result.state == "SUCCESS":
        data = result.result
        return jsonify({
            "status": "completed",
            "task_id": task_id,
            **data,
            "download_url": f"/api/v1/pdf/download/{data['file_id']}",
        })
    elif result.state == "FAILURE":
        return jsonify({
            "status": "failed",
            "task_id": task_id,
            "error": str(result.info),
        }), 500
    else:
        return jsonify({"status": result.state, "task_id": task_id})


# ---------- Bulk PDF Generation ----------

@pdf_bp.route("/bulk", methods=["POST"])
def generate_bulk():
    """Submit a bulk PDF generation job.

    DATA CONSISTENCY: The Django monolith must send complete data snapshots
    for ALL documents in the request. This ensures every PDF in the batch
    uses data captured at the same point in time — preventing the stale-data
    bug where doc #87 shows different data than doc #1 because the DB was
    updated between renders.

    Returns a job_id for progress tracking and download.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Request body must be JSON"}), 400

    try:
        validated = bulk_schema.load(payload)
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    documents = validated["documents"]
    task = generate_bulk_pdfs_task.delay(documents)

    return jsonify({
        "status": "queued",
        "job_id": task.id,
        "total_documents": len(documents),
        "status_url": f"/api/v1/pdf/bulk/{task.id}/status",
        "progress_url": f"/api/v1/pdf/bulk/{task.id}/progress",
        "download_url": f"/api/v1/pdf/bulk/{task.id}/download",
    }), 202


# ---------- Bulk Job Status (Polling) ----------

@pdf_bp.route("/bulk/<job_id>/status", methods=["GET"])
def bulk_status(job_id):
    """Get current status of a bulk generation job."""
    result = celery_app.AsyncResult(job_id)

    if result.state == "PENDING":
        return jsonify({"status": "pending", "job_id": job_id})
    elif result.state == "PROGRESS":
        return jsonify({"status": "processing", "job_id": job_id, **result.info})
    elif result.state == "SUCCESS":
        data = result.result
        return jsonify({
            "status": "completed",
            "job_id": job_id,
            **data,
            "download_url": f"/api/v1/pdf/bulk/{job_id}/download",
        })
    elif result.state == "FAILURE":
        return jsonify({
            "status": "failed",
            "job_id": job_id,
            "error": str(result.info),
        }), 500
    else:
        return jsonify({"status": result.state, "job_id": job_id})


# ---------- Bulk Job Progress (SSE) ----------

@pdf_bp.route("/bulk/<job_id>/progress", methods=["GET"])
def bulk_progress_sse(job_id):
    """Server-Sent Events stream for real-time bulk job progress.

    Designed for unreliable connections — if the connection drops, the
    client can reconnect and get the current state. SSE has built-in
    reconnection support via the EventSource API.
    """
    def event_stream():
        last_state = None
        while True:
            result = celery_app.AsyncResult(job_id)
            state = result.state
            info = result.info if result.info else {}

            if state == "PROGRESS":
                data = json.dumps({
                    "status": "processing",
                    **info,
                })
                yield f"data: {data}\n\n"
            elif state == "SUCCESS":
                data = json.dumps({
                    "status": "completed",
                    **result.result,
                    "download_url": f"/api/v1/pdf/bulk/{job_id}/download",
                })
                yield f"data: {data}\n\n"
                break
            elif state == "FAILURE":
                data = json.dumps({
                    "status": "failed",
                    "error": str(info),
                })
                yield f"data: {data}\n\n"
                break
            elif state == "PENDING":
                yield f"data: {json.dumps({'status': 'pending'})}\n\n"

            time.sleep(1)  # Poll interval

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- Download Endpoints ----------

@pdf_bp.route("/download/<file_id>", methods=["GET"])
def download_single(file_id):
    """Download a single generated PDF.

    Supports HTTP Range headers for resumable downloads — critical for
    users on unreliable internet connections. If a download fails midway,
    the client can resume from where it left off.
    """
    pdf_path = get_pdf_path(file_id)
    if not pdf_path:
        return jsonify({"error": "File not found or expired"}), 404

    file_size = os.path.getsize(pdf_path)
    range_header = request.headers.get("Range")

    if range_header:
        # Parse Range: bytes=start-end
        try:
            ranges = range_header.replace("bytes=", "").split("-")
            start = int(ranges[0])
            end = int(ranges[1]) if ranges[1] else file_size - 1
        except (ValueError, IndexError):
            return jsonify({"error": "Invalid Range header"}), 416

        if start >= file_size:
            return jsonify({"error": "Range not satisfiable"}), 416

        end = min(end, file_size - 1)
        length = end - start + 1

        with open(pdf_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        resp = Response(
            data,
            status=206,
            mimetype="application/pdf",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": length,
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"attachment; filename={file_id}.pdf",
            },
        )
        return resp

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{file_id}.pdf",
    )


@pdf_bp.route("/bulk/<job_id>/download", methods=["GET"])
def download_bulk(job_id):
    """Download the ZIP archive for a completed bulk job.

    Supports Range headers for resumable downloads. ZIP files can be
    large (50+ PDFs * ~500KB = 25MB+), so resumability is critical
    for SMB users on unreliable connections.
    """
    result = celery_app.AsyncResult(job_id)

    if result.state != "SUCCESS":
        return jsonify({
            "error": "Job not yet completed",
            "status": result.state,
        }), 404

    zip_path = result.result.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"error": "ZIP archive not found"}), 404

    file_size = os.path.getsize(zip_path)
    range_header = request.headers.get("Range")

    if range_header:
        try:
            ranges = range_header.replace("bytes=", "").split("-")
            start = int(ranges[0])
            end = int(ranges[1]) if ranges[1] else file_size - 1
        except (ValueError, IndexError):
            return jsonify({"error": "Invalid Range header"}), 416

        if start >= file_size:
            return jsonify({"error": "Range not satisfiable"}), 416

        end = min(end, file_size - 1)
        length = end - start + 1

        with open(zip_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return Response(
            data,
            status=206,
            mimetype="application/zip",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": length,
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"attachment; filename=bulk_{job_id}.zip",
            },
        )

    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"bulk_{job_id}.zip",
    )


# ---------- Tamper Verification ----------

@pdf_bp.route("/verify/<file_id>", methods=["POST"])
def verify_pdf(file_id):
    """Verify that a PDF has not been tampered with since generation.

    Recomputes SHA-256 of the file on disk and compares with the hash
    stored at generation time. Returns match/mismatch status.
    """
    pdf_bytes = get_pdf_bytes(file_id)
    if not pdf_bytes:
        return jsonify({"error": "PDF not found or expired"}), 404

    try:
        result = verify_hash(file_id, pdf_bytes)
    except Exception as e:
        logger.warning(f"Hash verification failed (Redis may be down): {e}")
        # Fallback: compute hash locally but can't compare with stored hash
        import hashlib
        computed = hashlib.sha256(pdf_bytes).hexdigest()
        result = {
            "verified": False,
            "reason": "Hash registry unavailable — cannot verify against stored hash",
            "computed_hash": computed,
        }
    return jsonify(result), 200
