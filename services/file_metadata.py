from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from services.document_naming_service import resolve_pdf_storage_path as build_pdf_storage_path

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
    original_filename: str = "",
    signature_status: str = "",
    signature_validation_result: dict | None = None,
    agreement_number: str = "",
    training_key: str = "",
    generated_at: datetime | None = None,
    signed_at: datetime | None = None,
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
        "original_filename": Path(original_filename).name if original_filename else "",
        "signature_status": signature_status,
        "signature_validation_result": signature_validation_result or {},
        "agreement_number": agreement_number,
        "training_key": training_key,
        "generated_at": generated_at,
        "signed_at": signed_at,
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
    return build_pdf_storage_path(
        output_dir=output_dir,
        slug=slug,
        filename=filename,
        document_type=document_type,
        signed=signed,
    )
