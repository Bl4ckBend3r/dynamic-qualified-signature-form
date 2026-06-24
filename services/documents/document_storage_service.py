from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)


class DocumentStorageError(ValueError):
    pass


def validate_storage_path(storage_path: str) -> str:
    normalized = str(storage_path or "").replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part]
    if not normalized or normalized.startswith("/") or ":" in normalized or any(part == ".." for part in parts):
        raise DocumentStorageError("Nieprawidlowa sciezka dokumentu.")
    return normalized


class DocumentStorageService:
    def read_document_bytes(
        self,
        *,
        storage,
        slug: str,
        filename: str,
        metadata: Mapping[str, object] | None,
        submission_id: str = "",
        strict_metadata: bool = False,
    ) -> bytes:
        clean_filename = Path(filename).name
        if metadata and metadata.get("storage_path"):
            storage_path = validate_storage_path(str(metadata["storage_path"]))
            if hasattr(storage, "read_bytes"):
                return storage.read_bytes(storage_path)
            if hasattr(storage, "get_file_bytes"):
                return storage.get_file_bytes(storage_path)

        if strict_metadata:
            logger.error(
                "strict_document_metadata_missing area=documents submission_id=%s filename=%s reason=missing_submission_file_storage_path",
                submission_id,
                clean_filename,
            )
            raise DocumentStorageError("Brak metadanych dokumentu wymaganych w strict mode.")

        logger.warning(
            "Legacy PDF lookup by filename used for submission=%s filename=%s.",
            submission_id,
            clean_filename,
        )
        return storage.get_pdf_bytes(slug, clean_filename)

    def save_pdf(self, *, storage, slug: str, filename: str, document_bytes: bytes, document_type: str | None, signed: bool) -> None:
        storage.save_pdf(
            slug,
            filename,
            document_bytes,
            document_type=document_type,
            signed=signed,
        )
