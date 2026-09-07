from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping

from services.documents.document_storage_service import DocumentStorageService
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
        storage_path: str = "",
        workflow_step_at_upload: str = "",
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
            workflow_step_at_upload=workflow_step_at_upload,
            storage_path=storage_path,
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
        storage_path: str = "",
        workflow_step_at_upload: str = "",
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
            workflow_step_at_upload=workflow_step_at_upload,
            storage_path=storage_path,
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
        storage_path: str = "",
        workflow_step_at_upload: str = "",
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
                storage_path=storage_path,
                workflow_step_at_upload=workflow_step_at_upload,
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
        """Prepare metadata candidates from legacy FormSubmission fields."""
        row = submission.get("row") if isinstance(submission.get("row"), Mapping) else submission
        submission_id = str(submission.get("submission_id") or row.get("submission_id") or "").strip()
        form_slug = str(submission.get("form_slug") or row.get("form_slug") or "").strip()
        training_agreements = self._json_list(row.get("training_agreements"))
        training_filenames = {
            str(item.get("filename") or "").strip()
            for item in training_agreements
            if str(item.get("filename") or "").strip()
        }
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
            filename = str(row.get(field_name) or "").strip()
            if field_name == "agreement_filename" and filename in training_filenames:
                continue
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
        for index, agreement in enumerate(training_agreements, start=1):
            training_key = str(agreement.get("training_id") or agreement.get("id") or f"training_{index}")
            common = {
                "submission_id": submission_id,
                "form_slug": form_slug,
                "document_id": "training_agreement",
                "agreement_number": str(agreement.get("number") or agreement.get("agreement_number") or ""),
                "training_key": training_key,
            }
            filename = str(agreement.get("filename") or "").strip()
            if filename:
                prepared.append(
                    {
                        **common,
                        "filename": filename,
                        "document_type": SubmissionDocumentType.TRAINING_AGREEMENT,
                        "signed": False,
                        "generated_at": self._as_datetime(agreement.get("generated_at")),
                    }
                )
            signed_filename = str(agreement.get("signed_filename") or "").strip()
            if signed_filename:
                prepared.append(
                    {
                        **common,
                        "filename": signed_filename,
                        "document_type": SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
                        "signed": True,
                    }
                )
        return prepared

    def backfill_existing_legacy_documents(self, submission: Mapping[str, Any]) -> list[dict]:
        """Create missing SubmissionFile rows only for PDFs confirmed in storage."""
        if not getattr(self.submission_repository, "supports_file_metadata", False):
            return []
        storage = self.storage
        storage_service = DocumentStorageService()
        created = []
        for candidate in self.sync_from_legacy_fields(submission):
            filename = Path(str(candidate.get("filename") or "")).name
            if not filename:
                continue
            existing = self.get_document_file(
                candidate["submission_id"],
                document_type=candidate["document_type"],
                filename=filename,
                training_key=str(candidate.get("training_key") or ""),
            )
            storage_document_type = (
                "declaration"
                if candidate["document_type"] in {
                    SubmissionDocumentType.DECLARATION,
                    SubmissionDocumentType.SIGNED_DECLARATION,
                }
                else "agreement"
                if candidate["document_type"] in {
                    SubmissionDocumentType.AGREEMENT,
                    SubmissionDocumentType.SIGNED_AGREEMENT,
                    SubmissionDocumentType.TRAINING_AGREEMENT,
                    SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
                }
                else None
            )
            storage_path = ""
            try:
                from services.file_metadata import resolve_pdf_storage_path

                storage_path = resolve_pdf_storage_path(
                    storage,
                    candidate["form_slug"],
                    filename,
                    storage_document_type,
                    bool(candidate.get("signed")),
                )
            except ValueError:
                continue
            if existing and str(existing.get("storage_path") or "") == storage_path:
                continue
            if not storage_service.document_exists(
                storage=storage,
                slug=candidate["form_slug"],
                filename=filename,
                metadata={"storage_path": storage_path, "public_submission_id": candidate["submission_id"]},
            ):
                self.logger.warning(
                    "Historical document missing in storage public_submission_id=%s filename=%s "
                    "document_type=%s storage_path=%s.",
                    candidate["submission_id"],
                    filename,
                    candidate["document_type"],
                    storage_path,
                )
                continue
            metadata = {
                **candidate,
                "filename": filename,
                "storage_path": storage_path,
                "storage": storage,
                "status": "signed" if candidate.get("signed") else "generated",
            }
            recorded = self.record_document_metadata(**metadata)
            if recorded:
                created.append(candidate)
        return created

    def list_available_documents(self, submission: Mapping[str, Any]) -> list[dict]:
        submission_id = str(submission.get("submission_id") or "").strip()
        form_slug = str(submission.get("form_slug") or "").strip()
        files = self.list_documents(submission_id)
        storage_service = DocumentStorageService()
        available = []
        for file_row in files:
            item = dict(file_row)
            if str(item.get("status") or "").strip().lower() == "superseded":
                continue
            item["storage_exists"] = storage_service.document_exists(
                storage=self.storage,
                slug=form_slug,
                filename=str(item.get("filename") or ""),
                metadata=item,
            )
            if item["storage_exists"]:
                available.append(item)
        return available

    @staticmethod
    def _json_list(value: Any) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        try:
            parsed = json.loads(str(value or ""))
        except (json.JSONDecodeError, TypeError):
            return []
        return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc)
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
