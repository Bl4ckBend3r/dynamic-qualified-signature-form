from __future__ import annotations

import logging
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from services.process_service import ProcessStatus

logger = logging.getLogger(__name__)


FORM_FIELD_MAP: dict[str, str] = {
    # Participant data.
    "imiona": "imiona",
    "imie": "imiona",
    "first_name": "imiona",
    "nazwisko": "nazwisko",
    "last_name": "nazwisko",
    "obywatelstwo": "obywatelstwo",
    "data_urodzenia": "data_urodzenia",
    "miejsce_urodzenia": "miejsce_urodzenia",
    "pesel": "pesel",
    "plec": "plec",
    "wiek": "wiek",
    "wyksztalcenie": "wyksztalcenie",
    # Address.
    "wojewodztwo": "wojewodztwo",
    "powiat": "powiat",
    "gmina": "gmina",
    "miejscowosc": "miejscowosc",
    "kod_pocztowy": "kod_pocztowy",
    "ulica": "ulica",
    "nr_budynku": "nr_budynku",
    "nr_lokalu": "nr_lokalu",
    # Contact.
    "telefon": "telefon",
    "email": "email",
    # Target group and extra status fields.
    "zamieszkuje_lubuskie": "zamieszkuje_lubuskie",
    "zamieszkanie_lubuskie": "zamieszkuje_lubuskie",
    "pracuje_lubuskie": "pracuje_lubuskie",
    "praca_lubuskie": "pracuje_lubuskie",
    "osoba_niepelnosprawna": "osoba_niepelnosprawna",
    "osoba_z_niepelnosprawnosciami": "osoba_niepelnosprawna",
    "specjalne_potrzeby": "specjalne_potrzeby",
    "specjalne_potrzeby_opis": "specjalne_potrzeby_opis",
    "mniejszosc_narodowa": "mniejszosc_narodowa",
    "osoba_bezdomna": "osoba_bezdomna",
    "niekorzystna_sytuacja": "niekorzystna_sytuacja",
    "dzial_wsparcia": "dzial_wsparcia",
    # Consents from the current JSON and legacy/test aliases.
    "osw_regulamin": "osw_regulamin",
    "accept_regulamin": "osw_regulamin",
    "osw_kryteria": "osw_kryteria",
    "osw_finansowanie": "osw_finansowanie",
    "osw_brak_gwarancji": "osw_brak_gwarancji",
    "osw_rodo": "osw_rodo",
    "accept_rodo": "osw_rodo",
    "osw_ewaluacja": "osw_ewaluacja",
    "osw_zatrudnienie": "osw_zatrudnienie",
    "osw_monitoring": "osw_monitoring",
    "osw_prawdziwosc": "osw_prawdziwosc",
    "accept_odpowiedzialnosc": "osw_prawdziwosc",
    # Declaration form fields.
    "deklaracja_18_lat": "deklaracja_18_lat",
    "deklaracja_lubuskie": "deklaracja_lubuskie",
    "deklaracja_wlasna_inicjatywa": "deklaracja_wlasna_inicjatywa",
    "deklaracja_brak_dzialalnosci": "deklaracja_brak_dzialalnosci",
    "deklaracja_brak_ksztalcenia": "deklaracja_brak_ksztalcenia",
    "deklaracja_obszar_wiejski": "deklaracja_obszar_wiejski",
    "deklaracja_niepelnosprawnosc": "deklaracja_niepelnosprawnosc",
    "deklaracja_umiejetnosci_podstawowe": "deklaracja_umiejetnosci_podstawowe",
    "deklaracja_grupa_niekorzystna": "deklaracja_grupa_niekorzystna",
    "deklaracja_zgoda_wizerunek": "deklaracja_zgoda_wizerunek",
    "deklaracja_prawdziwosc_danych": "deklaracja_prawdziwosc_danych",
    # Training and generated document metadata kept in form_submissions.
    "selected_trainings": "selected_trainings",
    "training_agreements": "training_agreements",
    "pdf_filename": "pdf_filename",
    "signed_pdf_filename": "signed_pdf_filename",
    "signature_status": "signature_status",
    "signature_request_id": "signature_request_id",
    "signature_method": "signature_method",
    "access_token": "access_token",
    "form_slug": "form_slug",
    "form_name": "form_name",
    "submission_id": "submission_id",
    "created_at": "created_at",
    # Workflow/status columns.
    "process_status": "process_status",
    "workflow_step": "workflow_step",
    "officer_decision": "officer_decision",
    "officer_decision_reason": "officer_decision_reason",
    "officer_decision_email_requested": "officer_decision_email_requested",
    "officer_decision_email_sent": "officer_decision_email_sent",
    "acceptance_required": "acceptance_required",
    "acceptance_email_sent": "acceptance_email_sent",
    "decision_email_sent": "decision_email_sent",
    "decision_email_sent_for": "decision_email_sent_for",
    "akceptacja": "akceptacja",
    "declaration_required": "declaration_required",
    "declaration_generated": "declaration_generated",
    "declaration_filename": "declaration_filename",
    "declaration_signed": "declaration_signed",
    "declaration_signature_type": "declaration_signature_type",
    "declaration_signature_valid": "declaration_signature_valid",
    "declaration_signature_error": "declaration_signature_error",
    "declaration_signed_filename": "declaration_signed_filename",
    "agreement_required": "agreement_required",
    "agreement_blocked": "agreement_blocked",
    "agreement_block_reason": "agreement_block_reason",
    "agreement_generated": "agreement_generated",
    "agreement_filename": "agreement_filename",
    "agreement_generated_at": "agreement_generated_at",
    "agreement_signed": "agreement_signed",
    "agreement_signature_type": "agreement_signature_type",
    "agreement_signature_valid": "agreement_signature_valid",
    "agreement_signature_error": "agreement_signature_error",
    "agreement_signed_filename": "agreement_signed_filename",
    "office_agreement_signed_email_sent": "office_agreement_signed_email_sent",
    "office_agreement_signed_email_sent_for": "office_agreement_signed_email_sent_for",
    "agreement_success_email_sent": "agreement_success_email_sent",
    "agreement_success_email_sent_for": "agreement_success_email_sent_for",
    "requirements_rejection_email_sent": "requirements_rejection_email_sent",
    "correction_required": "correction_required",
    "correction_message": "correction_message",
    "correction_fields": "correction_fields",
    "correction_requested_at": "correction_requested_at",
    "correction_completed_at": "correction_completed_at",
    "data_json": "data_json",
}

FORM_SUBMISSION_COLUMNS = set(FORM_FIELD_MAP.values()) | {
    "id",
}

BOOLEAN_COLUMNS = {
    "osw_regulamin",
    "osw_kryteria",
    "osw_finansowanie",
    "osw_brak_gwarancji",
    "osw_rodo",
    "osw_ewaluacja",
    "osw_zatrudnienie",
    "osw_monitoring",
    "osw_prawdziwosc",
    "deklaracja_zgoda_wizerunek",
    "deklaracja_prawdziwosc_danych",
}

DATE_COLUMNS = {
    "data_urodzenia",
    "agreement_generated_at",
}

DATETIME_COLUMNS = {
    "created_at",
    "correction_requested_at",
    "correction_completed_at",
}

INTEGER_COLUMNS = {"wiek"}
NUMERIC_COLUMNS: set[str] = set()
JSON_COLUMNS = {"data_json"}

TEXT_COLUMNS = (
    FORM_SUBMISSION_COLUMNS
    - BOOLEAN_COLUMNS
    - DATE_COLUMNS
    - DATETIME_COLUMNS
    - INTEGER_COLUMNS
    - NUMERIC_COLUMNS
    - JSON_COLUMNS
)

REQUIRED_FORM_COLUMNS = {
    "imiona",
    "nazwisko",
    "pesel",
    "telefon",
    "email",
    "miejscowosc",
    "wojewodztwo",
}

REQUIRED_CONSENT_COLUMNS = {
    "osw_regulamin",
    "osw_rodo",
    "osw_prawdziwosc",
}

STATUS_DEFAULTS: dict[str, Any] = {
    "process_status": ProcessStatus.FORM_SUBMITTED.value,
    "workflow_step": "",
    "officer_decision": "",
    "officer_decision_reason": "",
    "officer_decision_email_requested": "",
    "officer_decision_email_sent": "",
    "acceptance_required": "",
    "acceptance_email_sent": "",
    "decision_email_sent": "",
    "decision_email_sent_for": "",
    "akceptacja": "",
    "pdf_filename": "",
    "signed_pdf_filename": "",
    "signature_status": "manual",
    "signature_request_id": "mobywatel-manual",
    "signature_method": "",
    "declaration_generated": "",
    "declaration_filename": "",
    "declaration_signed": "",
    "declaration_signature_type": "",
    "declaration_signature_valid": "",
    "declaration_signature_error": "",
    "declaration_signed_filename": "",
    "agreement_blocked": "",
    "agreement_block_reason": "",
    "agreement_generated": "",
    "agreement_filename": "",
    "agreement_signed": "",
    "agreement_signature_type": "",
    "agreement_signature_valid": "",
    "agreement_signature_error": "",
    "agreement_signed_filename": "",
    "office_agreement_signed_email_sent": "",
    "office_agreement_signed_email_sent_for": "",
    "agreement_success_email_sent": "",
    "agreement_success_email_sent_for": "",
    "requirements_rejection_email_sent": "",
}


def build_submission_from_form(
    form_data,
    form_config: Mapping[str, Any] | None = None,
    *,
    include_metadata: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, list[str]]]:
    """Map request.form/JSON/form dict keys to form_submissions columns."""

    raw_data = _extract_raw_mapping(form_data)
    field_types = _field_types_from_config(form_config)
    checkbox_columns = _checkbox_columns_from_config(form_config)

    mapped: dict[str, Any] = dict(STATUS_DEFAULTS)
    saved_fields: list[str] = []
    skipped_fields: list[str] = []

    for field_name, raw_value in raw_data.items():
        column_name = FORM_FIELD_MAP.get(field_name, field_name if field_name in FORM_SUBMISSION_COLUMNS else "")
        if not column_name or column_name not in FORM_SUBMISSION_COLUMNS or column_name == "id":
            skipped_fields.append(field_name)
            continue

        field_type = field_types.get(field_name) or field_types.get(column_name)
        mapped[column_name] = convert_value_for_column(column_name, raw_value, field_type=field_type)
        saved_fields.append(f"{field_name}->{column_name}")

    mapped["data_json"] = _build_dynamic_data_json(raw_data, mapped.get("data_json"))

    for column_name in checkbox_columns:
        if column_name not in mapped:
            mapped[column_name] = False

    if "created_at" not in mapped or mapped["created_at"] in {"", None}:
        mapped["created_at"] = datetime.now()

    if include_metadata:
        return mapped, {
            "saved_fields": saved_fields,
            "skipped_fields": skipped_fields,
        }
    return mapped


def validate_required_submission_fields(
    submission: Mapping[str, Any],
    form_config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    required_columns = set(REQUIRED_FORM_COLUMNS)

    for column_name in required_columns:
        if _is_empty_value(submission.get(column_name)):
            errors[column_name] = "Pole jest wymagane."

    required_consents = set(REQUIRED_CONSENT_COLUMNS)
    for field in (form_config or {}).get("fields", []):
        if field.get("type") != "checkbox" or not field.get("required"):
            continue
        field_name = field.get("name")
        column_name = FORM_FIELD_MAP.get(field_name or "", field_name or "")
        if column_name in FORM_SUBMISSION_COLUMNS:
            required_consents.add(column_name)

    for column_name in required_consents:
        if submission.get(column_name) is not True:
            errors[column_name] = "Wymagane oswiadczenie musi byc zaakceptowane."

    return errors


def convert_value_for_column(column_name: str, raw_value: Any, *, field_type: str | None = None) -> Any:
    if column_name in BOOLEAN_COLUMNS or field_type == "checkbox":
        return _to_bool(raw_value)
    if column_name in DATE_COLUMNS:
        return _to_date(raw_value)
    if column_name in DATETIME_COLUMNS:
        return _to_datetime(raw_value)
    if column_name in INTEGER_COLUMNS:
        return _to_int(raw_value)
    if column_name in NUMERIC_COLUMNS:
        return _to_decimal(raw_value)
    if column_name in JSON_COLUMNS:
        return _to_json_dict(raw_value)
    if isinstance(raw_value, list):
        return ",".join(str(item).strip() for item in raw_value if str(item).strip())
    return str(raw_value or "").strip()


def serialize_value_for_app(column_name: str, value: Any) -> str:
    if value is None:
        return ""
    if column_name in BOOLEAN_COLUMNS:
        return "Tak" if bool(value) else "Nie"
    if isinstance(value, datetime):
        if column_name == "created_at":
            return value.strftime("%d.%m.%Y")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if column_name in JSON_COLUMNS:
        return value if isinstance(value, str) else json.dumps(value or {}, ensure_ascii=False)
    return str(value)


def _extract_raw_mapping(form_data) -> dict[str, Any]:
    if form_data is None:
        return {}

    if hasattr(form_data, "keys"):
        data: dict[str, Any] = {}
        for key in form_data.keys():
            if hasattr(form_data, "getlist"):
                values = form_data.getlist(key)
                data[key] = values if len(values) > 1 else (values[0] if values else "")
            else:
                data[key] = form_data.get(key)
        return data

    if isinstance(form_data, Mapping):
        return dict(form_data)

    return {}


def _field_types_from_config(form_config: Mapping[str, Any] | None) -> dict[str, str]:
    field_types: dict[str, str] = {}
    for field in (form_config or {}).get("fields", []):
        field_name = field.get("name")
        if field_name:
            field_types[field_name] = field.get("type", "")
            column_name = FORM_FIELD_MAP.get(field_name, field_name)
            field_types[column_name] = field.get("type", "")
    return field_types


def _checkbox_columns_from_config(form_config: Mapping[str, Any] | None) -> set[str]:
    columns: set[str] = set()
    for field in (form_config or {}).get("fields", []):
        if field.get("type") != "checkbox":
            continue
        field_name = field.get("name")
        column_name = FORM_FIELD_MAP.get(field_name or "", field_name or "")
        if column_name in FORM_SUBMISSION_COLUMNS:
            columns.add(column_name)
    return columns


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set)):
        return any(_to_bool(item) for item in value)
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "t", "yes", "y", "tak", "on", "checked"}


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.warning("Pominieto niepoprawna date dla PostgreSQL: %s", text)
    return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed
        except ValueError:
            continue
    logger.warning("Pominieto niepoprawny datetime dla PostgreSQL: %s", text)
    return None


def _to_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        logger.warning("Pominieto niepoprawna liczbe calkowita dla PostgreSQL: %s", text)
        return None


def _to_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        logger.warning("Pominieto niepoprawna liczbe dziesietna dla PostgreSQL: %s", text)
        return None


def _to_json_dict(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _build_dynamic_data_json(raw_data: Mapping[str, Any], explicit_value: Any) -> dict:
    data_json = _to_json_dict(explicit_value)
    if data_json:
        return data_json
    for key, value in raw_data.items():
        if key == "data_json":
            continue
        if isinstance(value, (list, tuple, set)):
            data_json[key] = [str(item).strip() for item in value]
        elif isinstance(value, (date, datetime)):
            data_json[key] = value.isoformat()
        else:
            data_json[key] = str(value or "").strip()
    return data_json


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
