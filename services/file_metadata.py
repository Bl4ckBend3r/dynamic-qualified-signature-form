from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def record_submission_file(
    *,
    submission_repository,
    submission_id: str,
    form_slug: str,
    filename: str,
    storage=None,
    file_bytes: bytes | None = None,
    document_id: str = "",
    document_type: str = "",
    signed: bool = False,
    status: str = "uploaded",
    mime_type: str = "application/pdf",
) -> bool:
    if not submission_repository or not hasattr(submission_repository, "record_file"):
        return False

    metadata = {
        "document_id": document_id,
        "document_type": document_type,
        "filename": Path(filename).name,
        "storage_path": resolve_pdf_storage_path(storage, form_slug, filename, document_type, signed),
        "mime_type": mime_type,
        "size_bytes": len(file_bytes) if file_bytes is not None else None,
        "checksum_sha256": hashlib.sha256(file_bytes).hexdigest() if file_bytes is not None else "",
        "signed": signed,
        "status": status,
    }
    recorded = bool(submission_repository.record_file(submission_id, metadata))
    logger.info(
        "Metadane pliku %s dla zgloszenia %s: %s.",
        filename,
        submission_id,
        "zapisane" if recorded else "pominiete",
    )
    return recorded


def resolve_pdf_storage_path(
    storage: Any,
    slug: str,
    filename: str,
    document_type: str | None = None,
    signed: bool | None = None,
) -> str:
    if storage and hasattr(storage, "pdf_storage_path"):
        return storage.pdf_storage_path(slug, filename, document_type=document_type, signed=signed)

    output_dir = str(getattr(storage, "output_dir", "output")).strip("/") if storage else "output"
    clean_filename = Path(filename).name
    if document_type in {"declaration", "deklaracja"}:
        signature_dir = "podpisane" if signed else "niepodpisane"
        return f"{output_dir}/{slug}/pdf/deklaracja/{signature_dir}/{clean_filename}"
    if document_type in {"agreement", "training_agreement", "umowa", "umowy"}:
        signature_dir = "podpisane" if signed else "niepodpisane"
        return f"{output_dir}/{slug}/pdf/umowy/{signature_dir}/{clean_filename}"
    return f"{output_dir}/{slug}/pdf/{clean_filename}"
