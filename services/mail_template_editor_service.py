from __future__ import annotations

from datetime import datetime
from typing import Any

from services.form_config_service import TRIGGER_DESCRIPTIONS
from services.status_catalog import WORKFLOW_STATUS_LABELS


TRIGGER_EVENT_LABELS = {
    "manual": "Wiadomość wysyłana ręcznie",
    "manual_bulk": "Wiadomość zbiorcza",
    "application_submitted": "Wniosek złożony",
    "submission_received": "Potwierdzenie odebrania wniosku",
    "officer_accepted": "Wniosek zaakceptowany",
    "officer_rejected": "Wniosek odrzucony",
    "auto_rejected_by_condition": "Automatyczne odrzucenie",
    "returned_for_correction": "Wysłanie do poprawy",
    "additional_fields_completed": "Uzupełniono dodatkowe pola",
    "document_generated": "Dokument wygenerowany",
    "document_uploaded": "Dokument wgrany",
    "declaration_signed": "Deklaracja podpisana",
    "agreement_ready": "Umowa gotowa",
    "agreement_signed_by_user": "Umowa podpisana przez beneficjenta",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "stage_rollback": "Cofnięcie etapu",
    "email_requested": "Workflow wymaga wiadomości",
}


VARIABLE_GROUPS = (
    (
        "Zmienne systemowe",
        (
            ("app_name", "Nazwa aplikacji", "Portal formularzy"),
            ("current_year", "Bieżący rok", str(datetime.now().year)),
            ("base_url", "Adres bazowy aplikacji", "https://formularze.example.com"),
            ("contact_email", "Adres kontaktowy", "kontakt@example.com"),
        ),
    ),
    (
        "Formularz",
        (
            ("form_name", "Nazwa formularza", "Program szkoleniowy"),
            ("form_title", "Tytuł formularza", "Formularz zgłoszeniowy"),
            ("form_slug", "Techniczny identyfikator formularza", "program-szkoleniowy"),
            ("form_description", "Opis formularza", "Zgłoszenie udziału w programie"),
        ),
    ),
    (
        "Zgłoszenie",
        (
            ("submission_id", "Wewnętrzny numer zgłoszenia", "123"),
            ("public_submission_id", "Publiczny identyfikator zgłoszenia", "6ef64b28-530b"),
            ("created_at", "Data utworzenia zgłoszenia", "28.07.2026 10:30"),
            ("updated_at", "Data ostatniej aktualizacji", "28.07.2026 11:15"),
            ("process_status", "Techniczny status procesu", "SUBMITTED"),
            ("process_status_label", "Czytelna nazwa statusu procesu", "Wniosek złożony"),
            ("status_label", "Czytelna nazwa statusu (alias)", "Wniosek złożony"),
        ),
    ),
    (
        "Workflow i decyzja",
        (
            ("officer_decision", "Decyzja urzędnika", "accepted"),
            ("officer_decision_reason", "Uzasadnienie decyzji", "Wniosek spełnia wymagania."),
            ("correction_message", "Wiadomość dotycząca korekty", "Uzupełnij brakujące dane."),
            ("user_instruction", "Instrukcja dla uczestnika", "Podpisz wygenerowany dokument."),
            ("acceptance_required", "Czy akceptacja jest wymagana", "Tak"),
            ("declaration_required", "Czy deklaracja jest wymagana", "Tak"),
            ("agreement_required", "Czy umowa jest wymagana", "Nie"),
        ),
    ),
    (
        "Dokumenty",
        (
            ("pdf_filename", "Nazwa wygenerowanego PDF", "zgloszenie.pdf"),
            ("declaration_filename", "Nazwa deklaracji", "deklaracja.pdf"),
            ("agreement_filename", "Nazwa umowy", "umowa.pdf"),
            ("signed_pdf_filename", "Nazwa podpisanego PDF", "zgloszenie-podpisane.pdf"),
            ("declaration_signed_filename", "Nazwa podpisanej deklaracji", "deklaracja-podpisana.pdf"),
            ("agreement_signed_filename", "Nazwa podpisanej umowy", "umowa-podpisana.pdf"),
            ("status_url", "Link do statusu zgłoszenia", "https://formularze.example.com/status/abc"),
            ("podpisz_url", "Link do podpisania dokumentów", "https://formularze.example.com/podpisz/abc"),
            ("document_url", "Link do pobrania dokumentu", "https://formularze.example.com/dokument/abc"),
        ),
    ),
)

TRAINING_VARIABLES = (
    ("selected_trainings", "Wybrane szkolenia", "Excel, Zarządzanie projektem"),
    ("selected_trainings_count", "Liczba wybranych szkoleń", "2"),
    ("trainings_total_price", "Łączna cena szkoleń", "1 200,00 zł"),
    ("all_selected_trainings_total_formatted", "Sformatowana łączna cena szkoleń", "1 200,00 zł"),
)


def trigger_event_options(form) -> list[dict[str, str]]:
    values = dict(TRIGGER_EVENT_LABELS)
    values.update({key: _humanize(key) for key in TRIGGER_DESCRIPTIONS})
    definition = getattr(form, "definition_json", None) or {}
    workflow = definition.get("workflow") or {}
    for collection in (definition.get("notifications") or [], workflow.get("notifications") or []):
        for item in collection if isinstance(collection, list) else []:
            value = str((item or {}).get("event") or (item or {}).get("trigger") or "").strip()
            if value:
                values.setdefault(value, _humanize(value))
    return [{"value": value, "label": label} for value, label in sorted(values.items(), key=lambda item: item[1])]


def trigger_status_options(form) -> list[dict[str, str]]:
    values = dict(WORKFLOW_STATUS_LABELS)
    workflow = (getattr(form, "definition_json", None) or {}).get("workflow") or {}
    for step in workflow.get("steps") or []:
        value = str((step or {}).get("status") or (step or {}).get("id") or "").strip()
        if value:
            values.setdefault(value, str((step or {}).get("label") or (step or {}).get("name") or _humanize(value)))
    return [{"value": value, "label": label} for value, label in sorted(values.items(), key=lambda item: item[1])]


def build_variable_catalog(form, preview_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = preview_context or {}
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category, definitions in VARIABLE_GROUPS:
        variables = [_variable(name, description, example, context) for name, description, example in definitions]
        groups.append({"category": category, "variables": variables})
        seen.update(item["name"] for item in variables)

    field_variables = []
    for field in _form_fields(form):
        name = str(field.get("name") or "").strip()
        if not name or name in seen:
            continue
        field_variables.append(
            _variable(
                name,
                str(field.get("label") or _humanize(name)),
                _field_example(field),
                context,
            )
        )
        seen.add(name)
    if field_variables:
        groups.insert(3, {"category": "Pola formularza i uczestnik", "variables": field_variables})

    if _supports_trainings(form, seen):
        training_variables = [
            _variable(name, description, example, context)
            for name, description, example in TRAINING_VARIABLES
            if name not in seen
        ]
        groups.append(
            {
                "category": "Szkolenia",
                "variables": training_variables,
            }
        )
    return groups


def _form_fields(form) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for field in sorted(getattr(form, "fields", []) or [], key=lambda item: getattr(item, "sort_order", 0)):
        if getattr(field, "active", True):
            result.append(
                {
                    "name": getattr(field, "name", ""),
                    "label": getattr(field, "label", ""),
                    "type": getattr(field, "type", "text"),
                    "default": getattr(field, "default_value", ""),
                    "options": getattr(field, "options", []),
                }
            )
    for field in (getattr(form, "definition_json", None) or {}).get("fields") or []:
        if isinstance(field, dict) and field.get("active", True):
            result.append(field)
    return result


def _supports_trainings(form, field_names: set[str]) -> bool:
    definition = getattr(form, "definition_json", None) or {}
    return bool(
        definition.get("trainings")
        or definition.get("training_catalog")
        or any("training" in name or "szkolen" in name for name in field_names)
    )


def _field_example(field: dict[str, Any]) -> str:
    if field.get("default"):
        return str(field["default"])
    options = field.get("options") or []
    if isinstance(options, list) and options:
        first = options[0]
        return str(first.get("label") or first.get("value") or "") if isinstance(first, dict) else str(first)
    field_type = str(field.get("type") or "")
    return {
        "email": "jan.kowalski@example.com",
        "tel": "+48 600 000 000",
        "phone": "+48 600 000 000",
        "date": "28.07.2026",
        "checkbox": "Tak",
        "number": "1",
    }.get(field_type, f"Przykładowa wartość: {field.get('label') or _humanize(str(field.get('name') or 'pole'))}")


def _variable(name: str, description: str, fallback: str, context: dict[str, Any]) -> dict[str, str]:
    raw_example = context.get(name, fallback)
    if raw_example is None or raw_example == "":
        raw_example = fallback
    return {
        "name": name,
        "placeholder": "{{ " + name + " }}",
        "description": description,
        "example": str(raw_example),
    }


def _humanize(value: str) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()
