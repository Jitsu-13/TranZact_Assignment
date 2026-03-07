"""Tests for template rendering engine."""

import pytest
from src.services.template_engine import render_template, render_chunked_template, get_available_templates
from src.config import Config


class TestTemplateEngine:
    def test_available_templates(self):
        templates = get_available_templates()
        assert "invoice" in templates
        assert "purchase_order" in templates

    def test_render_invoice_template(self):
        data = {
            "document_number": "INV-001",
            "date": "2024-03-15",
            "company": {"name": "Test Corp", "address": "123 St"},
            "bill_to": {"name": "Client Inc", "address": "456 Ave"},
            "ship_to": {"name": "Client Inc", "address": "456 Ave"},
            "line_items": [
                {"description": "Widget", "hsn_code": "1234", "quantity": 10,
                 "unit": "pcs", "rate": 100, "amount": 1000, "gst_rate": 18,
                 "gst_amount": 180, "total": 1180},
            ],
            "subtotal": 1000,
            "cgst": 90,
            "sgst": 90,
            "grand_total": 1180,
        }
        html = render_template("invoice", data)
        assert "INV-001" in html
        assert "Test Corp" in html
        assert "Widget" in html
        assert "TAX INVOICE" in html

    def test_render_purchase_order_template(self):
        data = {
            "document_number": "PO-042",
            "date": "2024-03-10",
            "company": {"name": "Buyer Co"},
            "vendor": {"name": "Supplier Co"},
            "ship_to": {"name": "Buyer Warehouse"},
            "line_items": [
                {"description": "Steel Rod", "quantity": 100, "unit": "kg",
                 "rate": 65, "amount": 6500},
            ],
            "subtotal": 6500,
            "grand_total": 7670,
        }
        html = render_template("purchase_order", data)
        assert "PO-042" in html
        assert "PURCHASE ORDER" in html
        assert "Steel Rod" in html

    def test_chunked_rendering_small_doc(self):
        data = {
            "document_number": "INV-SMALL",
            "date": "2024-01-01",
            "company": {"name": "Test"},
            "bill_to": {"name": "Client"},
            "ship_to": {"name": "Client"},
            "line_items": [
                {"description": f"Item {i}", "quantity": 1, "unit": "pcs",
                 "rate": 10, "amount": 10, "gst_rate": 18, "gst_amount": 1.8, "total": 11.8}
                for i in range(10)
            ],
            "subtotal": 100,
            "grand_total": 118,
        }
        chunks = render_chunked_template("invoice", data)
        assert len(chunks) == 1  # Small doc, no chunking

    def test_chunked_rendering_large_doc(self):
        original_threshold = Config.CHUNK_THRESHOLD
        original_chunk_size = Config.CHUNK_SIZE
        Config.CHUNK_THRESHOLD = 50
        Config.CHUNK_SIZE = 20

        try:
            items = [
                {"description": f"Item {i}", "quantity": 1, "unit": "pcs",
                 "rate": 10, "amount": 10, "gst_rate": 18, "gst_amount": 1.8, "total": 11.8}
                for i in range(100)
            ]
            data = {
                "document_number": "INV-LARGE",
                "date": "2024-01-01",
                "company": {"name": "Test"},
                "bill_to": {"name": "Client"},
                "ship_to": {"name": "Client"},
                "line_items": items,
                "subtotal": 1000,
                "grand_total": 1180,
            }
            chunks = render_chunked_template("invoice", data)
            assert len(chunks) == 5  # 100 items / 20 per chunk
            # First chunk should have chunk_info but hide_totals
            assert "Page 1 of 5" in chunks[0]
            # Last chunk should show totals
            assert "Grand Total" in chunks[-1]
        finally:
            Config.CHUNK_THRESHOLD = original_threshold
            Config.CHUNK_SIZE = original_chunk_size

    def test_currency_filter(self):
        data = {
            "document_number": "INV-CUR",
            "date": "2024-01-01",
            "company": {"name": "Test"},
            "bill_to": {"name": "Client"},
            "ship_to": {"name": "Client"},
            "line_items": [
                {"description": "Expensive Item", "quantity": 1, "unit": "pcs",
                 "rate": 12500.50, "amount": 12500.50, "gst_rate": 18,
                 "gst_amount": 2250.09, "total": 14750.59},
            ],
            "subtotal": 12500.50,
            "grand_total": 14750.59,
        }
        html = render_template("invoice", data)
        assert "₹" in html
