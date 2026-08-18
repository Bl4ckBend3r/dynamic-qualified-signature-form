from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from services.documents.agreement_template_context_service import (
    AgreementVariableCatalog,
    build_agreement_template_context,
)
from services.qualification_condition_service import BOOLEAN_FIELD_TYPES, normalize_yes_no_value


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
    def variables(
        cls,
        fields: Iterable[Any] = (),
        *,
        form_definition: Mapping[str, Any] | None = None,
        include_criteria_technical: bool = True,
    ) -> list[dict[str, Any]]:
        fields = tuple(fields)

        definition_fields = tuple(
            (form_definition or {}).get("fields") or ()
        )

        catalog_fields = definition_fields or fields

        variables = [
            dict(item)
            for item in AgreementVariableCatalog.variables(catalog_fields)
            if item["category"] not in _EXCLUDED_CATEGORIES
        ]

        by_name = {
            item["name"]: item
            for item in variables
        }

        for item in _DECLARATION_VARIABLES:
            by_name[item["name"]] = {
                **item,
                "placeholder": "{{ " + item["name"] + " }}",
            }

        for field in catalog_fields:
            name = _field_value(field, "name")

            if not name:
                continue

            label = _field_value(field, "label") or name

            field_type = (
                _field_value(field, "field_type")
                or _field_value(field, "type")
            ).casefold()

            if field_type == "repeatable_group":
                continue

            by_name[f"{name}_display"] = {
                "category": "Pola formularza",
                "name": f"{name}_display",
                "label": f"{label} — wartość do dokumentu",
                "type": "text",
                "example": _field_example(by_name.get(name)),
                "description": (
                    "Czytelna wartość pola; listy są łączone, "
                    "a pola logiczne mają wartość Tak/Nie."
                ),
                "placeholder": "{{ " + name + "_display }}",
            }

            if field_type in {"checkbox", "boolean", "bool"}:
                by_name[f"{name}_yes_no"] = {
                    "category": "Pola formularza",
                    "name": f"{name}_yes_no",
                    "label": f"{label} — Tak/Nie",
                    "type": "text",
                    "example": "Tak",
                    "description": (
                        "Jednoznaczna reprezentacja logiczna Tak/Nie."
                    ),
                    "placeholder": "{{ " + name + "_yes_no }}",
                }

        if include_criteria_technical:
            for criterion in declaration_criteria_catalog(
                form_definition,
                fields,
            ):
                prefix = criterion["variable_prefix"]

                technical = (
                    ("label", "Etykieta", criterion["label"]),
                    (
                        "answer",
                        "Odpowiedź",
                        criterion.get("example")
                        or "Przykładowa odpowiedź",
                    ),
                    ("yes_checked", "Znacznik TAK", "X"),
                    ("no_checked", "Znacznik NIE", ""),
                    ("other_enabled", "Kolumna innej odpowiedzi", ""),
                    ("other_checked", "Znacznik innej odpowiedzi", ""),
                    ("other_label", "Inna odpowiedź", ""),
                )

                for suffix, label_suffix, example in technical:
                    variable_name = f"{prefix}_{suffix}"

                    by_name[variable_name] = {
                        "category": (
                            "Kryteria kwalifikacyjne — techniczne"
                        ),
                        "name": variable_name,
                        "label": (
                            f"{criterion['label']} — {label_suffix}"
                        ),
                        "type": "text",
                        "example": example,
                        "description": (
                            "Techniczna zmienna tabeli kryteriów "
                            "do szablonów DOCX i HTML."
                        ),
                        "placeholder": (
                            "{{ " + variable_name + " }}"
                        ),
                    }

        return list(by_name.values())

    @classmethod
    def context_names(
        cls,
        fields: Iterable[Any] = (),
        *,
        form_definition: Mapping[str, Any] | None = None,
    ) -> set[str]:
        return {item["name"] for item in cls.variables(fields, form_definition=form_definition)} | {
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


def declaration_variable_catalog(
    fields: Iterable[Any] = (),
    *,
    form_definition: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return DeclarationVariableCatalog.variables(fields, form_definition=form_definition)


def declaration_builder_variable_catalog(
    fields: Iterable[Any] = (),
    *,
    form_definition: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    variables = DeclarationVariableCatalog.variables(
        fields,
        form_definition=form_definition,
        include_criteria_technical=False,
    )
    return [*variables, *declaration_criteria_catalog(form_definition, fields)]


def declaration_criteria_catalog(
    form_definition: Mapping[str, Any] | None,
    fields: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Build semantic document criteria from the existing qualification config."""
    definition = dict(form_definition or {})
    field_definitions: dict[str, dict[str, Any]] = {}
    for raw_field in definition.get("fields") or []:
        if isinstance(raw_field, Mapping) and str(raw_field.get("name") or raw_field.get("id") or "").strip():
            name = str(raw_field.get("name") or raw_field.get("id") or "").strip()
            field_definitions[name] = dict(raw_field)
    for field in fields:
        name = _field_value(field, "name")
        if not name:
            continue
        merged = dict(field_definitions.get(name) or {})
        for key in ("label", "type", "field_type", "options"):
            raw = _field_raw(field, key)
            if raw not in (None, "", []):
                merged[key] = raw
        merged["name"] = name
        field_definitions[name] = merged

    qualification = definition.get("qualification_conditions") or {}
    document_labels = definition.get("document_field_labels") or {}
    conditions = qualification.get("conditions") if isinstance(qualification, Mapping) else []
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for condition in conditions if isinstance(conditions, list) else []:
        if not isinstance(condition, Mapping) or condition.get("is_active", True) is False:
            continue
        field_key = str(condition.get("field_name") or "").strip()
        if not field_key or field_key in used:
            continue
        used.add(field_key)
        field = field_definitions.get(field_key, {})
        label = str(
            field.get("document_label")
            or (document_labels.get(field_key) if isinstance(document_labels, Mapping) else "")
            or field.get("label")
            or condition.get("field_label")
            or field_key
        ).strip()
        field_type = str(field.get("field_type") or field.get("type") or "text").strip().casefold()
        options = _field_options(field)
        option_states = [normalize_yes_no_value(option["value"]) for option in options]
        has_other = any(state is None for state in option_states)
        answer_type = "yes_no" if field_type in BOOLEAN_FIELD_TYPES or (options and not has_other) else "choice" if options else "text"
        result.append({
            "category": "Kryteria kwalifikacyjne",
            "name": field_key,
            "field_key": field_key,
            "variable_prefix": _criterion_variable_prefix(field_key),
            "label": label,
            "type": "criterion",
            "answer_type": answer_type,
            "options": options,
            "example": _criterion_example(field, answer_type),
            "description": "Kryterium z konfiguracji warunków kwalifikacyjnych formularza.",
            "placeholder": "",
        })
    return result


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
            normalized = normalize_yes_no_value(value)
            result[f"{name}_yes_no"] = "Tak" if normalized is True else "Nie" if normalized is False else ""
    for criterion in declaration_criteria_catalog(form_definition, fields):
        field_key = criterion["field_key"]
        prefix = criterion["variable_prefix"]
        value = source.get(field_key, result.get(field_key, ""))
        normalized = normalize_yes_no_value(value) if criterion["answer_type"] != "text" else None
        answer = _criterion_display_value(value, criterion)
        missing = _is_empty_value(value)
        result.update({
            f"{prefix}_label": criterion["label"],
            f"{prefix}_answer": answer,
            f"{prefix}_yes_checked": "X" if normalized is True else "",
            f"{prefix}_no_checked": "X" if normalized is False else "",
            f"{prefix}_other_enabled": "X" if criterion["answer_type"] != "yes_no" else "",
            f"{prefix}_other_checked": "X" if not missing and normalized is None else "",
            f"{prefix}_other_label": answer if not missing and normalized is None else "",
        })
    for variable in DeclarationVariableCatalog.variables(fields, form_definition=form_definition):
        result.setdefault(variable["name"], "")
    return result


def declaration_preview_context(
    fields: Iterable[Any] = (),
    *,
    form_definition: Mapping[str, Any] | None = None,
    submission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fields = tuple(fields)
    preview_fields = tuple(
            (form_definition or {}).get("fields") or fields
        )
    sample = {
        item["name"]: item.get("example", "")
        for item in DeclarationVariableCatalog.variables(
            preview_fields,
            form_definition=form_definition
        )
    }
    for criterion in declaration_criteria_catalog(form_definition, fields):
        sample[criterion["field_key"]] = criterion.get("example", "")
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
    for field in preview_fields:
        field_type = (
            _field_value(field, "field_type")
            or _field_value(field, "type")
        ).casefold()

        if field_type != "repeatable_group":
            continue

        group_name = _field_value(field, "name")

        if not group_name:
            continue

        # Jeżeli rzeczywiste zgłoszenie zawiera rekordy grupy,
        # pozostawiamy je bez zmian.
        current_value = sample.get(group_name)

        if isinstance(current_value, str):
            try:
                import json
                parsed_value = json.loads(current_value)
            except (TypeError, ValueError):
                parsed_value = []
        else:
            parsed_value = current_value

        if isinstance(parsed_value, list) and parsed_value:
            continue

        # Pola zagnieżdżone mogą znajdować się bezpośrednio
        # w definicji albo w config/config_json.
        child_fields = _field_raw(field, "fields")

        if not child_fields:
            config = _field_raw(field, "config")

            if not isinstance(config, Mapping):
                config = _field_raw(field, "config_json")

            if isinstance(config, Mapping):
                child_fields = config.get("fields")

        if not isinstance(child_fields, (list, tuple)):
            child_fields = []

        record_1 = {"id": "preview-repeatable-1"}
        record_2 = {"id": "preview-repeatable-2"}

        for child in child_fields:
            if not isinstance(child, Mapping):
                continue

            child_name = str(child.get("name") or "").strip()

            if not child_name:
                continue

            child_label = str(
                child.get("label")
                or child_name
            ).strip()

            child_type = str(
                child.get("type")
                or child.get("field_type")
                or "text"
            ).casefold()

            if child_type == "date":
                value_1 = "18.08.2026"
                value_2 = "19.08.2026"

            elif child_type == "time":
                value_1 = "08:30"
                value_2 = "10:15"

            elif child_type == "email":
                value_1 = "jan.kowalski@example.org"
                value_2 = "anna.nowak@example.org"

            elif child_type == "number":
                value_1 = "1"
                value_2 = "2"

            elif child_type == "checkbox":
                value_1 = ["Opcja 1"]
                value_2 = ["Opcja 2"]

            else:
                value_1 = f"{child_label} 1"
                value_2 = f"{child_label} 2"

            record_1[child_name] = value_1
            record_2[child_name] = value_2

        sample[group_name] = [
            record_1,
            record_2,
    ]
    sample["submission"] = dict(sample)
    return build_declaration_render_context(
        sample,
        form_definition=form_definition,
        fields=preview_fields,
    )

def _display_value(value: Any, field_type: str) -> str:
    normalized_type = field_type.strip().casefold()
    if normalized_type in {"checkbox", "boolean", "bool"}:
        normalized = normalize_yes_no_value(value)
        return "Tak" if normalized is True else "Nie" if normalized is False else ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value or "")


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


def _field_raw(field: Any, name: str) -> Any:
    return field.get(name) if isinstance(field, Mapping) else getattr(field, name, None)


def _field_options(field: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_options = field.get("options") or field.get("choices") or []
    if isinstance(raw_options, Mapping):
        raw_options = list(raw_options.items())
    result = []
    for option in raw_options if isinstance(raw_options, (list, tuple)) else []:
        if isinstance(option, Mapping):
            value = option.get("value", option.get("id", option.get("name", option.get("label", ""))))
            label = option.get("label", option.get("name", value))
        elif isinstance(option, tuple) and len(option) == 2:
            value, label = option
        else:
            value = label = option
        value_text = str(value or "").strip()
        label_text = str(label or value_text).strip()
        if value_text:
            result.append({"value": value_text, "label": label_text})
    return result


def _criterion_variable_prefix(field_key: str) -> str:
    normalized = "".join(character if character.isascii() and (character.isalnum() or character == "_") else "_" for character in field_key)
    normalized = normalized.strip("_") or "field"
    if normalized[0].isdigit():
        normalized = "field_" + normalized
    return "criterion_" + normalized


def _criterion_example(field: Mapping[str, Any], answer_type: str) -> Any:
    if answer_type == "yes_no":
        return True
    options = _field_options(field)
    return options[0]["value"] if options else "Przykładowa odpowiedź"


def _criterion_display_value(value: Any, criterion: Mapping[str, Any]) -> str:
    options = {str(item.get("value") or ""): str(item.get("label") or item.get("value") or "") for item in criterion.get("options") or []}
    if isinstance(value, (list, tuple, set)):
        return ", ".join(options.get(str(item), str(item)) for item in value)
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    raw = str(value or "").strip()
    if raw in options:
        return options[raw]
    normalized = normalize_yes_no_value(value)
    if normalized is not None and criterion.get("answer_type") == "yes_no":
        return "Tak" if normalized else "Nie"
    return raw


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _field_example(variable: Mapping[str, Any] | None) -> str:
    return str((variable or {}).get("example") or "Przykładowa wartość")
