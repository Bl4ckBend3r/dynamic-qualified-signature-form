from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from collections import Counter

from models import SubmissionDecision, SubmissionFile, SubmissionTraining, SubmissionWorkflowEvent
from services.nextcloud_storage import NextcloudStorageError
from services.process_service import ProcessStatus
from signature_verifier import verify_signed_pdf_bytes


SIGNED_BY_BENEFICIARY_TYPES = {"signed_agreement", "signed_training_agreement"}
OFFICE_FINAL_TYPE = "agreement_signed_by_office"
OFFICE_WAITING_STATUSES = {
    "agreement_uploaded_by_beneficiary",
    "agreement_waiting_for_office_signature",
    "agreement_signed_by_office",
    "locked",
}
INACTIVE_TRAINING_STATUSES = {
    "unselected",
    "cancelled",
    "cancelled_before_signed_agreement",
    "rejected",
    "inactive_without_signed_agreement",
}


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
    checked_count: int = 0
    found_count: int = 0
    missing_filenames: tuple[str, ...] = ()
    already_confirmed: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    training_ids: tuple[str, ...] = ()


class OfficeSignedAgreementService:
    """Registers exact, per-training office-signed files already placed in Nextcloud."""

    def __init__(self, storage, verifier=verify_signed_pdf_bytes) -> None:
        self._storage = storage
        self.verifier = verifier

    @property
    def storage(self):
        return self._storage

    def check(
        self,
        db,
        form,
        submission,
        *,
        actor,
        training_key: str | None = None,
        email_requested: bool = False,
    ) -> OfficeSignedAgreementResult:
        folder = self._folder(form)
        trainings = self._eligible_trainings(db, submission, training_key=training_key)
        if not trainings:
            raise OfficeSignedAgreementError("Nie znaleziono aktywnej umowy oczekującej na podpis urzędu.")
        pairs = [(training, self._beneficiary_filename(db, submission, training)) for training in trainings]
        expected = [filename for _, filename in pairs if filename]
        if not expected:
            raise OfficeSignedAgreementError("Nie znaleziono nazwy umowy podpisanej przez beneficjenta.")
        try:
            if not self.storage.exists(folder):
                return OfficeSignedAgreementResult(
                    False, folder, tuple(expected),
                    "Folder umów podpisanych przez urząd nie istnieje w Nextcloud.",
                    checked_count=len(pairs), missing_filenames=tuple(expected),
                )
        except NextcloudStorageError as exc:
            message = f"Nie udało się połączyć z Nextcloud: {type(exc).__name__}."
            return OfficeSignedAgreementResult(False, folder, tuple(expected), message, checked_count=len(pairs), errors=(message,))

        now = datetime.now(timezone.utc)
        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        found: list[str] = []
        missing: list[str] = []
        already: list[str] = []
        errors: list[str] = []
        changed_ids: list[str] = []
        changed_keys: list[str] = []
        conflicts = {filename for filename, count in Counter(expected).items() if count > 1}
        other_submission_names = db.query(SubmissionFile.filename).filter(
            SubmissionFile.form_slug == submission.form_slug,
            SubmissionFile.submission_id != submission.id,
            SubmissionFile.document_type.in_(SIGNED_BY_BENEFICIARY_TYPES),
            SubmissionFile.signed.is_(True),
            SubmissionFile.filename.in_(expected),
        ).all()
        conflicts.update(str(item[0]) for item in other_submission_names)
        reported_conflicts: set[str] = set()
        for training, filename in pairs:
            if not filename:
                errors.append(f"Brak nazwy umowy beneficjenta dla szkolenia {training.training_id}.")
                continue
            key = str(training.agreement_id or training.training_id)
            if filename in conflicts:
                if filename not in reported_conflicts:
                    errors.append(
                        f"Konflikt nazwy {filename}: więcej niż jedna umowa w zgłoszeniu oczekuje tego samego pliku."
                    )
                    reported_conflicts.add(filename)
                continue
            existing = db.query(SubmissionFile.id).filter(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.document_type == OFFICE_FINAL_TYPE,
                SubmissionFile.training_key.in_([key, training.training_id]),
            ).first()
            if training.status == "agreement_signed_by_office" or existing:
                already.append(filename)
                continue
            path = f"{folder}/{filename}"
            try:
                file_exists = self.storage.exists(path)
            except NextcloudStorageError as exc:
                errors.append(f"{filename}: {type(exc).__name__}")
                continue
            if not file_exists:
                missing.append(filename)
                continue
            try:
                verification = dict(self.verifier(self.storage.read_bytes(path)))
            except Exception as exc:
                errors.append(f"{filename}: walidacja podpisu ({exc.__class__.__name__})")
                continue
            validation_status = str(verification.get("validation_status") or "INDETERMINATE").upper()
            if validation_status in {"INVALID", "UNSIGNED"}:
                errors.append(f"{filename}: {validation_status} — {verification.get('reason') or 'nieprawidłowy podpis'}")
                continue
            db.add(SubmissionFile(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                document_id="training_agreement",
                document_type=OFFICE_FINAL_TYPE,
                file_role="final_signed_agreement",
                storage_provider="nextcloud",
                filename=filename,
                original_filename=filename,
                storage_path=path,
                mime_type="application/pdf",
                signed=True,
                status="signed",
                signature_status=validation_status.lower(),
                signature_validation_result=verification,
                training_key=key,
                signed_at=now,
            ))
            training.status = "agreement_signed_by_office"
            training.updated_at = now
            found.append(filename)
            changed_ids.append(training.training_id)
            changed_keys.append(key)

        decision = None
        if found:
            pending = [
                item for item in db.query(SubmissionTraining).filter(
                    SubmissionTraining.submission_id == submission.id
                ).all()
                if item.status not in INACTIVE_TRAINING_STATUSES
                and item.status != "agreement_signed_by_office"
            ]
            final_status = (
                ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
                if not pending and not bool(getattr(form, "training_selection_open", True))
                else ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value
            )
            submission.process_status = final_status
            submission.workflow_step = final_status
            submission.agreement_signed_filename = found[0]
            submission.updated_at = now
            db.add(SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                previous_status=previous_status,
                new_status=final_status,
                previous_step=previous_step,
                new_step=final_status,
                actor_id=getattr(actor, "id", None),
                actor_email=str(getattr(actor, "email", "") or ""),
                actor_role=str(getattr(actor, "role", "") or "admin"),
                reason="Odnaleziono finalne umowy w folderze Nextcloud podpisów urzędu.",
                source="agreement_signed_by_office",
                side_effects={"training_ids": changed_ids, "filenames": found},
                created_at=now,
            ))
            decision = SubmissionDecision(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                decision="office_agreement_found",
                justification=f"Odnaleziono finalne umowy: {', '.join(found)}.",
                officer_id=getattr(actor, "id", None),
                officer_email=str(getattr(actor, "email", "") or ""),
                previous_status=previous_status,
                target_status=final_status,
                email_requested=email_requested,
                email_sent=False,
                decided_at=now,
            )
            db.add(decision)
            db.flush()
        message = f"Znaleziono: {len(found)}, brakujące: {len(missing)}, już zatwierdzone: {len(already)}, błędy: {len(errors)}."
        return OfficeSignedAgreementResult(
            bool(found), folder, tuple(expected), message,
            decision_record=decision,
            decision_status=(ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value if found else ""),
            final_status=(submission.process_status if found else ""),
            checked_count=len(pairs), found_count=len(found),
            missing_filenames=tuple(missing), already_confirmed=tuple(already),
            errors=tuple(errors), training_ids=tuple(changed_keys),
        )

    def _eligible_trainings(self, db, submission, *, training_key: str | None = None) -> list[SubmissionTraining]:
        rows = db.query(SubmissionTraining).filter(SubmissionTraining.submission_id == submission.id).all()
        eligible = [
            row for row in rows
            if row.is_locked or row.status in OFFICE_WAITING_STATUSES or row.agreement_file_id
        ]
        if training_key:
            key = str(training_key)
            eligible = [row for row in eligible if key in {row.training_id, str(row.agreement_id or "")}]
        return eligible

    @staticmethod
    def _beneficiary_filename(db, submission, training: SubmissionTraining) -> str:
        if training.agreement_file_id:
            item = db.get(SubmissionFile, training.agreement_file_id)
            if item and item.signed:
                return PurePosixPath(str(item.filename or "")).name
        keys = [str(training.agreement_id or ""), training.training_id]
        item = db.query(SubmissionFile).filter(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.document_type.in_(SIGNED_BY_BENEFICIARY_TYPES),
            SubmissionFile.signed.is_(True),
            SubmissionFile.training_key.in_(keys),
        ).order_by(SubmissionFile.id.desc()).first()
        return PurePosixPath(str(getattr(item, "filename", "") or "")).name

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
        return list(dict.fromkeys(PurePosixPath(str(row[0] or "")).name for row in rows if str(row[0] or "").strip()))
