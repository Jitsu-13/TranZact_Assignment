"""Integration tests for the API endpoints."""

import json
import os

import pytest


class TestHealthEndpoint:
    def test_health_check(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] in ("healthy", "degraded")
        assert "checks" in data
        assert "storage" in data


class TestSinglePdfGeneration:
    def test_generate_invoice_success(self, client, sample_invoice_data):
        """Test successful single invoice PDF generation."""
        resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps(sample_invoice_data),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "completed"
        assert "file_id" in data
        assert "sha256_hash" in data
        assert "download_url" in data
        assert data["file_size"] > 0

    def test_generate_purchase_order_success(self, client, sample_po_data):
        """Test successful single PO PDF generation."""
        resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps(sample_po_data),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "completed"
        assert "file_id" in data

    def test_generate_invalid_doc_type(self, client):
        resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps({"doc_type": "receipt", "data": {"document_number": "R-1", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_generate_missing_body(self, client):
        resp = client.post("/api/v1/pdf/generate")
        assert resp.status_code == 400

    def test_generate_empty_line_items(self, client):
        resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps({"doc_type": "invoice", "data": {"document_number": "INV-1", "line_items": []}}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_generate_missing_document_number(self, client):
        resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps({"doc_type": "invoice", "data": {"line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestDownload:
    def test_download_generated_pdf(self, client, sample_invoice_data):
        """Generate a PDF and then download it."""
        gen_resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps(sample_invoice_data),
            content_type="application/json",
        )
        file_id = gen_resp.get_json()["file_id"]

        dl_resp = client.get(f"/api/v1/pdf/download/{file_id}")
        assert dl_resp.status_code == 200
        assert dl_resp.content_type == "application/pdf"
        assert len(dl_resp.data) > 0

    def test_download_not_found(self, client):
        resp = client.get("/api/v1/pdf/download/nonexistent-id")
        assert resp.status_code == 404

    def test_download_with_range_header(self, client, sample_invoice_data):
        """Test resumable download with Range header."""
        gen_resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps(sample_invoice_data),
            content_type="application/json",
        )
        file_id = gen_resp.get_json()["file_id"]

        # Request first 1000 bytes
        dl_resp = client.get(
            f"/api/v1/pdf/download/{file_id}",
            headers={"Range": "bytes=0-999"},
        )
        assert dl_resp.status_code == 206
        assert len(dl_resp.data) == 1000
        assert "Content-Range" in dl_resp.headers


class TestVerification:
    def test_verify_intact_pdf(self, client, sample_invoice_data):
        """Generate a PDF and verify it's not tampered.

        When Redis is available, verification compares stored vs computed hash.
        When Redis is unavailable, the endpoint still returns 200 with a
        computed_hash but verified=False (graceful degradation).
        """
        gen_resp = client.post(
            "/api/v1/pdf/generate",
            data=json.dumps(sample_invoice_data),
            content_type="application/json",
        )
        file_id = gen_resp.get_json()["file_id"]

        verify_resp = client.post(f"/api/v1/pdf/verify/{file_id}")
        assert verify_resp.status_code == 200
        data = verify_resp.get_json()
        if data.get("verified"):
            # Redis is available — full verification
            assert data["match"] is True
            assert data["tampered"] is False
        else:
            # Redis unavailable — graceful degradation
            assert "computed_hash" in data

    def test_verify_not_found(self, client):
        resp = client.post("/api/v1/pdf/verify/nonexistent")
        assert resp.status_code == 404


class TestBulkEndpoint:
    def test_bulk_validation_empty(self, client):
        resp = client.post(
            "/api/v1/pdf/bulk",
            data=json.dumps({"documents": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_bulk_validation_invalid_doc(self, client):
        resp = client.post(
            "/api/v1/pdf/bulk",
            data=json.dumps({
                "documents": [{"doc_type": "bad_type", "data": {"document_number": "X", "line_items": [{"description": "x", "quantity": 1, "rate": 1, "amount": 1}]}}]
            }),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestErrorHandling:
    def test_404(self, client):
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code == 404

    def test_method_not_allowed(self, client):
        resp = client.delete("/api/v1/pdf/generate")
        assert resp.status_code == 405
