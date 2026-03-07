"""
Template Engine using Jinja2.

Renders document data into HTML using Jinja2 templates for purchase orders
and invoices. Handles chunking of large documents (500+ line items) into
multiple pages to prevent Puppeteer/Playwright OOM crashes.
"""

import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from src.config import Config

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _format_currency(value):
    """Format number as Indian currency."""
    try:
        return f"₹{float(value):,.2f}"
    except (ValueError, TypeError):
        return str(value)


_env.filters["currency"] = _format_currency


def render_template(doc_type: str, data: dict) -> str:
    """Render a document to HTML string.

    Args:
        doc_type: 'purchase_order' or 'invoice'
        data: Document data dict with header info and line_items list.

    Returns:
        Full HTML string ready for PDF rendering.
    """
    template_name = f"{doc_type}.html"
    template = _env.get_template(template_name)
    return template.render(**data)


def render_chunked_template(doc_type: str, data: dict) -> list[str]:
    """Render a large document as multiple HTML chunks.

    For documents with > CHUNK_THRESHOLD line items, we split into
    separate HTML pages to avoid Playwright OOM. Each chunk gets
    header/footer but only a subset of line items.

    Returns:
        List of HTML strings, one per chunk.
    """
    line_items = data.get("line_items", [])
    if len(line_items) <= Config.CHUNK_THRESHOLD:
        return [render_template(doc_type, data)]

    chunks = []
    total_items = len(line_items)
    chunk_size = Config.CHUNK_SIZE

    for i in range(0, total_items, chunk_size):
        chunk_items = line_items[i : i + chunk_size]
        chunk_data = {
            **data,
            "line_items": chunk_items,
            "chunk_info": {
                "current": (i // chunk_size) + 1,
                "total": (total_items + chunk_size - 1) // chunk_size,
                "item_start": i + 1,
                "item_end": min(i + chunk_size, total_items),
                "total_items": total_items,
            },
        }
        # Only show totals on the last chunk
        if i + chunk_size < total_items:
            chunk_data["hide_totals"] = True

        chunks.append(render_template(doc_type, chunk_data))

    return chunks


def get_available_templates() -> list[str]:
    """List available document template types."""
    templates = []
    for f in os.listdir(TEMPLATE_DIR):
        if f.endswith(".html") and f != "base.html":
            templates.append(f.replace(".html", ""))
    return templates
