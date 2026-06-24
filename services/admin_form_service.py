from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from form_loader import (
    FIELD_STAGE_INITIAL,
    SUPPORTED_FIELD_STAGES,
    has_additional_fields_after_acceptance,
    normalize_form_definition,
    validate_form_definition,
)
from models import Form, FormField
from services.documents.declaration_flow_service import training_section_insert_index
from services.form_config_service import FormConfigService
from validators.form_config_validator import FormConfigValidator


def get_declaration_training_field(form_definition: dict) -> dict:
    definition = normalize_admin_form_definition(form_definition or {})
    for document in definition.get("documents") or []:
        if not isinstance(document, dict) or document.get("id") != "declaration":
            continue
        for field in document.get("fields") or []:
            if isinstance(field, dict) and field.get("type") == "training_selection":
                return {"enabled": True, **dict(field)}
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


def validate_admin_form_config(form_definition: dict) -> list[str]:
    try:
        validate_form_definition(form_definition)
    except Exception as exc:
        return [str(exc)]
    validator = FormConfigValidator(skip_template_check=True)
    return validator.validate(form_definition)


def build_form_definition_from_admin_form(current_definition: dict, form_data) -> dict:
    definition = normalize_admin_form_definition(current_definition or {})
    workflow = parse_workflow_json(form_data.get("workflow_json", ""), definition.get("workflow") or {})
    workflow["name"] = form_data.get("workflow_name", workflow.get("name", "")).strip() or "Workflow"
    workflow["initial_step"] = form_data.get("workflow_initial_step", workflow.get("initial_step", "")).strip()
    workflow["requires_declaration"] = form_data.get("requires_declaration") == "on"
    workflow["requires_contract"] = form_data.get("requires_contract") == "on"
    workflow["declaration_template_html"] = form_data.get("declaration_template_html", "").strip()
    workflow["contract_template_html"] = form_data.get("contract_template_html", "").strip()
    definition["workflow"] = workflow
    definition = apply_training_selection_from_admin_form(definition, form_data)
    return normalize_admin_form_definition(definition)


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
        max_total = parse_optional_float(form_data.get("training_selection_max_total"))
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
    for index, name in enumerate(names):
        clean_name = str(name or "").strip()
        if not clean_name:
            continue
        item_id = str(item_ids[index] if index < len(item_ids) else "").strip()
        price = parse_optional_float(prices[index] if index < len(prices) else "")
        catalog.append(
            {
                "id": item_id or slugify_training_id(clean_name),
                "name": clean_name,
                "price": price or 0,
            }
        )
    return catalog


def parse_optional_float(value: Any) -> float | int | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


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
    input_pattern = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
    for tag, attrs in input_pattern.findall(html):
        name = html_attr(attrs, "name")
        if not name or name.startswith("_") or name == "csrf_token":
            continue
        field_type = tag.lower()
        if tag.lower() == "input":
            field_type = html_attr(attrs, "type") or "text"
        fields.append(
            {
                "type": field_type,
                "name": name,
                "label": humanize_field_name(name),
                "required": "required" in attrs.lower(),
            }
        )
    if not fields:
        raise ValueError("no fields")
    return {"title": Path(filename).stem, "fields": fields}


def build_definition_from_docx(content: bytes, filename: str) -> dict:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts = [item.text or "" for item in root.iter() if item.tag.endswith("}t") and item.text]
    raw = "\n".join(texts)
    candidates = re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", raw)
    fields = [
        {"type": "text", "name": name, "label": humanize_field_name(name), "required": False}
        for name in dict.fromkeys(candidates)
    ]
    if not fields:
        raise ValueError("no fields")
    return {"title": Path(filename).stem, "fields": fields}


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
