from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from services.beneficiary_agreement_service import can_edit_application_decision
from services.process_service import ProcessStatus
from services.status_catalog import get_status_label


APPLICATION_TARGETS = {
    ProcessStatus.OFFICER_ACCEPTED.value,
    ProcessStatus.OFFICER_REJECTED.value,
    ProcessStatus.ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS.value,
    "WAITING_FOR_CORRECTION",
}
AGREEMENT_TARGETS = {
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
    ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value,
    ProcessStatus.BENEFICIARY_AGREEMENT_CONFIRMED.value,
    ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED.value,
}
COMPLETED_STATUSES = {
    ProcessStatus.PROCESS_COMPLETED.value,
    ProcessStatus.PARTICIPANT_ACCEPTED.value,
    ProcessStatus.AGREEMENT_NOT_REQUIRED.value,
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value,
    ProcessStatus.AGREEMENT_SIGNED.value,
    ProcessStatus.BENEFICIARY_AGREEMENT_CONFIRMED.value,
}
BLOCKED_QUALIFICATION_STATUSES = {
    ProcessStatus.AUTO_REJECTED.value,
    ProcessStatus.RETURNED_FOR_CORRECTION.value,
}
AGREEMENT_BLOCKED_STATUSES = {ProcessStatus.AGREEMENT_BLOCKED.value}


def build_admin_workflow_view(
    submission,
    *,
    decisions: Iterable[Mapping[str, Any]] = (),
    can_review_agreement: bool = False,
    form_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(getattr(submission, "process_status", "") or "")
    decisions = list(decisions)
    application_decision = _latest_for_targets(decisions, APPLICATION_TARGETS)
    agreement_decision = _latest_for_targets(decisions, AGREEMENT_TARGETS)
    declaration_required = _yes(getattr(submission, "declaration_required", ""))
    agreement_required = _yes(getattr(submission, "agreement_required", ""))
    application_action, application_state = _application_action_and_state(submission)

    sections = [
        {
            "key": "application",
            "title": "1. Wniosek",
            "status": _application_status(submission),
            "decision": (
                "Odrzucone automatycznie"
                if status == ProcessStatus.AUTO_REJECTED.value
                else _decision_label(application_decision, getattr(submission, "officer_decision", ""))
            ),
            "updated_at": _decision_date(application_decision, getattr(submission, "updated_at", None)),
            "action": application_action,
            "state": application_state,
        },
        {
            "key": "declaration",
            "title": "2. Deklaracja",
            "status": _declaration_status(submission, declaration_required),
            "decision": "Deklaracja podpisana" if _yes(getattr(submission, "declaration_signature_valid", "")) else "-",
            "updated_at": getattr(submission, "updated_at", None),
            "action": _declaration_action(submission, declaration_required),
            "state": _declaration_state(submission, declaration_required),
        },
        {
            "key": "agreement",
            "title": "3. Umowa",
            "status": _agreement_status(submission, agreement_required),
            "decision": _decision_label(agreement_decision),
            "updated_at": _decision_date(agreement_decision, getattr(submission, "updated_at", None)),
            "action": _agreement_action(status, agreement_required, can_review_agreement),
            "state": _agreement_state(status, agreement_required, can_review_agreement),
        },
        {
            "key": "completion",
            "title": "4. Zakonczenie",
            "status": (
                "Proces zakończony" if status in COMPLETED_STATUSES
                else "Proces zatrzymany" if status in BLOCKED_QUALIFICATION_STATUSES
                else "Oczekuje"
            ),
            "decision": "-",
            "updated_at": getattr(submission, "updated_at", None),
            "action": "Brak dalszych czynności" if status in COMPLETED_STATUSES | BLOCKED_QUALIFICATION_STATUSES else "Etap przyszły",
            "state": "completed" if status in COMPLETED_STATUSES else "future",
        },
    ]
    return {
        "sections": sections,
        "can_edit_application_decision": can_edit_application_decision(submission) or _workflow_allows_decision(submission, form_config or {}),
        "can_review_agreement": can_review_agreement,
        "application_decision": application_decision,
        "agreement_decision": agreement_decision,
    }


def _workflow_allows_decision(submission, form_config: Mapping[str, Any]) -> bool:
    workflow = form_config.get("workflow") or {}
    current = str(getattr(submission, "workflow_stage", "") or getattr(submission, "workflow_step", "") or workflow.get("initial_step") or "")
    step = next((item for item in workflow.get("steps") or [] if str(item.get("id") or "") == current), None)
    return bool(step and (step.get("requires_officer_action") or step.get("type") == "manual_decision" or step.get("decisions")))


def _latest_for_targets(decisions: list[Mapping[str, Any]], targets: set[str]) -> Mapping[str, Any] | None:
    matches = [item for item in decisions if str(item.get("target_status") or "") in targets]
    return matches[-1] if matches else None


def _decision_label(decision: Mapping[str, Any] | None, legacy: str = "") -> str:
    value = str((decision or {}).get("decision") or legacy or "").lower()
    if "accepted" in value or "confirmed" in value or value == "tak":
        return "Tak"
    if "correction" in value:
        return "Do poprawy"
    if "rejected" in value or value == "nie":
        return "Nie"
    return "Brak decyzji"


def _decision_date(decision: Mapping[str, Any] | None, fallback: datetime | None) -> datetime | None:
    return (decision or {}).get("decided_at") or fallback


def _application_status(submission) -> str:
    status = str(getattr(submission, "process_status", "") or "")
    if status == ProcessStatus.AUTO_REJECTED.value:
        return "Odrzucony automatycznie"
    if status == ProcessStatus.RETURNED_FOR_CORRECTION.value:
        return "Wysłany do poprawy"
    if can_edit_application_decision(submission):
        return "Wniosek oczekuje na decyzję"
    decision = str(getattr(submission, "officer_decision", "") or "").lower()
    return "Wniosek odrzucony" if decision in {"rejected", "nie"} else "Wniosek zaakceptowany"


def _application_action_and_state(submission) -> tuple[str, str]:
    status = str(getattr(submission, "process_status", "") or "")
    if status == ProcessStatus.AUTO_REJECTED.value:
        return "Dalsze kroki zablokowane; administrator może wysłać zgłoszenie do poprawy", "completed"
    if status == ProcessStatus.RETURNED_FOR_CORRECTION.value:
        return "Oczekiwanie na ponowne uzupełnienie przez użytkownika", "current"
    if can_edit_application_decision(submission):
        return "Wniosek oczekuje na decyzję", "current"
    return "Etap zakończony", "completed"


def _declaration_status(submission, required: bool) -> str:
    if not required:
        return "Deklaracja niewymagana"
    if _yes(getattr(submission, "declaration_signature_valid", "")):
        return "Deklaracja podpisana"
    if _yes(getattr(submission, "declaration_generated", "")):
        return "Deklaracja oczekuje na podpis"
    return "Deklaracja oczekuje na przygotowanie"


def _declaration_action(submission, required: bool) -> str:
    if str(getattr(submission, "process_status", "") or "") in BLOCKED_QUALIFICATION_STATUSES:
        return "Etap zablokowany"
    if not required or _yes(getattr(submission, "declaration_signature_valid", "")):
        return "Etap zakończony"
    return "Oczekiwanie na beneficjenta"


def _declaration_state(submission, required: bool) -> str:
    if str(getattr(submission, "process_status", "") or "") in BLOCKED_QUALIFICATION_STATUSES:
        return "future"
    if not required or _yes(getattr(submission, "declaration_signature_valid", "")):
        return "completed"
    if can_edit_application_decision(submission):
        return "future"
    return "current"


def _agreement_status(submission, required: bool) -> str:
    if not required:
        return "Umowa niewymagana"
    return get_status_label(str(getattr(submission, "process_status", "") or ""))


def _agreement_action(status: str, required: bool, can_review: bool) -> str:
    if status in AGREEMENT_BLOCKED_STATUSES:
        return "Wymagana decyzja administratora"
    if status in BLOCKED_QUALIFICATION_STATUSES:
        return "Etap zablokowany"
    if not required:
        return "Etap pominięty"
    if can_review:
        return "Potwierdź podpisanie umowy przez urząd"
    if status in {
        ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value,
        ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED.value,
    }:
        return "Oczekiwanie na ponowne wgranie poprawnej umowy"
    if status in COMPLETED_STATUSES:
        return "Etap zakończony"
    return "Oczekiwanie na beneficjenta"


def _agreement_state(status: str, required: bool, can_review: bool) -> str:
    if status in AGREEMENT_BLOCKED_STATUSES:
        return "current"
    if status in BLOCKED_QUALIFICATION_STATUSES:
        return "future"
    if not required or status in COMPLETED_STATUSES:
        return "completed"
    if can_review or status in {
        ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value,
        ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED.value,
    }:
        return "current"
    return "future"


def _yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"tak", "yes", "true", "1"}
