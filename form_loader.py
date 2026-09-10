import json
import re
import unicodedata
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from services.training_service import format_admin_value, parse_training_snapshots


SUPPORTED_FIELD_TYPES = {
    "text",
    "textarea",
    "email",
    "number",
    "date",
    "select",
    "radio",
    "checkbox",
    "tel",
    "phone",
    "pesel",
    "section",
    "static_text",
    "training_selection",
    "repeatable_group",
    "file",
    "attachment",
    "time"
}
SUPPORTED_FIELD_WIDTHS = {"quarter", "third", "half", "two-thirds", "three-quarters", "full"}
LEGACY_FIELD_WIDTHS = {3: "quarter", 4: "third", 6: "half", 8: "two-thirds", 9: "three-quarters", 12: "full"}
NON_INPUT_FIELD_TYPES = {"section", "static_text"}


def field_is_user_input(field: Dict[str, Any]) -> bool:
    return (
        field.get("type") not in NON_INPUT_FIELD_TYPES
        and not bool(field.get("hidden"))
        and not bool(field.get("system"))
        and not bool(field.get("technical"))
    )

FIELD_STAGE_INITIAL = "initial_submission"
FIELD_STAGE_AFTER_ACCEPTANCE = "after_officer_acceptance"
SUPPORTED_FIELD_STAGES = {FIELD_STAGE_INITIAL, FIELD_STAGE_AFTER_ACCEPTANCE}

ALLOWED_SIGNATURE_MODES = {
    "none",
    "qualified",
    "trusted_profile",
    "optional",
}

DEFAULT_SIGNATURE_CONFIG = {
    "mode": "none",
    "allow_trusted_profile": False,
    "allow_qualified_signature": False,
    "require_before_submit": False,
    "show_user_choice": False,
}

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TEL_REGEX = re.compile(r"^(?:\+48\s?)?\d{3}\s?\d{3}\s?\d{3}$")
PESEL_REGEX = re.compile(r"^\d{11}$")
PESEL_ERROR_MESSAGE = "Podany numer PESEL jest nieprawidłowy."

PESEL_ALIASES = {"pesel"}
BIRTH_DATE_ALIASES = {"data_urodzenia", "birth_date", "date_of_birth"}
GENDER_ALIASES = {"plec", "płeć", "gender"}
AGE_ALIASES = {"wiek", "age"}
PROJECT_JOIN_DATE_ALIASES = {
    "data_przystapienia",
    "data_przystąpienia",
    "project_join_date",
    "joining_date",
    "start_date",
    "data_rozpoczecia",
}


def build_consents_view(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    from services.training_service import without_training_selection_section

    consents_view: List[Dict[str, Any]] = []

    for field in without_training_selection_section(form_definition.get("fields", [])):
        if field.get("type") != "checkbox":
            continue

        field_name = field.get("name")
        if not field_name:
            continue

        if not evaluate_visible_if(field.get("visible_if"), submission_data):
            continue

        accepted = submission_data.get(field_name, "Nie") == "Tak"

        options = field.get("options", [])
        option_label = ""
        if options and isinstance(options, list):
            first_option = options[0] or {}
            option_label = first_option.get("label", "")

        consent_text = (
            field.get("pdf_text")
            or option_label
            or field.get("label", "")
        )

        consents_view.append(
            {
                "name": field_name,
                "title": field.get("label", ""),
                "text": consent_text,
                "accepted": accepted,
                "accepted_label": "Tak" if accepted else "Nie",
                "required": bool(field.get("required", False)),
            }
        )

    return consents_view


def load_form_definition(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    validate_form_definition(data)
    return normalize_form_definition(data)

def _validate_field_definition(
    field: Dict[str, Any],
    *,
    inside_repeatable_group: bool = False,
) -> None:
    field_type = field.get("type")

    if field_type not in SUPPORTED_FIELD_TYPES:
        raise ValueError(f"Nieobsługiwany typ pola: {field_type}")

    if field_type not in {"section", "static_text"} and not field.get("name"):
        raise ValueError(
            f"Pole typu '{field_type}' musi zawierać 'name'."
        )

    if field_type in {"select", "radio"}:
        if not isinstance(field.get("options"), list):
            raise ValueError(
                f"Pole '{field.get('name')}' musi zawierać listę 'options'."
            )

    if field_type != "repeatable_group":
        return

    # W P1 nie obsługujemy repeatable_group wewnątrz repeatable_group.
    if inside_repeatable_group:
        raise ValueError(
            "Pole 'repeatable_group' nie może być zagnieżdżone "
            "wewnątrz innego 'repeatable_group'."
        )

    group_name = str(field.get("name") or "").strip()

    nested_fields = field.get("fields")

    if not isinstance(nested_fields, list):
        raise ValueError(
            f"Grupa '{group_name}' musi zawierać listę 'fields'."
        )

    min_items = field.get("min_items", 1)
    max_items = field.get("max_items", 20)

    # bool jest podtypem int w Pythonie, dlatego sprawdzamy go osobno.
    if (
        not isinstance(min_items, int)
        or isinstance(min_items, bool)
        or min_items < 0
    ):
        raise ValueError(
            f"Grupa '{group_name}' musi mieć 'min_items' "
            "będące liczbą całkowitą >= 0."
        )

    if (
        not isinstance(max_items, int)
        or isinstance(max_items, bool)
        or max_items < 0
    ):
        raise ValueError(
            f"Grupa '{group_name}' musi mieć 'max_items' "
            "będące liczbą całkowitą >= 0."
        )

    if max_items < min_items:
        raise ValueError(
            f"Grupa '{group_name}' musi mieć max_items >= min_items."
        )

    nested_names: set[str] = set()

    for index, nested_field in enumerate(nested_fields, start=1):
        if not isinstance(nested_field, dict):
            raise ValueError(
                f"Pole nr {index} w grupie '{group_name}' "
                "musi być obiektem."
            )

        _validate_field_definition(
            nested_field,
            inside_repeatable_group=True,
        )

        nested_name = str(
            nested_field.get("name") or ""
        ).strip()

        if not nested_name:
            continue

        if nested_name in nested_names:
            raise ValueError(
                f"Grupa '{group_name}' zawiera zduplikowaną "
                f"nazwę pola: '{nested_name}'."
            )
        nested_names.add(nested_name)

    contact_field = str(field.get("decision_contact_email_field") or "").strip()
    if contact_field:
        matching = next((item for item in nested_fields if str(item.get("name") or "") == contact_field), None)
        if not matching or matching.get("type") != "email":
            raise ValueError(
                f"Grupa '{group_name}' wskazuje pole kontaktowe '{contact_field}', które nie istnieje albo nie jest typu email."
            )

def validate_form_definition(form_definition: Dict[str, Any]) -> None:
    if "title" not in form_definition:
        raise ValueError("Brak pola 'title' w definicji formularza.")
    if "fields" not in form_definition or not isinstance(form_definition["fields"], list):
        raise ValueError("Brak listy 'fields' w definicji formularza.")

    signature = form_definition.get("signature")
    if signature is not None:
        if not isinstance(signature, dict):
            raise ValueError("Pole 'signature' musi być obiektem.")

        mode = signature.get("mode", "none")
        if mode not in ALLOWED_SIGNATURE_MODES:
            raise ValueError(
                f"Nieobsługiwany tryb podpisu: {mode}. "
                f"Dozwolone: {', '.join(sorted(ALLOWED_SIGNATURE_MODES))}"
            )

        if mode == "optional":
            allow_trusted_profile = bool(signature.get("allow_trusted_profile", False))
            allow_qualified_signature = bool(signature.get("allow_qualified_signature", False))

            if not allow_trusted_profile and not allow_qualified_signature:
                raise ValueError(
                    "Dla trybu podpisu 'optional' co najmniej jedna metoda podpisu musi być dozwolona."
                )

    seen_names: set[str] = set()

    for index, field in enumerate(
        form_definition["fields"],
        start=1,
    ):
        if not isinstance(field, dict):
            raise ValueError(
                f"Pole nr {index} musi być obiektem."
            )

        field_name = str(
            field.get("name") or ""
        ).strip()

        if field_name:
            if field_name in seen_names:
                raise ValueError(
                    f"Duplikat pola 'name': '{field_name}'. "
                    "Każde pole musi mieć unikalną nazwę."
                )

            seen_names.add(field_name)

        _validate_field_definition(field)


def normalize_signature_config(form_definition: Dict[str, Any]) -> Dict[str, Any]:
    signature = deepcopy(form_definition.get("signature") or {})
    normalized_signature = {**DEFAULT_SIGNATURE_CONFIG, **signature}

    mode = normalized_signature["mode"]
    if mode not in ALLOWED_SIGNATURE_MODES:
        mode = "none"
        normalized_signature["mode"] = mode

    if mode == "none":
        normalized_signature["allow_trusted_profile"] = False
        normalized_signature["allow_qualified_signature"] = False
        normalized_signature["require_before_submit"] = False
        normalized_signature["show_user_choice"] = False

    elif mode == "qualified":
        normalized_signature["allow_trusted_profile"] = False
        normalized_signature["allow_qualified_signature"] = True
        normalized_signature["show_user_choice"] = False

    elif mode == "trusted_profile":
        normalized_signature["allow_trusted_profile"] = True
        normalized_signature["allow_qualified_signature"] = False
        normalized_signature["show_user_choice"] = False

    elif mode == "optional":
        normalized_signature["show_user_choice"] = (
            normalized_signature["allow_trusted_profile"]
            or normalized_signature["allow_qualified_signature"]
        )

    form_definition["signature"] = normalized_signature
    return form_definition


def _normalize_field_definition(
    field: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = deepcopy(field)

    # Legacy alias.
    if normalized.get("type") == "attachment":
        normalized["type"] = "file"

    if normalized.get("id") and not normalized.get("name"):
        normalized["name"] = normalized["id"]

    normalized.setdefault("label", "")
    normalized.setdefault("placeholder", "")

    if "required" not in normalized:
        normalized["required"] = field_is_user_input(
            normalized
        )
    else:
        normalized["required"] = bool(
            normalized["required"]
        )

    normalized.setdefault("options", [])
    normalized.setdefault("help_text", "")
    normalized.setdefault("default", "")
    normalized.setdefault("validation", {})
    normalized.setdefault("visible_if", None)
    normalized.setdefault("readonly", False)

    # Normalizacja szerokości.
    raw_width = normalized.get("width", "full")

    if isinstance(raw_width, int) or str(
        raw_width
    ).isdigit():
        normalized["width"] = LEGACY_FIELD_WIDTHS.get(
            int(raw_width),
            "full",
        )
    elif raw_width not in SUPPORTED_FIELD_WIDTHS:
        normalized["width"] = "full"
    else:
        normalized["width"] = raw_width

    # Stage.
    if (
        normalized.get("stage")
        not in SUPPORTED_FIELD_STAGES
    ):
        normalized["stage"] = FIELD_STAGE_INITIAL

    # Konfiguracja pliku.
    if normalized.get("type") == "file":
        normalized.setdefault("description", "")
        normalized.setdefault(
            "allowed_extensions",
            ["pdf"],
        )
        normalized.setdefault(
            "allowed_mime_types",
            [],
        )
        normalized.setdefault("max_size_mb", 10)
        normalized.setdefault("max_files", 1)
        normalized.setdefault("required_if", None)
        normalized.setdefault(
            "document_type",
            "submission_attachment",
        )
        normalized.setdefault("category", "")

    # Konfiguracja grupy powtarzalnej.
    if normalized.get("type") == "repeatable_group":
        normalized.setdefault("min_items", 1)
        normalized.setdefault("max_items", 20)
        normalized.setdefault(
            "add_label",
            "Dodaj",
        )
        normalized.setdefault(
            "item_label",
            "Element",
        )

        normalized["fields"] = [
            _normalize_field_definition(child)
            for child in normalized.get(
                "fields",
                [],
            )
            if isinstance(child, dict)
        ]

    return normalized


def normalize_form_definition(
    form_definition: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = deepcopy(form_definition)

    normalized.setdefault("description", "")
    normalized.setdefault(
        "submit_label",
        "Generuj i wyślij",
    )

    normalized = normalize_signature_config(
        normalized
    )

    # Normalizacja wszystkich pól.
    # _normalize_field_definition() obsługuje również
    # pola wewnątrz repeatable_group.
    normalized["fields"] = [
        _normalize_field_definition(field)
        for field in normalized.get(
            "fields",
            [],
        )
        if isinstance(field, dict)
    ]

    from services.field_availability_service import (
        FieldAvailabilityService,
    )

    availability_service = (
        FieldAvailabilityService()
    )

    normalized["fields"] = [
        availability_service.normalize_field(
            field,
            normalized,
        )
        for field in normalized["fields"]
    ]

    return normalized


def fields_for_stage(form_definition: Dict[str, Any], stage: str) -> List[Dict[str, Any]]:
    from services.field_availability_service import FieldAvailabilityService

    return FieldAvailabilityService().visible_fields(form_definition, stage)


def form_definition_for_stage(form_definition: Dict[str, Any], stage: str) -> Dict[str, Any]:
    from services.training_service import without_training_selection_section

    normalized = normalize_form_definition(form_definition)
    return {
        **normalized,
        "fields": without_training_selection_section(fields_for_stage(normalized, stage)),
    }


def additional_fields_for_acceptance(form_definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    from services.training_service import without_training_selection_section

    return without_training_selection_section(
        fields_for_stage(
            normalize_form_definition(form_definition),
            FIELD_STAGE_AFTER_ACCEPTANCE,
        )
    )


def has_additional_fields_after_acceptance(form_definition: Dict[str, Any]) -> bool:
    return any(
        field.get("type") not in {"section", "static_text"}
        for field in additional_fields_for_acceptance(form_definition)
    )

def _normalize_repeatable_group_records(
    raw_value: Any,
) -> List[Dict[str, Any]]:
    if raw_value in (None, ""):
        return []

    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
    else:
        parsed = raw_value

    if not isinstance(parsed, list):
        return []

    records: List[Dict[str, Any]] = []
    used_ids: set[str] = set()

    for item in parsed:
        if not isinstance(item, dict):
            continue

        record = deepcopy(item)

        raw_id = str(record.get("record_uuid") or record.get("id") or "").strip()
        record_id = ""

        if raw_id:
            try:
                record_id = str(UUID(raw_id))
            except (ValueError, AttributeError, TypeError):
                record_id = ""

        # Brak UUID, niepoprawny UUID albo duplikat:
        # generujemy nową tożsamość rekordu.
        if not record_id or record_id in used_ids:
            record_id = str(uuid4())

            while record_id in used_ids:
                record_id = str(uuid4())

        # ``record_uuid`` is the canonical domain identifier. ``id`` remains
        # a compatibility alias for historical definitions and browser drafts.
        record["record_uuid"] = record_id
        record["id"] = record_id

        used_ids.add(record_id)
        records.append(record)

    return records

def extract_submission_data(form_definition: Dict[str, Any], request_form,) -> Dict[str, Any]:
    data: Dict[str, Any] = {}

    for field in form_definition["fields"]:
        field_type = field.get("type")
        field_name = field.get("name")

        if (
            field_type in {"section", "static_text", "file"}
            or not field_name
            or field.get("readonly")
            or field.get("hidden")
            or field.get("system")
            or field.get("technical")
        ):
            continue

        if field_type == "repeatable_group":
            data[field_name] = _normalize_repeatable_group_records(
                _get(request_form, field_name, [])
            )

        elif field_type == "checkbox":
            data[field_name] = (
                "Tak" if _is_checked(request_form, field_name) else "Nie"
            )

        elif field_type == "training_selection":
            data[field_name] = ",".join(
                _getlist(request_form, field_name)
            )

        else:
            data[field_name] = str(
                _get(request_form, field_name, "") or ""
            ).strip()

    signature = form_definition.get("signature", {})

    if signature.get("show_user_choice"):
        data["signature_method"] = str(
            _get(request_form, "signature_method", "") or ""
        ).strip()

    return data

def _get(request_data, key: str, default: Any = None) -> Any:
    if hasattr(request_data, "get"):
        return request_data.get(key, default)
    return default


def _getlist(request_data, key: str) -> list[str]:
    if hasattr(request_data, "getlist"):
        return [str(item).strip() for item in request_data.getlist(key) if str(item).strip()]
    value = _get(request_data, key, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _is_checked(request_data, key: str) -> bool:
    if hasattr(request_data, "getlist"):
        values = request_data.getlist(key)
        if not values:
            return False
        return any(str(value).strip().lower() not in {"", "0", "false", "nie", "no", "off"} for value in values)
    if key not in request_data:
        return False
    value = _get(request_data, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return any(str(item).strip().lower() in {"1", "true", "tak", "yes", "on", "checked"} for item in value)
    return str(value or "").strip().lower() in {"1", "true", "tak", "yes", "on", "checked"}


def evaluate_visible_if(rule: Any, current_data: Dict[str, Any]) -> bool:
    if not rule:
        return True

    field_name = rule.get("field")
    operator = rule.get("operator")
    expected = rule.get("value")
    if not operator:
        for candidate in ("equals", "not_equals", "in", "not_in", "is_empty", "is_not_empty"):
            if candidate in rule:
                operator = candidate
                expected = rule.get(candidate)
                break
    operator = operator or "equals"
    current_value = current_data.get(field_name, "")

    if operator == "equals":
        return current_value == expected
    if operator == "not_equals":
        return current_value != expected
    if operator == "in":
        return current_value in (rule.get("values") if "values" in rule else expected or [])
    if operator == "not_in":
        return current_value not in (rule.get("values") if "values" in rule else expected or [])
    if operator == "is_empty":
        return current_value in (None, "", [], {})
    if operator == "is_not_empty":
        return current_value not in (None, "", [], {})

    return True


def evaluate_scoped_condition(
    rule: Any,
    *,
    root_data: Dict[str, Any],
    current_data: Dict[str, Any] | None = None,
) -> bool:
    """Evaluate a condition in an explicit root or current-record scope."""
    if not rule:
        return True
    scope = str(rule.get("scope") or "current").strip().casefold()
    values = root_data if scope == "root" else (current_data or root_data)
    return evaluate_visible_if(rule, values)


def _validate_repeatable_child(field: Dict[str, Any], value: Any, *, required: bool) -> str | None:
    field_type = str(field.get("type") or "")
    label = str(field.get("label") or field.get("name") or "Pole")
    empty = value in (None, "", [], {})
    if required and empty:
        return f"Pole „{label}” jest wymagane."
    if empty:
        return None
    if field_type == "email" and not EMAIL_REGEX.fullmatch(str(value).strip()):
        return "Podaj poprawny adres e-mail."
    if field_type in {"tel", "phone"} and not TEL_REGEX.fullmatch(str(value).strip()):
        return "Podaj poprawny numer telefonu."
    if field_type == "number":
        try:
            float(value)
        except (TypeError, ValueError):
            return "Podaj poprawną wartość liczbową."
    if field_type in {"date", "time"}:
        try:
            datetime.strptime(str(value), "%Y-%m-%d" if field_type == "date" else "%H:%M")
        except ValueError:
            return "Podaj poprawną datę w formacie RRRR-MM-DD." if field_type == "date" else "Podaj poprawną godzinę w formacie GG:MM."
    if field_type in {"select", "radio"}:
        from services.form_option_service import option_value

        if value not in {option_value(option) for option in field.get("options", [])}:
            return "Wybrano nieprawidłową wartość."
    return None


def _validate_repeatable_group(field: Dict[str, Any], records: Any, root_data: Dict[str, Any]) -> Dict[str, str]:
    name = str(field.get("name") or "")
    label = str(field.get("label") or name)
    errors: Dict[str, str] = {}
    if not isinstance(records, list):
        return {name: f"Pole „{label}” ma nieprawidłowy format."}
    minimum = int(field.get("min_items", 1))
    maximum = int(field.get("max_items", 20))
    if len(records) < minimum:
        errors[name] = f"Pole „{label}” wymaga co najmniej {minimum} elementów."
    elif len(records) > maximum:
        errors[name] = f"Pole „{label}” może zawierać najwyżej {maximum} elementów."
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors[f"{name}.{index}"] = "Element grupy ma nieprawidłowy format."
            continue
        record_uuid = str(record.get("record_uuid") or record.get("id") or "")
        try:
            normalized_uuid = str(UUID(record_uuid))
        except (TypeError, ValueError, AttributeError):
            normalized_uuid = ""
        if not normalized_uuid or normalized_uuid in seen:
            errors[f"{name}.{index}.record_uuid"] = "Element grupy musi posiadać unikalny UUID."
            continue
        seen.add(normalized_uuid)
        for child in field.get("fields") or []:
            if not isinstance(child, dict) or not child.get("name") or child.get("readonly"):
                continue
            if not evaluate_scoped_condition(child.get("visible_if"), root_data=root_data, current_data=record):
                continue
            required = bool(child.get("required"))
            if child.get("required_if"):
                required = required or evaluate_scoped_condition(child.get("required_if"), root_data=root_data, current_data=record)
            error = _validate_repeatable_child(child, record.get(child["name"]), required=required)
            if error:
                errors[f"{name}.{normalized_uuid}.{child['name']}"] = error
    return errors


def validate_submission(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> Dict[str, str]:
    errors: Dict[str, str] = {}

    for field in form_definition["fields"]:
        field_type = field["type"]
        field_name = field.get("name")

        if field_type in {"section", "static_text", "file"} or not field_name or field.get("readonly"):
            continue

        if not evaluate_visible_if(field.get("visible_if"), submission_data):
            continue

        label = field.get("label", field_name)
        value = submission_data.get(field_name, "")

        if field_type == "repeatable_group":
            errors.update(_validate_repeatable_group(field, value, submission_data))
            continue

        required = bool(field.get("required"))
        if field.get("required_if"):
            required = required or evaluate_visible_if(field.get("required_if"), submission_data)
        if required:
            if field_type == "checkbox":
                if value != "Tak":
                    errors[field_name] = f"Pole „{label}” jest wymagane."
                    continue
            elif value == "":
                errors[field_name] = f"Pole „{label}” jest wymagane."
                continue

        if value == "":
            continue

        if field_type == "email" and not EMAIL_REGEX.match(value):
            errors[field_name] = "Podaj poprawny adres e-mail."

        is_phone_field = field_type in {"tel", "phone"} or (
            str(field_name or "").strip().casefold() == "telefon" and field_type in {"text", "number"}
        )
        if is_phone_field and not TEL_REGEX.fullmatch(str(value).strip()):
            errors[field_name] = "Podaj poprawny numer telefonu."

        if field_type == "pesel" and not validate_pesel(value):
            errors[field_name] = PESEL_ERROR_MESSAGE

        if field_type == "number":
            try:
                float(value)
            except ValueError:
                errors[field_name] = "Podaj poprawną wartość liczbową."

        if field_type == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                errors[field_name] = "Podaj poprawną datę w formacie RRRR-MM-DD."
                
        if field_type == "time":
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError:
                errors[field_name] = "Podaj poprawną godzinę w formacie GG:MM."

        if field_type in {"select", "radio"}:
            from services.form_option_service import option_value

            allowed_values = {option_value(option) for option in field.get("options", [])}
            if value not in allowed_values:
                errors[field_name] = "Wybrano nieprawidłową wartość."

    signature_errors = validate_signature_submission(form_definition, submission_data)
    errors.update(signature_errors)
    errors.update(validate_pesel_consistency(form_definition, submission_data, existing_errors=errors))

    return errors


def validate_signature_submission(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> Dict[str, str]:
    errors: Dict[str, str] = {}

    signature = form_definition.get("signature", {})
    mode = signature.get("mode", "none")
    require_before_submit = bool(signature.get("require_before_submit", False))
    selected_method = (submission_data.get("signature_method") or "").strip()

    if mode == "none":
        return errors

    if mode == "qualified":
        if selected_method and selected_method != "qualified":
            errors["signature_method"] = "Dla tego formularza dozwolony jest wyłącznie podpis kwalifikowany."
        return errors

    if mode == "trusted_profile":
        if selected_method and selected_method != "trusted_profile":
            errors["signature_method"] = "Dla tego formularza dozwolony jest wyłącznie Profil Zaufany."
        return errors

    if mode == "optional":
        allowed_methods = set()
        if signature.get("allow_qualified_signature"):
            allowed_methods.add("qualified")
        if signature.get("allow_trusted_profile"):
            allowed_methods.add("trusted_profile")

        if require_before_submit and not selected_method:
            errors["signature_method"] = "Wybierz metodę podpisu."
            return errors

        if selected_method and selected_method not in allowed_methods:
            errors["signature_method"] = "Wybrano nieprawidłową metodę podpisu."

    return errors


def parse_pesel(pesel: str, *, today: date | None = None) -> Dict[str, Any] | None:
    birth_date = _birth_date_from_pesel(pesel)
    if birth_date is None or not _validate_pesel_checksum(pesel):
        return None

    gender = "Mężczyzna" if int(pesel[9]) % 2 else "Kobieta"
    reference_date = today or date.today()
    return {
        "birth_date": birth_date,
        "gender": gender,
        "age": calculate_age(birth_date, reference_date),
    }


def _birth_date_from_pesel(pesel: str) -> date | None:
    if not PESEL_REGEX.match(pesel):
        return None

    year = int(pesel[0:2])
    encoded_month = int(pesel[2:4])
    day = int(pesel[4:6])
    century_offsets = {
        range(1, 13): 1900,
        range(21, 33): 2000,
        range(41, 53): 2100,
        range(61, 73): 2200,
        range(81, 93): 1800,
    }

    birth_century = None
    month = None
    for encoded_range, century in century_offsets.items():
        if encoded_month in encoded_range:
            birth_century = century
            month = encoded_month - (century - 1900) // 100 * 20
            if century == 1800:
                month = encoded_month - 80
            break

    if birth_century is None or month is None:
        return None

    try:
        return date(birth_century + year, month, day)
    except ValueError:
        return None


def calculate_age(birth_date: date, today: date | None = None) -> int:
    reference_date = today or date.today()
    age = reference_date.year - birth_date.year
    if (reference_date.month, reference_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def validate_pesel_consistency(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
    *,
    existing_errors: Dict[str, str] | None = None,
) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    fields = [
        field
        for field in form_definition.get("fields", [])
        if field.get("type") not in {"section", "static_text"} and field.get("name")
    ]
    pesel_field = _find_field(fields, PESEL_ALIASES, preferred_type="pesel")
    if not pesel_field:
        return errors

    pesel_name = pesel_field["name"]
    if existing_errors and pesel_name in existing_errors:
        return errors

    pesel_value = str(submission_data.get(pesel_name, "") or "").strip()
    if not pesel_value:
        return errors

    pesel_data = parse_pesel(
        pesel_value,
        today=_resolve_age_reference_date(fields, submission_data),
    )
    if not pesel_data:
        errors[pesel_name] = PESEL_ERROR_MESSAGE
        return errors

    birth_date_field = _find_field(fields, BIRTH_DATE_ALIASES)
    if birth_date_field:
        field_name = birth_date_field["name"]
        value = str(submission_data.get(field_name, "") or "").strip()
        if (
            value
            and not (existing_errors and field_name in existing_errors)
            and not _birth_date_matches(value, pesel_data["birth_date"])
        ):
            errors[field_name] = "Data urodzenia jest niezgodna z numerem PESEL."

    gender_field = _find_field(fields, GENDER_ALIASES)
    if gender_field:
        field_name = gender_field["name"]
        value = str(submission_data.get(field_name, "") or "").strip()
        if (
            value
            and not (existing_errors and field_name in existing_errors)
            and not _gender_matches(value, pesel_data["gender"])
        ):
            errors[field_name] = "Płeć jest niezgodna z numerem PESEL."

    age_field = _find_field(fields, AGE_ALIASES)
    if age_field:
        field_name = age_field["name"]
        value = str(submission_data.get(field_name, "") or "").strip()
        if (
            value
            and not (existing_errors and field_name in existing_errors)
            and value != str(pesel_data["age"])
        ):
            errors[field_name] = "Wiek jest niezgodny z datą urodzenia wyliczoną z PESEL."

    return errors


def apply_pesel_derived_values(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
    *,
    today: date | None = None,
) -> Dict[str, Any]:
    normalized_data = dict(submission_data)
    fields = [
        field
        for field in form_definition.get("fields", [])
        if field.get("type") not in {"section", "static_text"} and field.get("name")
    ]
    pesel_field = _find_field(fields, PESEL_ALIASES, preferred_type="pesel")
    if not pesel_field:
        return normalized_data

    pesel_value = str(normalized_data.get(pesel_field["name"], "") or "").strip()
    pesel_data = parse_pesel(pesel_value, today=_resolve_age_reference_date(fields, normalized_data, today=today))
    if not pesel_data:
        return normalized_data

    birth_date_field = _find_field(fields, BIRTH_DATE_ALIASES)
    if birth_date_field:
        normalized_data[birth_date_field["name"]] = _format_birth_date_for_field(
            birth_date_field,
            pesel_data["birth_date"],
        )

    gender_field = _find_field(fields, GENDER_ALIASES)
    if gender_field:
        normalized_data[gender_field["name"]] = _format_gender_for_field(gender_field, pesel_data["gender"])

    age_field = _find_field(fields, AGE_ALIASES)
    if age_field:
        normalized_data[age_field["name"]] = str(pesel_data["age"])

    return normalized_data


def validate_pesel(pesel: str) -> bool:
    if not PESEL_REGEX.match(pesel):
        return False
    return _birth_date_from_pesel(pesel) is not None and _validate_pesel_checksum(pesel)


def _validate_pesel_checksum(pesel: str) -> bool:
    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    checksum = sum(int(pesel[i]) * weights[i] for i in range(10))
    control_digit = (10 - (checksum % 10)) % 10
    return control_digit == int(pesel[10])


def _find_field(
    fields: List[Dict[str, Any]],
    aliases: set[str],
    *,
    preferred_type: str | None = None,
) -> Dict[str, Any] | None:
    normalized_aliases = {_normalize_field_name(alias) for alias in aliases}
    for field in fields:
        field_name = str(field.get("name") or "")
        if _normalize_field_name(field_name) in normalized_aliases:
            if preferred_type is None or field.get("type") == preferred_type:
                return field
    if preferred_type:
        for field in fields:
            if field.get("type") == preferred_type:
                return field
    return None


def _normalize_field_name(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "_", without_accents).strip("_")


def _birth_date_matches(value: str, expected: date) -> bool:
    accepted_values = {
        expected.strftime("%Y-%m-%d"),
        expected.strftime("%d.%m.%Y"),
    }
    return value in accepted_values


def _gender_matches(value: str, expected: str) -> bool:
    normalized_value = _normalize_field_name(value)
    normalized_expected = _normalize_field_name(expected)
    aliases = {
        "kobieta": {"kobieta", "k", "female", "woman"},
        "mezczyzna": {"mezczyzna", "m", "male", "man"},
    }
    return normalized_value in aliases.get(normalized_expected, {normalized_expected})


def _format_birth_date_for_field(field: Dict[str, Any], value: date) -> str:
    if field.get("type") == "date":
        return value.isoformat()
    return value.strftime("%d.%m.%Y")


def _format_gender_for_field(field: Dict[str, Any], value: str) -> str:
    if field.get("type") in {"select", "radio", "checkbox"}:
        for option in field.get("options", []):
            option_value = _option_value(option)
            if _gender_matches(option_value, value):
                return option_value
    return value


def _option_value(option: Any) -> str:
    if isinstance(option, dict):
        return str(option.get("value") or option.get("label") or "").strip()
    return str(option or "").strip()


def _resolve_age_reference_date(
    fields: List[Dict[str, Any]],
    submission_data: Dict[str, Any],
    *,
    today: date | None = None,
) -> date | None:
    join_date_field = _find_field(fields, PROJECT_JOIN_DATE_ALIASES)
    if not join_date_field:
        return today
    parsed = _parse_date_value(str(submission_data.get(join_date_field["name"], "") or "").strip())
    return parsed or today


def _parse_date_value(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def build_submission_view(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    from services.training_service import without_training_selection_section

    view: List[Dict[str, Any]] = []
    current_section = {
        "title": "Dane formularza",
        "items": [],
    }

    def should_skip_section(section: Dict[str, Any]) -> bool:
        title = (section.get("title") or "").strip().lower()
        return title == "oświadczenia"

    for field in without_training_selection_section(form_definition["fields"]):
        field_type = field["type"]

        if field_type == "section":
            if current_section["items"] and not should_skip_section(current_section):
                view.append(current_section)

            current_section = {
                "title": field.get("label", "Sekcja"),
                "items": [],
            }
            continue

        if field_type in {"static_text", "file"}:
            continue

        field_name = field.get("name")
        if not field_name:
            continue

        if not evaluate_visible_if(field.get("visible_if"), submission_data):
            continue

        current_section["items"].append({
            "label": field.get("label", field_name),
            "value": format_value_for_pdf(field_type, submission_data.get(field_name, "")),
        })

    if current_section["items"] and not should_skip_section(current_section):
        view.append(current_section)

    return view


def format_value_for_pdf(field_type: str, value: str) -> str:
    if field_type == "training_selection":
        trainings = parse_training_snapshots(value)
        if not trainings:
            return "Brak danych"
        return "\n".join(
            f"{training.get('name', '')} - {training.get('price_formatted') or 'Brak danych o cenie'}"
            for training in trainings
        )
    if field_type == "checkbox":
        return "Tak" if value == "Tak" else "Nie"
    return format_admin_value(value)


def resolve_signature_method(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> Optional[str]:
    signature = form_definition.get("signature", {})
    mode = signature.get("mode", "none")

    if mode == "none":
        return None

    if mode == "qualified":
        return "qualified"

    if mode == "trusted_profile":
        return "trusted_profile"

    if mode == "optional":
        selected_method = (submission_data.get("signature_method") or "").strip()
        if selected_method in {"qualified", "trusted_profile"}:
            return selected_method

    return None
