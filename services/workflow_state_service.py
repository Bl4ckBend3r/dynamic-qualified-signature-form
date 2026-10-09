from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from services.status_catalog import LEGACY_STATUS_MAP, ProcessStatusCode, normalize_status


logger = logging.getLogger(__name__)


class FinalOutcome(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DocumentState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    READY = "READY"
    GENERATED = "GENERATED"
    WAITING_FOR_SIGNATURE = "WAITING_FOR_SIGNATURE"
    SIGNED = "SIGNED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class LayeredWorkflowState:
    workflow_stage: str
    officer_decision: str
    document_states: dict[str, Any]
    final_outcome: FinalOutcome
    normalized_process_status: ProcessStatusCode
    legacy_process_status: str
    used_fallback: bool = False


_STATUS_STAGE_MAP = {
    "FORM_SUBMITTED": "submission",
    "SUBMITTED": "submission",
    "WAITING_FOR_OFFICER_DECISION": "officer_review",
    "WAITING_FOR_REVIEW": "officer_review",
    "OFFICER_ACCEPTED": "declaration",
    "REVIEW_ACCEPTED": "declaration",
    "OFFICER_REJECTED": "end_rejected",
    "REVIEW_REJECTED": "end_rejected",
    "PARTICIPANT_REJECTED": "end_rejected",
    "AUTO_REJECTED": "end_rejected",
    "RETURNED_FOR_CORRECTION": "waiting_for_correction",
    "WAITING_FOR_CORRECTION": "waiting_for_correction",
    "CORRECTED": "officer_review",
    "DECLARATION_NOT_REQUIRED": "training_selection",
    "DECLARATION_READY": "declaration",
    "DECLARATION_REQUIRED": "declaration",
    "DECLARATION_GENERATED": "declaration_signature",
    "DECLARATION_UPLOADED": "declaration_signature",
    "DECLARATION_WAITING_FOR_SIGNATURE": "declaration_signature",
    "DECLARATION_SIGNATURE_INVALID": "declaration_signature",
    "DECLARATION_SIGNED": "training_selection",
    "TRAINING_SELECTION_OPEN": "training_selection",
    "AGREEMENT_BLOCKED": "agreement",
    "AGREEMENT_REQUIRED": "agreement",
    "AGREEMENT_READY": "agreement",
    "AGREEMENT_GENERATED": "agreement_signature",
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE": "agreement_signature",
    "AGREEMENT_WAITING_FOR_SIGNATURE": "agreement_signature",
    "AGREEMENT_SIGNATURE_INVALID": "agreement_signature",
    "AGREEMENT_REJECTED_BY_OFFICE": "agreement_signature",
    "BENEFICIARY_AGREEMENT_REJECTED": "agreement_signature",
    "AGREEMENT_UPLOADED_BY_BENEFICIARY": "office_agreement_signature",
    "AGREEMENT_UPLOADED": "office_agreement_signature",
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE": "office_agreement_signature",
    "AGREEMENT_SIGNED_BY_OFFICE": "completed",
    "BENEFICIARY_AGREEMENT_CONFIRMED": "completed",
    "AGREEMENT_SIGNED": "completed",
    "AGREEMENT_NOT_REQUIRED": "completed",
    "PARTICIPANT_ACCEPTED": "completed",
    "PROCESS_COMPLETED": "completed",
    "COMPLETED": "completed",
    "PROCESS_CANCELLED": "cancelled",
    "CANCELLED": "cancelled",
}

_DECLARATION_STATES = {
    "DECLARATION_READY": DocumentState.READY,
    "DECLARATION_REQUIRED": DocumentState.PENDING,
    "DECLARATION_GENERATED": DocumentState.GENERATED,
    "DECLARATION_UPLOADED": DocumentState.SIGNED,
    "DECLARATION_WAITING_FOR_SIGNATURE": DocumentState.WAITING_FOR_SIGNATURE,
    "DECLARATION_SIGNATURE_INVALID": DocumentState.SIGNATURE_INVALID,
    "DECLARATION_SIGNED": DocumentState.SIGNED,
    "DECLARATION_NOT_REQUIRED": DocumentState.NOT_REQUIRED,
}

_AGREEMENT_STATES = {
    "AGREEMENT_BLOCKED": DocumentState.BLOCKED,
    "AGREEMENT_REQUIRED": DocumentState.PENDING,
    "AGREEMENT_READY": DocumentState.READY,
    "AGREEMENT_GENERATED": DocumentState.GENERATED,
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE": DocumentState.WAITING_FOR_SIGNATURE,
    "AGREEMENT_WAITING_FOR_SIGNATURE": DocumentState.WAITING_FOR_SIGNATURE,
    "AGREEMENT_SIGNATURE_INVALID": DocumentState.SIGNATURE_INVALID,
    "AGREEMENT_REJECTED_BY_OFFICE": DocumentState.SIGNATURE_INVALID,
    "BENEFICIARY_AGREEMENT_REJECTED": DocumentState.SIGNATURE_INVALID,
    "AGREEMENT_UPLOADED_BY_BENEFICIARY": DocumentState.SIGNED,
    "AGREEMENT_UPLOADED": DocumentState.SIGNED,
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE": DocumentState.SIGNED,
    "AGREEMENT_SIGNED_BY_OFFICE": DocumentState.SIGNED,
    "BENEFICIARY_AGREEMENT_CONFIRMED": DocumentState.SIGNED,
    "AGREEMENT_SIGNED": DocumentState.SIGNED,
    "AGREEMENT_NOT_REQUIRED": DocumentState.NOT_REQUIRED,
}


def layered_state_from_legacy(
    process_status: Any,
    *,
    workflow_step: Any = "",
    officer_decision: Any = "",
    document_states: Mapping[str, Any] | None = None,
    final_outcome: Any = "",
) -> LayeredWorkflowState:
    """Interpret old rows without losing their original status.

    ``process_status`` is deliberately not used as the canonical stage.  It is
    only translated when the newer ``workflow_stage``/``workflow_step`` value
    is absent.
    """

    raw = str(process_status or "").strip()
    known = raw in _STATUS_STAGE_MAP or raw in {item.value for item in ProcessStatusCode} or raw in LEGACY_STATUS_MAP
    normalized = normalize_status(raw)
    stage = str(workflow_step or "").strip() or _STATUS_STAGE_MAP.get(raw, "")
    used_fallback = False
    if not stage:
        stage = "legacy_unknown"
        used_fallback = True
        logger.warning("Unknown legacy workflow status: %s", raw or "<empty>")

    documents = {str(key): value if isinstance(value, dict) else str(value) for key, value in dict(document_states or {}).items()}
    if "declaration" not in documents and raw in _DECLARATION_STATES:
        documents["declaration"] = _DECLARATION_STATES[raw].value
    if "agreement" not in documents and raw in _AGREEMENT_STATES:
        documents["agreement"] = _AGREEMENT_STATES[raw].value

    outcome_raw = str(final_outcome or "").strip()
    if outcome_raw in {item.value for item in FinalOutcome}:
        outcome = FinalOutcome(outcome_raw)
    elif raw in {"OFFICER_REJECTED", "REVIEW_REJECTED", "PARTICIPANT_REJECTED", "AUTO_REJECTED"}:
        outcome = FinalOutcome.REJECTED
    elif raw in {"PROCESS_CANCELLED", "CANCELLED"}:
        outcome = FinalOutcome.CANCELLED
    elif normalized is ProcessStatusCode.COMPLETED:
        outcome = FinalOutcome.COMPLETED
    else:
        outcome = FinalOutcome.ACTIVE

    return LayeredWorkflowState(
        workflow_stage=stage,
        officer_decision=str(officer_decision or "").strip(),
        document_states=documents,
        final_outcome=outcome,
        normalized_process_status=normalized,
        legacy_process_status=raw,
        used_fallback=used_fallback or not known,
    )


def final_outcome_for_status(status: Any) -> FinalOutcome:
    return layered_state_from_legacy(status).final_outcome
