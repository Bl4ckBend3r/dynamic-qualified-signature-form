from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select

from models import FormSubmission, SubmissionFile, SubmissionWorkflowEvent
from services.status_catalog import LEGACY_STATUS_MAP, ProcessStatusCode, get_status_label


ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
ALLOWED_ROLES = {ROLE_ADMIN, ROLE_SUPER_ADMIN}
SUPERSEDED_FILE_STATUS = "superseded"


class StageRollbackError(ValueError):
    """Raised when an administrative stage rollback is not allowed."""


@dataclass(frozen=True)
class RollbackTarget:
    status: str
    label: str
    workflow_step: str


@dataclass(frozen=True)
class RollbackResult:
    previous_status: str
    new_status: str
    previous_step: str
    new_step: str
    superseded_files: int


class SubmissionStageRollbackService:
    """Validates and applies administrative workflow rollbacks.

    Detailed legacy statuses are retained because they distinguish declaration and
    agreement stages which the normalized status catalog intentionally merges.
    """

    _TARGET_ORDER = (
        (10, "FORM_SUBMITTED"),
        (20, "WAITING_FOR_OFFICER_DECISION"),
        (30, "OFFICER_ACCEPTED"),
        (35, "accepted_waiting_for_additional_fields"),
        (40, "additional_fields_completed"),
        (50, "DECLARATION_WAITING_FOR_SIGNATURE"),
        (60, "DECLARATION_SIGNED"),
        (70, "AGREEMENT_WAITING_FOR_SIGNATURE"),
    )

    _STATUS_RANK = {
        "DRAFT": 0,
        "FORM_SUBMITTED": 10,
        "SUBMITTED": 10,
        "WAITING_FOR_OFFICER_DECISION": 20,
        "WAITING_FOR_REVIEW": 20,
        "OFFICER_REJECTED": 25,
        "REVIEW_REJECTED": 25,
        "PARTICIPANT_REJECTED": 25,
        "WAITING_FOR_CORRECTION": 25,
        "CORRECTED": 27,
        "OFFICER_ACCEPTED": 30,
        "REVIEW_ACCEPTED": 30,
        "accepted_waiting_for_additional_fields": 35,
        "additional_fields_completed": 40,
        "DECLARATION_NOT_REQUIRED": 45,
        "DECLARATION_READY": 45,
        "WAITING_FOR_DOCUMENT": 45,
        "DECLARATION_WAITING_FOR_SIGNATURE": 50,
        "DECLARATION_SIGNATURE_INVALID": 55,
        "SIGNATURE_INVALID": 55,
        "DECLARATION_SIGNED": 60,
        "AGREEMENT_BLOCKED": 65,
        "AGREEMENT_READY": 65,
        "AGREEMENT_WAITING_FOR_SIGNATURE": 70,
        "WAITING_FOR_SIGNATURE": 70,
        "AGREEMENT_SIGNATURE_INVALID": 75,
        "AGREEMENT_SIGNED": 80,
        "AGREEMENT_NOT_REQUIRED": 80,
        "PARTICIPANT_ACCEPTED": 80,
        "PROCESS_COMPLETED": 80,
        "COMPLETED": 80,
        "CANCELLED": 80,
    }

    _FINAL_STATUSES = {
        "OFFICER_REJECTED",
        "REVIEW_REJECTED",
        "PARTICIPANT_REJECTED",
        "AGREEMENT_SIGNED",
        "AGREEMENT_NOT_REQUIRED",
        "PARTICIPANT_ACCEPTED",
        "PROCESS_COMPLETED",
        "COMPLETED",
        "CANCELLED",
    }

    _INVALID_ROLLBACK_TARGETS = {
        "OFFICER_REJECTED",
        "REVIEW_REJECTED",
        "PARTICIPANT_REJECTED",
        "DECLARATION_SIGNATURE_INVALID",
        "AGREEMENT_SIGNATURE_INVALID",
        "SIGNATURE_INVALID",
        "AGREEMENT_SIGNED",
        "AGREEMENT_NOT_REQUIRED",
        "PARTICIPANT_ACCEPTED",
        "PROCESS_COMPLETED",
        "COMPLETED",
        "CANCELLED",
    }

    def get_allowed_targets(
        self,
        db,
        submission: FormSubmission,
        *,
        actor_role: str,
        form_config: Mapping[str, Any] | None = None,
    ) -> list[RollbackTarget]:
        self._require_role(actor_role)
        current_status = str(submission.process_status or "").strip()
        if not current_status:
            return []
        if current_status in self._FINAL_STATUSES and actor_role != ROLE_SUPER_ADMIN:
            return []

        known_statuses = self._known_statuses(form_config)
        candidates = self._ordered_candidates(submission, current_status, form_config)
        history = self._history_candidates(db, submission, current_status, known_statuses)
        for status in history:
            if status not in candidates:
                candidates.append(status)

        candidates = [
            status
            for status in candidates
            if status != current_status
            and status in known_statuses
            and status not in self._INVALID_ROLLBACK_TARGETS
            and self._is_earlier(status, current_status, form_config)
        ]
        if actor_role == ROLE_ADMIN:
            candidates = candidates[:1]

        return [
            RollbackTarget(
                status=status,
                label=self._status_label(status, form_config),
                workflow_step=self._workflow_step_for(status, submission, form_config),
            )
            for status in candidates
        ]

    def rollback(
        self,
        db,
        submission: FormSubmission,
        *,
        target_status: str,
        reason: str,
        actor,
        form_config: Mapping[str, Any] | None = None,
    ) -> RollbackResult:
        role = str(getattr(actor, "role", "") or "").strip()
        self._require_role(role)
        reason = str(reason or "").strip()
        if not reason:
            raise StageRollbackError("Powód cofnięcia jest wymagany.")

        target_status = str(target_status or "").strip()
        allowed = {
            item.status: item
            for item in self.get_allowed_targets(
                db,
                submission,
                actor_role=role,
                form_config=form_config,
            )
        }
        target = allowed.get(target_status)
        if target is None:
            raise StageRollbackError("Wybrany status nie jest dozwolonym celem cofnięcia.")

        previous_status = str(submission.process_status or "")
        previous_step = str(submission.workflow_step or "")
        superseded_files = self._reset_later_stage_data(db, submission, target_status, form_config)
        submission.process_status = target_status
        submission.workflow_step = target.workflow_step
        submission.updated_at = datetime.now(timezone.utc)

        db.add(
            SubmissionWorkflowEvent(
                submission_id=submission.id,
                public_submission_id=submission.submission_id,
                form_slug=submission.form_slug,
                previous_status=previous_status,
                new_status=target_status,
                previous_step=previous_step,
                new_step=target.workflow_step,
                actor_id=getattr(actor, "id", None),
                actor_email=str(getattr(actor, "email", "") or ""),
                actor_role=role,
                reason=reason,
                source="stage_rollback",
            )
        )
        db.flush()
        return RollbackResult(
            previous_status=previous_status,
            new_status=target_status,
            previous_step=previous_step,
            new_step=target.workflow_step,
            superseded_files=superseded_files,
        )

    def _history_candidates(
        self,
        db,
        submission: FormSubmission,
        current_status: str,
        known_statuses: set[str],
    ) -> list[str]:
        events = db.execute(
            select(SubmissionWorkflowEvent)
            .where(
                (SubmissionWorkflowEvent.submission_id == submission.id)
                | (SubmissionWorkflowEvent.public_submission_id == submission.submission_id)
            )
            .order_by(SubmissionWorkflowEvent.created_at.desc(), SubmissionWorkflowEvent.id.desc())
        ).scalars().all()
        candidates: list[str] = []
        for event in events:
            for raw_status in (event.previous_status, event.new_status):
                status = self._preferred_raw_status(str(raw_status or ""), current_status, submission)
                if status and status != current_status and status in known_statuses and status not in candidates:
                    candidates.append(status)
        return candidates

    def _ordered_candidates(
        self,
        submission: FormSubmission,
        current_status: str,
        form_config: Mapping[str, Any] | None,
    ) -> list[str]:
        configured = self._configured_status_order(form_config)
        if current_status in configured:
            index = configured.index(current_status)
            return list(reversed(configured[:index]))

        current_rank = self._STATUS_RANK.get(current_status)
        if current_rank is None:
            return []
        declaration_required = self._is_yes(submission.declaration_required)
        agreement_required = self._is_yes(submission.agreement_required) or bool(
            str(submission.training_agreements or "").strip()
        )
        additional_fields_used = current_status in {
            "accepted_waiting_for_additional_fields",
            "additional_fields_completed",
        } or bool(str(submission.additional_fields_completed or "").strip())

        statuses = []
        for rank, status in self._TARGET_ORDER:
            if rank >= current_rank:
                continue
            if status in {"accepted_waiting_for_additional_fields", "additional_fields_completed"} and not additional_fields_used:
                continue
            if status in {"DECLARATION_WAITING_FOR_SIGNATURE", "DECLARATION_SIGNED"} and not declaration_required:
                continue
            if status == "AGREEMENT_WAITING_FOR_SIGNATURE" and not agreement_required:
                continue
            statuses.append(status)
        return list(reversed(statuses))

    def _known_statuses(self, form_config: Mapping[str, Any] | None) -> set[str]:
        statuses = set(self._STATUS_RANK)
        statuses.update(code.value for code in ProcessStatusCode)
        statuses.update(LEGACY_STATUS_MAP)
        statuses.update(self._configured_status_order(form_config))
        return statuses

    def _configured_status_order(self, form_config: Mapping[str, Any] | None) -> list[str]:
        workflow = dict((form_config or {}).get("workflow") or {})
        raw_statuses = workflow.get("statuses") or []
        if isinstance(raw_statuses, Mapping):
            return [str(status) for status in raw_statuses if str(status).strip()]
        if not isinstance(raw_statuses, list):
            return []
        return [
            str(item.get("id") if isinstance(item, Mapping) else item).strip()
            for item in raw_statuses
            if str(item.get("id") if isinstance(item, Mapping) else item).strip()
        ]

    def _is_earlier(
        self,
        target_status: str,
        current_status: str,
        form_config: Mapping[str, Any] | None,
    ) -> bool:
        configured = self._configured_status_order(form_config)
        if target_status in configured and current_status in configured:
            return configured.index(target_status) < configured.index(current_status)
        target_rank = self._STATUS_RANK.get(target_status)
        current_rank = self._STATUS_RANK.get(current_status)
        return target_rank is not None and current_rank is not None and target_rank < current_rank

    def _preferred_raw_status(self, status: str, current_status: str, submission: FormSubmission) -> str:
        if status not in {code.value for code in ProcessStatusCode}:
            return status
        if status == "SUBMITTED":
            return "FORM_SUBMITTED"
        if status in {"WAITING_FOR_REVIEW", "CORRECTED"}:
            return "WAITING_FOR_OFFICER_DECISION"
        if status == "REVIEW_ACCEPTED":
            return "OFFICER_ACCEPTED"
        if status == "WAITING_FOR_DOCUMENT":
            return "DECLARATION_SIGNED" if self._is_yes(submission.declaration_required) else "OFFICER_ACCEPTED"
        if status == "WAITING_FOR_SIGNATURE":
            current_rank = self._STATUS_RANK.get(current_status, 0)
            return "AGREEMENT_WAITING_FOR_SIGNATURE" if current_rank > 70 else "DECLARATION_WAITING_FOR_SIGNATURE"
        return status

    def _workflow_step_for(
        self,
        status: str,
        submission: FormSubmission,
        form_config: Mapping[str, Any] | None,
    ) -> str:
        available_steps = {
            str(step.get("id") or "")
            for step in ((form_config or {}).get("workflow") or {}).get("steps", [])
            if isinstance(step, Mapping)
        }

        if status in {"DRAFT", "FORM_SUBMITTED", "SUBMITTED"}:
            preferred = "submission"
        elif status in {
            "WAITING_FOR_OFFICER_DECISION",
            "WAITING_FOR_REVIEW",
            "WAITING_FOR_CORRECTION",
            "CORRECTED",
        }:
            preferred = "waiting_for_correction" if status == "WAITING_FOR_CORRECTION" else "officer_review"
        elif status in {"OFFICER_ACCEPTED", "REVIEW_ACCEPTED", "accepted_waiting_for_additional_fields", "additional_fields_completed"}:
            preferred = self._first_existing_step(
                available_steps,
                "declaration" if self._is_yes(submission.declaration_required) else "",
                "agreement" if self._is_yes(submission.agreement_required) else "",
                "training_agreements",
                "officer_review",
            )
        elif status == "DECLARATION_WAITING_FOR_SIGNATURE":
            preferred = "declaration_signature"
        elif status == "DECLARATION_SIGNED":
            preferred = self._first_existing_step(available_steps, "agreement", "training_agreements", "declaration_signature")
        elif status == "AGREEMENT_WAITING_FOR_SIGNATURE":
            preferred = self._first_existing_step(available_steps, "training_agreements_signature", "agreement_signature")
        else:
            preferred = status
        return preferred if not available_steps or preferred in available_steps else str(submission.workflow_step or "")

    @staticmethod
    def _first_existing_step(available: set[str], *candidates: str) -> str:
        for candidate in candidates:
            if candidate and (not available or candidate in available):
                return candidate
        return next((candidate for candidate in candidates if candidate), "")

    def _reset_later_stage_data(
        self,
        db,
        submission: FormSubmission,
        target_status: str,
        form_config: Mapping[str, Any] | None,
    ) -> int:
        rank = self._STATUS_RANK.get(target_status, -1)
        superseded_types: set[str] = set()

        if rank < 60:
            submission.agreement_blocked = ""
            submission.agreement_block_reason = ""
            submission.agreement_generated = ""
            submission.agreement_filename = ""
            submission.agreement_generated_at = None
            submission.agreement_signed = ""
            submission.agreement_signature_type = ""
            submission.agreement_signature_valid = ""
            submission.agreement_signature_error = ""
            submission.agreement_signed_filename = ""
            submission.training_agreements = ""
            submission.agreement_success_email_sent = ""
            submission.agreement_success_email_sent_for = ""
            submission.office_agreement_signed_email_sent = ""
            submission.office_agreement_signed_email_sent_for = ""
            submission.requirements_rejection_email_sent = ""
            superseded_types.update({"agreement", "training_agreement", "signed_agreement", "signed_training_agreement"})
        elif rank < 70:
            submission.agreement_blocked = ""
            submission.agreement_block_reason = ""
            submission.agreement_generated = ""
            submission.agreement_filename = ""
            submission.agreement_generated_at = None
            submission.agreement_signed = ""
            submission.agreement_signature_type = ""
            submission.agreement_signature_valid = ""
            submission.agreement_signature_error = ""
            submission.agreement_signed_filename = ""
            submission.training_agreements = ""
            submission.agreement_success_email_sent = ""
            submission.agreement_success_email_sent_for = ""
            submission.office_agreement_signed_email_sent = ""
            submission.office_agreement_signed_email_sent_for = ""
            submission.requirements_rejection_email_sent = ""
            superseded_types.update({"agreement", "training_agreement", "signed_agreement", "signed_training_agreement"})
        else:
            submission.agreement_signed = ""
            submission.agreement_signature_type = ""
            submission.agreement_signature_valid = ""
            submission.agreement_signature_error = ""
            submission.agreement_signed_filename = ""
            submission.agreement_success_email_sent = ""
            submission.agreement_success_email_sent_for = ""
            submission.office_agreement_signed_email_sent = ""
            submission.office_agreement_signed_email_sent_for = ""
            superseded_types.update({"signed_agreement", "signed_training_agreement"})
            submission.training_agreements = self._clear_signed_training_files(submission.training_agreements)

        if rank < 50:
            submission.declaration_generated = ""
            submission.declaration_filename = ""
            submission.declaration_signed = ""
            submission.declaration_signature_type = ""
            submission.declaration_signature_valid = ""
            submission.declaration_signature_error = ""
            submission.declaration_signed_filename = ""
            superseded_types.update({"declaration", "signed_declaration"})
        elif rank < 60:
            submission.declaration_signed = ""
            submission.declaration_signature_type = ""
            submission.declaration_signature_valid = ""
            submission.declaration_signature_error = ""
            submission.declaration_signed_filename = ""
            superseded_types.add("signed_declaration")

        if rank <= 20:
            submission.officer_decision = ""
            submission.officer_decision_reason = ""
            submission.officer_decision_email_requested = ""
            submission.officer_decision_email_sent = ""
            submission.acceptance_required = ""
            submission.acceptance_email_sent = ""
            submission.decision_email_sent = ""
            submission.decision_email_sent_for = ""
            submission.akceptacja = ""
            submission.correction_required = ""
            submission.correction_message = ""
            submission.correction_fields = ""
            submission.correction_requested_at = None
            submission.correction_completed_at = None

        if rank < 40:
            submission.additional_fields_completed = ""

        if target_status not in {"WAITING_FOR_CORRECTION", "CORRECTED"}:
            submission.correction_required = ""
            submission.correction_message = ""
            submission.correction_fields = ""
            submission.correction_requested_at = None
            submission.correction_completed_at = None

        if not superseded_types:
            return 0
        files = db.execute(
            select(SubmissionFile).where(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.document_type.in_(superseded_types),
                SubmissionFile.status != SUPERSEDED_FILE_STATUS,
            )
        ).scalars().all()
        for file_row in files:
            file_row.status = SUPERSEDED_FILE_STATUS
            file_row.updated_at = datetime.now(timezone.utc)
        return len(files)

    @staticmethod
    def _clear_signed_training_files(raw_value: str) -> str:
        try:
            agreements = json.loads(str(raw_value or ""))
        except (TypeError, json.JSONDecodeError):
            return raw_value or ""
        if not isinstance(agreements, list):
            return raw_value or ""
        for agreement in agreements:
            if isinstance(agreement, dict):
                agreement["signed_filename"] = ""
                agreement["signed"] = False
        return json.dumps(agreements, ensure_ascii=False)

    def _status_label(self, status: str, form_config: Mapping[str, Any] | None) -> str:
        workflow = dict((form_config or {}).get("workflow") or {})
        raw_statuses = workflow.get("statuses") or []
        if isinstance(raw_statuses, Mapping):
            item = raw_statuses.get(status)
            if isinstance(item, Mapping):
                return str(item.get("label") or item.get("name") or status)
            if item:
                return str(item)
        elif isinstance(raw_statuses, list):
            for item in raw_statuses:
                if isinstance(item, Mapping) and str(item.get("id") or "") == status:
                    return str(item.get("label") or item.get("name") or status)
        return get_status_label(status)

    @staticmethod
    def _is_yes(value: Any) -> bool:
        return str(value or "").strip().lower() in {"tak", "yes", "true", "1"}

    @staticmethod
    def _require_role(role: str) -> None:
        if role not in ALLOWED_ROLES:
            raise PermissionError("Brak uprawnień do cofania etapu zgłoszenia.")
