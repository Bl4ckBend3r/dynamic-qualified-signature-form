from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models import Form, FormField, FormSubmission
from services.admin_form_service import normalize_admin_form_definition
from services.form_option_service import option_label_for_value
from services.training_service import format_admin_value, parse_training_snapshots
from services.workflow_service import workflow_status_label
from services.status_catalog import WORKFLOW_STATUS_LABELS


SUBMISSION_SORT_FIELDS = {
    "submission_id",
    "full_name",
    "nazwisko",
    "email",
    "telefon",
    "created_at",
    "form_slug",
    "process_status",
    "officer_decision",
    "workflow_stage",
}


def admin_status_label(status_id: str, form: Form | None = None) -> str:
    form_config = normalize_admin_form_definition(form.definition_json or {}) if form else {}
    return workflow_status_label(status_id, form_config)


def build_status_filter_options(
    forms: list[Form] | Form | None,
    submissions: list[FormSubmission] | None = None,
) -> list[dict[str, str]]:
    """Build stable Polish status choices from each form workflow and stored rows."""
    form_list = [] if forms is None else (forms if isinstance(forms, list) else [forms])
    labels: dict[str, str] = {}
    for form in form_list:
        raw_definition = form.definition_json or {}
        definition = normalize_admin_form_definition({**raw_definition, "fields": raw_definition.get("fields") or []})
        workflow = definition.get("workflow") or {}
        raw_statuses = workflow.get("statuses") or []
        if isinstance(raw_statuses, dict):
            raw_statuses = [
                {"id": key, **(value if isinstance(value, dict) else {"label": value})}
                for key, value in raw_statuses.items()
            ]
        for status in raw_statuses if isinstance(raw_statuses, list) else []:
            if not isinstance(status, dict):
                continue
            code = str(status.get("id") or status.get("value") or "").strip()
            if code:
                labels.setdefault(code, str(status.get("label") or status.get("name") or admin_status_label(code, form)))
        for step in workflow.get("steps") or []:
            if not isinstance(step, dict) or step.get("active") is False:
                continue
            code = str(step.get("status") or step.get("status_code") or step.get("id") or "").strip()
            if code:
                labels.setdefault(
                    code,
                    str(step.get("admin_label") or step.get("user_label") or step.get("label") or step.get("name") or admin_status_label(code, form)),
                )

    # Forms without an explicit workflow use the complete platform catalog.
    # Custom workflows remain intentionally limited to their configured stages.
    if not labels:
        labels.update(WORKFLOW_STATUS_LABELS)
    for submission in submissions or []:
        code = str(submission.process_status or "").strip()
        if code:
            form = next((item for item in form_list if item.slug == submission.form_slug), None)
            labels.setdefault(code, admin_status_label(code, form))
    return [
        {"value": code, "label": label}
        for code, label in sorted(labels.items(), key=lambda item: (item[1].casefold(), item[0]))
    ]


def paginate_submissions(
    submissions: list[FormSubmission],
    page_value: Any,
    per_page_value: Any,
) -> tuple[list[FormSubmission], dict[str, int | bool]]:
    try:
        page = max(1, int(page_value or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(per_page_value or 50)
    except (TypeError, ValueError):
        per_page = 50
    per_page = min(200, max(10, per_page))
    total = len(submissions)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    return submissions[start : start + per_page], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_previous": page > 1,
        "has_next": page < pages,
    }


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

BUILTIN_SENSITIVE_FIELDS = {
    "imiona", "nazwisko", "obywatelstwo", "wyksztalcenie",
    "pesel", "data_urodzenia", "miejsce_urodzenia", "plec", "wiek",
    "telefon", "email", "wojewodztwo", "powiat", "gmina", "miejscowosc",
    "ulica", "nr_budynku", "nr_lokalu", "kod_pocztowy",
    "osoba_niepelnosprawna", "specjalne_potrzeby", "specjalne_potrzeby_opis",
    "mniejszosc_narodowa", "osoba_bezdomna", "niekorzystna_sytuacja",
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


def build_submission_detail_sections(
    form: Form,
    submission: FormSubmission,
    form_config: dict | None = None,
    *,
    include_sensitive: bool = True,
    timezone_name: str = "Europe/Warsaw",
) -> dict:
    form_config = normalize_admin_form_definition(form_config if form_config is not None else form.definition_json or {})
    labels = build_field_labels(form_config)
    options_by_field = build_field_options(form_config)
    classified_fields = {
        str(field.get("name") or field.get("key") or "")
        for field in form_config.get("fields", []) if isinstance(field, dict)
        and str(field.get("data_classification") or field.get("sensitivity") or "normal") != "normal"
    }
    sensitive_fields = BUILTIN_SENSITIVE_FIELDS | classified_fields
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
            if not include_sensitive and field_name in sensitive_fields:
                display_value = "Dane ukryte — brak uprawnienia"
            elif field_name == "created_at":
                display_value = format_business_datetime(value, "%d.%m.%Y %H:%M", timezone_name=timezone_name)
            else:
                display_value = option_label_for_value(options_by_field.get(field_name), value) if field_name in options_by_field else format_admin_value(value)
            items.append({"label": labels.get(field_name, DEFAULT_LABELS.get(field_name, field_name)), "value": display_value})
            used_fields.add(field_name)
        if items:
            sections.append({"title": title, "items": items})

    dynamic_items = []
    for key, value in (submission.data_json or {}).items():
        if str(key).startswith("_") or key in used_fields or key in TECHNICAL_FIELDS or is_empty_admin_value(value):
            continue
        display_value = "Dane ukryte — brak uprawnienia" if not include_sensitive and key in sensitive_fields else (option_label_for_value(options_by_field.get(key), value) if key in options_by_field else format_admin_value(value))
        dynamic_items.append({"label": labels.get(key, key), "value": display_value})
        used_fields.add(key)
    if dynamic_items:
        sections.append({"title": "Dodatkowe dane formularza", "items": dynamic_items})

    technical_items = [
        {"label": key, "value": format_admin_value(value)}
        for key, value in row.items()
        if key in TECHNICAL_FIELDS and key not in {"access_token", "data_json"} and not is_empty_admin_value(value)
    ]
    return {
        "sections": sections,
        "trainings": parse_training_snapshots(submission.selected_trainings),
        "technical_items": technical_items,
        "qualification": build_qualification_detail(submission.data_json or {}, form_config) if include_sensitive else None,
    }


def build_qualification_detail(data_json: dict, form_config: dict | None = None) -> dict | None:
    evaluation = data_json.get("_qualification") if isinstance(data_json, dict) else None
    if not isinstance(evaluation, dict):
        return None
    results = []
    options_by_field = build_field_options(form_config or {})
    for item in evaluation.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "field_label": item.get("field_label") or item.get("field_name") or "Warunek",
                "operator": item.get("operator") or "",
                "expected_value": option_label_for_value(options_by_field.get(item.get("field_name")), item.get("expected_value")) if item.get("field_name") in options_by_field else format_admin_value(item.get("expected_value")),
                "actual_value": option_label_for_value(options_by_field.get(item.get("field_name")), item.get("actual_value")) if item.get("field_name") in options_by_field else format_admin_value(item.get("actual_value")),
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


def build_field_options(form_config: dict) -> dict[str, list]:
    result: dict[str, list] = {}
    fields = list(form_config.get("fields") or [])
    documents = form_config.get("documents") or []
    if isinstance(documents, dict):
        documents = documents.values()
    for document in documents:
        if isinstance(document, dict):
            fields.extend(document.get("fields") or [])
    for field in fields:
        if isinstance(field, dict) and field.get("name") and field.get("options"):
            result[str(field["name"])] = list(field.get("options") or [])
    return result


def is_empty_admin_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return not value
    return False


def filter_submissions(
    submissions: list[FormSubmission],
    args,
    *,
    timezone_name: str = "Europe/Warsaw",
) -> list[FormSubmission]:
    q = str(args.get("q") or "").strip().lower()
    status = str(args.get("status") or "").strip()
    field = str(args.get("field") or "").strip()
    operator = str(args.get("operator") or "contains").strip()
    value = str(args.get("value") or "").strip()
    value_to = str(args.get("value_to") or "").strip()
    date_from = parse_date_value(args.get("date_from"))
    date_to = parse_date_value(args.get("date_to"))
    submission_id = str(args.get("submission_id") or "").strip().casefold()
    full_name = str(args.get("full_name") or "").strip().casefold()
    email = str(args.get("email") or "").strip().casefold()
    phone = str(args.get("telefon") or args.get("phone") or "").strip().casefold()
    form_slug = str(args.get("form_slug") or "").strip()
    workflow_stage = str(args.get("workflow_stage") or "").strip().casefold()

    def matches(submission: FormSubmission) -> bool:
        if status and submission.process_status != status:
            return False
        if form_slug and submission.form_slug != form_slug:
            return False
        if submission_id and submission_id not in str(submission.submission_id or "").casefold():
            return False
        name_value = " ".join(
            part for part in [str(getattr(submission, "imiona", "") or ""), str(getattr(submission, "nazwisko", "") or "")] if part
        ).casefold()
        if full_name and full_name not in name_value:
            return False
        if email and email not in str(submission.email or "").casefold():
            return False
        if phone and phone not in str(getattr(submission, "telefon", "") or "").casefold():
            return False
        stage_value = str(getattr(submission, "workflow_stage", "") or getattr(submission, "workflow_step", "") or "").casefold()
        if workflow_stage and workflow_stage not in stage_value:
            return False
        if (date_from or date_to) and not matches_created_at_range(
            submission.created_at,
            date_from,
            date_to,
            timezone_name=timezone_name,
        ):
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


def matches_created_at_range(raw_value, date_from, date_to, *, timezone_name: str = "Europe/Warsaw") -> bool:
    if not raw_value:
        return False
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_zone = timezone.utc
    value = raw_value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local_value = value.astimezone(local_zone)
    if date_from:
        start = datetime.combine(date_from, time.min, tzinfo=local_zone)
        if local_value < start:
            return False
    if date_to:
        end = datetime.combine(date_to, time.max, tzinfo=local_zone)
        if local_value > end:
            return False
    return True


def format_business_datetime(raw_value, format_string: str = "%Y-%m-%d %H:%M", *, timezone_name: str = "Europe/Warsaw") -> str:
    """Format a stored UTC timestamp in the configured business timezone."""
    if not raw_value:
        return ""
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_zone = timezone.utc
    value = raw_value
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return str(raw_value)
    if not isinstance(value, datetime):
        return str(raw_value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_zone).strftime(format_string)


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
    sort_field = sort_field if sort_field in SUBMISSION_SORT_FIELDS else "created_at"
    direction = direction if direction in {"asc", "desc"} else "desc"
    reverse = direction == "desc"

    def sort_key(submission: FormSubmission):
        if sort_field == "full_name":
            value = f"{getattr(submission, 'imiona', '') or ''} {getattr(submission, 'nazwisko', '') or ''}".strip()
        elif sort_field == "workflow_stage":
            value = getattr(submission, "workflow_stage", "") or getattr(submission, "workflow_step", "") or ""
        else:
            value = submission_value(submission, sort_field)
        return "" if value is None else str(value).lower()

    if sort_field == "created_at":
        return sorted(submissions, key=lambda item: item.created_at or datetime.min, reverse=reverse)
    return sorted(submissions, key=sort_key, reverse=reverse)
