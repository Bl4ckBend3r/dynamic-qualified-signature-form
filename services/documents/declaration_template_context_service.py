from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from services.documents.agreement_template_context_service import (
    AgreementVariableCatalog,
    build_agreement_template_context,
)


_EXCLUDED_CATEGORIES = {"Umowa", "Szkolenie", "Wszystkie szkolenia"}
_DECLARATION_VARIABLES = (
    {
        "category": "Zgłoszenie",
        "name": "generated_date",
        "label": "Data wygenerowania deklaracji",
        "type": "date",
        "example": "11.08.2026",
        "description": "Data przygotowania deklaracji.",
    },
    {
        "category": "Zgłoszenie",
        "name": "declaration_date",
        "label": "Data deklaracji",
        "type": "date",
        "example": "11.08.2026",
        "description": "Data złożenia lub wygenerowania deklaracji.",
    },
    {
        "category": "Zgłoszenie",
        "name": "declaration_filename",
        "label": "Nazwa pliku deklaracji",
        "type": "text",
        "example": "Jan_Kowalski-deklaracja.pdf",
        "description": "Nazwa wynikowego pliku PDF.",
    },
)


class DeclarationVariableCatalog:
    """Variables shared by declaration builder, previews and production PDFs."""

    @classmethod
    def variables(cls, fields: Iterable[Any] = ()) -> list[dict[str, str]]:
        fields = tuple(fields)
        variables = [
            dict(item)
            for item in AgreementVariableCatalog.variables(fields)
            if item["category"] not in _EXCLUDED_CATEGORIES
        ]
        by_name = {item["name"]: item for item in variables}
        for item in _DECLARATION_VARIABLES:
            by_name[item["name"]] = {**item, "placeholder": "{{ " + item["name"] + " }}"}
        for field in fields:
            name = _field_value(field, "name")
            if not name:
                continue
            label = _field_value(field, "label") or name
            field_type = (_field_value(field, "field_type") or _field_value(field, "type")).casefold()
            by_name[f"{name}_display"] = {
                "category": "Pola formularza",
                "name": f"{name}_display",
                "label": f"{label} — wartość do dokumentu",
                "type": "text",
                "example": _field_example(by_name.get(name)),
                "description": "Czytelna wartość pola; listy są łączone, a pola logiczne mają wartość Tak/Nie.",
                "placeholder": "{{ " + name + "_display }}",
            }
            if field_type in {"checkbox", "boolean", "bool"}:
                by_name[f"{name}_yes_no"] = {
                    "category": "Pola formularza",
                    "name": f"{name}_yes_no",
                    "label": f"{label} — Tak/Nie",
                    "type": "text",
                    "example": "Tak",
                    "description": "Jednoznaczna reprezentacja logiczna Tak/Nie.",
                    "placeholder": "{{ " + name + "_yes_no }}",
                }
        return list(by_name.values())

    @classmethod
    def context_names(cls, fields: Iterable[Any] = ()) -> set[str]:
        return {item["name"] for item in cls.variables(fields)} | {
            "submission",
            "form_definition",
            "submission_view",
            "consents_view",
            "pdf_image_url",
            "pdf_image_alt",
            "pdf_image_alignment",
            "pdf_image_width",
            "pdf_image_is_logo",
            "document_type",
            "generated_at",
        }


def declaration_variable_catalog(fields: Iterable[Any] = ()) -> list[dict[str, str]]:
    return DeclarationVariableCatalog.variables(fields)


def build_declaration_render_context(
    context: Mapping[str, Any],
    *,
    form_definition: Mapping[str, Any] | None = None,
    fields: Iterable[Any] = (),
) -> dict[str, Any]:
    fields = tuple(fields) or tuple((form_definition or {}).get("fields") or ())
    result = build_agreement_template_context(
        context,
        form_definition=form_definition,
        fields=fields,
    )
    source = _flatten_values(result)
    generated_date = result.get("generated_date") or date.today().strftime("%d.%m.%Y")
    result.update({
        "generated_date": generated_date,
        "declaration_date": result.get("declaration_date") or generated_date,
        "declaration_filename": str(result.get("declaration_filename") or ""),
        "document_type": "declaration",
        "selected_trainings": [],
        "selected_trainings_normalized": [],
    })
    for field in fields:
        name = _field_value(field, "name")
        if not name:
            continue
        value = source.get(name, result.get(name, ""))
        field_type = _field_value(field, "field_type") or _field_value(field, "type")
        result[name] = value
        result[f"{name}_display"] = _display_value(value, field_type)
        if field_type.casefold() in {"checkbox", "boolean", "bool"}:
            result[f"{name}_yes_no"] = "Tak" if _boolean_value(value) else "Nie"
    for variable in DeclarationVariableCatalog.variables(fields):
        result.setdefault(variable["name"], "")
    return result


def declaration_preview_context(
    fields: Iterable[Any] = (),
    *,
    form_definition: Mapping[str, Any] | None = None,
    submission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fields = tuple(fields)
    sample = {item["name"]: item.get("example", "") for item in DeclarationVariableCatalog.variables(fields)}
    sample.update({
        "submission_id": "EXAMPLE-0001",
        "public_submission_id": "EXAMPLE-0001",
        "imiona": "Jan",
        "nazwisko": "Kowalski",
        "email": "jan.kowalski@example.org",
        "telefon": "+48 500 600 700",
        "pesel": "90010112345",
        "ulica": "Przykładowa",
        "nr_budynku": "12",
        "nr_lokalu": "3",
        "kod_pocztowy": "65-001",
        "miejscowosc": "Zielona Góra",
        "wojewodztwo": "lubuskie",
        "generated_date": date.today(),
        "declaration_filename": "Jan_Kowalski-deklaracja.pdf",
        "form_definition": dict(form_definition or {}),
    })
    if submission is not None:
        sample.update(_flatten_values(submission))
    sample["submission"] = dict(sample)
    return build_declaration_render_context(sample, form_definition=form_definition, fields=fields)


def _display_value(value: Any, field_type: str) -> str:
    normalized_type = field_type.strip().casefold()
    if normalized_type in {"checkbox", "boolean", "bool"}:
        return "Tak" if _boolean_value(value) else "Nie"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value or "")


def _boolean_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on", "tak", "x"}


def _flatten_values(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    for container_name in ("row", "submission", "data_json"):
        nested = result.get(container_name)
        if isinstance(nested, Mapping):
            for key, item in nested.items():
                result.setdefault(str(key), item)
            nested_data = nested.get("data_json")
            if isinstance(nested_data, Mapping):
                for key, item in nested_data.items():
                    result.setdefault(str(key), item)
    return result


def _field_value(field: Any, name: str) -> str:
    value = field.get(name) if isinstance(field, Mapping) else getattr(field, name, None)
    return str(value or "").strip()


def _field_example(variable: Mapping[str, Any] | None) -> str:
    return str((variable or {}).get("example") or "Przykładowa wartość")
