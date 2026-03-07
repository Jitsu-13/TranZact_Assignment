"""
Storage Service.

Manages generated PDF files on disk. Handles:
- File retrieval for downloads
- ZIP archive creation for bulk downloads
- Cleanup of expired files (retention policy)
- Resumable download support via HTTP Range headers

In production, this could be swapped to S3-compatible storage. We use
local disk here to keep costs under the ₹12,500/month budget constraint
and avoid egress charges.
"""

import os
import time
import zipfile
from io import BytesIO

from src.config import Config
from src.logger import logger


def get_pdf_path(file_id: str) -> str | None:
    """Get the full path for a stored PDF, or None if not found."""
    path = os.path.join(Config.PDF_STORAGE_DIR, f"{file_id}.pdf")
    if os.path.exists(path):
        return path
    return None


def get_pdf_bytes(file_id: str) -> bytes | None:
    """Read PDF bytes from storage."""
    path = get_pdf_path(file_id)
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


def create_zip_archive(file_entries: list[dict]) -> str:
    """Create a ZIP archive from multiple generated PDFs.

    Args:
        file_entries: List of dicts with 'file_id' and 'filename' keys.
            filename is the human-readable name inside the ZIP.

    Returns:
        Path to the created ZIP file.
    """
    os.makedirs(Config.PDF_STORAGE_DIR, exist_ok=True)
    zip_id = f"bulk_{int(time.time())}"
    zip_path = os.path.join(Config.PDF_STORAGE_DIR, f"{zip_id}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in file_entries:
            pdf_path = get_pdf_path(entry["file_id"])
            if pdf_path:
                arcname = entry.get("filename", f"{entry['file_id']}.pdf")
                zf.write(pdf_path, arcname)
            else:
                logger.warning(f"PDF not found for file_id={entry['file_id']}, skipping")

    logger.info(f"ZIP archive created: {zip_path}, {len(file_entries)} files")
    return zip_path


def cleanup_expired_files():
    """Remove PDFs and ZIPs older than the retention period."""
    storage_dir = Config.PDF_STORAGE_DIR
    if not os.path.exists(storage_dir):
        return

    cutoff = time.time() - (Config.PDF_RETENTION_HOURS * 3600)
    removed = 0

    for filename in os.listdir(storage_dir):
        filepath = os.path.join(storage_dir, filename)
        if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
            os.remove(filepath)
            removed += 1

    if removed > 0:
        logger.info(f"Cleanup: removed {removed} expired files")


def get_storage_stats() -> dict:
    """Get storage usage statistics."""
    storage_dir = Config.PDF_STORAGE_DIR
    if not os.path.exists(storage_dir):
        return {"total_files": 0, "total_size_mb": 0}

    files = os.listdir(storage_dir)
    total_size = sum(
        os.path.getsize(os.path.join(storage_dir, f))
        for f in files
        if os.path.isfile(os.path.join(storage_dir, f))
    )
    return {
        "total_files": len(files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }
