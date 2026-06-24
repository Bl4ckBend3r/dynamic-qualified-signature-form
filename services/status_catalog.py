from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProcessStatusCode(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    WAITING_FOR_CORRECTION = "WAITING_FOR_CORRECTION"
    CORRECTED = "CORRECTED"
    WAITING_FOR_DOCUMENT = "WAITING_FOR_DOCUMENT"
    WAITING_FOR_SIGNATURE = "WAITING_FOR_SIGNATURE"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class StatusDefinition:
    code: ProcessStatusCode
    label: str
    final: bool = False
    rejected: bool = False
    requires_user_action: bool = False
    requires_officer_action: bool = False
    allowed_transitions: tuple[ProcessStatusCode, ...] = ()


STATUS_CATALOG: dict[ProcessStatusCode, StatusDefinition] = {
    ProcessStatusCode.DRAFT: StatusDefinition(ProcessStatusCode.DRAFT, "Szkic", allowed_transitions=(ProcessStatusCode.SUBMITTED,)),
    ProcessStatusCode.SUBMITTED: StatusDefinition(
        ProcessStatusCode.SUBMITTED,
        "Wniosek złożony",
        requires_officer_action=True,
        allowed_transitions=(ProcessStatusCode.WAITING_FOR_REVIEW,),
    ),
    ProcessStatusCode.WAITING_FOR_REVIEW: StatusDefinition(
        ProcessStatusCode.WAITING_FOR_REVIEW,
        "Oczekuje na decyzję urzędnika",
        requires_officer_action=True,
        allowed_transitions=(ProcessStatusCode.REVIEW_ACCEPTED, ProcessStatusCode.REVIEW_REJECTED),
    ),
    ProcessStatusCode.REVIEW_ACCEPTED: StatusDefinition(
        ProcessStatusCode.REVIEW_ACCEPTED,
        "Wniosek zaakceptowany",
        requires_user_action=True,
        allowed_transitions=(
            ProcessStatusCode.WAITING_FOR_DOCUMENT,
            ProcessStatusCode.WAITING_FOR_SIGNATURE,
            ProcessStatusCode.COMPLETED,
        ),
    ),
    ProcessStatusCode.REVIEW_REJECTED: StatusDefinition(
        ProcessStatusCode.REVIEW_REJECTED,
        "Wniosek odrzucony",
        final=True,
        rejected=True,
    ),
    ProcessStatusCode.WAITING_FOR_CORRECTION: StatusDefinition(
        ProcessStatusCode.WAITING_FOR_CORRECTION,
        "Oczekuje na korektę",
        requires_user_action=True,
        allowed_transitions=(ProcessStatusCode.CORRECTED, ProcessStatusCode.CANCELLED),
    ),
    ProcessStatusCode.CORRECTED: StatusDefinition(
        ProcessStatusCode.CORRECTED,
        "Korekta przesłana",
        requires_officer_action=True,
        allowed_transitions=(ProcessStatusCode.WAITING_FOR_REVIEW,),
    ),
    ProcessStatusCode.WAITING_FOR_DOCUMENT: StatusDefinition(
        ProcessStatusCode.WAITING_FOR_DOCUMENT,
        "Oczekuje na dokument",
        requires_user_action=True,
        allowed_transitions=(ProcessStatusCode.WAITING_FOR_SIGNATURE, ProcessStatusCode.COMPLETED),
    ),
    ProcessStatusCode.WAITING_FOR_SIGNATURE: StatusDefinition(
        ProcessStatusCode.WAITING_FOR_SIGNATURE,
        "Oczekuje na podpis",
        requires_user_action=True,
        allowed_transitions=(ProcessStatusCode.SIGNATURE_INVALID, ProcessStatusCode.COMPLETED),
    ),
    ProcessStatusCode.SIGNATURE_INVALID: StatusDefinition(
        ProcessStatusCode.SIGNATURE_INVALID,
        "Podpis wymaga poprawy",
        requires_user_action=True,
        allowed_transitions=(ProcessStatusCode.WAITING_FOR_SIGNATURE, ProcessStatusCode.CANCELLED),
    ),
    ProcessStatusCode.COMPLETED: StatusDefinition(ProcessStatusCode.COMPLETED, "Proces zakończony", final=True),
    ProcessStatusCode.CANCELLED: StatusDefinition(ProcessStatusCode.CANCELLED, "Proces anulowany", final=True, rejected=True),
}


LEGACY_STATUS_MAP: dict[str, ProcessStatusCode] = {
    "FORM_SUBMITTED": ProcessStatusCode.SUBMITTED,
    "WAITING_FOR_OFFICER_DECISION": ProcessStatusCode.WAITING_FOR_REVIEW,
    "OFFICER_ACCEPTED": ProcessStatusCode.REVIEW_ACCEPTED,
    "accepted_waiting_for_additional_fields": ProcessStatusCode.REVIEW_ACCEPTED,
    "additional_fields_completed": ProcessStatusCode.REVIEW_ACCEPTED,
    "OFFICER_REJECTED": ProcessStatusCode.REVIEW_REJECTED,
    "PARTICIPANT_REJECTED": ProcessStatusCode.REVIEW_REJECTED,
    "DECLARATION_NOT_REQUIRED": ProcessStatusCode.WAITING_FOR_DOCUMENT,
    "DECLARATION_READY": ProcessStatusCode.WAITING_FOR_DOCUMENT,
    "DECLARATION_WAITING_FOR_SIGNATURE": ProcessStatusCode.WAITING_FOR_SIGNATURE,
    "DECLARATION_SIGNED": ProcessStatusCode.WAITING_FOR_DOCUMENT,
    "DECLARATION_SIGNATURE_INVALID": ProcessStatusCode.SIGNATURE_INVALID,
    "AGREEMENT_NOT_REQUIRED": ProcessStatusCode.COMPLETED,
    "AGREEMENT_BLOCKED": ProcessStatusCode.WAITING_FOR_DOCUMENT,
    "AGREEMENT_READY": ProcessStatusCode.WAITING_FOR_DOCUMENT,
    "AGREEMENT_WAITING_FOR_SIGNATURE": ProcessStatusCode.WAITING_FOR_SIGNATURE,
    "AGREEMENT_SIGNED": ProcessStatusCode.COMPLETED,
    "AGREEMENT_SIGNATURE_INVALID": ProcessStatusCode.SIGNATURE_INVALID,
    "PARTICIPANT_ACCEPTED": ProcessStatusCode.COMPLETED,
    "PROCESS_COMPLETED": ProcessStatusCode.COMPLETED,
}


LEGACY_STATUS_LABELS: dict[str, str] = {
    "FORM_SUBMITTED": "Wniosek złożony",
    "WAITING_FOR_OFFICER_DECISION": "Oczekuje na decyzję urzędnika",
    "OFFICER_ACCEPTED": "Wniosek zaakceptowany",
    "OFFICER_REJECTED": "Wniosek odrzucony",
    "accepted_waiting_for_additional_fields": "Wniosek zaakceptowany - uzupełnij dodatkowe informacje",
    "additional_fields_completed": "Dodatkowe informacje uzupełnione",
    "DECLARATION_NOT_REQUIRED": "Deklaracja niewymagana",
    "DECLARATION_READY": "Deklaracja gotowa",
    "DECLARATION_WAITING_FOR_SIGNATURE": "Deklaracja oczekuje na podpis",
    "DECLARATION_SIGNED": "Deklaracja podpisana",
    "DECLARATION_SIGNATURE_INVALID": "Podpis deklaracji wymaga poprawy",
    "AGREEMENT_NOT_REQUIRED": "Umowa niewymagana",
    "AGREEMENT_BLOCKED": "Umowa zablokowana",
    "AGREEMENT_READY": "Umowa gotowa do wygenerowania",
    "AGREEMENT_WAITING_FOR_SIGNATURE": "Umowa oczekuje na podpis",
    "AGREEMENT_SIGNED": "Umowa podpisana",
    "AGREEMENT_SIGNATURE_INVALID": "Podpis umowy wymaga poprawy",
    "PARTICIPANT_ACCEPTED": "Proces zaakceptowany",
    "PARTICIPANT_REJECTED": "Proces odrzucony",
    "PROCESS_COMPLETED": "Proces zakończony",
}


DECLARATION_COMPLETED_RAW_STATUSES = {
    "AGREEMENT_READY",
    "AGREEMENT_WAITING_FOR_SIGNATURE",
    "AGREEMENT_SIGNED",
    "AGREEMENT_SIGNATURE_INVALID",
    "AGREEMENT_NOT_REQUIRED",
    "PARTICIPANT_ACCEPTED",
    "PROCESS_COMPLETED",
}

AGREEMENT_COMPLETED_RAW_STATUSES = {"AGREEMENT_SIGNED", "PARTICIPANT_ACCEPTED", "PROCESS_COMPLETED"}


def normalize_status(value: str | None) -> ProcessStatusCode:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ProcessStatusCode.DRAFT
    try:
        return ProcessStatusCode(raw_value)
    except ValueError:
        return LEGACY_STATUS_MAP.get(raw_value, ProcessStatusCode.DRAFT)


def get_status_label(value: str | None) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return "Nieznany status: brak"
    if raw_value in LEGACY_STATUS_LABELS:
        return LEGACY_STATUS_LABELS[raw_value]
    status = normalize_status(raw_value)
    if raw_value == status.value:
        return STATUS_CATALOG[status].label
    return f"Nieznany status: {raw_value}"


def is_final_status(value: str | None) -> bool:
    return STATUS_CATALOG[normalize_status(value)].final


def is_rejected_status(value: str | None) -> bool:
    return STATUS_CATALOG[normalize_status(value)].rejected


def requires_user_action(value: str | None) -> bool:
    return STATUS_CATALOG[normalize_status(value)].requires_user_action


def requires_officer_action(value: str | None) -> bool:
    return STATUS_CATALOG[normalize_status(value)].requires_officer_action


def can_transition(from_status: str | None, to_status: str | None) -> bool:
    source = normalize_status(from_status)
    target = normalize_status(to_status)
    if source == target:
        return True
    return target in STATUS_CATALOG[source].allowed_transitions


def is_declaration_stage_completed(value: str | None) -> bool:
    raw_value = str(value or "").strip()
    return raw_value in DECLARATION_COMPLETED_RAW_STATUSES or normalize_status(raw_value) == ProcessStatusCode.COMPLETED


def is_agreement_stage_completed(value: str | None) -> bool:
    raw_value = str(value or "").strip()
    return raw_value in AGREEMENT_COMPLETED_RAW_STATUSES or normalize_status(raw_value) == ProcessStatusCode.COMPLETED


def build_status_view(value: str | None) -> dict:
    status = normalize_status(value)
    return {
        "current_status": status.value,
        "raw_status": str(value or "").strip(),
        "label": get_status_label(value),
        "is_final": is_final_status(value),
        "is_rejected": is_rejected_status(value),
        "requires_user_action": requires_user_action(value),
        "requires_officer_action": requires_officer_action(value),
        "declaration_stage_completed": is_declaration_stage_completed(value),
        "agreement_stage_completed": is_agreement_stage_completed(value),
    }


def export_status_catalog_for_frontend() -> dict:
    return {
        "statuses": {
            code.value: {
                "identifier": code.value,
                "label": definition.label,
                "final": definition.final,
                "rejected": definition.rejected,
                "requires_user_action": definition.requires_user_action,
                "requires_officer_action": definition.requires_officer_action,
                "allowed_transitions": [target.value for target in definition.allowed_transitions],
            }
            for code, definition in STATUS_CATALOG.items()
        },
        "legacy_mappings": {legacy: target.value for legacy, target in LEGACY_STATUS_MAP.items()},
        "legacy_labels": LEGACY_STATUS_LABELS,
    }
