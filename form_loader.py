import json
import re
import unicodedata
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    "pesel",
    "section",
    "static_text",
    "training_selection",
}

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
TEL_REGEX = re.compile(r"^[0-9+\s\-()]{7,20}$")
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
    consents_view: List[Dict[str, Any]] = []

    for field in form_definition.get("fields", []):
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

    for field in form_definition["fields"]:
        field_type = field.get("type")
        if field_type not in SUPPORTED_FIELD_TYPES:
            raise ValueError(f"Nieobsługiwany typ pola: {field_type}")

        if field_type not in {"section", "static_text"} and not field.get("name"):
            raise ValueError(f"Pole typu '{field_type}' musi zawierać 'name'.")

        if field_type in {"select", "radio"} and not isinstance(field.get("options"), list):
            raise ValueError(f"Pole '{field.get('name')}' musi zawierać listę 'options'.")


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


def normalize_form_definition(form_definition: Dict[str, Any]) -> Dict[str, Any]:
    normalized = deepcopy(form_definition)
    normalized.setdefault("description", "")
    normalized.setdefault("submit_label", "Generuj i wyślij")

    normalized = normalize_signature_config(normalized)

    for field in normalized["fields"]:
        if field.get("id") and not field.get("name"):
            field["name"] = field["id"]
        field.setdefault("label", "")
        field.setdefault("placeholder", "")
        field.setdefault("required", False)
        field.setdefault("options", [])
        field.setdefault("help_text", "")
        field.setdefault("default", "")
        field.setdefault("validation", {})
        field.setdefault("width", "full")
        field.setdefault("visible_if", None)
        field.setdefault("readonly", False)
        if field.get("stage") not in SUPPORTED_FIELD_STAGES:
            field["stage"] = FIELD_STAGE_INITIAL

    return normalized


def fields_for_stage(form_definition: Dict[str, Any], stage: str) -> List[Dict[str, Any]]:
    wanted_stage = stage if stage in SUPPORTED_FIELD_STAGES else FIELD_STAGE_INITIAL
    fields = form_definition.get("fields", [])
    result: List[Dict[str, Any]] = []
    pending_sections: List[Dict[str, Any]] = []
    for field in fields:
        field_type = field.get("type")
        if field_type in {"section", "static_text"}:
            pending_sections.append(field)
            continue
        if field.get("stage", FIELD_STAGE_INITIAL) != wanted_stage:
            continue
        result.extend(pending_sections)
        pending_sections = []
        result.append(field)
    return result


def form_definition_for_stage(form_definition: Dict[str, Any], stage: str) -> Dict[str, Any]:
    normalized = normalize_form_definition(form_definition)
    return {**normalized, "fields": fields_for_stage(normalized, stage)}


def additional_fields_for_acceptance(form_definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return fields_for_stage(normalize_form_definition(form_definition), FIELD_STAGE_AFTER_ACCEPTANCE)


def has_additional_fields_after_acceptance(form_definition: Dict[str, Any]) -> bool:
    return any(
        field.get("type") not in {"section", "static_text"}
        for field in additional_fields_for_acceptance(form_definition)
    )


def extract_submission_data(form_definition: Dict[str, Any], request_form) -> Dict[str, Any]:
    data: Dict[str, Any] = {}

    for field in form_definition["fields"]:
        field_type = field["type"]
        field_name = field.get("name")

        if field_type in {"section", "static_text"} or not field_name:
            continue

        if field_type == "checkbox":
            data[field_name] = "Tak" if _is_checked(request_form, field_name) else "Nie"
        elif field_type == "training_selection":
            data[field_name] = ",".join(_getlist(request_form, field_name))
        else:
            data[field_name] = str(_get(request_form, field_name, "") or "").strip()

    signature = form_definition.get("signature", {})
    if signature.get("show_user_choice"):
        data["signature_method"] = str(_get(request_form, "signature_method", "") or "").strip()

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
    operator = rule.get("operator", "equals")
    expected = rule.get("value")
    current_value = current_data.get(field_name, "")

    if operator == "equals":
        return current_value == expected
    if operator == "not_equals":
        return current_value != expected
    if operator == "in":
        return current_value in rule.get("values", [])
    if operator == "not_in":
        return current_value not in rule.get("values", [])

    return True


def validate_submission(
    form_definition: Dict[str, Any],
    submission_data: Dict[str, Any],
) -> Dict[str, str]:
    errors: Dict[str, str] = {}

    for field in form_definition["fields"]:
        field_type = field["type"]
        field_name = field.get("name")

        if field_type in {"section", "static_text"} or not field_name:
            continue

        if not evaluate_visible_if(field.get("visible_if"), submission_data):
            continue

        label = field.get("label", field_name)
        value = submission_data.get(field_name, "")

        if field.get("required"):
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

        if field_type == "tel" and not TEL_REGEX.match(value):
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

        if field_type in {"select", "radio"} and value not in field.get("options", []):
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
    view: List[Dict[str, Any]] = []
    current_section = {
        "title": "Dane formularza",
        "items": [],
    }

    def should_skip_section(section: Dict[str, Any]) -> bool:
        title = (section.get("title") or "").strip().lower()
        return title == "oświadczenia"

    for field in form_definition["fields"]:
        field_type = field["type"]

        if field_type == "section":
            if current_section["items"] and not should_skip_section(current_section):
                view.append(current_section)

            current_section = {
                "title": field.get("label", "Sekcja"),
                "items": [],
            }
            continue

        if field_type == "static_text":
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
    if field_type == "checkbox":
        return "Tak" if value == "Tak" else "Nie"
    return value if value != "" else "—"


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
