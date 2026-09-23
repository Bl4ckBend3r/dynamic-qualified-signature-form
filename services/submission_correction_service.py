from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select

from models import FormSubmission, SubmissionFile, SubmissionWorkflowEvent
from services.form_submission_mapper import BOOLEAN_COLUMNS, DATE_COLUMNS, FORM_FIELD_MAP, INTEGER_COLUMNS
from services.process_service import ProcessStatus


SUPERSEDED_FILE_STATUS = "superseded"


class SubmissionCorrectionError(ValueError):
    pass


@dataclass(frozen=True)
class SubmissionCorrectionResult:
    previous_status: str
    new_status: str
    cleared: bool
    superseded_files: int
    recipient_email: str


class SubmissionCorrectionService:
    ALLOWED_ROLES = {"admin", "super_admin"}

    def return_for_correction(
        self,
        db,
        submission: FormSubmission,
        *,
        form_config: Mapping[str, Any],
        reason: str,
        message_to_user: str,
        clear_submission: bool,
        actor,
    ) -> SubmissionCorrectionResult:
        if (form_config.get("workflow") or {}).get("flow_mode") == "explicit":
            raise SubmissionCorrectionError("Skieruj zgłoszenie do poprawy przez opcję decyzji skonfigurowaną w bieżącym etapie.")
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise SubmissionCorrectionError("Podaj powód wysłania zgłoszenia do poprawy.")
        actor_role = str(getattr(actor, "role", "") or "").strip()
        if actor_role not in self.ALLOWED_ROLES:
            raise PermissionError("Brak uprawnień do wysłania zgłoszenia do poprawy.")

        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        recipient_email = str(submission.email or "").strip()
        now = datetime.now(timezone.utc)
        superseded_files = 0
        if clear_submission:
            self._clear_user_answers(submission, form_config)
            self._clear_generated_state(submission)
            files = db.execute(
                select(SubmissionFile).where(
                    SubmissionFile.submission_id == submission.id,
                    SubmissionFile.status != SUPERSEDED_FILE_STATUS,
                )
            ).scalars().all()
            for file_row in files:
                file_row.status = SUPERSEDED_FILE_STATUS
                file_row.updated_at = now
            superseded_files = len(files)

        previous_data = dict(submission.data_json or {})
        system_data = {
            key: value
            for key, value in previous_data.items()
            if str(key).startswith("_")
        }
        history = list(system_data.get("_correction_history") or [])
        history.append(
            {
                "requested_at": now.isoformat(),
                "reason": normalized_reason,
                "message_to_user": str(message_to_user or "").strip(),
                "cleared": bool(clear_submission),
                "actor_id": getattr(actor, "id", None),
                "actor_email": str(getattr(actor, "email", "") or ""),
                "actor_role": actor_role,
                "previous_status": previous_status,
            }
        )
        system_data["_correction_history"] = history
        submission.data_json = system_data if clear_submission else {
            **{key: value for key, value in previous_data.items() if not str(key).startswith("_")},
            **system_data,
        }
        submission.process_status = ProcessStatus.RETURNED_FOR_CORRECTION.value
        submission.workflow_step = "returned_for_correction"
        submission.correction_required = "Tak"
        submission.correction_message = str(message_to_user or "").strip() or normalized_reason
        submission.correction_fields = ",".join(
            str(field.get("name") or "").strip()
            for field in (form_config.get("fields") or [])
            if isinstance(field, Mapping) and str(field.get("name") or "").strip()
        )
        submission.correction_requested_at = now
        submission.correction_completed_at = None
        submission.updated_at = now
        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                previous_status=previous_status,
                new_status=ProcessStatus.RETURNED_FOR_CORRECTION.value,
                previous_step=previous_step,
                new_step="returned_for_correction",
                actor_id=getattr(actor, "id", None),
                actor_email=str(getattr(actor, "email", "") or ""),
                actor_role=actor_role,
                reason=normalized_reason,
                source="returned_for_correction",
            )
        )
        return SubmissionCorrectionResult(
            previous_status=previous_status,
            new_status=ProcessStatus.RETURNED_FOR_CORRECTION.value,
            cleared=bool(clear_submission),
            superseded_files=superseded_files,
            recipient_email=recipient_email,
        )

    @staticmethod
    def _clear_user_answers(submission: FormSubmission, form_config: Mapping[str, Any]) -> None:
        for field in form_config.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            field_name = str(field.get("name") or "").strip()
            column_name = FORM_FIELD_MAP.get(field_name, field_name)
            if not column_name or not hasattr(submission, column_name):
                continue
            if column_name in BOOLEAN_COLUMNS:
                setattr(submission, column_name, False)
            elif column_name in DATE_COLUMNS or column_name in INTEGER_COLUMNS:
                setattr(submission, column_name, None)
            else:
                setattr(submission, column_name, "")

    @staticmethod
    def _clear_generated_state(submission: FormSubmission) -> None:
        text_fields = (
            "selected_trainings",
            "training_agreements",
            "pdf_filename",
            "signed_pdf_filename",
            "signature_method",
            "officer_decision",
            "officer_decision_reason",
            "officer_decision_email_requested",
            "officer_decision_email_sent",
            "acceptance_required",
            "acceptance_email_sent",
            "decision_email_sent",
            "decision_email_sent_for",
            "akceptacja",
            "declaration_generated",
            "declaration_filename",
            "declaration_signed",
            "declaration_signature_type",
            "declaration_signature_valid",
            "declaration_signature_error",
            "declaration_signed_filename",
            "agreement_blocked",
            "agreement_block_reason",
            "agreement_generated",
            "agreement_filename",
            "agreement_signed",
            "agreement_signature_type",
            "agreement_signature_valid",
            "agreement_signature_error",
            "agreement_signed_filename",
            "office_agreement_signed_email_sent",
            "office_agreement_signed_email_sent_for",
            "agreement_success_email_sent",
            "agreement_success_email_sent_for",
            "requirements_rejection_email_sent",
            "additional_fields_completed",
        )
        for field_name in text_fields:
            setattr(submission, field_name, "")
        submission.signature_status = "manual"
        submission.signature_request_id = "mobywatel-manual"
        submission.agreement_generated_at = None
