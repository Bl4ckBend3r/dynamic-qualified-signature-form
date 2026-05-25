from __future__ import annotations

import string
from pathlib import Path

from form_loader import SUPPORTED_FIELD_TYPES


ALLOWED_FILENAME_PLACEHOLDERS = {
    "first_name",
    "last_name",
    "participant_name",
    "submission_id",
    "training_id",
    "agreement_sequence",
    "generated_date",
}


class FormConfigValidator:
    def __init__(self, template_root: str | Path | None = None) -> None:
        self.template_root = Path(template_root) if template_root else Path.cwd() / "templates"

    def validate(self, form_config: dict) -> list[str]:
        errors: list[str] = []
        if not str(form_config.get("title") or "").strip():
            errors.append("title is required")
        if not isinstance(form_config.get("fields"), list):
            errors.append("fields must be a list")
        else:
            self._validate_fields(form_config["fields"], errors)
        documents = form_config.get("documents") or []
        document_ids = self._validate_documents(documents, errors)
        self._validate_workflow(form_config.get("workflow") or {}, document_ids, errors)
        self._validate_notifications(form_config.get("notifications") or [], errors)
        return errors

    def _validate_fields(self, fields: list[dict], errors: list[str]) -> None:
        for index, field in enumerate(fields):
            field_type = field.get("type")
            if field_type not in SUPPORTED_FIELD_TYPES:
                errors.append(f"fields[{index}].type is unsupported: {field_type}")
            if field_type not in {"section", "static_text"} and not field.get("name"):
                errors.append(f"fields[{index}].name is required")

    def _validate_documents(self, documents: list[dict], errors: list[str]) -> set[str]:
        ids = set()
        for index, document in enumerate(documents):
            document_id = str(document.get("id") or "").strip()
            if not document_id:
                errors.append(f"documents[{index}].id is required")
            else:
                ids.add(document_id)
            kind = document.get("kind")
            if not kind:
                errors.append(f"documents[{index}].kind is required")
            if kind == "generated_pdf" and not document.get("template"):
                errors.append(f"documents[{index}].template is required for generated_pdf")
            self._validate_filename_pattern(document.get("filename_pattern", ""), f"documents[{index}].filename_pattern", errors)
        return ids

    def _validate_workflow(self, workflow: dict, document_ids: set[str], errors: list[str]) -> None:
        steps = workflow.get("steps") or []
        step_ids = {step.get("id") for step in steps}
        for index, step in enumerate(steps):
            for key in ("next",):
                target = step.get(key)
                if target and target not in step_ids:
                    errors.append(f"workflow.steps[{index}].{key} references unknown step: {target}")
            for decision, target in (step.get("decisions") or {}).items():
                if target not in step_ids:
                    errors.append(f"workflow.steps[{index}].decisions.{decision} references unknown step: {target}")
            document_id = step.get("document_id")
            if document_id and document_id not in document_ids:
                errors.append(f"workflow.steps[{index}].document_id references unknown document: {document_id}")

    def _validate_notifications(self, notifications: list[dict], errors: list[str]) -> None:
        for index, notification in enumerate(notifications):
            template = notification.get("template")
            if template and not (self.template_root / template).exists():
                errors.append(f"notifications[{index}].template does not exist: {template}")

    def _validate_filename_pattern(self, pattern: str, path: str, errors: list[str]) -> None:
        if not pattern:
            return
        formatter = string.Formatter()
        for _, field_name, _, _ in formatter.parse(pattern):
            if field_name and field_name not in ALLOWED_FILENAME_PLACEHOLDERS:
                errors.append(f"{path} contains unsupported placeholder: {field_name}")
