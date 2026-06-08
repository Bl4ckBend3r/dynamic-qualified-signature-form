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
from services.form_config_service import FormConfigService
from validators.form_config_validator import FormConfigValidator


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
    return normalize_admin_form_definition(definition)


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
