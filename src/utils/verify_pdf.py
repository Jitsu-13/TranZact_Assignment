"""
Standalone PDF Verification Utility.

Usage:
    python -m src.utils.verify_pdf <file_id>

Verifies that a generated PDF has not been tampered with by comparing
its current SHA-256 hash against the hash stored at generation time.
"""

import sys
import hashlib

from src.services.storage import get_pdf_bytes
from src.services.hash_registry import get_hash


def verify(file_id: str) -> None:
    pdf_bytes = get_pdf_bytes(file_id)
    if not pdf_bytes:
        print(f"ERROR: PDF not found for file_id={file_id}")
        sys.exit(1)

    stored_hash = get_hash(file_id)
    if not stored_hash:
        print(f"WARNING: No hash record found for file_id={file_id}")
        print("The file may have expired from the hash registry.")
        sys.exit(1)

    computed_hash = hashlib.sha256(pdf_bytes).hexdigest()

    print(f"File ID:       {file_id}")
    print(f"Stored hash:   {stored_hash}")
    print(f"Computed hash: {computed_hash}")
    print(f"Match:         {'YES — file is intact' if stored_hash == computed_hash else 'NO — FILE HAS BEEN TAMPERED WITH'}")

    sys.exit(0 if stored_hash == computed_hash else 2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m src.utils.verify_pdf <file_id>")
        sys.exit(1)
    verify(sys.argv[1])
