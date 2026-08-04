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


def build_public_submission_status(row: Mapping[str, Any]) -> dict[str, Any]:
    state = build_process_state(row)
    status = state.status
    decision = get_officer_decision(row)
    rejected = status in REJECTED_STATUSES or decision == OfficerDecision.REJECTED
    correction = status in CORRECTION_STATUSES
    blocked = state.agreement_blocked or status == ProcessStatus.AGREEMENT_BLOCKED
    accepted = decision == OfficerDecision.ACCEPTED
    declaration_required = is_declaration_required(row)
    agreement_required = is_agreement_required(row)
    explicit_status = str(row.get("process_status") or "").strip()
    declaration_completed = is_declaration_signature_valid(row) or (
        bool(explicit_status) and status in DECLARATION_COMPLETED_STATUSES
    )
    training_agreements = _training_agreements(row.get("training_agreements"))
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
            "agreement_generated", "agreement_downloaded", "agreement_waiting_for_beneficiary_signature"
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
    messages = _deduplicate_messages(
        [
            ("application", f"Status wniosku: {application_status}"),
            ("declaration", f"Status deklaracji: {declaration_status}") if declaration_required else None,
            ("agreement", f"Status umowy: {agreement_status}") if agreement_required or blocked else None,
            ("blocking_reason", f"Powód blokady: {blocking_reason}") if blocking_reason else None,
            ("status_reason", f"Powód: {status_reason}") if status_reason and not blocking_reason else None,
            ("next_action", f"Co dalej? {next_action}") if next_action else None,
        ]
    )
    return {
        "effective_process_status": status.value,
        "process_status_label": get_status_label(status.value),
        "application_status": application_status,
        "declaration_status": declaration_status,
        "agreement_status": agreement_status,
        "blocking_reason": blocking_reason,
        "status_reason": status_reason,
        "next_action": next_action,
        "status_title": headline,
        "status_description": description,
        "status_variant": variant,
        "status_messages": messages,
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
            "Wymagana jest poprawa",
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
    if not state["accepted"]:
        return (
            "Wniosek oczekuje na decyzję urzędnika",
            "Dokumenty będą dostępne po zakończeniu weryfikacji.",
            "Poczekaj na decyzję urzędnika.",
            "neutral",
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
    if state["status"] == ProcessStatus.AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE:
        return (
            "Umowa oczekuje na podpis beneficjenta",
            "Umowa została pobrana. Podpisz dokument i wgraj podpisaną umowę.",
            "Wgraj podpisaną umowę. Dopiero wtedy szkolenie i miejsce zostaną zablokowane.",
            "success",
        )
    if state["agreement_required"] and state["declaration_completed"]:
        if state["agreement_generated"]:
            return (
                "Umowa jest gotowa do podpisania",
                "Pobierz umowę, podpisz ją elektronicznie i wgraj podpisany plik.",
                "Podpisz i wgraj umowę.",
                "success",
            )
        return (
            "Umowa jest gotowa do wygenerowania",
            "Deklaracja została zakończona poprawnie. Możesz przejść do wygenerowania umowy.",
            "Wygeneruj umowę.",
            "success",
        )
    if state["declaration_required"] and not state["declaration_completed"]:
        if state["declaration_generated"]:
            return (
                "Deklaracja jest gotowa do podpisania",
                "Pobierz deklarację, podpisz ją elektronicznie i wgraj podpisany plik.",
                "Podpisz i wgraj deklarację.",
                "success",
            )
        return (
            "Deklaracja oczekuje na wypełnienie",
            "Wniosek został zaakceptowany. Uzupełnij deklarację, aby wygenerować dokument.",
            "Wypełnij deklarację.",
            "success",
        )
    return (
        "Wniosek został zaakceptowany",
        "Wniosek przeszedł weryfikację.",
        "Nie musisz teraz wykonywać dodatkowych czynności.",
        "success",
    )


def _deduplicate_messages(items) -> list[dict[str, str]]:
    result = []
    seen = set()
    for item in items:
        if not item:
            continue
        message_type, text = item
        key = (str(message_type).strip().casefold(), " ".join(str(text).split()).casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append({"type": message_type, "text": text})
    return result


def _training_agreements(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
