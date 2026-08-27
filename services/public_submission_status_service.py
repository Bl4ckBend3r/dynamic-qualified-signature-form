from __future__ import annotations

import json
from typing import Any, Mapping

from services.process_service import (
    OfficerDecision,
    ProcessStatus,
    build_process_state,
    get_officer_decision,
    is_agreement_required,
    is_agreement_signature_valid,
    is_declaration_required,
    is_declaration_signature_valid,
    is_yes,
)
from services.status_catalog import get_status_label
from services.workflow_config_service import DEFAULT_STEP_STATUS


REJECTED_STATUSES = {
    ProcessStatus.AUTO_REJECTED,
    ProcessStatus.OFFICER_REJECTED,
    ProcessStatus.PARTICIPANT_REJECTED,
}
CORRECTION_STATUSES = {
    ProcessStatus.RETURNED_FOR_CORRECTION,
    ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE,
    ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED,
}
DECLARATION_COMPLETED_STATUSES = {
    ProcessStatus.DECLARATION_SIGNED,
    ProcessStatus.AGREEMENT_BLOCKED,
    ProcessStatus.AGREEMENT_READY,
    ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE,
    ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY,
    ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE,
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE,
    ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE,
    ProcessStatus.AGREEMENT_UPLOADED,
    ProcessStatus.AGREEMENT_SIGNED,
    ProcessStatus.PROCESS_COMPLETED,
    ProcessStatus.PARTICIPANT_ACCEPTED,
}
AGREEMENT_WAITING_FOR_OFFICE_STATUSES = {
    ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY,
    ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE,
    ProcessStatus.AGREEMENT_UPLOADED,
}
COMPLETED_STATUSES = {
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE,
    ProcessStatus.AGREEMENT_SIGNED,
    ProcessStatus.BENEFICIARY_AGREEMENT_CONFIRMED,
    ProcessStatus.PROCESS_COMPLETED,
    ProcessStatus.PARTICIPANT_ACCEPTED,
}
ACCEPTED_STATUSES = {
    ProcessStatus.OFFICER_ACCEPTED,
    ProcessStatus.ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS,
    ProcessStatus.ADDITIONAL_FIELDS_COMPLETED,
    ProcessStatus.DECLARATION_NOT_REQUIRED,
    ProcessStatus.DECLARATION_READY,
    ProcessStatus.DECLARATION_WAITING_FOR_SIGNATURE,
    ProcessStatus.DECLARATION_SIGNED,
    ProcessStatus.DECLARATION_SIGNATURE_INVALID,
    ProcessStatus.TRAINING_SELECTION_OPEN,
    ProcessStatus.AGREEMENT_NOT_REQUIRED,
    ProcessStatus.AGREEMENT_BLOCKED,
    ProcessStatus.AGREEMENT_READY,
    ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE,
    ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY,
    ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE,
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE,
    ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE,
    ProcessStatus.AGREEMENT_WAITING_FOR_SIGNATURE,
    ProcessStatus.AGREEMENT_UPLOADED,
    ProcessStatus.BENEFICIARY_AGREEMENT_CONFIRMED,
    ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED,
    ProcessStatus.AGREEMENT_SIGNED,
    ProcessStatus.AGREEMENT_SIGNATURE_INVALID,
    ProcessStatus.PARTICIPANT_ACCEPTED,
    ProcessStatus.PROCESS_COMPLETED,
}


def build_public_submission_status(
    row: Mapping[str, Any],
    *,
    form_config: Mapping[str, Any] | None = None,
    current_step: str | None = None,
) -> dict[str, Any]:
    state = build_process_state(row)
    workflow_context = _workflow_context(row, form_config=form_config, current_step=current_step)
    status = _status_for_workflow_context(workflow_context) or state.status
    decision = get_officer_decision(row)
    rejected = status in REJECTED_STATUSES or decision == OfficerDecision.REJECTED
    correction = status in CORRECTION_STATUSES
    blocked = state.agreement_blocked or status == ProcessStatus.AGREEMENT_BLOCKED
    accepted = decision == OfficerDecision.ACCEPTED or status in ACCEPTED_STATUSES
    declaration_required = is_declaration_required(row)
    agreement_required = is_agreement_required(row)
    explicit_status = str(row.get("process_status") or "").strip()
    declaration_completed = is_declaration_signature_valid(row) or (
        bool(explicit_status) and status in DECLARATION_COMPLETED_STATUSES
    )
    training_agreements = _active_training_agreements(row)
    pending_training_agreements = [
        item for item in training_agreements
        if item.get("filename") and not bool(item.get("signature_valid"))
    ]
    normalized_training_states = any("participant_status" in item for item in training_agreements)
    uploadable_training_agreements = [
        item for item in pending_training_agreements
        if not normalized_training_states
        or bool(item.get("agreement_downloaded"))
        or str(item.get("participant_status") or "") in {
            "agreement_downloaded", "agreement_waiting_for_beneficiary_signature"
        }
    ]
    agreement_completed = status in COMPLETED_STATUSES and not pending_training_agreements
    beneficiary_signed = (
        is_agreement_signature_valid(row) or status in AGREEMENT_WAITING_FOR_OFFICE_STATUSES
    ) and not pending_training_agreements

    application_status = _application_status(status, decision)
    declaration_status = _declaration_status(
        row,
        accepted=accepted,
        required=declaration_required,
        completed=declaration_completed,
    )
    agreement_status = _agreement_status(
        row,
        status=status,
        required=agreement_required,
        blocked=blocked,
        completed=agreement_completed,
        beneficiary_signed=beneficiary_signed,
        can_generate=state.can_generate_agreement,
    )

    headline, description, next_action, variant = _primary_message(
        status=status,
        rejected=rejected,
        correction=correction,
        blocked=blocked,
        accepted=accepted,
        declaration_required=declaration_required,
        declaration_completed=declaration_completed,
        agreement_required=agreement_required,
        agreement_completed=agreement_completed,
        beneficiary_signed=beneficiary_signed,
        declaration_generated=is_yes(row.get("declaration_generated")),
        agreement_generated=is_yes(row.get("agreement_generated")),
    )
    configured_step = workflow_context.get("config") or {}
    description = str(configured_step.get("description") or description).strip()
    next_action = str(configured_step.get("next_action") or next_action).strip()

    can_download_declaration = bool(row.get("declaration_filename")) and accepted and not declaration_completed
    can_upload_signed_declaration = (
        can_download_declaration
        and not rejected
        and not correction
        and not is_declaration_signature_valid(row)
    )
    can_download_agreement = (
        bool(row.get("agreement_filename") or row.get("training_agreements"))
        and not blocked
        and not rejected
        and not correction
    )
    can_upload_signed_agreement = (
        can_download_agreement
        and (
            bool(uploadable_training_agreements)
            or (not training_agreements and not beneficiary_signed)
        )
        and not agreement_completed
    )
    can_fill_declaration = (
        accepted
        and declaration_required
        and not declaration_completed
        and not rejected
        and not correction
    )

    blocking_reason = state.block_reason if blocked else ""
    status_reason = (
        blocking_reason
        or (str(row.get("correction_message") or "").strip() if correction else "")
        or (str(row.get("officer_decision_reason") or "").strip() if rejected else "")
    )
    current_status = {
        "variant": variant,
        "title": headline,
        "message": description,
        "next_action": next_action,
        "reason": status_reason,
        "step": workflow_context.get("id") or status.value,
    }
    return {
        "status": current_status,
        "current_workflow_step": current_status["step"],
        "effective_process_status": state.status.value,
        "process_status_label": get_status_label(state.status.value),
        "application_status": application_status,
        "declaration_status": declaration_status,
        "agreement_status": agreement_status,
        "blocking_reason": blocking_reason,
        "status_reason": status_reason,
        "next_action": next_action,
        "status_title": headline,
        "status_description": description,
        "status_variant": variant,
        "status_messages": [{"type": "current", "text": headline}],
        "declaration_completed": declaration_completed,
        "agreement_completed": agreement_completed,
        "agreement_blocked": blocked,
        "can_view_status_details": accepted or blocked,
        "can_fill_declaration": can_fill_declaration,
        "can_download_declaration": can_download_declaration,
        "can_upload_signed_declaration": can_upload_signed_declaration,
        "can_generate_agreement": bool(
            state.can_generate_agreement
            and accepted
            and not rejected
            and not correction
            and not blocked
        ),
        "can_download_agreement": can_download_agreement,
        "can_upload_signed_agreement": can_upload_signed_agreement,
    }


def _application_status(status: ProcessStatus, decision: OfficerDecision) -> str:
    if status == ProcessStatus.AUTO_REJECTED:
        return "Wniosek odrzucony automatycznie"
    if decision == OfficerDecision.REJECTED or status in REJECTED_STATUSES:
        return "Wniosek odrzucony przez urzędnika"
    if status == ProcessStatus.RETURNED_FOR_CORRECTION:
        return "Wniosek wysłany do poprawy"
    if decision == OfficerDecision.ACCEPTED:
        return "Wniosek zaakceptowany przez urzędnika"
    return "Wniosek oczekuje na decyzję urzędnika"


def _declaration_status(row, *, accepted: bool, required: bool, completed: bool) -> str:
    if not required:
        return "Deklaracja niewymagana"
    if completed:
        return "Etap zakończony poprawnie"
    if is_yes(row.get("declaration_generated")) or row.get("declaration_filename"):
        return "Deklaracja gotowa do podpisania"
    if accepted:
        return "Deklaracja oczekuje na wypełnienie"
    return "Deklaracja jeszcze niedostępna"


def _agreement_status(
    row,
    *,
    status: ProcessStatus,
    required: bool,
    blocked: bool,
    completed: bool,
    beneficiary_signed: bool,
    can_generate: bool,
) -> str:
    if blocked:
        return "Umowa zablokowana"
    if not required:
        return "Umowa niewymagana"
    if completed:
        return "Umowa podpisana przez urząd"
    if beneficiary_signed:
        return "Umowa oczekuje na podpis urzędu"
    if is_yes(row.get("agreement_generated")) or row.get("agreement_filename"):
        return "Umowa oczekuje na podpis beneficjenta"
    if can_generate or status == ProcessStatus.AGREEMENT_READY:
        return "Umowa gotowa do wygenerowania"
    return "Umowa jeszcze niedostępna"


def _workflow_context(
    row: Mapping[str, Any],
    *,
    form_config: Mapping[str, Any] | None,
    current_step: str | None,
) -> dict[str, Any]:
    persisted_step = str(row.get("workflow_stage") or row.get("workflow_step") or "").strip()
    step_id = str(current_step or persisted_step).strip() if persisted_step else ""
    workflow = (form_config or {}).get("workflow") or {}
    configured_step = next(
        (
            step
            for step in workflow.get("steps") or []
            if isinstance(step, Mapping) and str(step.get("id") or "").strip() == step_id
        ),
        None,
    )
    status_code = str(
        (configured_step or {}).get("status")
        or (configured_step or {}).get("status_code")
        or DEFAULT_STEP_STATUS.get(step_id)
        or ""
    ).strip()
    return {"id": step_id, "status": status_code, "config": configured_step or {}}


def _status_for_workflow_context(context: Mapping[str, Any]) -> ProcessStatus | None:
    status_code = str(context.get("status") or "").strip()
    if status_code in {"WAITING_FOR_CORRECTION", "CORRECTION_REQUIRED"}:
        return ProcessStatus.RETURNED_FOR_CORRECTION
    try:
        return ProcessStatus(status_code)
    except ValueError:
        return None


def _primary_message(**state) -> tuple[str, str, str, str]:
    if state["rejected"]:
        return (
            "Wniosek został odrzucony",
            "Weryfikacja wniosku zakończyła się odrzuceniem.",
            "Sprawdź przekazane uzasadnienie. Jeśli formularz na to pozwala, popraw dane i wyślij wniosek ponownie.",
            "danger",
        )
    if state["correction"]:
        return (
            "Wniosek wymaga poprawy",
            "Przekazane dane lub dokument wymagają poprawy.",
            "Zapoznaj się z uwagami i wykonaj wskazaną korektę.",
            "warning",
        )
    if state["blocked"]:
        return (
            "Umowa nie może zostać wygenerowana",
            "Deklaracja została zweryfikowana, ale warunki wymagane do wygenerowania umowy nie zostały spełnione.",
            "Na tym etapie nie możesz wykonać kolejnej czynności. W razie pytań skontaktuj się z administratorem formularza.",
            "warning",
        )
    if state["status"] == ProcessStatus.FORM_SUBMITTED:
        return (
            "Wniosek został złożony",
            "Wniosek został zapisany i oczekuje na rozpoczęcie weryfikacji.",
            "Oczekuj na weryfikację przez urzędnika.",
            "neutral",
        )
    if state["status"] == ProcessStatus.WAITING_FOR_OFFICER_DECISION:
        return (
            "Wniosek oczekuje na decyzję",
            "Wniosek jest weryfikowany przez urzędnika.",
            "Nie musisz teraz wykonywać żadnych działań.",
            "neutral",
        )
    if not state["accepted"]:
        return (
            "Wniosek oczekuje na decyzję urzędnika",
            "Dokumenty będą dostępne po zakończeniu weryfikacji.",
            "Poczekaj na decyzję urzędnika.",
            "neutral",
        )
    if state["status"] == ProcessStatus.ACCEPTED_WAITING_FOR_ADDITIONAL_FIELDS:
        return (
            "Uzupełnij wymagane dane",
            "Wniosek został zaakceptowany. Uzupełnij dodatkowe informacje, aby kontynuować.",
            "Uzupełnij i zapisz wymagane pola.",
            "warning",
        )
    if state["status"] == ProcessStatus.DECLARATION_SIGNATURE_INVALID:
        return (
            "Podpis deklaracji wymaga poprawy",
            "Wgrany podpis deklaracji nie przeszedł weryfikacji.",
            "Podpisz deklarację ponownie i wgraj poprawny plik.",
            "danger",
        )
    if state["status"] == ProcessStatus.AGREEMENT_SIGNATURE_INVALID:
        return (
            "Podpis umowy wymaga poprawy",
            "Wgrany podpis umowy nie przeszedł weryfikacji.",
            "Podpisz umowę ponownie i wgraj poprawny plik.",
            "danger",
        )
    if state["agreement_completed"]:
        return (
            "Proces został zakończony",
            "Wszystkie wymagane dokumenty zostały prawidłowo obsłużone.",
            "Nie musisz wykonywać kolejnych czynności.",
            "success",
        )
    if state["beneficiary_signed"]:
        return (
            "Umowa oczekuje na podpis urzędu",
            "Podpisana umowa została przekazana i oczekuje na zakończenie obsługi po stronie urzędu.",
            "Poczekaj na podpis i potwierdzenie urzędu.",
            "neutral",
        )
    if state["status"] == ProcessStatus.TRAINING_SELECTION_OPEN:
        return (
            "Wybierz szkolenie",
            "Wybierz dostępne szkolenie, aby kontynuować proces.",
            "Wybierz szkolenie.",
            "warning",
        )
    if state["status"] == ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE:
        return (
            "Umowa jest gotowa do podpisania",
            "Pobierz umowę, podpisz ją elektronicznie i wgraj podpisany plik.",
            "Pobierz umowę, podpisz ją i wgraj podpisany plik w sekcji „Wgraj podpisane umowy”.",
            "warning",
        )
    if state["agreement_required"] and state["declaration_completed"]:
        if state["agreement_generated"]:
            return (
                "Umowa jest gotowa do podpisania",
                "Pobierz umowę, podpisz ją elektronicznie i wgraj podpisany plik.",
                "Pobierz umowę, podpisz ją i wgraj podpisany plik w sekcji „Wgraj podpisane umowy”.",
                "warning",
            )
        return (
            "Umowa jest gotowa do wygenerowania",
            "Deklaracja została zakończona poprawnie. Możesz przejść do wygenerowania umowy.",
            "Wygeneruj umowę.",
            "warning",
        )
    if state["declaration_required"] and not state["declaration_completed"]:
        if state["declaration_generated"]:
            return (
                "Deklaracja jest gotowa do podpisania",
                "Pobierz deklarację, podpisz ją elektronicznie i wgraj podpisany plik.",
                "Podpisz i wgraj deklarację.",
                "warning",
            )
        return (
            "Uzupełnij deklarację",
            "Wniosek został zaakceptowany. Uzupełnij deklarację, aby wygenerować dokument.",
            "Wypełnij deklarację.",
            "warning",
        )
    return (
        "Wniosek został zaakceptowany",
        "Wniosek przeszedł weryfikację.",
        "Nie musisz teraz wykonywać dodatkowych czynności.",
        "success",
    )


def _training_agreements(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _active_training_agreements(row: Mapping[str, Any]) -> list[dict]:
    agreements = _training_agreements(row.get("training_agreements"))
    selected = _training_agreements(row.get("selected_trainings"))
    selected_ids = {str(item.get("id") or item.get("training_id") or "").strip() for item in selected}
    raw_selection = row.get("selected_trainings")
    snapshot_present = raw_selection is not None and str(raw_selection).strip() not in {"", "null", "None"}
    inactive = {
        "unselected", "cancelled", "cancelled_before_signed_agreement",
        "rejected", "inactive_without_signed_agreement",
    }
    result = []
    for agreement in agreements:
        nested = agreement.get("training") if isinstance(agreement.get("training"), dict) else {}
        training_id = str(nested.get("id") or agreement.get("training_id") or agreement.get("id") or "").strip()
        status = str(agreement.get("participant_status") or "")
        if status in inactive:
            continue
        if snapshot_present and training_id not in selected_ids and not (
            agreement.get("is_locked") or agreement.get("signature_valid")
        ):
            continue
        result.append(agreement)
    return result
