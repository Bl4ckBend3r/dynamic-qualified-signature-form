import json
import re
from copy import deepcopy
from datetime import datetime
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

    return normalized


def extract_submission_data(form_definition: Dict[str, Any], request_form) -> Dict[str, Any]:
    data: Dict[str, Any] = {}

    for field in form_definition["fields"]:
        field_type = field["type"]
        field_name = field.get("name")

        if field_type in {"section", "static_text"} or not field_name:
            continue

        if field_type == "checkbox":
            data[field_name] = "Tak" if field_name in request_form else "Nie"
        elif field_type == "training_selection":
            data[field_name] = ",".join(request_form.getlist(field_name))
        else:
            data[field_name] = request_form.get(field_name, "").strip()

    signature = form_definition.get("signature", {})
    if signature.get("show_user_choice"):
        data["signature_method"] = request_form.get("signature_method", "").strip()

    return data


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
            errors[field_name] = "Podaj poprawny numer PESEL."

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


def validate_pesel(pesel: str) -> bool:
    if not PESEL_REGEX.match(pesel):
        return False

    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    checksum = sum(int(pesel[i]) * weights[i] for i in range(10))
    control_digit = (10 - (checksum % 10)) % 10
    return control_digit == int(pesel[10])


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
