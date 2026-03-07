"""
PDF Generator Service.

Core service that orchestrates HTML rendering -> PDF conversion.
Handles both simple single-page documents and large chunked documents
that require splitting and merging to avoid OOM.

Key design decisions:
- Chunked rendering for 500+ line items prevents Playwright OOM crashes
- Each chunk rendered separately, then merged with pypdf
- SHA-256 hash computed immediately after generation for tamper evidence
- Uses a persistent background event loop for Playwright (async objects
  are loop-bound and cannot be reused across different loops)
"""

import asyncio
import concurrent.futures
import hashlib
import os
import threading
import time
from io import BytesIO
from uuid import uuid4

from pypdf import PdfMerger

from src.config import Config
from src.logger import logger
from src.services.browser_pool import get_browser_pool
from src.services.template_engine import render_chunked_template

# Persistent event loop running in a background thread.
# Playwright async objects (browsers, pages) are bound to the loop they
# were created on. We must reuse the same loop across all calls.
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_event_loop():
    """Start a persistent background event loop if not already running."""
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
        logger.info("Background event loop started for Playwright")


def _run_async(coro):
    """Submit a coroutine to the persistent event loop and wait for result."""
    _ensure_event_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=Config.RENDER_TIMEOUT_MS / 1000 + 30)


async def _render_html_to_pdf(html: str) -> bytes:
    """Render a single HTML string to PDF bytes using Playwright."""
    pool = get_browser_pool()
    await pool.initialize()

    browser, page = await pool.acquire_page()
    try:
        await page.set_content(html, wait_until="networkidle",
                               timeout=Config.RENDER_TIMEOUT_MS)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "10mm", "right": "10mm"},
        )
        return pdf_bytes
    finally:
        await pool.release_page(page)


async def _render_and_merge_chunks(html_chunks: list[str]) -> bytes:
    """Render multiple HTML chunks to PDFs and merge them."""
    if len(html_chunks) == 1:
        return await _render_html_to_pdf(html_chunks[0])

    merger = PdfMerger()
    try:
        for i, html in enumerate(html_chunks):
            logger.info(f"Rendering chunk {i + 1}/{len(html_chunks)}")
            pdf_bytes = await _render_html_to_pdf(html)
            merger.append(BytesIO(pdf_bytes))

        output = BytesIO()
        merger.write(output)
        return output.getvalue()
    finally:
        merger.close()


def generate_pdf(doc_type: str, data: dict) -> dict:
    """Generate a single PDF synchronously.

    Args:
        doc_type: 'purchase_order' or 'invoice'
        data: Document data with line_items, header info, etc.

    Returns:
        dict with keys: file_id, file_path, file_size, sha256_hash, generation_time_ms
    """
    start = time.time()
    file_id = str(uuid4())

    # Render HTML chunks (single chunk for small docs, multiple for large)
    html_chunks = render_chunked_template(doc_type, data)
    logger.info(
        f"Generating PDF {file_id}: type={doc_type}, "
        f"line_items={len(data.get('line_items', []))}, chunks={len(html_chunks)}"
    )

    # Run async rendering on the persistent background event loop
    pdf_bytes = _run_async(_render_and_merge_chunks(html_chunks))

    # Store to disk
    os.makedirs(Config.PDF_STORAGE_DIR, exist_ok=True)
    file_path = os.path.join(Config.PDF_STORAGE_DIR, f"{file_id}.pdf")
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)

    # Compute tamper-evidence hash
    sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()

    elapsed_ms = int((time.time() - start) * 1000)
    logger.info(f"PDF {file_id} generated in {elapsed_ms}ms, size={len(pdf_bytes)} bytes")

    return {
        "file_id": file_id,
        "file_path": file_path,
        "file_size": len(pdf_bytes),
        "sha256_hash": sha256_hash,
        "generation_time_ms": elapsed_ms,
    }
