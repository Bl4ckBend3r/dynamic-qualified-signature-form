from __future__ import annotations

from datetime import datetime
from typing import Any

from models import Form, FormField, FormSubmission
from services.admin_form_service import normalize_admin_form_definition
from services.training_service import format_admin_value, parse_training_snapshots
from services.workflow_service import workflow_status_label


def admin_status_label(status_id: str, form: Form | None = None) -> str:
    form_config = normalize_admin_form_definition(form.definition_json or {}) if form else {}
    return workflow_status_label(status_id, form_config)


def submission_value(submission: FormSubmission, field_name: str) -> Any:
    if hasattr(submission, field_name):
        return getattr(submission, field_name)
    return (submission.data_json or {}).get(field_name, "")


def build_filter_fields(fields: list[FormField], submissions: list[FormSubmission]) -> list[tuple[str, str]]:
    technical_fields = [
        ("created_at", "Data utworzenia"),
        ("process_status", "Status procesu"),
        ("officer_decision", "Decyzja urzednika"),
        ("email", "E-mail"),
        ("nazwisko", "Nazwisko"),
        ("submission_id", "ID zgloszenia"),
    ]
    seen = {name for name, _ in technical_fields}
    result = list(technical_fields)
    for field in fields:
        if field.name not in seen:
            result.append((field.name, field.label or field.name))
            seen.add(field.name)
    for submission in submissions:
        for key in (submission.data_json or {}).keys():
            if not str(key).startswith("_") and key not in seen:
                result.append((key, key))
                seen.add(key)
    return result


SECTION_FIELDS = [
    ("Dane podstawowe", ["submission_id", "form_name", "created_at", "imiona", "nazwisko", "obywatelstwo", "wyksztalcenie"]),
    ("Dane kontaktowe", ["email", "telefon"]),
    ("Adres", ["wojewodztwo", "powiat", "gmina", "miejscowosc", "kod_pocztowy", "ulica", "nr_budynku", "nr_lokalu"]),
    ("Dane z PESEL", ["pesel", "data_urodzenia", "miejsce_urodzenia", "plec", "wiek"]),
    (
        "Deklaracje i oswiadczenia",
        [
            "zamieszkuje_lubuskie",
            "pracuje_lubuskie",
            "osoba_niepelnosprawna",
            "specjalne_potrzeby",
            "specjalne_potrzeby_opis",
            "mniejszosc_narodowa",
            "osoba_bezdomna",
            "niekorzystna_sytuacja",
            "dzial_wsparcia",
            "osw_regulamin",
            "osw_kryteria",
            "osw_finansowanie",
            "osw_brak_gwarancji",
            "osw_rodo",
            "osw_ewaluacja",
            "osw_zatrudnienie",
            "osw_monitoring",
            "osw_prawdziwosc",
            "deklaracja_18_lat",
            "deklaracja_lubuskie",
            "deklaracja_wlasna_inicjatywa",
            "deklaracja_brak_dzialalnosci",
            "deklaracja_brak_ksztalcenia",
            "deklaracja_obszar_wiejski",
            "deklaracja_niepelnosprawnosc",
            "deklaracja_umiejetnosci_podstawowe",
            "deklaracja_grupa_niekorzystna",
            "deklaracja_zgoda_wizerunek",
            "deklaracja_prawdziwosc_danych",
        ],
    ),
    (
        "Status zgloszenia",
        [
            "process_status",
            "workflow_step",
            "acceptance_required",
            "acceptance_email_sent",
            "declaration_required",
            "declaration_generated",
            "declaration_signed",
            "agreement_required",
            "agreement_blocked",
            "agreement_signed",
            "correction_required",
            "additional_fields_completed",
        ],
    ),
    (
        "Pliki i dokumenty",
        [
            "pdf_filename",
            "signed_pdf_filename",
            "declaration_filename",
            "declaration_signed_filename",
            "agreement_filename",
            "agreement_signed_filename",
            "signature_status",
            "signature_method",
        ],
    ),
    (
        "Decyzje urzednika",
        [
            "officer_decision",
            "officer_decision_reason",
            "officer_decision_email_requested",
            "officer_decision_email_sent",
            "decision_email_sent",
            "decision_email_sent_for",
        ],
    ),
]


TECHNICAL_FIELDS = {
    "id",
    "access_token",
    "data_json",
    "selected_trainings",
    "training_agreements",
    "signature_request_id",
    "updated_at",
}


DEFAULT_LABELS = {
    "submission_id": "ID zgloszenia",
    "form_name": "Formularz",
    "created_at": "Data utworzenia",
    "process_status": "Status procesu",
    "workflow_step": "Krok workflow",
    "officer_decision": "Decyzja urzednika",
    "officer_decision_reason": "Uzasadnienie decyzji",
    "selected_trainings": "Wybrane szkolenia",
}


def build_submission_detail_sections(form: Form, submission: FormSubmission) -> dict:
    form_config = normalize_admin_form_definition(form.definition_json or {})
    labels = build_field_labels(form_config)
    row = {column.name: getattr(submission, column.name) for column in submission.__table__.columns}
    row.update(submission.data_json or {})
    used_fields: set[str] = set()
    sections = []
    for title, field_names in SECTION_FIELDS:
        items = []
        for field_name in field_names:
            value = row.get(field_name)
            if is_empty_admin_value(value):
                continue
            items.append({"label": labels.get(field_name, DEFAULT_LABELS.get(field_name, field_name)), "value": format_admin_value(value)})
            used_fields.add(field_name)
        if items:
            sections.append({"title": title, "items": items})

    dynamic_items = []
    for key, value in (submission.data_json or {}).items():
        if str(key).startswith("_") or key in used_fields or key in TECHNICAL_FIELDS or is_empty_admin_value(value):
            continue
        dynamic_items.append({"label": labels.get(key, key), "value": format_admin_value(value)})
        used_fields.add(key)
    if dynamic_items:
        sections.append({"title": "Dodatkowe dane formularza", "items": dynamic_items})

    technical_items = [
        {"label": key, "value": format_admin_value(value)}
        for key, value in row.items()
        if key in TECHNICAL_FIELDS and not is_empty_admin_value(value)
    ]
    return {
        "sections": sections,
        "trainings": parse_training_snapshots(submission.selected_trainings),
        "technical_items": technical_items,
        "qualification": build_qualification_detail(submission.data_json or {}),
    }


def build_qualification_detail(data_json: dict) -> dict | None:
    evaluation = data_json.get("_qualification") if isinstance(data_json, dict) else None
    if not isinstance(evaluation, dict):
        return None
    results = []
    for item in evaluation.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "field_label": item.get("field_label") or item.get("field_name") or "Warunek",
                "operator": item.get("operator") or "",
                "expected_value": format_admin_value(item.get("expected_value")),
                "actual_value": format_admin_value(item.get("actual_value")),
                "passed": bool(item.get("passed")),
                "officer_message": str(item.get("officer_message") or ""),
            }
        )
    return {
        "enabled": bool(evaluation.get("enabled")),
        "passed": bool(evaluation.get("passed")),
        "evaluated_at": evaluation.get("evaluated_at") or "",
        "user_message": str(evaluation.get("user_message") or ""),
        "results": results,
    }


def build_field_labels(form_config: dict) -> dict[str, str]:
    labels = dict(DEFAULT_LABELS)
    for field in form_config.get("fields") or []:
        if field.get("name"):
            labels[field["name"]] = field.get("label") or field["name"]
    documents = form_config.get("documents") or []
    if isinstance(documents, dict):
        documents = documents.values()
    for document in documents:
        if not isinstance(document, dict):
            continue
        for field in document.get("fields") or []:
            if isinstance(field, dict) and field.get("name"):
                labels[field["name"]] = field.get("label") or field["name"]
    return labels


def is_empty_admin_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return not value
    return False


def filter_submissions(submissions: list[FormSubmission], args) -> list[FormSubmission]:
    q = str(args.get("q") or "").strip().lower()
    status = str(args.get("status") or "").strip()
    field = str(args.get("field") or "").strip()
    operator = str(args.get("operator") or "contains").strip()
    value = str(args.get("value") or "").strip()
    value_to = str(args.get("value_to") or "").strip()

    def matches(submission: FormSubmission) -> bool:
        if status and submission.process_status != status:
            return False
        if field and not matches_field_filter(submission_value(submission, field), operator, value, value_to):
            return False
        if not q:
            return True
        haystack = [
            submission.submission_id,
            submission.email,
            submission.nazwisko,
            submission.process_status,
            submission.officer_decision,
            *(str(item) for item in (submission.data_json or {}).values()),
        ]
        return any(q in str(item).lower() for item in haystack)

    return [submission for submission in submissions if matches(submission)]


def matches_field_filter(raw_value: Any, operator: str, expected: str, expected_to: str = "") -> bool:
    value_text = "" if raw_value is None else str(raw_value)
    value_lower = value_text.lower()
    expected_lower = expected.lower()
    operator = operator or "contains"

    if operator == "empty":
        return value_text.strip() == ""
    if operator == "not_empty":
        return value_text.strip() != ""
    if operator == "equals":
        return value_lower == expected_lower
    if operator == "not_equals":
        return value_lower != expected_lower
    if operator == "not_contains":
        return expected_lower not in value_lower
    if operator == "date_range":
        return matches_date_range(raw_value, expected, expected_to)
    return expected_lower in value_lower


def matches_date_range(raw_value: Any, expected_from: str, expected_to: str) -> bool:
    value_date = parse_date_value(raw_value)
    if not value_date:
        return False
    from_date = parse_date_value(expected_from)
    to_date = parse_date_value(expected_to)
    if from_date and value_date < from_date:
        return False
    if to_date and value_date > to_date:
        return False
    return True


def parse_date_value(value: Any):
    if not value:
        return None
    if hasattr(value, "date"):
        return value.date()
    text = str(value).strip()
    for candidate in [text, text[:10]]:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def sort_submissions(submissions: list[FormSubmission], sort_field: str, direction: str) -> list[FormSubmission]:
    reverse = direction != "asc"

    def sort_key(submission: FormSubmission):
        value = submission_value(submission, sort_field)
        return "" if value is None else str(value).lower()

    if sort_field == "created_at":
        return sorted(submissions, key=lambda item: item.created_at or datetime.min, reverse=reverse)
    return sorted(submissions, key=sort_key, reverse=reverse)
