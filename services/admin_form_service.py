from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from uuid import uuid4

from form_loader import (
    FIELD_STAGE_INITIAL,
    SUPPORTED_FIELD_TYPES,
    SUPPORTED_FIELD_STAGES,
    has_additional_fields_after_acceptance,
    normalize_form_definition,
    validate_form_definition,
)
from models import Form, FormField
from services.documents.declaration_flow_service import training_section_insert_index
from services.form_config_service import FormConfigService
from services.field_availability_service import FieldAvailabilityService
from services.qualification_condition_service import QualificationConditionService
from services.training_catalog_service import TrainingCatalogService
from services.workflow_config_service import WorkflowConfigNormalizer, WorkflowConfigValidator
from services.training_service import decimal_price_to_storage
from validators.form_config_validator import FormConfigValidator


def get_declaration_training_field(form_definition: dict) -> dict:
    definition = normalize_admin_form_definition(form_definition or {})
    field = TrainingCatalogService.get_training_field(definition)
    if field:
        normalized_field = {"enabled": True, **field}
        normalized_field["catalog"] = TrainingCatalogService.get_trainings_for_field(
            normalized_field,
            active_only=False,
        )
        return normalized_field
    return {
        "enabled": False,
        "type": "training_selection",
        "name": "selected_trainings",
        "label": "Wybierz szkolenia",
        "required": True,
        "max_total_amount": "",
        "currency": "PLN",
        "catalog": [],
    }


def parse_uploaded_form_definition(content: bytes, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        return json.loads(content.decode("utf-8-sig"))
    if suffix == ".html":
        return build_definition_from_html(content.decode("utf-8-sig", errors="ignore"), filename)
    if suffix == ".docx":
        return build_definition_from_docx(content, filename)
    raise ValueError("unsupported format")


def normalize_admin_form_definition(form_definition: dict) -> dict:
    normalized = normalize_form_definition(form_definition)
    return FormConfigService().normalize_form_config(normalized)


def validate_admin_form_config(form_definition: dict, *, validate_visual_workflow: bool = True) -> list[str]:
    try:
        validate_form_definition(form_definition)
    except Exception as exc:
        return [str(exc)]
    validator = FormConfigValidator(skip_template_check=True)
    errors = validator.validate(form_definition)
    training_field = TrainingCatalogService.get_training_field(form_definition)
    errors.extend(TrainingCatalogService().validate_field(training_field))
    if validate_visual_workflow:
        errors.extend(
            WorkflowConfigValidator().validate(
                form_definition.get("workflow") or {},
                form_definition,
            )
        )
    return list(dict.fromkeys(errors))


def build_form_definition_from_admin_form(
    current_definition: dict,
    form_data,
    *,
    allow_advanced_json: bool = False,
) -> dict:
    definition = normalize_admin_form_definition(current_definition or {})
    full_definition_value = str(form_data.get("form_definition_json", "") or "").strip()
    use_full_definition = allow_advanced_json and form_data.get("use_form_definition_json") == "on"
    if use_full_definition:
        parsed_definition = json.loads(full_definition_value)
        if not isinstance(parsed_definition, dict):
            raise ValueError("Pełna konfiguracja JSON musi być obiektem.")
        definition = normalize_admin_form_definition(parsed_definition)
    normalizer = WorkflowConfigNormalizer()
    builder_value = str(form_data.get("workflow_builder_json", "") or "").strip()
    advanced_value = str(form_data.get("workflow_json", "") or "").strip()
    use_advanced_json = allow_advanced_json and form_data.get("workflow_use_advanced_json") == "on"
    if use_advanced_json:
        workflow = parse_workflow_json(advanced_value, definition.get("workflow") or {})
    elif builder_value:
        workflow = parse_workflow_json(builder_value, definition.get("workflow") or {})
    elif allow_advanced_json and advanced_value and advanced_value not in {"{}", "null"}:
        # Kompatybilność ze starszym panelem, który wysyłał wyłącznie workflow_json.
        workflow = parse_workflow_json(advanced_value, definition.get("workflow") or {})
    else:
        workflow = dict(definition.get("workflow") or {})
    workflow = normalizer.normalize(workflow)
    previous_declaration_template_source = str(workflow.get("declaration_template_source") or "")
    previous_contract_template_source = str(workflow.get("contract_template_source") or "")
    workflow["name"] = form_data.get("workflow_name", workflow.get("name", "")).strip() or "Workflow"
    requested_initial_step = form_data.get("workflow_initial_step", workflow.get("initial_step", "")).strip()
    workflow["initial_step"] = requested_initial_step
    workflow["requires_declaration"] = form_data.get("requires_declaration") == "on"
    workflow["requires_contract"] = form_data.get("requires_contract") == "on"
    workflow["requires_agreement_confirmation"] = form_data.get("requires_agreement_confirmation") == "on"
    workflow["send_email_notifications"] = form_data.get("send_email_notifications") == "on"
    workflow["allow_correction"] = form_data.get("allow_correction") == "on"
    workflow["electronic_signature_required"] = form_data.get("electronic_signature_required") == "on"
    workflow["signed_document_uploader"] = form_data.get("signed_document_uploader", "beneficiary").strip() or "beneficiary"
    workflow["declaration_template_html"] = form_data.get("declaration_template_html", "").strip()
    requested_declaration_source = str(form_data.get("declaration_template_source") or "").strip().casefold()
    if not requested_declaration_source and workflow["declaration_template_html"]:
        requested_declaration_source = "html"
    declaration_source = requested_declaration_source or str(workflow.get("declaration_template_source") or "builder").strip().casefold()
    workflow["declaration_template_source"] = declaration_source if declaration_source in {"builder", "html", "docx"} else "builder"
    workflow["declaration_docx_template"] = dict(workflow.get("declaration_docx_template") or {})
    if workflow["declaration_template_source"] == "builder" and not isinstance(workflow.get("declaration_builder_document"), dict):
        from services.documents.document_builder_service import default_document_builder_document

        workflow["declaration_builder_document"] = default_document_builder_document("declaration")
    declaration_builder_json = str(form_data.get("declaration_builder_json") or "").strip()
    if declaration_builder_json:
        from services.documents.document_builder_service import normalize_document_builder_document

        parsed_declaration_builder = json.loads(declaration_builder_json)
        if not isinstance(parsed_declaration_builder, dict):
            raise ValueError("Konfiguracja kreatora deklaracji musi być obiektem JSON.")
        workflow["declaration_builder_document"] = normalize_document_builder_document(parsed_declaration_builder, "declaration")
    if (
        workflow["declaration_template_source"] == "builder"
        and isinstance(workflow.get("declaration_builder_document"), dict)
        and (bool(declaration_builder_json) or previous_declaration_template_source != "builder" or not workflow.get("declaration_builder_active_document"))
    ):
        from services.documents.document_builder_service import normalize_document_builder_document

        workflow["declaration_builder_active_document"] = normalize_document_builder_document(
            workflow["declaration_builder_document"], "declaration"
        )
    workflow["declaration_filename_pattern"] = (
        form_data.get("declaration_filename_pattern", workflow.get("declaration_filename_pattern", "")).strip()
        or "{first_name}_{last_name}-deklaracja.pdf"
    )
    workflow["declaration_generation_mode"] = "single"
    workflow["contract_template_html"] = form_data.get("contract_template_html", "").strip()
    requested_template_source = str(form_data.get("contract_template_source") or "").strip().casefold()
    # Starsze formularze administracyjne i integracje przesyłały sam HTML,
    # zanim wybór źródła stał się jawnym polem.
    if not requested_template_source and workflow["contract_template_html"]:
        requested_template_source = "html"
    template_source = requested_template_source or str(workflow.get("contract_template_source") or "builder").strip().casefold()
    workflow["contract_template_source"] = template_source if template_source in {"builder", "html", "docx"} else "builder"
    workflow["contract_docx_template"] = dict(workflow.get("contract_docx_template") or {})
    if workflow["contract_template_source"] == "builder" and not isinstance(workflow.get("contract_builder_document"), dict):
        from services.documents.agreement_builder_service import default_agreement_builder_document

        workflow["contract_builder_document"] = default_agreement_builder_document()
    builder_json = str(form_data.get("contract_builder_json") or "").strip()
    if builder_json:
        from services.documents.agreement_builder_service import normalize_agreement_builder_document

        parsed_builder = json.loads(builder_json)
        if not isinstance(parsed_builder, dict):
            raise ValueError("Konfiguracja kreatora umowy musi być obiektem JSON.")
        workflow["contract_builder_document"] = normalize_agreement_builder_document(parsed_builder)
    if (
        workflow["contract_template_source"] == "builder"
        and isinstance(workflow.get("contract_builder_document"), dict)
        and (bool(builder_json) or previous_contract_template_source != "builder" or not workflow.get("contract_builder_active_document"))
    ):
        from services.documents.agreement_builder_service import normalize_agreement_builder_document

        workflow["contract_builder_active_document"] = normalize_agreement_builder_document(workflow["contract_builder_document"])
    workflow["contract_generation_mode"] = "per_training"
    workflow["contract_show_all_trainings_total"] = form_data.get("contract_show_all_trainings_total") == "on"
    workflow["contract_filename_pattern"] = (
        form_data.get("contract_filename_pattern", workflow.get("contract_filename_pattern", "")).strip()
        or "{first_name}_{last_name}-{training_id}-umowa.pdf"
    )
    workflow["contract_number_pattern"] = (
        form_data.get("contract_number_pattern", workflow.get("contract_number_pattern", "")).strip()
        or "{submission_id}/{agreement_sequence}/{generated_date}"
    )
    workflow["managed_documents"] = bool(
        form_data.get("workflow_controls_present") == "1"
        or form_data.get("requires_declaration")
        or form_data.get("requires_contract")
        or form_data.get("declaration_template_html")
        or form_data.get("contract_template_html")
        or workflow.get("declaration_docx_template")
        or workflow.get("contract_docx_template")
    )
    workflow["decision_settings"] = _workflow_decision_settings(form_data, workflow.get("decision_settings") or [])
    workflow["email_notifications"] = _workflow_email_notifications(form_data, workflow.get("email_notifications") or [])
    workflow = normalizer.normalize(workflow)
    if "workflow_initial_step" in form_data:
        workflow["initial_step"] = requested_initial_step
    if builder_value or use_advanced_json:
        workflow_errors = WorkflowConfigValidator().validate(workflow)
        if workflow_errors:
            raise ValueError(" ".join(workflow_errors))
    definition["workflow"] = workflow
    if "assignment_mode" in form_data:
        assignment_mode = str(form_data.get("assignment_mode") or "manual").strip()
        if assignment_mode not in {"manual", "round_robin"}:
            raise ValueError("Nieprawidłowy tryb automatycznego przydzielania spraw.")
        eligible_users = [
            int(item) for item in form_data.getlist("assignment_eligible_user_ids")
            if str(item).isdigit()
        ]
        definition["assignment"] = {
            **dict(definition.get("assignment") or {}),
            "mode": assignment_mode,
            "eligible_users": list(dict.fromkeys(eligible_users)),
        }
    if "qualification_conditions_json" in form_data:
        raw_conditions = str(form_data.get("qualification_conditions_json") or "").strip() or "[]"
        parsed_conditions = json.loads(raw_conditions)
        if not isinstance(parsed_conditions, list):
            raise ValueError("Konfiguracja warunków kwalifikujących musi być listą.")
        qualification_service = QualificationConditionService()
        qualification_config = qualification_service.normalize_config(
            {
                "enabled": form_data.get("qualification_conditions_enabled") == "on",
                "conditions": parsed_conditions,
            },
            definition.get("fields") or [],
        )
        qualification_errors = qualification_service.validate_config(
            qualification_config,
            definition.get("fields") or [],
        )
        if qualification_errors:
            raise ValueError(" ".join(qualification_errors))
        definition["qualification_conditions"] = qualification_config
    definition = apply_training_selection_from_admin_form(definition, form_data)
    return normalize_admin_form_definition(definition)


def _workflow_decision_settings(form_data, existing: list[dict]) -> list[dict]:
    existing_by_id = {str(item.get("id") or ""): dict(item) for item in existing if isinstance(item, dict)}
    definitions = (
        ("application_decision", "Decyzja o akceptacji wniosku", ""),
        ("declaration_confirmation", "Potwierdzenie podpisanej deklaracji", ""),
        ("agreement_confirmation", "Potwierdzenie podpisania umowy przez urząd", ""),
        ("correction_required", "Wymagana korekta", ""),
        ("application_rejection", "Odrzucenie wniosku", ""),
        ("agreement_rejection", "Skierowanie umowy do poprawy", ""),
    )
    known_ids = {item[0] for item in definitions}
    definitions = (*definitions, *(
        (decision_id, str(item.get("label") or decision_id), str(item.get("step_id") or ""))
        for decision_id, item in existing_by_id.items()
        if decision_id and decision_id not in known_ids
    ))
    result = []
    for decision_id, label, default_step in definitions:
        current = existing_by_id.get(decision_id, {})
        result.append(
            {
                **current,
                "id": decision_id,
                "label": _modern_decision_label(
                    form_data.get(f"decision_{decision_id}_label", current.get("label", label)).strip() or label
                ),
                "step_id": form_data.get(f"decision_{decision_id}_step", current.get("step_id", default_step)).strip(),
                "assigned_stage": form_data.get(f"decision_{decision_id}_step", current.get("step_id", default_step)).strip(),
                "values": ["accepted", "rejected", "correction"],
                "yes_status": form_data.get(
                    f"decision_{decision_id}_yes_status", current.get("yes_status", current.get("status_on_yes", ""))
                ).strip(),
                "no_status": form_data.get(
                    f"decision_{decision_id}_no_status", current.get("no_status", current.get("status_on_no", ""))
                ).strip(),
                "correction_status": form_data.get(
                    f"decision_{decision_id}_correction_status",
                    current.get("correction_status", current.get("status_on_correction", "")),
                ).strip(),
                "reason_required": form_data.get(f"decision_{decision_id}_reason_required") == "on",
                "require_reason": form_data.get(f"decision_{decision_id}_reason_required") == "on",
                "send_email": form_data.get(f"decision_{decision_id}_send_email") == "on",
                "user_message": form_data.get(
                    f"decision_{decision_id}_user_message", current.get("user_message", "")
                ).strip(),
                "system_action": form_data.get(
                    f"decision_{decision_id}_system_action", current.get("system_action", "")
                ).strip(),
                "active": (
                    form_data.get(f"decision_{decision_id}_active") == "on"
                    if f"decision_{decision_id}_active" in form_data
                    else bool(current.get("active", current.get("step_id", default_step)))
                ),
            }
        )
    return result


def _workflow_email_notifications(form_data, existing: list[dict]) -> list[dict]:
    existing_by_id = {str(item.get("id") or ""): dict(item) for item in existing if isinstance(item, dict)}
    events = (
        ("application_accepted", "Po akceptacji wniosku"),
        ("application_rejected", "Po odrzuceniu wniosku"),
        ("correction_required", "Po wymaganiu korekty"),
        ("declaration_uploaded", "Po wgraniu deklaracji"),
        ("beneficiary_agreement_confirmed", "Po podpisaniu umowy przez urząd"),
        ("beneficiary_agreement_rejected", "Po skierowaniu umowy do poprawy"),
    )
    result = []
    for event_id, label in events:
        current = existing_by_id.get(event_id, {})
        result.append(
            {
                **current,
                "id": event_id,
                "label": label,
                "enabled": form_data.get(f"notification_{event_id}_enabled") == "on",
                "template_type": form_data.get(
                    f"notification_{event_id}_template",
                    current.get("template_type", event_id),
                ).strip(),
                "manual_confirmation": form_data.get(f"notification_{event_id}_manual") == "on",
                "automatic": form_data.get(f"notification_{event_id}_automatic") == "on",
            }
        )
    return result


def _modern_decision_label(value: str) -> str:
    return {
        "Potwierdzenie podpisanej umowy przez beneficjenta": "Potwierdzenie podpisania umowy przez urząd",
        "Umowa podpisana przez beneficjenta": "Umowa podpisana przez urząd",
        "Odrzucenie podpisanej umowy": "Skierowanie umowy do poprawy",
    }.get(value, value)


def apply_training_selection_from_admin_form(definition: dict, form_data) -> dict:
    enabled = form_data.get("training_selection_enabled") == "on"
    documents = [dict(document) for document in definition.get("documents") or [] if isinstance(document, dict)]
    declaration = next((document for document in documents if document.get("id") == "declaration"), None)
    if declaration is None and not enabled:
        definition["documents"] = documents
        return definition
    if declaration is None:
        declaration = {
            "id": "declaration",
            "label": "Deklaracja",
            "kind": "generated_pdf",
            "enabled": True,
            "signature_required": True,
            "fields": [],
        }
        documents.append(declaration)

    current_fields = [dict(field) for field in declaration.get("fields") or [] if isinstance(field, dict)]
    existing_training_index = next(
        (index for index, field in enumerate(current_fields) if field.get("type") == "training_selection"),
        None,
    )
    fields = [field for field in current_fields if field.get("type") != "training_selection"]

    if enabled:
        training_field = {
            "type": "training_selection",
            "name": form_data.get("training_selection_name", "selected_trainings").strip() or "selected_trainings",
            "label": form_data.get("training_selection_label", "Wybierz szkolenia").strip() or "Wybierz szkolenia",
            "required": form_data.get("training_selection_required") == "on",
            "currency": form_data.get("training_selection_currency", "PLN").strip() or "PLN",
            "catalog": parse_training_catalog(form_data),
        }
        max_total = decimal_price_to_storage(form_data.get("training_selection_max_total"))
        if max_total is not None:
            training_field["max_total_amount"] = max_total
        insert_at = training_section_insert_index(fields)
        if insert_at is None:
            insert_at = min(existing_training_index, len(fields)) if existing_training_index is not None else len(fields)
        fields.insert(insert_at, training_field)

    declaration["fields"] = fields
    definition["documents"] = documents
    return definition


def parse_training_catalog(form_data) -> list[dict]:
    catalog = []
    item_ids = form_data.getlist("training_item_id")
    names = form_data.getlist("training_item_name")
    prices = form_data.getlist("training_item_price")
    capacities = form_data.getlist("training_item_capacity")
    descriptions = form_data.getlist("training_item_description")
    admin_comments = form_data.getlist("training_item_admin_comment")
    low_comments = form_data.getlist("training_item_low_seats_comment")
    dates_by_training = parse_training_dates_from_form(form_data, len(names))
    active_values = form_data.getlist("training_item_active")
    active_indexes = {int(item) for item in active_values if str(item).isdigit()}
    default_active = form_data.get("training_active_present") != "1" and not active_values
    sort_orders = form_data.getlist("training_item_sort_order")
    for index, name in enumerate(names):
        clean_name = str(name or "").strip()
        if not clean_name:
            if default_active or index in active_indexes:
                raise ValueError(
                    f"Aktywne szkolenie {index + 1} musi mieć nazwę."
                )
            continue
        item_id = str(item_ids[index] if index < len(item_ids) else "").strip()
        training_id = item_id or f"trn_{uuid4().hex}"
        capacity = parse_required_capacity(capacities[index] if index < len(capacities) else "")
        item = {
            "id": training_id,
            "name": clean_name,
            "price": decimal_price_to_storage(
                prices[index] if index < len(prices) else ""
            ),
            "capacity": capacity,
            "description": str(
                descriptions[index] if index < len(descriptions) else ""
            ).strip(),
            "low_seats_comment": str(
                low_comments[index] if index < len(low_comments) else ""
            ).strip(),
            "dates": dates_by_training[index],
            "active": default_active or index in active_indexes,
            "sort_order": parse_optional_int_value(
                sort_orders[index] if index < len(sort_orders) else "",
                index + 1,
            ),
        }
        admin_comment = str(
            admin_comments[index] if index < len(admin_comments) else ""
        ).strip()
        if admin_comment:
            item["admin_comment"] = admin_comment
        catalog.append(item)
    return sorted(catalog, key=lambda item: (item["sort_order"], item["name"].lower()))


def parse_optional_int_value(value: Any, fallback: int) -> int:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError("Kolejność szkolenia musi być liczbą całkowitą.") from exc
    if parsed < 0:
        raise ValueError("Kolejność szkolenia nie może być mniejsza niż 0.")
    return parsed


def parse_training_dates_from_form(form_data, training_count: int) -> list[list[dict]]:
    dates_by_training: list[list[dict]] = [[] for _ in range(training_count)]
    training_indexes = form_data.getlist("training_date_training_index")
    if training_indexes:
        start_dates = form_data.getlist("training_date_start_date")
        end_dates = form_data.getlist("training_date_end_date")
        start_times = form_data.getlist("training_date_start_time")
        end_times = form_data.getlist("training_date_end_time")
        locations = form_data.getlist("training_date_location")
        descriptions = form_data.getlist("training_date_description")
        for row_index, training_index_value in enumerate(training_indexes):
            try:
                training_index = int(str(training_index_value).strip())
            except ValueError as exc:
                raise ValueError("Nie można przypisać terminu do szkolenia.") from exc
            if training_index < 0 or training_index >= training_count:
                raise ValueError("Nie można przypisać terminu do szkolenia.")

            start_date = _form_list_value(start_dates, row_index)
            end_date = _form_list_value(end_dates, row_index)
            start_time = _form_list_value(start_times, row_index)
            end_time = _form_list_value(end_times, row_index)
            location = _form_list_value(locations, row_index)
            description = _form_list_value(descriptions, row_index)
            if not any((start_date, end_date, start_time, end_time, location, description)):
                continue
            validate_training_date_range(
                start_date,
                end_date,
                start_time,
                end_time,
                row_index + 1,
            )
            dates_by_training[training_index].append(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": location,
                    "description": description,
                }
            )
        for dates in dates_by_training:
            dates.sort(key=lambda item: (item["start_date"], item.get("start_time") or ""))
        return dates_by_training

    legacy_values = form_data.getlist("training_item_dates")
    for index in range(training_count):
        legacy_value = legacy_values[index] if index < len(legacy_values) else ""
        dates_by_training[index] = parse_training_dates_text(legacy_value)
    return dates_by_training


def _form_list_value(values: list[Any], index: int) -> str:
    return str(values[index] if index < len(values) else "").strip()


def parse_required_capacity(value: Any) -> int:
    text = str(value or "").strip()
    if text == "":
        raise ValueError("Liczba miejsc szkolenia jest wymagana.")
    try:
        capacity = int(text)
    except ValueError as exc:
        raise ValueError("Liczba miejsc szkolenia musi być liczbą całkowitą.") from exc
    if capacity < 0:
        raise ValueError("Liczba miejsc szkolenia nie może być mniejsza niż 0.")
    return capacity


def parse_training_dates_text(value: Any) -> list[dict]:
    dates = []
    for line_no, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        start_date = parts[0] if len(parts) > 0 else ""
        end_date = parts[1] if len(parts) > 1 else ""
        start_time = parts[2] if len(parts) > 2 else ""
        end_time = parts[3] if len(parts) > 3 else ""
        location = parts[4] if len(parts) > 4 else ""
        description = parts[5] if len(parts) > 5 else ""
        validate_training_date_range(start_date, end_date, start_time, end_time, line_no)
        dates.append(
            {
                "start_date": start_date,
                "end_date": end_date,
                "start_time": start_time,
                "end_time": end_time,
                "location": location,
                "description": description,
            }
        )
    return sorted(dates, key=lambda item: (item["start_date"], item.get("start_time") or ""))


def validate_training_date_range(start_date: str, end_date: str, start_time: str, end_time: str, line_no: int) -> None:
    if not start_date:
        raise ValueError(f"Termin szkolenia w wierszu {line_no} musi mieć datę rozpoczęcia.")
    try:
        parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Data rozpoczęcia terminu w wierszu {line_no} musi mieć format RRRR-MM-DD.") from exc
    parsed_end = parsed_start
    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"Data zakończenia terminu w wierszu {line_no} musi mieć format RRRR-MM-DD.") from exc
    if parsed_end < parsed_start:
        raise ValueError("Data zakończenia szkolenia nie może być wcześniejsza niż data rozpoczęcia.")
    if start_time:
        validate_time(start_time, "Godzina rozpoczęcia", line_no)
    if end_time:
        validate_time(end_time, "Godzina zakończenia", line_no)
    if parsed_end == parsed_start and start_time and end_time and end_time < start_time:
        raise ValueError("Godzina zakończenia szkolenia nie może być wcześniejsza niż godzina rozpoczęcia.")


def validate_time(value: str, label: str, line_no: int) -> None:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{label} terminu w wierszu {line_no} musi mieć format GG:MM.") from exc


def slugify_training_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return slug or "szkolenie"


def parse_workflow_json(raw_value: str, fallback: dict) -> dict:
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return dict(fallback or {})
    parsed = json.loads(raw_value)
    if not isinstance(parsed, dict):
        raise ValueError("workflow must be an object")
    return parsed


def build_definition_from_html(html: str, filename: str) -> dict:
    fields: list[dict] = []
    errors: list[str] = []
    seen_names: set[str] = set()
    input_pattern = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
    for index, (tag, attrs) in enumerate(input_pattern.findall(html), start=1):
        name = html_attr(attrs, "name")
        field_type = (html_attr(attrs, "type") or "text").lower() if tag.lower() == "input" else tag.lower()
        if field_type in {"submit", "button", "reset", "image"}:
            continue
        if not name:
            if field_type != "hidden":
                errors.append(f"Kontrolka HTML nr {index} nie ma atrybutu 'name'.")
            continue
        if name.startswith("_") or name == "csrf_token":
            continue
        if name in seen_names:
            errors.append(f"Duplikat pola HTML o nazwie '{name}'.")
            continue
        seen_names.add(name)
        if field_type == "hidden":
            continue
        if field_type not in SUPPORTED_FIELD_TYPES:
            errors.append(f"Pole '{name}' ma nieobsługiwany typ HTML '{field_type}'.")
            continue
        fields.append(
            {
                "type": field_type,
                "name": name,
                "label": humanize_field_name(name),
                "required": True,
            }
        )
    if errors:
        raise ValueError("Nie można zaimportować HTML: " + " ".join(errors))
    if not fields:
        raise ValueError(
            "Nie wykryto pól formularza w HTML. Dodaj kontrolki input, select lub textarea z atrybutem 'name'."
        )
    return {"title": Path(filename).stem, "fields": fields}


def build_definition_from_docx(content: bytes, filename: str) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("Plik DOCX jest uszkodzony albo nie zawiera dokumentu Word.") from exc

    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in (item for item in root.iter() if item.tag.endswith("}p")):
        text = "".join(
            child.text or ""
            for child in paragraph.iter()
            if child.tag.endswith("}t") or child.tag.endswith("}tab")
        ).strip()
        if text:
            paragraphs.append(text)
    raw = "\n".join(paragraphs)
    fields_by_name: dict[str, dict] = {}

    def add_field(label: str, *, field_type: str = "text", options: list[dict] | None = None) -> None:
        clean_label = re.sub(r"\s+", " ", label).strip(" :-_\t")
        if not clean_label:
            return
        name = _docx_field_name(clean_label)
        if not name or name in fields_by_name:
            return
        field = {"type": field_type, "name": name, "label": clean_label, "required": True}
        if options:
            field["options"] = options
        fields_by_name[name] = field

    for name in re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", raw):
        fields_by_name.setdefault(
            name,
            {"type": "text", "name": name, "label": humanize_field_name(name), "required": True},
        )

    checkbox_group: list[str] = []
    for line in paragraphs:
        without_placeholders = re.sub(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}", "", line).strip()
        checkbox_match = re.match(r"^(?:☐|□|\[\s*[ xX]?\s*\])\s*(.+)$", without_placeholders)
        if checkbox_match:
            checkbox_group.append(checkbox_match.group(1).strip())
            continue
        if checkbox_group:
            label = "Wybór"
            options = [{"value": _docx_field_name(item), "label": item} for item in checkbox_group]
            add_field(label, field_type="checkbox", options=options)
            checkbox_group = []

        label_match = re.match(
            r"^(.{2,120}?)(?::\s*(?:_{3,}|\.{3,}|$)|\s+(?:_{3,}|\.{3,})$)",
            without_placeholders,
        )
        if label_match:
            add_field(label_match.group(1))
    if checkbox_group:
        options = [{"value": _docx_field_name(item), "label": item} for item in checkbox_group]
        add_field("Wybór", field_type="checkbox", options=options)

    fields = list(fields_by_name.values())
    if not fields:
        raise ValueError(
            "Nie wykryto pól w DOCX. Oznacz pola jako {{ nazwa_pola }} albo użyj etykiety "
            "z dwukropkiem i miejscem do wpisania, np. „Imię: ______”."
        )
    return {"title": Path(filename).stem, "fields": fields}


def _docx_field_name(label: str) -> str:
    ascii_label = "".join(
        char for char in unicodedata.normalize("NFKD", str(label).casefold()) if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_label).strip("_")[:80]


def html_attr(attrs: str, name: str) -> str:
    match = re.search(rf'\b{name}\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def humanize_field_name(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def sync_form_fields(db, form: Form, form_definition: dict) -> None:
    existing_fields = {field.name: field for field in form.fields}
    for field in existing_fields.values():
        field.active = False
    current_section = ""
    order = 0
    for field in detect_form_fields(form_definition):
        if field.get("type") == "section":
            current_section = field.get("label", "")
            continue
        name = field.get("name")
        if not name:
            continue
        form_field = existing_fields.get(name) or FormField(form_id=form.id, name=name)
        form_field.label = field.get("label", name)
        form_field.type = field.get("type", "text")
        form_field.required = bool(field.get("required"))
        form_field.options = field.get("options") or []
        form_field.default_value = str(field.get("default", ""))
        form_field.section = current_section
        normalized_field = FieldAvailabilityService().normalize_field(field, form_definition)
        form_field.availability_json = normalized_field.get("availability") or []
        form_field.stage = normalize_field_stage(field.get("stage"))
        form_field.sort_order = order
        form_field.active = True
        db.add(form_field)
        order += 1


def detect_form_fields(form_definition: dict) -> list[dict]:
    fields = list(form_definition.get("fields") or [])
    documents = ((form_definition.get("process") or {}).get("documents") or form_definition.get("documents") or {})
    if isinstance(documents, dict):
        for document in documents.values():
            if isinstance(document, dict):
                fields.extend(document.get("fields") or [])
    return fields


def normalize_field_stage(value: Any) -> str:
    stage = str(value or "").strip()
    return stage if stage in SUPPORTED_FIELD_STAGES else FIELD_STAGE_INITIAL


def form_has_additional_fields(form: Form) -> bool:
    return has_additional_fields_after_acceptance(normalize_admin_form_definition(form.definition_json or {}))
