from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from models import Form, FormField
from services.field_availability_service import FieldAvailabilityService


LAYOUT_WIDTHS = {
    "quarter": 3,
    "third": 4,
    "half": 6,
    "two-thirds": 8,
    "three-quarters": 9,
    "full": 12,
}
WIDTH_BY_SPAN = {span: name for name, span in LAYOUT_WIDTHS.items()}


class FormBuilderError(ValueError):
    pass


@dataclass(frozen=True)
class FormBuilderResult:
    fields: list[FormField]
    document_labels: dict[str, str]


def width_name(value: Any) -> str:
    if isinstance(value, int) or str(value or "").isdigit():
        return WIDTH_BY_SPAN.get(int(value), "full")
    value = str(value or "full").strip()
    return value if value in LAYOUT_WIDTHS else "full"


def serialize_builder_fields(form: Form, fields: Iterable[FormField]) -> list[dict]:
    definition = deepcopy(form.definition_json or {})
    configs = {
        str(item.get("name")): item
        for item in definition.get("fields") or []
        if isinstance(item, dict) and item.get("name")
    }
    document_labels = definition.get("document_field_labels") or {}
    result = []
    for field in fields:
        config = configs.get(field.name, {})
        result.append(
            {
                "id": field.id,
                "name": field.name,
                "label": field.label or field.name,
                "document_label": document_labels.get(field.name, config.get("document_label", "")),
                "type": field.type or "text",
                "required": bool(field.required),
                "width": width_name(config.get("width")),
                "width_span": LAYOUT_WIDTHS[width_name(config.get("width"))],
                "placeholder": str(config.get("placeholder") or ""),
                "section": field.section or "",
                "options": deepcopy(field.options or []),
            }
        )
    return result


def apply_builder_state(
    db,
    form: Form,
    state: Any,
    *,
    field_types: set[str],
    availability_definition: dict,
) -> FormBuilderResult:
    if not isinstance(state, list):
        raise FormBuilderError("Nieprawidłowy format danych edytora.")
    if len(state) > 250:
        raise FormBuilderError("Formularz może zawierać maksymalnie 250 pól.")

    all_fields = {field.id: field for field in form.fields if field.id is not None}
    fields_by_name = {field.name: field for field in form.fields}
    original_definition = deepcopy(form.definition_json or {})
    original_configs = {
        str(item.get("name")): deepcopy(item)
        for item in original_definition.get("fields") or []
        if isinstance(item, dict) and item.get("name")
    }
    availability_service = FieldAvailabilityService()
    initial_step = availability_service.initial_step(availability_definition)
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    saved_fields: list[FormField] = []
    saved_configs: list[dict] = []
    document_labels: dict[str, str] = {}

    for order, item in enumerate(state):
        if not isinstance(item, dict):
            raise FormBuilderError("Każde pole edytora musi być obiektem.")
        field_id = item.get("id")
        name = str(item.get("name") or "").strip()
        label = str(item.get("label") or "").strip()
        field_type = str(item.get("type") or "text").strip()
        if not name or not name.replace("_", "").isalnum():
            raise FormBuilderError("Nazwa pola może zawierać tylko litery, cyfry i podkreślenia.")
        if name in seen_names:
            raise FormBuilderError(f"Nazwa pola „{name}” występuje więcej niż raz.")
        if field_type not in field_types:
            raise FormBuilderError(f"Nieobsługiwany typ pola: {field_type}.")

        field = None
        if field_id not in (None, ""):
            try:
                parsed_id = int(field_id)
            except (TypeError, ValueError) as exc:
                raise FormBuilderError("Nieprawidłowy identyfikator pola.") from exc
            field = all_fields.get(parsed_id)
            if field is None or field.form_id != form.id or parsed_id in seen_ids:
                raise FormBuilderError("Pole nie należy do edytowanego formularza.")
            if field.name != name:
                raise FormBuilderError("Nazwy istniejącego pola nie można zmienić.")
            seen_ids.add(parsed_id)
        else:
            field = fields_by_name.get(name)
            if field and field.active:
                raise FormBuilderError(f"Pole „{name}” już istnieje.")
            if field is None:
                field = FormField(form_id=form.id, name=name)
                db.add(field)

        seen_names.add(name)
        field.active = True
        field.label = label or name
        field.type = field_type
        field.section = str(item.get("section") or "").strip()
        field.sort_order = order
        field.options = _normalize_options(field_type, item.get("options"))

        availability = deepcopy(field.availability_json or [])
        if not availability:
            availability = [
                {"step": initial_step, "visible": True, "editable": True, "required": False}
            ]
        initial_permission = next((entry for entry in availability if entry.get("step") == initial_step), None)
        if initial_permission is None:
            initial_permission = {"step": initial_step, "visible": True, "editable": True, "required": False}
            availability.insert(0, initial_permission)
        initial_permission["required"] = bool(
            item.get("required") and initial_permission.get("visible") and initial_permission.get("editable")
        )
        field.availability_json = availability
        field.required = bool(initial_permission["required"])
        field.stage = next(
            (str(entry.get("step")) for entry in availability if entry.get("visible")), initial_step
        )

        config = original_configs.get(name, {"name": name})
        config.update(
            {
                "name": name,
                "label": field.label,
                "type": field_type,
                "required": field.required,
                "options": deepcopy(field.options),
                "width": width_name(item.get("width")),
                "placeholder": str(item.get("placeholder") or "").strip(),
                "stage": field.stage,
                "availability": deepcopy(availability),
            }
        )
        saved_fields.append(field)
        saved_configs.append(config)
        document_label = str(item.get("document_label") or "").strip()
        if document_label:
            document_labels[name] = document_label
            config["document_label"] = document_label
        else:
            config.pop("document_label", None)

    for field in form.fields:
        if field not in saved_fields:
            field.active = False

    original_definition["fields"] = saved_configs
    if document_labels:
        original_definition["document_field_labels"] = document_labels
    else:
        original_definition.pop("document_field_labels", None)
    form.definition_json = original_definition
    db.flush()
    return FormBuilderResult(saved_fields, document_labels)


def _normalize_options(field_type: str, value: Any) -> list:
    if field_type not in {"select", "radio", "checkbox"}:
        return []
    if not isinstance(value, list):
        return []
    result = []
    for option in value:
        if isinstance(option, dict):
            option_value = str(option.get("value") or option.get("label") or "").strip()
            label = str(option.get("label") or option_value).strip()
        else:
            raw_option = str(option or "").strip()
            if field_type == "checkbox":
                option_value, _, label = raw_option.partition("|")
                option_value = option_value.strip()
                label = label.strip() or option_value
            else:
                option_value = label = raw_option
        if not option_value:
            continue
        result.append({"value": option_value, "label": label} if field_type == "checkbox" else option_value)
    return result
