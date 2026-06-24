from __future__ import annotations

from datetime import datetime
from typing import Any

from models import Form, FormField, FormSubmission
from services.admin_form_service import normalize_admin_form_definition
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
            if key not in seen:
                result.append((key, key))
                seen.add(key)
    return result


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
