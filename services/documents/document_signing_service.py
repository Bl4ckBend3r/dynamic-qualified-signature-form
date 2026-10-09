from __future__ import annotations

import logging
import tempfile
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from flask import current_app, has_app_context

from services.documents.document_storage_service import DocumentStorageService
from services.submission_document_service import SubmissionDocumentService, SubmissionDocumentType
from services.upload_validation import UploadValidationError, validate_pdf_upload
from signature_verifier import resolve_signature_policy, signature_verification_result, verify_signed_pdf

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
            self._record_verification_rejection("unsigned")
            raise ValueError("Przeslany plik nie zawiera podpisu PDF.")
        outcome = signature_verification_result(verification, ["mszafir"])
        if outcome != "VALID_SIGNATURE":
            self._record_verification_rejection(str(verification.get("reason_code") or outcome).lower())
            if outcome == "SIGNATURE_TYPE_NOT_ALLOWED":
                raise ValueError("Przeslany plik nie jest podpisem Szafir / KIR.")
            if outcome == "VERIFICATION_ERROR":
                raise ValueError("Nie udało się obecnie zweryfikować podpisu PDF.")
            if outcome == "NO_SIGNATURE":
                raise ValueError("Przeslany plik nie zawiera podpisu PDF.")
            self._record_verification_rejection("unsupported_signer")
            raise ValueError(
                "Nie udało się potwierdzić poprawności podpisu elektronicznego. "
                "Upewnij się, że wgrywasz oryginalny plik pobrany bezpośrednio po podpisaniu dokumentu. "
                "Nie otwieraj i nie zapisuj ponownie podpisanego PDF przed wgraniem."
            )

        storage_path = self.document_storage_service.save_pdf(
            storage=self.storage,
            slug=slug,
            filename=signed_pdf_filename,
            document_bytes=uploaded_bytes,
            document_type=None,
            signed=True,
        )
        logger.info("document_signature_verified", extra={"event": "document_signature_verified", "operation": "signature_verification"})
        self.submission_repository.update(submission_id, {"signed_pdf_filename": signed_pdf_filename})
        recorded = self.submission_document_service.record_signed_document(
            submission_id=submission_id,
            form_slug=slug,
            filename=signed_pdf_filename,
            file_bytes=uploaded_bytes,
            document_id="form_submission",
            document_type=SubmissionDocumentType.SIGNED_FORM_PDF,
            original_filename=str(uploaded_file.filename or ""),
            signature_status=str(verification.get("validation_status") or "INDETERMINATE").lower(),
            signature_validation_result=verification,
            storage_path=storage_path,
            storage=self.storage,
        )
        if getattr(self.submission_repository, "supports_file_metadata", False) and not recorded:
            raise RuntimeError("Nie udalo sie zapisac metadanych podpisanego dokumentu.")
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

    def verify_stored_pdf(self, pdf_bytes, temp_dir, *, storage, slug, filename, storage_path, allowed_signatures=None):
        """Compare the stored artifact to the upload, then verify the original bytes once."""
        diagnostics = {"upload_sha256": sha256(pdf_bytes).hexdigest(), "storage_sha256": None, "verifier_sha256": None}
        try:
            stored = self.document_storage_service.read_document_bytes(storage=storage, slug=slug,
                filename=filename, metadata={"storage_path": storage_path}, strict_metadata=True)
        except Exception:
            return {**diagnostics, "validation_status": "ERROR", "reason_code": "STORAGE_READ_ERROR"}
        diagnostics["storage_sha256"] = sha256(stored).hexdigest()
        if diagnostics["storage_sha256"] != diagnostics["upload_sha256"]:
            return {**diagnostics, "validation_status": "ERROR", "reason_code": "UPLOAD_STORAGE_HASH_MISMATCH"}
        verification = self.verify_uploaded_pdf(pdf_bytes, temp_dir, allowed_signatures=allowed_signatures)
        verification.update({key: value for key, value in diagnostics.items() if key != "verifier_sha256"})
        return verification

    def verify_uploaded_pdf(self, pdf_bytes, temp_dir, *, allowed_signatures=None):
        """Verify uploaded bytes through the shared backend router and admission policy."""
        try:
            verification = dict(self._verify_pdf(pdf_bytes, temp_dir))
        except Exception:
            verification = {"validation_status": "ERROR", "reason_code": "VERIFIER_EXCEPTION"}
        verification.update(resolve_signature_policy(verification, allowed_signatures))
        outcome = signature_verification_result(verification, allowed_signatures)
        verification["result"] = outcome
        if outcome == "SIGNATURE_TYPE_NOT_ALLOWED":
            verification["reason_code"] = "SIGNATURE_TYPE_NOT_ALLOWED"
        return verification

    def _verify_pdf(self, pdf_bytes: bytes, temp_dir: str | Path) -> Mapping[str, object]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=temp_dir) as tmp_signed:
            tmp_signed_path = Path(tmp_signed.name)
            tmp_signed.write(pdf_bytes)
        verifier_digest = None
        try:
            verifier_digest = sha256(tmp_signed_path.read_bytes()).hexdigest()
            if verifier_digest != sha256(pdf_bytes).hexdigest():
                return {"validation_status": "ERROR", "reason_code": "VERIFIER_INPUT_HASH_MISMATCH", "verifier_sha256": verifier_digest}
            metrics = current_app.extensions.get("observability_metrics") if has_app_context() else None
            if metrics is None:
                result = self.verifier(tmp_signed_path)
            else:
                with metrics.operation("signature_verification", "pdf"):
                    result = self.verifier(tmp_signed_path)
            return {**result, "verifier_sha256": verifier_digest}
        except Exception:
            return {"validation_status": "ERROR", "reason_code": "VERIFIER_EXCEPTION", "verifier_sha256": verifier_digest}
        finally:
            tmp_signed_path.unlink(missing_ok=True)

    @staticmethod
    def _record_verification_rejection(reason: str) -> None:
        if not has_app_context():
            return
        metrics = current_app.extensions.get("observability_metrics")
        if metrics is not None:
            metrics.operation_failures.labels(operation="signature_verification", kind="pdf").inc()
        current_app.logger.warning(
            "document_signature_verification_failed",
            extra={
                "event": "document_signature_verification_failed",
                "operation": "signature_verification",
                "failure_reason": reason,
            },
        )
