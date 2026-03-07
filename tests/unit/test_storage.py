"""Tests for storage service."""

import os
import tempfile
import time

import pytest

from src.services.storage import get_pdf_path, get_pdf_bytes, create_zip_archive, cleanup_expired_files, get_storage_stats
from src.config import Config


class TestStorage:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self._original_dir = Config.PDF_STORAGE_DIR
        Config.PDF_STORAGE_DIR = self.test_dir

    def teardown_method(self):
        Config.PDF_STORAGE_DIR = self._original_dir
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_test_pdf(self, file_id: str, content: bytes = b"%PDF-1.4 test content"):
        path = os.path.join(self.test_dir, f"{file_id}.pdf")
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_get_pdf_path_exists(self):
        self._create_test_pdf("test-123")
        path = get_pdf_path("test-123")
        assert path is not None
        assert path.endswith("test-123.pdf")

    def test_get_pdf_path_not_found(self):
        assert get_pdf_path("nonexistent") is None

    def test_get_pdf_bytes(self):
        content = b"%PDF-1.4 hello world"
        self._create_test_pdf("test-bytes", content)
        result = get_pdf_bytes("test-bytes")
        assert result == content

    def test_get_pdf_bytes_not_found(self):
        assert get_pdf_bytes("missing") is None

    def test_create_zip_archive(self):
        self._create_test_pdf("file-a", b"pdf content A")
        self._create_test_pdf("file-b", b"pdf content B")

        entries = [
            {"file_id": "file-a", "filename": "Invoice_001.pdf"},
            {"file_id": "file-b", "filename": "Invoice_002.pdf"},
        ]
        zip_path = create_zip_archive(entries)
        assert os.path.exists(zip_path)
        assert zip_path.endswith(".zip")

        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "Invoice_001.pdf" in names
            assert "Invoice_002.pdf" in names

    def test_create_zip_skips_missing_files(self):
        self._create_test_pdf("exists")
        entries = [
            {"file_id": "exists", "filename": "found.pdf"},
            {"file_id": "missing", "filename": "not_found.pdf"},
        ]
        zip_path = create_zip_archive(entries)
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            assert len(zf.namelist()) == 1
            assert "found.pdf" in zf.namelist()

    def test_cleanup_expired_files(self):
        # Create a file and backdate its mtime
        path = self._create_test_pdf("old-file")
        old_time = time.time() - (Config.PDF_RETENTION_HOURS * 3600 + 100)
        os.utime(path, (old_time, old_time))

        self._create_test_pdf("new-file")

        cleanup_expired_files()

        assert not os.path.exists(os.path.join(self.test_dir, "old-file.pdf"))
        assert os.path.exists(os.path.join(self.test_dir, "new-file.pdf"))

    def test_get_storage_stats(self):
        self._create_test_pdf("stat-1", b"a" * 1024 * 1024)  # 1MB
        self._create_test_pdf("stat-2", b"b" * 2048 * 1024)  # 2MB

        stats = get_storage_stats()
        assert stats["total_files"] == 2
        assert stats["total_size_mb"] > 0

    def test_get_storage_stats_empty_dir(self):
        stats = get_storage_stats()
        assert stats["total_files"] == 0
