from __future__ import annotations

from datetime import datetime
from typing import Any

from form_loader import evaluate_scoped_condition
from services.form_option_service import option_label_for_value
from services.mail_training_context_service import find_training_field
from services.workflow_mail_trigger_service import WorkflowMailTriggerService


VARIABLE_GROUPS = (
    (
        "Osoba z listy — osobne powiadomienia",
        (
            ("participant.record_uuid", "UUID odbiorcy", "6ef64b28-530b-4df0-b421-53e6c8dd2735"),
            ("participant.first_name", "Imię odbiorcy", "Jan"),
            ("participant.last_name", "Nazwisko odbiorcy", "Kowalski"),
            ("participant.full_name", "Imię i nazwisko odbiorcy", "Jan Kowalski"),
            ("participant.email", "E-mail tylko tego odbiorcy", "jan@example.org"),
            ("participant.phone", "Telefon tylko tego odbiorcy", "+48 500 000 000"),
            ("submitted_at", "Data złożenia", "02.09.2026"),
            ("current_status", "Publiczna etykieta statusu", "Wniosek oczekuje na weryfikację"),
            ("public_status_url", "Publiczny link tylko do statusu", "https://formularze.example.com/sprawdz-status"),
            ("participant_action_url", "Chroniony link — tylko dla wnioskodawcy, dla pozostałych pusty", ""),
        ),
    ),
    (
        "Systemowe",
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
            ("current_stage", "Techniczny identyfikator bieżącego etapu", "officer_review"),
            ("current_stage_label", "Czytelna nazwa bieżącego etapu", "Weryfikacja przez urzędnika"),
            ("status_label", "Czytelna nazwa statusu (alias)", "Wniosek złożony"),
        ),
    ),
    (
        "Workflow",
        (
            ("trigger_event", "Zdarzenie wyzwalające wiadomość", "application_submitted"),
            ("trigger_status", "Status wyzwalający wiadomość", "FORM_SUBMITTED"),
            ("trigger_decision", "Decyzja wyzwalająca wiadomość", "accepted"),
            ("correction_message", "Wiadomość dotycząca korekty", "Uzupełnij brakujące dane."),
            ("user_instruction", "Instrukcja dla uczestnika", "Podpisz wygenerowany dokument."),
            ("acceptance_required", "Czy akceptacja jest wymagana", "Tak"),
            ("declaration_required", "Czy deklaracja jest wymagana", "Tak"),
            ("agreement_required", "Czy umowa jest wymagana", "Nie"),
            ("participant_name", "Imię i nazwisko uczestnika dla SLA", "Jan Kowalski"),
            ("step_label", "Nazwa etapu objętego SLA", "Weryfikacja urzędnika"),
            ("due_at", "Termin etapu SLA", "2026-08-20 14:30"),
            ("overdue_by", "Czas przekroczenia terminu", "1 day, 2:00:00"),
            ("overdue_hours", "Liczba godzin po terminie", "26"),
            ("assigned_officer", "E-mail przypisanego urzędnika", "anna.kowalska@example.org"),
            ("sla_actor_type", "Strona odpowiedzialna za etap", "office"),
        ),
    ),
    (
        "Decyzje urzędnika",
        (
            ("officer_decision", "Decyzja urzędnika", "accepted"),
            ("officer_decision_reason", "Uzasadnienie decyzji", "Wniosek spełnia wymagania."),
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
            ("signed_agreement_download_link", "Bezpieczny link do podpisanej umowy", "https://formularze.example.com/dokument/umowa-podpisana.pdf"),
            ("signed_agreement_filename", "Nazwa finalnej podpisanej umowy", "umowa-podpisana.pdf"),
            ("agreement_number", "Numer umowy", "UM/2026/123"),
            ("agreement_signed_by_office_at", "Data podpisania przez urząd", "29.07.2026 12:00"),
            ("signed_agreements_table", "Podpisane umowy jako tabela HTML", "Tabela podpisanych umów"),
            ("signed_agreements_list", "Podpisane umowy jako lista HTML", "Lista podpisanych umów"),
            ("signed_agreements_text", "Podpisane umowy jako tekst", "1. umowa-podpisana.pdf"),
            ("status_url", "Link do statusu zgłoszenia", "https://formularze.example.com/status/abc"),
            ("draft_resume_url", "Link do wznowienia wersji roboczej", "https://formularze.example.com/form/przyklad/draft/token"),
            ("draft_expires_at", "Data wygaśnięcia wersji roboczej", "2026-09-12 14:30"),
            ("podpisz_url", "Link do podpisania dokumentów", "https://formularze.example.com/podpisz/abc"),
            ("document_url", "Link do pobrania dokumentu", "https://formularze.example.com/dokument/abc"),
            ("pobierz_url", "Link do pobrania dokumentu (alias)", "https://formularze.example.com/dokument/abc"),
        ),
    ),
    (
        "E-mail",
        (
            ("email", "Adres e-mail uczestnika", "jan.kowalski@example.com"),
            ("reply_to", "Adres odpowiedzi dla wiadomości", "kontakt@example.com"),
            ("sender_name", "Nazwa nadawcy wiadomości", "Portal formularzy"),
        ),
    ),
)

TRAINING_VARIABLES = (
    ("available_trainings", "Dostępne szkolenia, domyślnie jako tabela HTML", "Tabela szkoleń z cenami i dostępnością"),
    ("available_trainings_table", "Dostępne szkolenia jako tabela HTML", "Tabela szkoleń"),
    ("available_trainings_list", "Dostępne szkolenia jako lista HTML", "Lista szkoleń"),
    ("available_trainings_text", "Dostępne szkolenia jako tekst", "Dostępne szkolenia: …"),
    ("available_trainings_list_text", "Dostępne szkolenia jako lista tekstowa", "1. Nazwa szkolenia …"),
    ("selected_trainings", "Szkolenia wybrane przez użytkownika", "Excel, Zarządzanie projektem"),
    ("selected_trainings_table", "Wybrane szkolenia jako tabela HTML", "Tabela wybranych szkoleń"),
    ("selected_trainings_list", "Wybrane szkolenia jako lista HTML", "Lista wybranych szkoleń"),
    ("selected_trainings_text", "Wybrane szkolenia jako tekst", "1. Excel …"),
    ("selected_trainings_count", "Liczba wybranych szkoleń", "2"),
    ("trainings_total_price", "Łączna wartość wybranych szkoleń", "1 200,00 zł"),
    ("all_selected_trainings_total_formatted", "Sformatowana łączna cena szkoleń", "1 200,00 zł"),
)
TRAINING_VARIABLE_NAMES = {item[0] for item in TRAINING_VARIABLES}


def trigger_event_options(form) -> list[dict[str, str]]:
    return WorkflowMailTriggerService().options_for_form(form)["events"]


def trigger_status_options(form) -> list[dict[str, str]]:
    return WorkflowMailTriggerService().options_for_form(form)["statuses"]


def build_variable_catalog(form, preview_context: dict[str, Any] | None = None, *,
                           definition: dict | None = None, include_sensitive: bool = True,
                           actual_values: bool = False, include_form_fields: bool = True) -> list[dict[str, Any]]:
    context = preview_context or {}
    definition = definition if definition is not None else (getattr(form, "definition_json", None) or {})
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category, definitions in VARIABLE_GROUPS:
        variables = [_variable(name, description, example, context, actual_values=actual_values) for name, description, example in definitions]
        groups.append({"category": category, "variables": variables})
        seen.update(item["name"] for item in variables)

    field_variables = []
    field_definitions = _form_fields(form, definition, include_form_fields=include_form_fields)
    field_names = {str(field.get("name") or "") for field in field_definitions}
    if not include_sensitive:
        from services.admin_submission_service import BUILTIN_SENSITIVE_FIELDS
    else:
        BUILTIN_SENSITIVE_FIELDS = set()
    for field in field_definitions:
        name = str(field.get("name") or "").strip()
        if actual_values and field.get("visible_if") and not evaluate_scoped_condition(
                field["visible_if"], root_data=context.get("data_json") or context,
                current_data=context.get("data_json") or context):
            continue
        if (not name or name in seen or name in TRAINING_VARIABLE_NAMES
                or not include_sensitive and (name in BUILTIN_SENSITIVE_FIELDS
                    or str(field.get("data_classification") or field.get("sensitivity") or "normal") != "normal"
                    or field.get("type") == "repeatable_group" and any(
                        str(child.get("data_classification") or child.get("sensitivity") or "normal") != "normal"
                        for child in field.get("fields") or [] if isinstance(child, dict)))):
            continue
        field_variables.append(
            _variable(
                name,
                str(field.get("label") or _humanize(name)),
                _field_example(field),
                context,
                actual_values=actual_values,
                field=field,
            )
        )
        seen.add(name)
        if field.get("type") in {"repeatable_group", "checkbox", "multi_select", "multiselect", "multiple_select", "checkbox_group", "checkbox-group"}:
            text_name = f"{name}_text"
            if text_name not in seen and text_name not in field_names:
                field_variables.append(_variable(text_name, f"{field.get('label') or _humanize(name)} — tekst",
                    "", context, actual_values=actual_values))
                seen.add(text_name)
    if field_variables:
        groups.insert(3, {"category": "Pola formularza", "variables": field_variables})

    if _supports_trainings(form, seen, definition):
        training_variables = [
            _variable(name, description, example, context, actual_values=actual_values)
            for name, description, example in TRAINING_VARIABLES
            if name not in seen
        ]
        email_group_index = next(
            (index for index, group in enumerate(groups) if group["category"] == "E-mail"),
            len(groups),
        )
        groups.insert(
            email_group_index,
            {
                "category": "Szkolenia",
                "variables": training_variables,
            }
        )
    return groups


def _form_fields(form, definition: dict, *, include_form_fields: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if include_form_fields:
        for field in sorted(getattr(form, "fields", []) or [], key=lambda item: getattr(item, "sort_order", 0)):
            if getattr(field, "active", True):
                result.append({"name": getattr(field, "name", ""), "label": getattr(field, "label", ""),
                    "type": getattr(field, "type", "text"), "default": getattr(field, "default_value", ""),
                    "options": getattr(field, "options", [])})
    for field in definition.get("fields") or []:
        if isinstance(field, dict) and field.get("active", True) and field.get("enabled", True):
            result.append(field)
    return result


def _supports_trainings(form, _field_names: set[str], definition: dict) -> bool:
    from types import SimpleNamespace
    return find_training_field(SimpleNamespace(definition_json=definition)) is not None


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


def _variable(name: str, description: str, fallback: str, context: dict[str, Any], *,
              actual_values: bool = False, field: dict | None = None) -> dict[str, Any]:
    raw_example = context
    for part in name.split('.'):
        raw_example = raw_example.get(part, "" if actual_values else fallback) if isinstance(raw_example, dict) else ("" if actual_values else fallback)
    if raw_example is None or raw_example == "":
        raw_example = "" if actual_values else fallback
    if field and raw_example != "":
        if isinstance(raw_example, (list, tuple)) and field.get("type") == "repeatable_group":
            raw_example = f"{len(raw_example)} rekordów"
        elif isinstance(raw_example, (list, tuple, set)):
            raw_example = ", ".join(option_label_for_value(field.get("options"), item) for item in raw_example)
        elif field.get("type") == "checkbox" and isinstance(raw_example, bool):
            raw_example = "Tak" if raw_example else "Nie"
        elif field.get("options"):
            raw_example = option_label_for_value(field.get("options"), raw_example)
    return {
        "name": name,
        "key": name,
        "placeholder": "{{ " + name + " }}",
        "label": description,
        "description": description,
        "type": str(field.get("type") or "text") if field else "",
        "example": str(raw_example),
        "available_in_html": True,
        "available_in_txt": True,
    }


def _humanize(value: str) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()
