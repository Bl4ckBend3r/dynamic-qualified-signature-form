from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from services.file_metadata import record_submission_file

logger = logging.getLogger(__name__)


class SubmissionDocumentType:
    FORM_PDF = "form_pdf"
    SIGNED_FORM_PDF = "signed_form_pdf"
    DECLARATION = "declaration"
    SIGNED_DECLARATION = "signed_declaration"
    AGREEMENT = "agreement"
    SIGNED_AGREEMENT = "signed_agreement"
    TRAINING_AGREEMENT = "training_agreement"
    SIGNED_TRAINING_AGREEMENT = "signed_training_agreement"


GENERATED_STATUS = "generated"
SIGNED_STATUS = "signed"


class SubmissionDocumentService:
    """Dual-write adapter that treats SubmissionFile as document metadata."""

    def __init__(self, submission_repository=None, storage=None, log: logging.Logger | None = None) -> None:
        self.submission_repository = submission_repository
        self.storage = storage
        self.logger = log or logger

    def record_generated_document(
        self,
        *,
        submission_id: str,
        form_slug: str,
        filename: str,
        file_bytes: bytes | None = None,
        document_id: str = "",
        document_type: str = "",
        original_filename: str = "",
        agreement_number: str = "",
        training_key: str = "",
        generated_at: datetime | None = None,
        storage=None,
    ) -> bool:
        return self.record_document_metadata(
            submission_id=submission_id,
            form_slug=form_slug,
            filename=filename,
            file_bytes=file_bytes,
            document_id=document_id,
            document_type=document_type,
            original_filename=original_filename,
            agreement_number=agreement_number,
            training_key=training_key,
            generated_at=generated_at or datetime.now(timezone.utc),
            signed=False,
            status=GENERATED_STATUS,
            storage=storage,
        )

    def record_signed_document(
        self,
        *,
        submission_id: str,
        form_slug: str,
        filename: str,
        file_bytes: bytes | None = None,
        document_id: str = "",
        document_type: str = "",
        original_filename: str = "",
        signature_status: str = "",
        signature_validation_result: Mapping[str, Any] | None = None,
        agreement_number: str = "",
        training_key: str = "",
        signed_at: datetime | None = None,
        storage=None,
    ) -> bool:
        return self.record_document_metadata(
            submission_id=submission_id,
            form_slug=form_slug,
            filename=filename,
            file_bytes=file_bytes,
            document_id=document_id,
            document_type=document_type,
            original_filename=original_filename,
            signature_status=signature_status,
            signature_validation_result=dict(signature_validation_result or {}),
            agreement_number=agreement_number,
            training_key=training_key,
            signed_at=signed_at or datetime.now(timezone.utc),
            signed=True,
            status=SIGNED_STATUS,
            storage=storage,
        )

    def record_document_metadata(
        self,
        *,
        submission_id: str,
        form_slug: str,
        filename: str,
        file_bytes: bytes | None = None,
        document_id: str = "",
        document_type: str = "",
        original_filename: str = "",
        signed: bool = False,
        status: str = "uploaded",
        signature_status: str = "",
        signature_validation_result: Mapping[str, Any] | None = None,
        agreement_number: str = "",
        training_key: str = "",
        generated_at: datetime | None = None,
        signed_at: datetime | None = None,
        storage=None,
    ) -> bool:
        try:
            return record_submission_file(
                submission_repository=self.submission_repository,
                submission_id=submission_id,
                form_slug=form_slug,
                filename=filename,
                storage=storage or self.storage,
                file_bytes=file_bytes,
                document_id=document_id,
                document_type=document_type,
                signed=signed,
                status=status,
                original_filename=original_filename,
                signature_status=signature_status,
                signature_validation_result=dict(signature_validation_result or {}),
                agreement_number=agreement_number,
                training_key=training_key,
                generated_at=generated_at,
                signed_at=signed_at,
            )
        except Exception:
            self.logger.warning(
                "Pominieto dual-write metadanych dokumentu %s dla zgloszenia %s.",
                filename,
                submission_id,
                exc_info=True,
            )
            return False

    def get_document_by_type(self, submission_id: str, document_type: str) -> dict | None:
        if not self.submission_repository or not hasattr(self.submission_repository, "get_document_by_type"):
            return None
        try:
            return self.submission_repository.get_document_by_type(submission_id, document_type)
        except Exception:
            self.logger.warning(
                "Nie udalo sie odczytac dokumentu typu %s dla zgloszenia %s.",
                document_type,
                submission_id,
                exc_info=True,
            )
            return None

    def list_documents(self, submission_id: str) -> list[dict]:
        if not self.submission_repository or not hasattr(self.submission_repository, "list_submission_files"):
            return []
        try:
            return self.submission_repository.list_submission_files(submission_id)
        except Exception:
            self.logger.warning("Nie udalo sie odczytac metadanych dokumentow dla %s.", submission_id, exc_info=True)
            return []

    def get_document_file(
        self,
        submission_id: str,
        *,
        document_type: str,
        filename: str = "",
        training_key: str = "",
    ) -> dict | None:
        if not self.submission_repository or not hasattr(self.submission_repository, "get_submission_file_for_document"):
            return None
        try:
            return self.submission_repository.get_submission_file_for_document(
                submission_id,
                document_type=document_type,
                filename=filename,
                training_key=training_key,
            )
        except Exception:
            self.logger.warning(
                "Nie udalo sie odczytac metadanych dokumentu %s dla %s.",
                document_type,
                submission_id,
                exc_info=True,
            )
            return None

    def sync_from_legacy_fields(self, submission: Mapping[str, Any]) -> list[dict]:
        """Prepare legacy metadata records without writing a backfill in P4.0."""
        submission_id = str(submission.get("submission_id") or "").strip()
        form_slug = str(submission.get("form_slug") or "").strip()
        candidates = [
            ("pdf_filename", SubmissionDocumentType.FORM_PDF, False),
            ("signed_pdf_filename", SubmissionDocumentType.SIGNED_FORM_PDF, True),
            ("declaration_filename", SubmissionDocumentType.DECLARATION, False),
            ("declaration_signed_filename", SubmissionDocumentType.SIGNED_DECLARATION, True),
            ("agreement_filename", SubmissionDocumentType.AGREEMENT, False),
            ("agreement_signed_filename", SubmissionDocumentType.SIGNED_AGREEMENT, True),
        ]
        prepared = []
        for field_name, document_type, signed in candidates:
            filename = str(submission.get(field_name) or "").strip()
            if filename:
                prepared.append(
                    {
                        "submission_id": submission_id,
                        "form_slug": form_slug,
                        "filename": filename,
                        "document_id": field_name,
                        "document_type": document_type,
                        "signed": signed,
                    }
                )
        return prepared
