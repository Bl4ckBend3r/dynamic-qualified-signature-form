from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from models import SubmissionDecision, SubmissionFile, SubmissionWorkflowEvent
from services.process_service import ProcessStatus
from services.submission_document_service import SubmissionDocumentType


APPLICATION_REVIEW_STATUSES = {
    ProcessStatus.FORM_SUBMITTED.value,
    ProcessStatus.WAITING_FOR_OFFICER_DECISION.value,
    "SUBMITTED",
    "WAITING_FOR_REVIEW",
    "CORRECTED",
}
AGREEMENT_DECISIONS = {"accepted", "rejected", "correction"}
AGREEMENT_DECISION_ROLES = {"admin", "super_admin"}
OFFICE_SIGNATURE_REVIEW_STATUSES = {
    ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY.value,
    ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value,
    ProcessStatus.AGREEMENT_UPLOADED.value,
}
SIGNED_AGREEMENT_TYPES = {
    SubmissionDocumentType.SIGNED_AGREEMENT,
    SubmissionDocumentType.SIGNED_TRAINING_AGREEMENT,
}


class BeneficiaryAgreementDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class BeneficiaryAgreementDecisionResult:
    previous_status: str
    decision_status: str
    final_status: str
    decision_record: SubmissionDecision


def can_edit_application_decision(submission) -> bool:
    return str(getattr(submission, "process_status", "") or "") in APPLICATION_REVIEW_STATUSES


def has_uploaded_signed_agreement(db, submission) -> bool:
    return (
        db.query(SubmissionFile.id)
        .filter(
            SubmissionFile.submission_id == submission.id,
            SubmissionFile.document_type.in_(SIGNED_AGREEMENT_TYPES),
            SubmissionFile.signed.is_(True),
        )
        .first()
        is not None
    )


class BeneficiaryAgreementService:
    def can_review(self, db, submission) -> bool:
        return (
            str(getattr(submission, "process_status", "") or "") in OFFICE_SIGNATURE_REVIEW_STATUSES
            and has_uploaded_signed_agreement(db, submission)
        )

    def decide(
        self,
        db,
        submission,
        *,
        decision: str,
        reason: str,
        actor,
        email_requested: bool = False,
    ) -> BeneficiaryAgreementDecisionResult:
        actor_role = str(getattr(actor, "role", "") or "")
        if actor_role not in AGREEMENT_DECISION_ROLES:
            raise BeneficiaryAgreementDecisionError("Nie masz uprawnień do oceny podpisanej umowy.")

        normalized_decision = str(decision or "").strip().lower()
        normalized_reason = str(reason or "").strip()
        if normalized_decision not in AGREEMENT_DECISIONS:
            raise BeneficiaryAgreementDecisionError("Wybierz poprawną decyzję dotyczącą umowy.")
        if normalized_decision in {"rejected", "correction"} and not normalized_reason:
            raise BeneficiaryAgreementDecisionError("Podaj powód odrzucenia albo skierowania umowy do poprawy.")
        if str(getattr(submission, "agreement_required", "") or "").strip().lower() != "tak":
            raise BeneficiaryAgreementDecisionError("Ten formularz nie wymaga umowy.")
        if str(getattr(submission, "process_status", "") or "") not in OFFICE_SIGNATURE_REVIEW_STATUSES:
            raise BeneficiaryAgreementDecisionError("Decyzja jest dostępna dopiero po wgraniu podpisanej umowy.")
        if not has_uploaded_signed_agreement(db, submission):
            raise BeneficiaryAgreementDecisionError("Nie znaleziono wgranej podpisanej umowy.")

        now = datetime.now(timezone.utc)
        previous_status = str(submission.process_status or "")
        accepted = normalized_decision == "accepted"
        decision_status = (
            ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value
            if accepted
            else ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value
        )
        final_status = ProcessStatus.PROCESS_COMPLETED.value if accepted else decision_status

        self._record_event(
            db,
            submission,
            previous_status=previous_status,
            new_status=decision_status,
            actor=actor,
            reason=normalized_reason,
            source=(
                "agreement_signed_by_office"
                if accepted
                else "agreement_rejected_by_office"
            ),
            created_at=now,
        )
        if accepted:
            self._record_event(
                db,
                submission,
                previous_status=decision_status,
                new_status=final_status,
                actor=actor,
                reason="agreement_signed_by_office",
                source="process_completed",
                created_at=now,
            )
        else:
            self._reset_rejected_agreement(submission, normalized_reason)

        submission.process_status = final_status
        submission.workflow_step = "completed" if accepted else "agreement_correction"
        submission.updated_at = now
        decision_record = SubmissionDecision(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            decision=f"office_agreement_{normalized_decision}",
            justification=normalized_reason,
            officer_id=getattr(actor, "id", None),
            officer_email=str(getattr(actor, "email", "") or ""),
            previous_status=previous_status,
            target_status=decision_status,
            email_requested=bool(email_requested),
            email_sent=False,
            decided_at=now,
        )
        db.add(decision_record)
        db.flush()
        return BeneficiaryAgreementDecisionResult(
            previous_status=previous_status,
            decision_status=decision_status,
            final_status=final_status,
            decision_record=decision_record,
        )

    @staticmethod
    def _record_event(
        db,
        submission,
        *,
        previous_status: str,
        new_status: str,
        actor,
        reason: str,
        source: str,
        created_at: datetime,
    ) -> None:
        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                previous_status=previous_status,
                new_status=new_status,
                previous_step=str(submission.workflow_step or ""),
                new_step="completed" if new_status == ProcessStatus.PROCESS_COMPLETED.value else source,
                actor_id=getattr(actor, "id", None),
                actor_email=str(getattr(actor, "email", "") or ""),
                actor_role=str(getattr(actor, "role", "") or "officer"),
                reason=reason,
                source=source,
                created_at=created_at,
            )
        )

    @staticmethod
    def _reset_rejected_agreement(submission, reason: str) -> None:
        submission.agreement_signed = ""
        submission.agreement_signature_valid = ""
        submission.agreement_signature_error = reason
        submission.agreement_signed_filename = ""
        raw_agreements = str(submission.training_agreements or "").strip()
        if not raw_agreements:
            return
        try:
            agreements = json.loads(raw_agreements)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(agreements, list):
            return
        for agreement in agreements:
            if not isinstance(agreement, dict):
                continue
            agreement["signed"] = False
            agreement["signature_valid"] = False
            agreement["signature_error"] = reason
            agreement["signed_filename"] = ""
        submission.training_agreements = json.dumps(agreements, ensure_ascii=False)
