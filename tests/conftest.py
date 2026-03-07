"""Shared test fixtures."""

import asyncio
import os
import shutil
import tempfile

import pytest

from src.app import create_app
from src.config import Config


@pytest.fixture
def app():
    """Create test application."""
    # Use temp directory for test PDFs
    test_storage = tempfile.mkdtemp()
    Config.PDF_STORAGE_DIR = test_storage

    app = create_app(testing=True)
    yield app

    # Cleanup test PDF storage
    shutil.rmtree(test_storage, ignore_errors=True)


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def sample_invoice_data():
    """Sample invoice data for testing."""
    return {
        "doc_type": "invoice",
        "data": {
            "document_number": "INV-2024-001",
            "date": "2024-03-15",
            "due_date": "2024-04-15",
            "company": {
                "name": "TranZact Technologies Pvt. Ltd.",
                "address": "123 Tech Park, Bangalore, Karnataka 560001",
                "phone": "+91-80-1234-5678",
                "email": "billing@tranzact.com",
                "gstin": "29ABCDE1234F1Z5",
            },
            "bill_to": {
                "name": "Acme Manufacturing Ltd.",
                "address": "456 Industrial Area, Mumbai, Maharashtra 400001",
                "gstin": "27FGHIJ5678K2L6",
                "state": "Maharashtra",
                "state_code": "27",
            },
            "ship_to": {
                "name": "Acme Manufacturing Ltd.",
                "address": "789 Warehouse Rd, Pune, Maharashtra 411001",
                "state": "Maharashtra",
            },
            "line_items": [
                {
                    "description": "Steel Rod 12mm TMT",
                    "hsn_code": "7214",
                    "quantity": 500,
                    "unit": "kg",
                    "rate": 65.00,
                    "amount": 32500.00,
                    "gst_rate": 18,
                    "gst_amount": 5850.00,
                    "total": 38350.00,
                },
                {
                    "description": "Cement OPC 53 Grade",
                    "hsn_code": "2523",
                    "quantity": 100,
                    "unit": "bags",
                    "rate": 380.00,
                    "amount": 38000.00,
                    "gst_rate": 28,
                    "gst_amount": 10640.00,
                    "total": 48640.00,
                },
                {
                    "description": "PVC Pipe 4 inch",
                    "hsn_code": "3917",
                    "quantity": 50,
                    "unit": "pcs",
                    "rate": 250.00,
                    "amount": 12500.00,
                    "gst_rate": 18,
                    "gst_amount": 2250.00,
                    "total": 14750.00,
                },
            ],
            "subtotal": 83000.00,
            "cgst": 9370.00,
            "cgst_rate": "9",
            "sgst": 9370.00,
            "sgst_rate": "9",
            "grand_total": 101740.00,
            "amount_in_words": "Rupees One Lakh One Thousand Seven Hundred and Forty Only",
            "bank_details": {
                "bank_name": "HDFC Bank",
                "account_number": "12345678901234",
                "ifsc": "HDFC0001234",
                "branch": "Bangalore Main Branch",
            },
            "terms": "Payment due within 30 days. Late payment subject to 1.5% monthly interest.",
        },
    }


@pytest.fixture
def sample_po_data():
    """Sample purchase order data for testing."""
    return {
        "doc_type": "purchase_order",
        "data": {
            "document_number": "PO-2024-042",
            "date": "2024-03-10",
            "delivery_date": "2024-03-25",
            "status": "Approved",
            "company": {
                "name": "TranZact Technologies Pvt. Ltd.",
                "address": "123 Tech Park, Bangalore, Karnataka 560001",
                "gstin": "29ABCDE1234F1Z5",
            },
            "vendor": {
                "name": "Global Steel Suppliers",
                "address": "321 Metal Market, Jamshedpur, Jharkhand 831001",
                "gstin": "20MNOPQ3456R7S8",
                "contact": "Rajesh Kumar - +91-9876543210",
            },
            "ship_to": {
                "name": "TranZact Warehouse",
                "address": "Plot 7, Industrial Estate, Bangalore 560058",
                "contact": "Warehouse Manager - +91-9988776655",
            },
            "line_items": [
                {
                    "description": "MS Flat Bar 50x6mm",
                    "hsn_code": "7216",
                    "quantity": 200,
                    "unit": "kg",
                    "rate": 55.00,
                    "amount": 11000.00,
                },
                {
                    "description": "GI Wire 2.5mm",
                    "hsn_code": "7217",
                    "quantity": 100,
                    "unit": "kg",
                    "rate": 85.00,
                    "amount": 8500.00,
                },
            ],
            "subtotal": 19500.00,
            "igst": 3510.00,
            "igst_rate": "18",
            "grand_total": 23010.00,
            "terms": "Delivery within 15 days. Quality inspection at receiver's end.",
            "notes": "Urgent requirement for Project Phoenix. Priority dispatch requested.",
        },
    }


@pytest.fixture
def large_document_data():
    """Document with 500+ line items to test chunked rendering."""
    items = []
    for i in range(550):
        items.append({
            "description": f"Item #{i+1} - Industrial Component Type-{chr(65 + (i % 26))}",
            "hsn_code": f"{7200 + (i % 100)}",
            "quantity": 10 + (i % 50),
            "unit": "pcs",
            "rate": round(100 + (i * 1.5), 2),
            "amount": round((10 + (i % 50)) * (100 + (i * 1.5)), 2),
            "gst_rate": 18,
            "gst_amount": round((10 + (i % 50)) * (100 + (i * 1.5)) * 0.18, 2),
            "total": round((10 + (i % 50)) * (100 + (i * 1.5)) * 1.18, 2),
        })

    subtotal = sum(item["amount"] for item in items)
    gst = round(subtotal * 0.18, 2)

    return {
        "doc_type": "invoice",
        "data": {
            "document_number": "INV-2024-LARGE-001",
            "date": "2024-03-15",
            "company": {
                "name": "Large Scale Industries Pvt. Ltd.",
                "address": "Industrial Belt, Surat, Gujarat",
                "gstin": "24XYZAB1234C5D6",
            },
            "bill_to": {
                "name": "Mega Construction Corp.",
                "address": "Sector 15, Noida, UP",
                "gstin": "09LMNOP7890Q1R2",
            },
            "ship_to": {
                "name": "Mega Construction Corp.",
                "address": "Site Office, Greater Noida",
            },
            "line_items": items,
            "subtotal": subtotal,
            "igst": gst,
            "igst_rate": "18",
            "grand_total": round(subtotal + gst, 2),
        },
    }
