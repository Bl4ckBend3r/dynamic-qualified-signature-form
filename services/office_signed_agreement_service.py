from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from models import SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.nextcloud_storage import NextcloudStorageError
from services.process_service import ProcessStatus


SIGNED_BY_BENEFICIARY_TYPES = {"signed_agreement", "signed_training_agreement"}
OFFICE_FINAL_TYPE = "agreement_signed_by_office"


class OfficeSignedAgreementError(ValueError):
    pass


@dataclass(frozen=True)
class OfficeSignedAgreementResult:
    found: bool
    folder: str
    expected_filenames: tuple[str, ...]
    message: str = ""
    decision_record: SubmissionDecision | None = None
    decision_status: str = ""
    final_status: str = ""


class OfficeSignedAgreementService:
    """Registers final office-signed files already placed in Nextcloud."""

    def check(self, db, form, submission, *, actor) -> OfficeSignedAgreementResult:
        if str(submission.process_status or "") != ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value:
            raise OfficeSignedAgreementError("Sprawdzenie jest dostępne wyłącznie na etapie oczekiwania na podpis urzędu.")
        folder = self._folder(form)
        expected = self._expected_files(db, submission)
        if not expected:
            raise OfficeSignedAgreementError("Nie znaleziono nazwy umowy podpisanej przez beneficjenta.")
        storage = self.storage
        try:
            if not storage.exists(folder):
                return OfficeSignedAgreementResult(False, folder, tuple(expected), "Folder umów podpisanych przez urząd nie istnieje w Nextcloud.")
            missing = [name for name in expected if not storage.exists(f"{folder}/{name}")]
        except NextcloudStorageError as exc:
            return OfficeSignedAgreementResult(False, folder, tuple(expected), f"Nie udało się połączyć z Nextcloud: {type(exc).__name__}.")
        if missing:
            return OfficeSignedAgreementResult(False, folder, tuple(expected), "Nie znaleziono pliku podpisanego przez urząd.")

        now = datetime.now(timezone.utc)
        for filename in expected:
            path = f"{folder}/{filename}"
            exists = db.query(SubmissionFile.id).filter(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.document_type == OFFICE_FINAL_TYPE,
                SubmissionFile.storage_path == path,
            ).first()
            if not exists:
                db.add(SubmissionFile(
                    submission_id=submission.id,
                    public_submission_id=submission.submission_id,
                    form_slug=submission.form_slug,
                    document_id="agreement",
                    document_type=OFFICE_FINAL_TYPE,
                    file_role="final_signed_agreement",
                    storage_provider="nextcloud",
                    filename=filename,
                    original_filename=filename,
                    storage_path=path,
                    mime_type="application/pdf",
                    signed=True,
                    status="signed",
                    signature_status="office_signed",
                    signed_at=now,
                ))
        previous_status = str(submission.process_status or "")
        submission.process_status = ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
        submission.workflow_step = "agreement_signed_by_office"
        submission.agreement_signed_filename = expected[0]
        submission.updated_at = now
        db.add(SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            previous_status=previous_status,
            new_status=ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
            previous_step="office_agreement_signature",
            new_step="agreement_signed_by_office",
            actor_id=getattr(actor, "id", None),
            actor_email=str(getattr(actor, "email", "") or ""),
            actor_role=str(getattr(actor, "role", "") or "admin"),
            reason="Plik odnaleziony w folderze Nextcloud podpisów urzędu.",
            source="agreement_signed_by_office",
            created_at=now,
        ))
        decision = SubmissionDecision(
            submission_id=submission.id, public_submission_id=submission.submission_id,
            form_slug=submission.form_slug, decision="office_agreement_found",
            justification="Plik podpisany przez urząd został odnaleziony w Nextcloud.",
            officer_id=getattr(actor, "id", None), officer_email=str(getattr(actor, "email", "") or ""),
            previous_status=previous_status, target_status=ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
            email_requested=True, email_sent=False, decided_at=now,
        )
        db.add(decision)
        db.flush()
        return OfficeSignedAgreementResult(True, folder, tuple(expected), decision_record=decision,
            decision_status=ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
            final_status=ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value)

    @property
    def storage(self):
        return self._storage

    def __init__(self, storage) -> None:
        self._storage = storage

    def _folder(self, form) -> str:
        slug = str(getattr(form, "slug", "") or "").strip()
        if not slug:
            raise OfficeSignedAgreementError("Brak sluga formularza potrzebnego do zbudowania ścieżki Nextcloud.")
        if hasattr(self.storage, "office_signed_agreement_directory"):
            return self.storage.office_signed_agreement_directory(slug)
        output_dir = str(getattr(self.storage, "output_dir", "output") or "output").strip("/")
        return f"{output_dir}/{slug}/pdf/umowy/podpisane_przez_urzad"

    def folder_for_form(self, form) -> str:
        return self._folder(form)

    @staticmethod
    def _expected_files(db, submission) -> list[str]:
        rows = db.query(SubmissionFile.filename).filter(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.document_type.in_(SIGNED_BY_BENEFICIARY_TYPES),
            SubmissionFile.signed.is_(True),
        ).all()
        names = [PurePosixPath(str(row[0] or "")).name for row in rows if str(row[0] or "").strip()]
        if not names and str(submission.agreement_signed_filename or "").strip():
            names = [PurePosixPath(submission.agreement_signed_filename).name]
        return list(dict.fromkeys(names))
