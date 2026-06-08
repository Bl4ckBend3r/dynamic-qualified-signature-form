from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ProcessStatus(StrEnum):
    FORM_SUBMITTED = "FORM_SUBMITTED"
    WAITING_FOR_OFFICER_DECISION = "WAITING_FOR_OFFICER_DECISION"
    OFFICER_ACCEPTED = "OFFICER_ACCEPTED"
    OFFICER_REJECTED = "OFFICER_REJECTED"
    ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS = "accepted_waiting_for_additional_fields"
    ADDITIONAL_FIELDS_COMPLETED = "additional_fields_completed"
    DECLARATION_NOT_REQUIRED = "DECLARATION_NOT_REQUIRED"
    DECLARATION_READY = "DECLARATION_READY"
    DECLARATION_WAITING_FOR_SIGNATURE = "DECLARATION_WAITING_FOR_SIGNATURE"
    DECLARATION_SIGNED = "DECLARATION_SIGNED"
    DECLARATION_SIGNATURE_INVALID = "DECLARATION_SIGNATURE_INVALID"
    AGREEMENT_NOT_REQUIRED = "AGREEMENT_NOT_REQUIRED"
    AGREEMENT_BLOCKED = "AGREEMENT_BLOCKED"
    AGREEMENT_READY = "AGREEMENT_READY"
    AGREEMENT_WAITING_FOR_SIGNATURE = "AGREEMENT_WAITING_FOR_SIGNATURE"
    AGREEMENT_SIGNED = "AGREEMENT_SIGNED"
    AGREEMENT_SIGNATURE_INVALID = "AGREEMENT_SIGNATURE_INVALID"
    PARTICIPANT_ACCEPTED = "PARTICIPANT_ACCEPTED"
    PARTICIPANT_REJECTED = "PARTICIPANT_REJECTED"
    PROCESS_COMPLETED = "PROCESS_COMPLETED"


class OfficerDecision(StrEnum):
    ACCEPTED = "TAK"
    REJECTED = "NIE"
    MISSING = ""


class SignatureType(StrEnum):
    MSZAFIR = "mszafir"
    TRUSTED_PROFILE = "profil_zaufany"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


YES_VALUES = {"tak", "yes", "true", "1"}
NO_VALUES = {"nie", "no", "false", "0"}

# New target field names.
FIELD_PROCESS_STATUS = "process_status"
FIELD_OFFICER_DECISION = "officer_decision"
FIELD_OFFICER_DECISION_REASON = "officer_decision_reason"
FIELD_OFFICER_DECISION_EMAIL_REQUESTED = "officer_decision_email_requested"
FIELD_OFFICER_DECISION_EMAIL_SENT = "officer_decision_email_sent"

# Backward-compatible field names currently used by the application.
LEGACY_FIELD_OFFICER_DECISION = "acceptance_required"
LEGACY_FIELD_OFFICER_EMAIL_REQUESTED = "acceptance_email_sent"
LEGACY_FIELD_DECISION_EMAIL_SENT = "decision_email_sent"


@dataclass(frozen=True)
class ProcessState:
    status: ProcessStatus
    officer_decision: OfficerDecision
    can_generate_declaration: bool
    can_sign_documents: bool
    can_generate_agreement: bool
    agreement_blocked: bool
    block_reason: str


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_yes_no(value: Any) -> str:
    normalized = normalize_text(value).lower()

    if normalized in YES_VALUES:
        return "TAK"

    if normalized in NO_VALUES:
        return "NIE"

    return ""


def get_field(row: Mapping[str, Any], primary: str, fallback: str | None = None) -> str:
    primary_value = normalize_text(row.get(primary))

    if primary_value:
        return primary_value

    if fallback:
        return normalize_text(row.get(fallback))

    return ""


def get_officer_decision(row: Mapping[str, Any]) -> OfficerDecision:
    raw_value = get_field(
        row,
        FIELD_OFFICER_DECISION,
        LEGACY_FIELD_OFFICER_DECISION,
    )
    value = normalize_yes_no(raw_value)

    if value == "TAK" or normalize_text(raw_value).lower() == "accepted":
        return OfficerDecision.ACCEPTED

    if value == "NIE" or normalize_text(raw_value).lower() == "rejected":
        return OfficerDecision.REJECTED

    return OfficerDecision.MISSING


def is_yes(value: Any) -> bool:
    return normalize_yes_no(value) == "TAK"


def should_send_officer_decision_email(row: Mapping[str, Any]) -> bool:
    decision = get_officer_decision(row)

    if decision == OfficerDecision.MISSING:
        return False

    requested = get_field(
        row,
        FIELD_OFFICER_DECISION_EMAIL_REQUESTED,
        LEGACY_FIELD_OFFICER_EMAIL_REQUESTED,
    )
    sent = get_field(
        row,
        FIELD_OFFICER_DECISION_EMAIL_SENT,
        LEGACY_FIELD_DECISION_EMAIL_SENT,
    )

    return is_yes(requested) and not is_yes(sent)


def get_agreement_block_reason(row: Mapping[str, Any]) -> str:
    return get_field(row, "agreement_block_reason")


def is_declaration_required(row: Mapping[str, Any]) -> bool:
    return is_yes(row.get("declaration_required"))


def is_agreement_required(row: Mapping[str, Any]) -> bool:
    return is_yes(row.get("agreement_required"))


def is_agreement_blocked(row: Mapping[str, Any]) -> bool:
    return is_yes(row.get("agreement_blocked"))


def is_declaration_signature_valid(row: Mapping[str, Any]) -> bool:
    return is_yes(row.get("declaration_signature_valid"))


def is_agreement_signature_valid(row: Mapping[str, Any]) -> bool:
    return is_yes(row.get("agreement_signature_valid"))


def resolve_process_status(row: Mapping[str, Any]) -> ProcessStatus:
    if is_agreement_signature_valid(row):
        return ProcessStatus.AGREEMENT_SIGNED

    if is_yes(row.get("agreement_signed")) and not is_agreement_signature_valid(row):
        return ProcessStatus.AGREEMENT_SIGNATURE_INVALID

    if is_declaration_signature_valid(row):
        if is_yes(row.get("agreement_generated")):
            return ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE

        if not is_agreement_required(row):
            return ProcessStatus.AGREEMENT_NOT_REQUIRED

        if not is_agreement_blocked(row):
            return ProcessStatus.AGREEMENT_READY

    if is_yes(row.get("declaration_signed")) and not is_declaration_signature_valid(row):
        return ProcessStatus.DECLARATION_SIGNATURE_INVALID

    explicit_status = normalize_text(row.get(FIELD_PROCESS_STATUS))

    if explicit_status:
        try:
            return ProcessStatus(explicit_status)
        except ValueError:
            pass

    decision = get_officer_decision(row)

    if decision == OfficerDecision.REJECTED:
        return ProcessStatus.OFFICER_REJECTED

    if decision == OfficerDecision.MISSING:
        return ProcessStatus.WAITING_FOR_OFFICER_DECISION

    if not is_declaration_required(row):
        if not is_agreement_required(row):
            return ProcessStatus.PARTICIPANT_ACCEPTED
        return ProcessStatus.DECLARATION_NOT_REQUIRED

    if is_yes(row.get("agreement_generated")):
        return ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE

    if is_agreement_blocked(row):
        return ProcessStatus.AGREEMENT_BLOCKED

    if is_yes(row.get("declaration_generated")):
        return ProcessStatus.DECLARATION_WAITING_FOR_SIGNATURE

    return ProcessStatus.OFFICER_ACCEPTED


def build_process_state(row: Mapping[str, Any]) -> ProcessState:
    status = resolve_process_status(row)
    decision = get_officer_decision(row)
    agreement_blocked = is_agreement_blocked(row)
    block_reason = get_agreement_block_reason(row)

    can_generate_declaration = (
        status in {ProcessStatus.OFFICER_ACCEPTED, ProcessStatus.ADDITIONAL_FIELDS_COMPLETED}
        and is_declaration_required(row)
    )
    can_sign_documents = decision == OfficerDecision.ACCEPTED
    can_generate_agreement = (
        is_agreement_required(row)
        and (is_declaration_signature_valid(row) or not is_declaration_required(row))
        and not agreement_blocked
        and not is_yes(row.get("agreement_generated"))
        and not is_agreement_signature_valid(row)
    )

    return ProcessState(
        status=status,
        officer_decision=decision,
        can_generate_declaration=can_generate_declaration,
        can_sign_documents=can_sign_documents,
        can_generate_agreement=can_generate_agreement,
        agreement_blocked=agreement_blocked,
        block_reason=block_reason,
    )


def build_initial_process_fields(
    *,
    declaration_required: bool = False,
    agreement_required: bool = False,
) -> dict[str, str]:
    return {
        FIELD_PROCESS_STATUS: ProcessStatus.FORM_SUBMITTED.value,
        FIELD_OFFICER_DECISION: "",
        FIELD_OFFICER_DECISION_REASON: "",
        FIELD_OFFICER_DECISION_EMAIL_REQUESTED: "",
        FIELD_OFFICER_DECISION_EMAIL_SENT: "",
        "declaration_required": "Tak" if declaration_required else "Nie",
        "declaration_generated": "",
        "declaration_filename": "",
        "declaration_signed": "",
        "declaration_signature_type": "",
        "declaration_signature_valid": "",
        "declaration_signature_error": "",
        "declaration_signed_filename": "",
        "agreement_required": "Tak" if agreement_required else "Nie",
        "agreement_blocked": "",
        "agreement_block_reason": "",
        "agreement_generated": "",
        "agreement_filename": "",
        "agreement_signed": "",
        "agreement_signature_type": "",
        "agreement_signature_valid": "",
        "agreement_signature_error": "",
        "agreement_signed_filename": "",
        "office_agreement_signed_email_sent": "",
        "office_agreement_signed_email_sent_for": "",
        "agreement_success_email_sent": "",
        "agreement_success_email_sent_for": "",
        "requirements_rejection_email_sent": "",
    }


def build_legacy_process_fields() -> dict[str, str]:
    return {
        LEGACY_FIELD_OFFICER_DECISION: "",
        LEGACY_FIELD_OFFICER_EMAIL_REQUESTED: "",
        LEGACY_FIELD_DECISION_EMAIL_SENT: "",
        "decision_email_sent_for": "",
        "akceptacja": "",
    }
