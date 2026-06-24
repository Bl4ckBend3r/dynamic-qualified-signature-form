from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from services.documents.document_storage_service import DocumentStorageService
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType
from services.upload_validation import UploadValidationError, validate_pdf_upload
from signature_verifier import verify_signed_pdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignedSubmissionPdfUpload:
    signed_filename: str
    verification: Mapping[str, object]


class DocumentSigningService:
    def __init__(
        self,
        *,
        storage=None,
        submission_repository=None,
        submission_service=None,
        document_service=None,
        document_storage_service: DocumentStorageService | None = None,
        submission_document_service: SubmissionDocumentService | None = None,
        verifier: Callable[[Path], Mapping[str, object]] = verify_signed_pdf,
    ) -> None:
        self.storage = storage
        self.submission_repository = submission_repository
        self.submission_service = submission_service
        self.document_service = document_service
        self.document_storage_service = document_storage_service or DocumentStorageService()
        self.submission_document_service = submission_document_service or SubmissionDocumentService(
            submission_repository=submission_repository,
            storage=storage,
        )
        self.verifier = verifier

    def upload_signed_submission_pdf(
        self,
        *,
        slug: str,
        submission_id: str,
        uploaded_file,
        temp_dir: str | Path,
    ) -> SignedSubmissionPdfUpload:
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("Nie wybrano pliku PDF.")
        if not self.storage or not self.submission_repository or not self.submission_service:
            raise RuntimeError("DocumentSigningService is not configured.")

        signed_pdf_filename = self.submission_service.build_signed_pdf_filename(slug, submission_id)
        uploaded_bytes = uploaded_file.read()
        try:
            validate_pdf_upload(uploaded_file.filename, uploaded_bytes, getattr(uploaded_file, "mimetype", None))
        except UploadValidationError as exc:
            raise ValueError(str(exc)) from exc

        verification = self._verify_pdf(uploaded_bytes, temp_dir)
        if not verification.get("is_signed"):
            raise ValueError("Przeslany plik nie zawiera podpisu PDF.")
        if not verification.get("is_szafir_signature"):
            raise ValueError("Przeslany plik nie jest podpisem Szafir / KIR.")

        self.document_storage_service.save_pdf(
            storage=self.storage,
            slug=slug,
            filename=signed_pdf_filename,
            document_bytes=uploaded_bytes,
            document_type=None,
            signed=True,
        )
        logger.info("Upload podpisanego PDF do Nextcloud zakonczony sukcesem: %s", signed_pdf_filename)
        self.submission_repository.update(submission_id, {"signed_pdf_filename": signed_pdf_filename})
        self.submission_document_service.record_signed_document(
            submission_id=submission_id,
            form_slug=slug,
            filename=signed_pdf_filename,
            file_bytes=uploaded_bytes,
            document_id="form_submission",
            document_type=SubmissionDocumentType.SIGNED_FORM_PDF,
            original_filename=str(uploaded_file.filename or ""),
            signature_status="valid",
            signature_validation_result=verification,
            storage=self.storage,
        )
        return SignedSubmissionPdfUpload(
            signed_filename=signed_pdf_filename,
            verification=verification,
        )

    def upload_signed_document(
        self,
        *,
        submission: dict,
        document_id: str,
        uploaded_file,
        instance_id: str | None = None,
    ) -> dict:
        if not self.document_service:
            raise RuntimeError("DocumentSigningService document_service is not configured.")
        return self.document_service.upload_signed_document(
            submission,
            document_id,
            uploaded_file,
            instance_id=instance_id,
        )

    def _verify_pdf(self, pdf_bytes: bytes, temp_dir: str | Path) -> Mapping[str, object]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=temp_dir) as tmp_signed:
            tmp_signed_path = Path(tmp_signed.name)
            tmp_signed.write(pdf_bytes)
        try:
            return self.verifier(tmp_signed_path)
        finally:
            tmp_signed_path.unlink(missing_ok=True)
