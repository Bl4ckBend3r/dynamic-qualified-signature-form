from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from services.file_metadata import resolve_pdf_storage_path

logger = logging.getLogger(__name__)


class DocumentStorageError(ValueError):
    pass


@dataclass(frozen=True)
class _PdfRollbackState:
    storage_path: str
    existed: bool
    previous_bytes: bytes | None


@dataclass(frozen=True)
class PdfWriteReceipt:
    storage_path: str
    slug: str
    filename: str
    document_type: str | None
    signed: bool
    rollback_state: _PdfRollbackState | None


def validate_storage_path(storage_path: str) -> str:
    normalized = str(storage_path or "").replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part]
    if not normalized or normalized.startswith("/") or ":" in normalized or any(part == ".." for part in parts):
        raise DocumentStorageError("Nieprawidlowa sciezka dokumentu.")
    return normalized


class DocumentStorageService:
    @contextmanager
    def transactional_pdf_write(
        self,
        *,
        storage,
        slug: str,
        filename: str,
        document_bytes: bytes,
        document_type: str | None,
        signed: bool,
    ) -> Iterator[str]:
        """Write a PDF and compensate the storage write if a later step fails."""
        receipt = self.save_pdf_recoverable(
            storage=storage,
            slug=slug,
            filename=filename,
            document_bytes=document_bytes,
            document_type=document_type,
            signed=signed,
        )
        try:
            yield receipt.storage_path
        except Exception:
            try:
                self.rollback_pdf_write(storage=storage, receipt=receipt)
            except Exception:
                logger.exception(
                    "Nie udalo sie wycofac zapisu PDF po bledzie dalszego etapu: %s.",
                    receipt.storage_path,
                )
            raise

    def save_pdf_recoverable(
        self,
        *,
        storage,
        slug: str,
        filename: str,
        document_bytes: bytes,
        document_type: str | None,
        signed: bool,
    ) -> PdfWriteReceipt:
        """Write a PDF and return the state needed for explicit compensation."""
        storage_path = resolve_pdf_storage_path(
            storage,
            slug,
            filename,
            document_type=document_type,
            signed=signed,
        )
        rollback_state = self._snapshot_pdf(storage=storage, storage_path=storage_path)
        self.save_pdf(
            storage=storage,
            slug=slug,
            filename=filename,
            document_bytes=document_bytes,
            document_type=document_type,
            signed=signed,
        )
        return PdfWriteReceipt(
            storage_path=storage_path,
            slug=slug,
            filename=filename,
            document_type=document_type,
            signed=signed,
            rollback_state=rollback_state,
        )

    def rollback_pdf_write(self, *, storage, receipt: PdfWriteReceipt) -> None:
        self._restore_pdf(
            storage=storage,
            slug=receipt.slug,
            filename=receipt.filename,
            document_type=receipt.document_type,
            signed=receipt.signed,
            rollback_state=receipt.rollback_state,
        )

    def save_attachment(self, *, storage, slug: str, filename: str, content: bytes, mime_type: str) -> str:
        """Keep ordinary attachments beside document PDFs behind the storage layer."""
        from services.upload_validation import validate_upload_filename, SAFE_ATTACHMENT_EXTENSIONS
        validate_upload_filename(filename, allowed_suffixes={f".{ext}" for ext in SAFE_ATTACHMENT_EXTENSIONS})
        output_dir = str(getattr(storage, "output_dir", "output")).strip("/")
        path = validate_storage_path(f"{output_dir}/{slug}/documents/{filename}")
        if hasattr(storage, "write_bytes"):
            if hasattr(storage, "ensure_form_output_structure"):
                storage.ensure_form_output_structure(slug)
            if hasattr(storage, "mkdir"):
                storage.mkdir(f"{output_dir}/{slug}/documents")
            storage.write_bytes(path, content, mime_type)
        else:
            local_path = Path(path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content)
        return path

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

    @staticmethod
    def _snapshot_pdf(*, storage, storage_path: str) -> _PdfRollbackState | None:
        if not hasattr(storage, "exists"):
            return None
        existed = bool(storage.exists(storage_path))
        if not existed:
            return _PdfRollbackState(storage_path=storage_path, existed=False, previous_bytes=None)
        if hasattr(storage, "read_bytes"):
            previous_bytes = storage.read_bytes(storage_path)
        elif hasattr(storage, "get_file_bytes"):
            previous_bytes = storage.get_file_bytes(storage_path)
        else:
            raise DocumentStorageError("Storage nie pozwala bezpiecznie odtworzyc istniejacego PDF.")
        return _PdfRollbackState(storage_path=storage_path, existed=True, previous_bytes=previous_bytes)

    @staticmethod
    def _restore_pdf(
        *,
        storage,
        slug: str,
        filename: str,
        document_type: str | None,
        signed: bool,
        rollback_state: _PdfRollbackState | None,
    ) -> None:
        if rollback_state is None:
            logger.error(
                "Storage nie udostepnia exists(); nie mozna bezpiecznie wycofac zapisu PDF: %s.",
                filename,
            )
            return
        if rollback_state.existed:
            if hasattr(storage, "write_bytes"):
                storage.write_bytes(rollback_state.storage_path, rollback_state.previous_bytes or b"", "application/pdf")
                return
            storage.save_pdf(
                slug,
                filename,
                rollback_state.previous_bytes or b"",
                document_type=document_type,
                signed=signed,
            )
            return
        if hasattr(storage, "delete"):
            storage.delete(rollback_state.storage_path, missing_ok=True)
            return
        Path(rollback_state.storage_path).unlink(missing_ok=True)
