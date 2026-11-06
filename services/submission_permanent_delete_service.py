"""Permanent removal of submissions and their submission-owned records."""

from __future__ import annotations

from sqlalchemy import delete, or_, select

from models import (
    EmailLog, FormDraft, FormSubmission, RepeatableGroupItemDecision,
    SubmissionAssignmentHistory, SubmissionChecklistEvidence,
    SubmissionChecklistItemResult, SubmissionChecklistResultHistory,
    SubmissionConsent, SubmissionDeadlineNotification, SubmissionDecision,
    SubmissionFile, SubmissionInternalNote, SubmissionInternalNoteMention,
    SubmissionInternalNoteRevision, SubmissionStepDeadline, SubmissionTraining,
    SubmissionWorkflowEvent, TrainingAttendanceRecord,
    TrainingParticipantActionHistory, TrainingSurveyAnswer,
    TrainingSurveyInvitation, TrainingSurveyResponse,
)
from services.documents.document_storage_service import validate_storage_path


class SubmissionPermanentDeleteError(ValueError):
    pass


class SubmissionPermanentDeleteService:
    def __init__(self, *, storage, audit_log_service):
        self.storage = storage
        self.audit_log_service = audit_log_service

    @staticmethod
    def _ids(db, column, condition):
        return list(db.execute(select(column).where(condition)).scalars())

    @staticmethod
    def _delete(db, model, column, ids):
        if ids:
            db.execute(delete(model).where(column.in_(ids)))

    def delete(self, db, *, form, submission_ids: list[int], actor) -> list[str]:
        """Delete all selected rows in the caller's transaction; return exclusive paths.

        The caller must commit before deleting the returned storage paths.
        """
        if not submission_ids or any(type(item) is not int or item <= 0 for item in submission_ids):
            raise SubmissionPermanentDeleteError("Wybierz poprawne identyfikatory zgłoszeń.")
        wanted = set(submission_ids)
        submissions = list(db.execute(
            select(FormSubmission).where(
                FormSubmission.id.in_(wanted), FormSubmission.form_slug == form.slug
            ).execution_options(include_deleted_submissions=True).with_for_update()
        ).scalars())
        if {item.id for item in submissions} != wanted:
            raise SubmissionPermanentDeleteError("Nie znaleziono wszystkich wybranych zgłoszeń.")

        file_rows = list(db.execute(select(SubmissionFile).where(SubmissionFile.submission_id.in_(wanted))).scalars())
        owned_paths = {row.storage_path for row in file_rows if row.storage_path}
        exclusive_paths = []
        prefix = f"{str(getattr(self.storage, 'output_dir', 'output')).strip('/')}/{form.slug}/"
        for path in sorted(owned_paths):
            try:
                clean_path = validate_storage_path(path)
            except ValueError:
                continue
            if not clean_path.startswith(prefix):
                continue
            if clean_path.startswith(f"{prefix}audit/") or clean_path.endswith("/dane.csv"):
                continue
            shared = db.execute(select(SubmissionFile.id).where(
                SubmissionFile.storage_path == path,
                SubmissionFile.submission_id.not_in(wanted),
            ).limit(1)).first()
            filename = clean_path.rsplit("/", 1)[-1]
            legacy_reference = db.execute(select(FormSubmission.id).where(
                FormSubmission.id.not_in(wanted),
                FormSubmission.form_slug == form.slug,
                or_(
                    FormSubmission.pdf_filename == filename,
                    FormSubmission.declaration_filename == filename,
                    FormSubmission.declaration_signed_filename == filename,
                    FormSubmission.agreement_filename == filename,
                    FormSubmission.agreement_signed_filename == filename,
                ),
            ).execution_options(include_deleted_submissions=True).limit(1)).first()
            if not shared and not legacy_reference:
                exclusive_paths.append(clean_path)

        training_ids = self._ids(db, SubmissionTraining.id, SubmissionTraining.submission_id.in_(wanted))
        file_ids = [row.id for row in file_rows]
        if file_ids:
            foreign_evidence = db.execute(
                select(SubmissionChecklistEvidence.id)
                .join(SubmissionChecklistItemResult, SubmissionChecklistEvidence.result_id == SubmissionChecklistItemResult.id)
                .where(SubmissionChecklistEvidence.submission_file_id.in_(file_ids),
                       SubmissionChecklistItemResult.submission_id.not_in(wanted))
                .limit(1)
            ).first()
            foreign_training = db.execute(select(SubmissionTraining.id).where(
                SubmissionTraining.agreement_file_id.in_(file_ids),
                SubmissionTraining.submission_id.not_in(wanted),
            ).limit(1)).first()
            if foreign_evidence or foreign_training:
                raise SubmissionPermanentDeleteError("Wybrane zgłoszenie ma powiązania z innym zgłoszeniem.")
        result_ids = self._ids(db, SubmissionChecklistItemResult.id, SubmissionChecklistItemResult.submission_id.in_(wanted))
        note_ids = self._ids(db, SubmissionInternalNote.id, SubmissionInternalNote.submission_id.in_(wanted))
        deadline_ids = self._ids(db, SubmissionStepDeadline.id, SubmissionStepDeadline.submission_id.in_(wanted))
        invitation_ids = self._ids(db, TrainingSurveyInvitation.id, TrainingSurveyInvitation.submission_training_id.in_(training_ids)) if training_ids else []
        response_ids = self._ids(db, TrainingSurveyResponse.id, TrainingSurveyResponse.invitation_id.in_(invitation_ids)) if invitation_ids else []

        # Child-first order works on PostgreSQL/MariaDB and SQLite with FK checks enabled.
        self._delete(db, TrainingSurveyAnswer, TrainingSurveyAnswer.response_id, response_ids)
        self._delete(db, TrainingSurveyResponse, TrainingSurveyResponse.invitation_id, invitation_ids)
        self._delete(db, TrainingSurveyInvitation, TrainingSurveyInvitation.submission_training_id, training_ids)
        self._delete(db, TrainingAttendanceRecord, TrainingAttendanceRecord.submission_training_id, training_ids)
        self._delete(db, TrainingParticipantActionHistory, TrainingParticipantActionHistory.submission_training_id, training_ids)
        self._delete(db, SubmissionChecklistEvidence, SubmissionChecklistEvidence.result_id, result_ids)
        self._delete(db, SubmissionChecklistEvidence, SubmissionChecklistEvidence.submission_file_id, file_ids)
        self._delete(db, SubmissionChecklistResultHistory, SubmissionChecklistResultHistory.result_id, result_ids)
        self._delete(db, SubmissionChecklistItemResult, SubmissionChecklistItemResult.submission_id, wanted)
        self._delete(db, SubmissionInternalNoteMention, SubmissionInternalNoteMention.note_id, note_ids)
        self._delete(db, SubmissionInternalNoteRevision, SubmissionInternalNoteRevision.note_id, note_ids)
        self._delete(db, SubmissionInternalNote, SubmissionInternalNote.submission_id, wanted)
        self._delete(db, SubmissionDeadlineNotification, SubmissionDeadlineNotification.submission_step_deadline_id, deadline_ids)
        self._delete(db, SubmissionStepDeadline, SubmissionStepDeadline.submission_id, wanted)
        self._delete(db, SubmissionConsent, SubmissionConsent.submission_id, wanted)
        self._delete(db, FormDraft, FormDraft.submission_id, wanted)
        self._delete(db, SubmissionDecision, SubmissionDecision.submission_id, wanted)
        self._delete(db, EmailLog, EmailLog.submission_id, wanted)
        self._delete(db, RepeatableGroupItemDecision, RepeatableGroupItemDecision.submission_id, wanted)
        self._delete(db, SubmissionAssignmentHistory, SubmissionAssignmentHistory.submission_id, wanted)
        self._delete(db, SubmissionWorkflowEvent, SubmissionWorkflowEvent.submission_id, wanted)
        self._delete(db, SubmissionTraining, SubmissionTraining.submission_id, wanted)
        self._delete(db, SubmissionFile, SubmissionFile.submission_id, wanted)
        self._delete(db, FormSubmission, FormSubmission.id, wanted)
        db.flush()
        # Audit storage is external to the DB. An audit failure prevents commit.
        for submission in submissions:
            self.audit_log_service.log_event(
                "permanent_delete", submission.submission_id, form.slug,
                actor=actor.email,
                metadata={"submission_pk": submission.id, "actor_user_id": actor.id},
            )
        return exclusive_paths

    def delete_files(self, paths: list[str], *, db_factory) -> list[str]:
        """Run only after the database transaction has committed."""
        failed = []
        for path in paths:
            try:
                with db_factory() as db:
                    # A concurrent submission may have acquired the same path.
                    if db.execute(select(SubmissionFile.id).where(SubmissionFile.storage_path == path).limit(1)).first():
                        continue
                    filename = path.rsplit("/", 1)[-1]
                    if db.execute(select(FormSubmission.id).where(or_(
                        FormSubmission.pdf_filename == filename,
                        FormSubmission.declaration_filename == filename,
                        FormSubmission.declaration_signed_filename == filename,
                        FormSubmission.agreement_filename == filename,
                        FormSubmission.agreement_signed_filename == filename,
                    )).execution_options(include_deleted_submissions=True).limit(1)).first():
                        continue
            except Exception:
                failed.append(path)
                continue
            try:
                self.storage.delete(path, missing_ok=True)
            except Exception:
                failed.append(path)
        return failed
