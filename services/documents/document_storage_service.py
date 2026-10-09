from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

from services.file_metadata import resolve_pdf_storage_path

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
            try:
                if hasattr(storage, "read_bytes"):
                    return storage.read_bytes(storage_path)
                if hasattr(storage, "get_file_bytes"):
                    return storage.get_file_bytes(storage_path)
                local_path = Path(storage_path)
                if local_path.is_file():
                    return local_path.read_bytes()
            except Exception:
                if strict_metadata:
                    raise
                logger.warning(
                    "Document storage_path lookup failed; trying legacy filename lookup "
                    "submission_id=%s filename=%s storage_path=%s.",
                    submission_id,
                    clean_filename,
                    storage_path,
                    exc_info=True,
                )

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

    def save_pdf(
        self,
        *,
        storage,
        slug: str,
        filename: str,
        document_bytes: bytes,
        document_type: str | None,
        signed: bool,
    ) -> str:
        if hasattr(storage, "save_pdf"):
            storage.save_pdf(
                slug,
                filename,
                document_bytes,
                document_type=document_type,
                signed=signed,
            )
        else:
            storage_path = resolve_pdf_storage_path(
                storage,
                slug,
                filename,
                document_type=document_type,
                signed=signed,
            )
            local_path = Path(storage_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(document_bytes)

        return resolve_pdf_storage_path(
            storage,
            slug,
            filename,
            document_type=document_type,
            signed=signed,
        )

    def document_exists(
        self,
        *,
        storage,
        slug: str,
        filename: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        clean_filename = Path(filename).name
        storage_path = str((metadata or {}).get("storage_path") or "").strip()
        if storage_path:
            try:
                validated_path = validate_storage_path(storage_path)
                if hasattr(storage, "exists") and storage.exists(validated_path):
                    return True
                if not hasattr(storage, "exists") and Path(validated_path).is_file():
                    return True
            except Exception:
                logger.warning(
                    "Nie udalo sie sprawdzic storage_path dokumentu submission_id=%s filename=%s storage_path=%s.",
                    (metadata or {}).get("public_submission_id", ""),
                    clean_filename,
                    storage_path,
                    exc_info=True,
                )

        try:
            storage.get_pdf_bytes(slug, clean_filename)
            return True
        except Exception:
            return False
