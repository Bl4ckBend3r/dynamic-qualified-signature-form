from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from models import FormSubmission, SubmissionDecision, SubmissionWorkflowEvent
from services.process_service import ProcessStatus, is_yes


ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
BLOCKED_AGREEMENT_ROLES = {ROLE_ADMIN, ROLE_SUPER_ADMIN}


class BlockedAgreementActionError(ValueError):
    """Raised when an administrative action is invalid for the current state."""


@dataclass(frozen=True)
class BlockedAgreementActionResult:
    previous_status: str
    new_status: str
    previous_block_reason: str


class BlockedAgreementAdminService:
    def unblock(
        self,
        db,
        submission: FormSubmission,
        *,
        reason: str,
        actor,
    ) -> BlockedAgreementActionResult:
        actor_role = self._actor_role(actor)
        if actor_role != ROLE_SUPER_ADMIN:
            raise PermissionError("Tylko superadministrator może ręcznie odblokować umowę.")
        normalized_reason = self._required_reason(reason, "Podaj uzasadnienie odblokowania umowy.")
        self._require_blocked(submission)

        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        previous_block_reason = str(submission.agreement_block_reason or "")
        now = datetime.now(timezone.utc)
        self._append_history(
            submission,
            action="unblocked",
            action_reason=normalized_reason,
            block_reason=previous_block_reason,
            previous_status=previous_status,
            new_status=ProcessStatus.AGREEMENT_READY.value,
            actor=actor,
            occurred_at=now,
        )
        submission.agreement_blocked = ""
        submission.agreement_block_reason = ""
        submission.process_status = ProcessStatus.AGREEMENT_READY.value
        submission.workflow_step = "agreement"
        submission.updated_at = now
        self._add_workflow_event(
            db,
            submission,
            previous_status=previous_status,
            new_status=ProcessStatus.AGREEMENT_READY.value,
            previous_step=previous_step,
            new_step="agreement",
            reason=normalized_reason,
            source="manual_agreement_unblock",
            actor=actor,
        )
        db.flush()
        return BlockedAgreementActionResult(
            previous_status=previous_status,
            new_status=ProcessStatus.AGREEMENT_READY.value,
            previous_block_reason=previous_block_reason,
        )

    def reject_final(
        self,
        db,
        submission: FormSubmission,
        *,
        reason: str,
        actor,
        email_requested: bool = False,
        authorized: bool = False,
    ) -> BlockedAgreementActionResult:
        actor_role = self._actor_role(actor)
        if not authorized and actor_role not in BLOCKED_AGREEMENT_ROLES:
            raise PermissionError("Brak uprawnień do zakończenia procesu jako odrzuconego.")
        normalized_reason = self._required_reason(reason, "Podaj powód ostatecznego odrzucenia.")
        self._require_blocked(submission)

        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        previous_block_reason = str(submission.agreement_block_reason or "")
        now = datetime.now(timezone.utc)
        self._append_history(
            submission,
            action="rejected_final",
            action_reason=normalized_reason,
            block_reason=previous_block_reason,
            previous_status=previous_status,
            new_status=ProcessStatus.OFFICER_REJECTED.value,
            actor=actor,
            occurred_at=now,
        )
        submission.agreement_blocked = ""
        submission.agreement_block_reason = ""
        submission.process_status = ProcessStatus.OFFICER_REJECTED.value
        submission.workflow_step = "rejected"
        submission.officer_decision = "rejected"
        submission.officer_decision_reason = normalized_reason
        submission.correction_required = "Nie"
        submission.updated_at = now
        self._add_workflow_event(
            db,
            submission,
            previous_status=previous_status,
            new_status=ProcessStatus.OFFICER_REJECTED.value,
            previous_step=previous_step,
            new_step="rejected",
            reason=normalized_reason,
            source="agreement_block_final_rejection",
            actor=actor,
        )
        db.add(
            SubmissionDecision(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                decision="rejected",
                justification=normalized_reason,
                officer_id=getattr(actor, "id", None),
                officer_email=str(getattr(actor, "email", "") or ""),
                previous_status=previous_status,
                target_status=ProcessStatus.OFFICER_REJECTED.value,
                email_requested=bool(email_requested),
                email_sent=False,
                decided_at=now,
            )
        )
        db.flush()
        return BlockedAgreementActionResult(
            previous_status=previous_status,
            new_status=ProcessStatus.OFFICER_REJECTED.value,
            previous_block_reason=previous_block_reason,
        )

    @staticmethod
    def _actor_role(actor) -> str:
        return str(getattr(actor, "role", "") or "").strip()

    @staticmethod
    def _required_reason(reason: str, message: str) -> str:
        normalized = str(reason or "").strip()
        if not normalized:
            raise BlockedAgreementActionError(message)
        return normalized

    @staticmethod
    def _require_blocked(submission: FormSubmission) -> None:
        if (
            str(submission.process_status or "") != ProcessStatus.AGREEMENT_BLOCKED.value
            and not is_yes(submission.agreement_blocked)
        ):
            raise BlockedAgreementActionError("Ta akcja jest dostępna tylko dla zablokowanej umowy.")

    @staticmethod
    def _append_history(
        submission: FormSubmission,
        *,
        action: str,
        action_reason: str,
        block_reason: str,
        previous_status: str,
        new_status: str,
        actor,
        occurred_at: datetime,
    ) -> None:
        data: dict[str, Any] = dict(submission.data_json or {})
        history = list(data.get("_agreement_block_history") or [])
        history.append(
            {
                "action": action,
                "action_reason": action_reason,
                "original_block_reason": block_reason,
                "previous_status": previous_status,
                "new_status": new_status,
                "actor_id": getattr(actor, "id", None),
                "actor_email": str(getattr(actor, "email", "") or ""),
                "actor_role": str(getattr(actor, "role", "") or ""),
                "occurred_at": occurred_at.isoformat(),
            }
        )
        data["_agreement_block_history"] = history
        submission.data_json = data

    @staticmethod
    def _add_workflow_event(
        db,
        submission: FormSubmission,
        *,
        previous_status: str,
        new_status: str,
        previous_step: str,
        new_step: str,
        reason: str,
        source: str,
        actor,
    ) -> None:
        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                previous_status=previous_status,
                new_status=new_status,
                previous_step=previous_step,
                new_step=new_step,
                actor_id=getattr(actor, "id", None),
                actor_email=str(getattr(actor, "email", "") or ""),
                actor_role=str(getattr(actor, "role", "") or ""),
                reason=reason,
                source=source,
            )
        )
