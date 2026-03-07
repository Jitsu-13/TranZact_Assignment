"""Tests for request validation schemas."""

import pytest
from marshmallow import ValidationError
from src.schemas import SinglePdfRequestSchema, BulkPdfRequestSchema


class TestSinglePdfRequestSchema:
    def setup_method(self):
        self.schema = SinglePdfRequestSchema()

    def test_valid_invoice_request(self):
        data = {
            "doc_type": "invoice",
            "data": {
                "document_number": "INV-001",
                "line_items": [{"description": "Item 1", "quantity": 1, "rate": 100, "amount": 100}],
            },
        }
        result = self.schema.load(data)
        assert result["doc_type"] == "invoice"

    def test_valid_purchase_order_request(self):
        data = {
            "doc_type": "purchase_order",
            "data": {
                "document_number": "PO-001",
                "line_items": [{"description": "Item 1", "quantity": 1, "rate": 100, "amount": 100}],
            },
        }
        result = self.schema.load(data)
        assert result["doc_type"] == "purchase_order"

    def test_invalid_doc_type(self):
        data = {
            "doc_type": "receipt",
            "data": {"document_number": "R-001", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]},
        }
        with pytest.raises(ValidationError) as exc_info:
            self.schema.load(data)
        assert "doc_type" in str(exc_info.value)

    def test_missing_doc_type(self):
        data = {"data": {"document_number": "X", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}}
        with pytest.raises(ValidationError):
            self.schema.load(data)

    def test_missing_document_number(self):
        data = {
            "doc_type": "invoice",
            "data": {"line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]},
        }
        with pytest.raises(ValidationError, match="document_number"):
            self.schema.load(data)

    def test_empty_line_items(self):
        data = {
            "doc_type": "invoice",
            "data": {"document_number": "INV-001", "line_items": []},
        }
        with pytest.raises(ValidationError, match="line_items"):
            self.schema.load(data)

    def test_missing_line_items(self):
        data = {"doc_type": "invoice", "data": {"document_number": "INV-001"}}
        with pytest.raises(ValidationError, match="line_items"):
            self.schema.load(data)

    def test_missing_data(self):
        data = {"doc_type": "invoice"}
        with pytest.raises(ValidationError):
            self.schema.load(data)


class TestBulkPdfRequestSchema:
    def setup_method(self):
        self.schema = BulkPdfRequestSchema()

    def test_valid_bulk_request(self):
        data = {
            "documents": [
                {
                    "doc_type": "invoice",
                    "data": {"document_number": "INV-001", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]},
                },
                {
                    "doc_type": "purchase_order",
                    "data": {"document_number": "PO-001", "line_items": [{"description": "y", "quantity": 2, "rate": 2, "amount": 4}]},
                },
            ]
        }
        result = self.schema.load(data)
        assert len(result["documents"]) == 2

    def test_empty_documents_list(self):
        with pytest.raises(ValidationError):
            self.schema.load({"documents": []})

    def test_exceeds_max_documents(self):
        docs = [
            {"doc_type": "invoice", "data": {"document_number": f"INV-{i}", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}}
            for i in range(101)
        ]
        with pytest.raises(ValidationError):
            self.schema.load({"documents": docs})

    def test_invalid_doc_type_in_bulk(self):
        data = {
            "documents": [
                {"doc_type": "unknown", "data": {"document_number": "X", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}},
            ]
        }
        with pytest.raises(ValidationError, match="doc_type"):
            self.schema.load(data)

    def test_missing_data_in_bulk_document(self):
        data = {"documents": [{"doc_type": "invoice"}]}
        with pytest.raises(ValidationError, match="data"):
            self.schema.load(data)
