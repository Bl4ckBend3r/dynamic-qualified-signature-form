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


def _normalize_nested_builder_fields(
    value: Any,
    *,
    field_types: set[str] | None = None,
) -> list[dict]:
    if not isinstance(value, list):
        return []

    allowed_types = (
        set(field_types)
        if field_types is not None
        else {
            "text",
            "textarea",
            "email",
            "tel",
            "number",
            "date",
            "time",
            "select",
            "radio",
            "checkbox",
            "pesel",
            "file",
        }
    )

    # P1: zakaz repeatable_group wewnątrz repeatable_group.
    allowed_types.discard("repeatable_group")

    result: list[dict] = []
    seen_names: set[str] = set()

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise FormBuilderError(
                f"Pole nr {index + 1} w grupie musi być obiektem."
            )

        name = str(item.get("name") or "").strip()
        label = str(item.get("label") or "").strip()
        field_type = str(item.get("type") or "text").strip()

        if not name or not name.replace("_", "").isalnum():
            raise FormBuilderError(
                "Nazwa pola w grupie może zawierać tylko "
                "litery, cyfry i podkreślenia."
            )

        if name in seen_names:
            raise FormBuilderError(
                f"Nazwa pola „{name}” występuje w grupie więcej niż raz."
            )

        if field_type not in allowed_types:
            raise FormBuilderError(
                f"Nieobsługiwany typ pola w grupie: {field_type}."
            )

        seen_names.add(name)

        config = deepcopy(item)

        # Techniczny klucz frontendu nie trafia do definition_json.
        config.pop("_key", None)
        config.pop("id", None)

        config["name"] = name
        config["label"] = label or name
        config["type"] = field_type
        config["required"] = bool(item.get("required"))
        config["width"] = width_name(item.get("width"))
        config["placeholder"] = str(
            item.get("placeholder") or ""
        ).strip()

        config["options"] = _normalize_options(
            field_type,
            item.get("options"),
        )

        result.append(config)

    return result

def serialize_builder_fields(
    form: Form,
    fields: Iterable[FormField],
) -> list[dict]:
    definition = deepcopy(form.definition_json or {})

    configs = {
        str(item.get("name")): item
        for item in definition.get("fields") or []
        if isinstance(item, dict) and item.get("name")
    }

    document_labels = (
        definition.get("document_field_labels") or {}
    )

    result = []

    for field in fields:
        config = deepcopy(
            configs.get(field.name, {})
        )

        item = {
            "id": field.id,
            "name": field.name,
            "label": field.label or field.name,
            "document_label": document_labels.get(
                field.name,
                config.get("document_label", ""),
            ),
            "type": field.type or "text",
            "required": bool(field.required),
            "availability": deepcopy(
                field.availability_json
                or config.get("availability")
                or []
            ),
            "document_usage": deepcopy(
                config.get("document_usage")
                or {}
            ),
            "width": width_name(
                config.get("width")
            ),
            "width_span": LAYOUT_WIDTHS[
                width_name(config.get("width"))
            ],
            "placeholder": str(
                config.get("placeholder") or ""
            ),
            "section": field.section or "",
            "data_classification": (
                field.data_classification
                or config.get(
                    "data_classification",
                    "normal",
                )
            ),
            "options": deepcopy(
                field.options or []
            ),
        }

        if field.type == "repeatable_group":
            item["min_items"] = int(
                config.get("min_items", 1)
            )
            item["max_items"] = int(
                config.get("max_items", 20)
            )
            item["add_label"] = str(
                config.get("add_label")
                or "Dodaj"
            )
            item["item_label"] = str(
                config.get("item_label")
                or "Element"
            )

            # Najważniejsze:
            item["fields"] = deepcopy(
                config.get("fields") or []
            )

        result.append(item)

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
    workflow_step_ids = availability_service.step_ids(availability_definition)
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
        classification = str(item.get("data_classification") or "normal").strip()
        if classification not in {"normal", "personal", "sensitive"}:
            raise FormBuilderError("Nieprawidłowa klasyfikacja danych pola.")
        field.data_classification = classification
        field.sort_order = order
        field.options = _normalize_options(field_type, item.get("options"))

        raw_availability = item.get("availability")
        if isinstance(raw_availability, list):
            supplied_steps = {
                str(entry.get("step") or "").strip()
                for entry in raw_availability
                if isinstance(entry, dict)
            }
            unknown_steps = supplied_steps - set(workflow_step_ids)
            if unknown_steps:
                raise FormBuilderError(
                    "Dostępność pola odwołuje się do nieistniejącego etapu: "
                    + ", ".join(sorted(unknown_steps))
                    + "."
                )
            normalized_by_step = {
                str(entry.get("step")): entry
                for entry in availability_service.normalize_field(
                    {"availability": raw_availability}, availability_definition
                )["availability"]
            }
            availability = [
                {
                    "step": step_id,
                    "visible": bool(normalized_by_step.get(step_id, {}).get("visible")),
                    "editable": bool(normalized_by_step.get(step_id, {}).get("editable")),
                    "required": bool(normalized_by_step.get(step_id, {}).get("required")),
                }
                for step_id in workflow_step_ids
            ]
        else:
            availability = deepcopy(field.availability_json or [])
            if not availability:
                availability = [
                    {"step": initial_step, "visible": True, "editable": True, "required": bool(item.get("required"))}
                ]
            initial_permission = next(
                (entry for entry in availability if entry.get("step") == initial_step),
                None,
            )
            if initial_permission is None:
                initial_permission = {
                    "step": initial_step,
                    "visible": True,
                    "editable": True,
                    "required": False,
                }
                availability.insert(0, initial_permission)
            initial_permission["required"] = bool(
                item.get("required")
                and initial_permission.get("visible")
                and initial_permission.get("editable")
            )
        validation_definition = {
            **availability_definition,
            "fields": [{"name": name, "availability": availability}],
        }
        availability_errors = availability_service.validate_config(validation_definition)
        if availability_errors:
            raise FormBuilderError(" ".join(availability_errors))
        initial_permission = next(
            (entry for entry in availability if entry.get("step") == initial_step),
            {"required": False},
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
                "document_usage": {
                    "declaration": bool(
                        (item.get("document_usage") or {}).get("declaration")
                    )
                },
                "data_classification": classification,
            }
        )
        if field_type == "repeatable_group":
            min_items = item.get("min_items", 1)
            max_items = item.get("max_items", 20)

            try:
                min_items = int(min_items)
                max_items = int(max_items)
            except (TypeError, ValueError) as exc:
                raise FormBuilderError(
                    "Minimalna i maksymalna liczba elementów "
                    "grupy muszą być liczbami całkowitymi."
                ) from exc

            if min_items < 0:
                raise FormBuilderError(
                    "Minimalna liczba elementów grupy "
                    "nie może być mniejsza od 0."
                )

            if max_items < min_items:
                raise FormBuilderError(
                    "Maksymalna liczba elementów grupy "
                    "nie może być mniejsza od minimalnej."
                )

            config["min_items"] = min_items
            config["max_items"] = max_items

            config["add_label"] = str(
                item.get("add_label")
                or "Dodaj"
            ).strip()

            config["item_label"] = str(
                item.get("item_label")
                or "Element"
            ).strip()

            config["fields"] = (
                _normalize_nested_builder_fields(
                    item.get("fields"),
                    field_types=field_types,
                )
            )

        else:
            config.pop("fields", None)
            config.pop("min_items", None)
            config.pop("max_items", None)
            config.pop("add_label", None)
            config.pop("item_label", None)
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
