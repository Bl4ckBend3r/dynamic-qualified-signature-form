from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from form_loader import (
    FIELD_STAGE_AFTER_ACCEPTANCE,
    apply_pesel_derived_values,
    extract_submission_data,
    form_definition_for_stage,
    has_additional_fields_after_acceptance,
    validate_submission,
)
from services.document_service import DocumentType, serialize_json_list
from services.process_service import ProcessStatus
from services.training_agreement_service import extract_training_selection, get_training_selection_field


@dataclass
class DeclarationFlowResult:
    success: bool
    message: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    declaration_definition: dict | None = None
    generated: dict | None = None
    error_code: str | None = None


class DeclarationFlowService:
    @staticmethod
    def build_declaration_form_definition(declaration_config: Mapping[str, Any]) -> dict:
        return {
            "title": declaration_config.get("form_title") or "Uzupelnienie deklaracji uczestnictwa",
            "description": declaration_config.get("form_description") or "",
            "submit_label": declaration_config.get("form_submit_label") or "Wygeneruj deklaracje PDF",
            "fields": fields_with_training_selection_in_training_section(declaration_config.get("fields") or []),
        }

    @staticmethod
    def build_additional_fields_definition(form_config: dict) -> dict:
        definition = form_definition_for_stage(form_config, FIELD_STAGE_AFTER_ACCEPTANCE)
        return {
            **definition,
            "title": "Dodatkowe informacje po akceptacji wniosku",
            "description": "Wniosek zostal zaakceptowany. Uzupelnij dodatkowe informacje, aby pobrac deklaracje.",
            "submit_label": "Zapisz dodatkowe informacje",
        }

    @staticmethod
    def additional_fields_completed(row: Mapping[str, Any]) -> bool:
        return str(row.get("additional_fields_completed") or "").strip().lower() == "tak"

    def requires_additional_fields(self, form_config: dict, row: Mapping[str, Any]) -> bool:
        return has_additional_fields_after_acceptance(form_config) and not self.additional_fields_completed(row)

    def prepare_declaration_form(self, *, submission: dict, form_config: dict, declaration_config: Mapping[str, Any]) -> DeclarationFlowResult:
        declaration_definition = self.build_declaration_form_definition(declaration_config)
        return DeclarationFlowResult(
            success=True,
            values=dict(submission["row"]),
            declaration_definition=declaration_definition,
        )

    def handle_declaration_post(
        self,
        *,
        submission_id: str,
        submission: dict,
        form_config: dict,
        declaration_config: Mapping[str, Any],
        form_data,
        rules_service,
        submission_repository,
        document_service,
        refresh_submission: Callable[[str], dict | None],
    ) -> DeclarationFlowResult:
        declaration_definition = self.build_declaration_form_definition(declaration_config)
        declaration_data = extract_submission_data(declaration_definition, form_data)
        declaration_data = apply_pesel_derived_values(declaration_definition, declaration_data)
        values = {**submission["row"], **declaration_data}
        errors = validate_submission(declaration_definition, declaration_data)
        training_field = get_training_selection_field(form_config)

        if training_field:
            selected_trainings, training_error = extract_training_selection(training_field, form_data)
            declaration_data["selected_trainings"] = serialize_json_list(selected_trainings)
            values["selected_trainings"] = declaration_data["selected_trainings"]
            if training_error:
                errors[training_field.get("name", "selected_trainings")] = training_error

        if errors:
            return DeclarationFlowResult(
                success=False,
                message="Deklaracja zawiera bledy. Popraw wskazane pola.",
                errors=errors,
                values=values,
                declaration_definition=declaration_definition,
                error_code="validation_error",
            )

        rule_updates = rules_service.apply_rules(submission["row"], form_config, declaration_data)
        updates = {**declaration_data, **rule_updates}
        submission_repository.update(submission_id, updates)
        refreshed_submission = refresh_submission(submission_id)
        generated = None
        if refreshed_submission:
            refreshed_submission["row"].update(updates)
            generated = document_service.generate_document(
                refreshed_submission,
                form_config,
                DocumentType.DECLARATION,
                context_extra=updates,
                force=True,
            )
        return DeclarationFlowResult(
            success=True,
            message="Deklaracja zostala wygenerowana.",
            values=values,
            declaration_definition=declaration_definition,
            generated=generated,
        )

    def save_additional_fields(
        self,
        *,
        submission_id: str,
        submission: dict,
        form_config: dict,
        form_data,
        submission_repository,
    ) -> DeclarationFlowResult:
        additional_definition = self.build_additional_fields_definition(form_config)
        additional_data = extract_submission_data(additional_definition, form_data)
        additional_data = apply_pesel_derived_values(additional_definition, additional_data)
        errors = validate_submission(additional_definition, additional_data)
        values = {**submission["row"], **additional_data}
        if errors:
            return DeclarationFlowResult(
                success=False,
                message="Dodatkowe informacje zawieraja bledy. Popraw wskazane pola.",
                errors=errors,
                values=values,
                declaration_definition=additional_definition,
                error_code="validation_error",
            )

        data_json = submission["row"].get("data_json") or {}
        if isinstance(data_json, str):
            try:
                data_json = json.loads(data_json)
            except json.JSONDecodeError:
                data_json = {}
        else:
            data_json = dict(data_json)
        data_json.update(additional_data)
        updates = {
            **additional_data,
            "data_json": data_json,
            "additional_fields_completed": "Tak",
            "process_status": ProcessStatus.ADDITIONAL_FIELDS_COMPLETED.value,
            "workflow_step": ProcessStatus.ADDITIONAL_FIELDS_COMPLETED.value,
        }
        submission_repository.update(submission_id, updates)
        return DeclarationFlowResult(
            success=True,
            message="Dodatkowe informacje zostaly zapisane. Mozesz pobrac deklaracje.",
            values=values,
        )

    @staticmethod
    def has_additional_fields(form_config: dict) -> bool:
        return has_additional_fields_after_acceptance(form_config)


def fields_with_training_selection_in_training_section(fields: list[dict]) -> list[dict]:
    normalized_fields = [dict(field) for field in fields if isinstance(field, Mapping)]
    training_fields = [field for field in normalized_fields if field.get("type") == "training_selection"]
    if not training_fields:
        return normalized_fields

    fields_without_training = [field for field in normalized_fields if field.get("type") != "training_selection"]
    insert_at = training_section_insert_index(fields_without_training)
    if insert_at is None:
        return normalized_fields
    return [
        *fields_without_training[:insert_at],
        *training_fields,
        *fields_without_training[insert_at:],
    ]


def training_section_insert_index(fields: list[dict]) -> int | None:
    for index, field in enumerate(fields):
        if field.get("type") == "section" and is_training_section_label(field.get("label")):
            return index + 1
    return None


def is_training_section_label(value: Any) -> bool:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    ascii_text = "".join(char for char in text if not unicodedata.combining(char))
    return "wybor" in ascii_text and "szkolen" in ascii_text
