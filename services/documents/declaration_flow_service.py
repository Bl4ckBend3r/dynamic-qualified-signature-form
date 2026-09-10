from __future__ import annotations

import json
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
from services.compliance_service import ComplianceError
from services.field_availability_service import FieldAvailabilityService
from services.process_service import ProcessStatus
from services.training_agreement_service import get_training_selection_field
from services.training_availability_service import TrainingAvailabilityService
from services.training_service import (
    format_price_pln,
    is_training_section_label,
    normalize_trainings_config,
    without_training_selection_section,
)


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
    def document_fields_definition(document: Mapping[str, Any]) -> dict:
        """Use the shared field renderer/validator without moving document fields."""
        from form_loader import normalize_form_definition
        definition = normalize_form_definition({
            "title": document.get("form_title") or document.get("label") or "Dokument",
            "description": document.get("form_description") or document.get("description") or "",
            "fields": list(document.get("fields") or []),
        })
        definition["fields"] = normalize_training_fields(definition["fields"])
        definition["submit_label"] = document.get("form_submit_label") or "Wygeneruj deklarację PDF"
        return definition

    def validate_document_fields(self, document, row, form_data, submission_repository):
        from services.training_service import selected_training_snapshots, normalize_training_catalog
        document = self.operational_document(document, row, submission_repository)
        definition = self.document_fields_definition(document)
        data = apply_pesel_derived_values(definition, extract_submission_data(definition, form_data))
        saved = {field["name"]: row["data_json"][field["name"]]
            for field in definition["fields"] if field.get("name") in (row.get("data_json") or {})}
        values = {**row, **saved, **data}
        errors = validate_submission(definition, values)
        for field in definition["fields"]:
            if field.get("type") != "training_selection" or field.get("readonly") or field.get("hidden"):
                continue
            availability = self._training_availability(submission=row, training_field=field, submission_repository=submission_repository)
            selected, error = selected_training_snapshots(field, form_data, availability)
            requested = set(form_data.getlist(field["name"]))
            if requested - {item["id"] for item in normalize_training_catalog(field)}:
                error = "Wybrano niedostępne szkolenie."
            if error:
                errors[field["name"]] = error
            data[field["name"]] = serialize_json_list(selected)
            values[field["name"]] = data[field["name"]]
        return DeclarationFlowResult(success=not errors, errors=errors, values=values,
            declaration_definition=definition, generated=None), data

    @staticmethod
    def operational_document(document, row, repository):
        """Refresh only the operational catalog; keep document/version rules."""
        from copy import deepcopy
        from sqlalchemy import select
        from models import Form
        from services.submission_training_service import SubmissionTrainingService
        if not hasattr(repository, "session_factory") or not any(
            field.get("type") == "training_selection" for field in document.get("fields") or []
        ):
            return document
        document = deepcopy(document)
        with repository.session_factory() as db:
            form = db.execute(select(Form).where(Form.slug == row["form_slug"])).scalar_one()
            document["fields"] = [
                SubmissionTrainingService.selection_field(form, {"id": "declaration", "fields": [field]})
                if field.get("type") == "training_selection" else field
                for field in document.get("fields") or []]
        return document

    def save_document_training_selection(self, document, row, form_data, services):
        from sqlalchemy import select
        from models import Form, FormSubmission
        fields = [f for f in document.get("fields") or [] if f.get("type") == "training_selection"
            and not f.get("readonly") and not f.get("hidden")]
        if not fields:
            return
        with services.submission_repository.session_factory() as db:
            submission = db.execute(select(FormSubmission).where(FormSubmission.submission_id == row["submission_id"])).scalar_one()
            form = db.execute(select(Form).where(Form.slug == submission.form_slug).with_for_update()).scalar_one()
            db.refresh(submission, with_for_update=True)
            for field in fields:
                field = services.submission_training_service.selection_field(form, {"id": "declaration", "fields": [field]})
                if not services.submission_training_service.can_select(form, submission, field):
                    from services.training_service import parse_training_snapshots
                    saved_ids = {item["id"] for item in parse_training_snapshots(submission.selected_trainings)}
                    if saved_ids == set(form_data.getlist(field["name"])):
                        continue
                    from services.submission_training_service import TrainingSelectionError
                    raise TrainingSelectionError("Wybór szkoleń jest zamknięty.")
                services.submission_training_service.save(db, submission, field, form_data.getlist(field["name"]),
                    availability=self._training_availability(submission=row, training_field=field, submission_repository=services.submission_repository),
                    advance_workflow=False)
            db.commit()

    @staticmethod
    def build_declaration_form_definition(
        declaration_config: Mapping[str, Any],
        form_config: Mapping[str, Any] | None = None,
        step: str | None = None,
        availability: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict:
        legacy_fields = list(declaration_config.get("fields") or [])
        workflow_fields: list[dict] = []
        if form_config:
            selected_step = step or FieldAvailabilityService().legacy_after_acceptance_step(form_config)
            workflow_fields = FieldAvailabilityService().visible_fields(form_config, selected_step)
        names = {str(field.get("name")) for field in workflow_fields if field.get("name")}
        return {
            "title": declaration_config.get("form_title") or "Uzupelnienie deklaracji uczestnictwa",
            "description": declaration_config.get("form_description") or "",
            "submit_label": declaration_config.get("form_submit_label") or "Wygeneruj deklaracje PDF",
            "fields": without_training_selection_section(
                workflow_fields + [field for field in legacy_fields if not field.get("name") or str(field.get("name")) not in names]
            ),
        }

    @staticmethod
    def build_additional_fields_definition(form_config: dict, step: str | None = None) -> dict:
        definition = form_definition_for_stage(form_config, step or FIELD_STAGE_AFTER_ACCEPTANCE)
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
        step = self._row_availability_step(form_config, row)
        return self.has_fields_for_step(form_config, step) and not self.additional_fields_completed(row)

    @staticmethod
    def _row_availability_step(form_config: dict, row: Mapping[str, Any]) -> str:
        service = FieldAvailabilityService()
        configured = set(service.step_ids(form_config))
        for key in ("workflow_stage", "workflow_step", "current_stage"):
            value = str(row.get(key) or "").strip()
            if value in configured:
                return value
        return service.legacy_after_acceptance_step(form_config)

    @staticmethod
    def has_fields_for_step(form_config: dict, step: str) -> bool:
        return any(
            field.get("type") not in {"section", "static_text"}
            for field in FieldAvailabilityService().visible_fields(form_config, step)
        )

    def prepare_declaration_form(
        self,
        *,
        submission: dict,
        form_config: dict,
        declaration_config: Mapping[str, Any],
        submission_repository=None,
    ) -> DeclarationFlowResult:
        step = self._row_availability_step(form_config, submission["row"])
        declaration_definition = self.build_declaration_form_definition(declaration_config, form_config, step)
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
        compliance_service=None,
    ) -> DeclarationFlowResult:
        step = self._row_availability_step(form_config, submission["row"])
        declaration_definition = self.build_declaration_form_definition(declaration_config, form_config, step)
        declaration_data = extract_submission_data(declaration_definition, form_data)
        declaration_data = apply_pesel_derived_values(declaration_definition, declaration_data)
        values = {**submission["row"], **declaration_data}
        errors = validate_submission(declaration_definition, values)

        if errors:
            return DeclarationFlowResult(
                success=False,
                message="Deklaracja zawiera bledy. Popraw wskazane pola.",
                errors=errors,
                values=values,
                declaration_definition=declaration_definition,
                error_code="validation_error",
            )

        if compliance_service is not None:
            try:
                compliance_service.record_submission_acceptances(
                    submission_id=submission_id,
                    form_version_id=submission["row"].get("form_version_id"),
                    submission_data=declaration_data,
                    step=step,
                )
            except ComplianceError as exc:
                return DeclarationFlowResult(
                    success=False,
                    message=str(exc),
                    errors={"compliance": str(exc)},
                    values=values,
                    declaration_definition=declaration_definition,
                    error_code="compliance_error",
                )

        rule_updates = rules_service.apply_rules(submission["row"], form_config, declaration_data)
        if (form_config.get("workflow") or {}).get("flow_mode") == "explicit":
            rule_updates = {k: v for k, v in rule_updates.items() if k not in {"process_status", "workflow_step", "workflow_stage"}}
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
                context_extra={**updates, "selected_trainings": [], "selected_trainings_normalized": []},
                force=True,
            )
        return DeclarationFlowResult(
            success=True,
            message="Deklaracja zostala wygenerowana.",
            values=values,
            declaration_definition=declaration_definition,
            generated=generated,
        )

    def _training_availability(
        self,
        *,
        submission: dict,
        training_field: Mapping[str, Any] | None,
        submission_repository=None,
    ) -> dict[str, dict]:
        if not training_field or not submission_repository:
            return {}
        return TrainingAvailabilityService(submission_repository).availability_for_field(
            form_slug=str(submission.get("form_slug") or ""),
            field=training_field,
            current_submission_id=str(submission.get("submission_id") or ""),
        )

    def save_additional_fields(
        self,
        *,
        submission_id: str,
        submission: dict,
        form_config: dict,
        form_data,
        submission_repository,
        compliance_service=None,
    ) -> DeclarationFlowResult:
        step = self._row_availability_step(form_config, submission["row"])
        additional_definition = self.build_additional_fields_definition(form_config, step)
        additional_data = extract_submission_data(additional_definition, form_data)
        additional_data = apply_pesel_derived_values(additional_definition, additional_data)
        values = {**submission["row"], **additional_data}
        errors = validate_submission(additional_definition, values)
        if errors:
            return DeclarationFlowResult(
                success=False,
                message="Dodatkowe informacje zawieraja bledy. Popraw wskazane pola.",
                errors=errors,
                values=values,
                declaration_definition=additional_definition,
                error_code="validation_error",
            )

        if compliance_service is not None:
            try:
                compliance_service.record_submission_acceptances(
                    submission_id=submission_id,
                    form_version_id=submission["row"].get("form_version_id"),
                    submission_data=additional_data,
                    step=step,
                )
            except ComplianceError as exc:
                return DeclarationFlowResult(
                    success=False,
                    message=str(exc),
                    errors={"compliance": str(exc)},
                    values=values,
                    declaration_definition=additional_definition,
                    error_code="compliance_error",
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
        if (form_config.get("workflow") or {}).get("flow_mode") == "explicit":
            updates.pop("process_status", None)
            updates.pop("workflow_step", None)
        submission_repository.update(submission_id, updates)
        if hasattr(submission_repository, "record_workflow_event"):
            submission_repository.record_workflow_event(
                submission_id,
                {
                    "previous_status": str(submission["row"].get("process_status") or ""),
                    "new_status": updates.get("process_status") or str(submission["row"].get("process_status") or ""),
                    "previous_step": step,
                    "new_step": step,
                    "actor_role": "participant",
                    "reason": "workflow_fields_updated",
                    "source": "workflow_fields_updated",
                    # Audit field names only; values may contain sensitive data.
                    "side_effects": {"changed_fields": sorted(additional_data)},
                },
            )
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


def normalize_training_fields(
    fields: list[dict],
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict]:
    normalized = []
    availability = availability or {}
    for field in fields:
        if field.get("type") == "training_selection":
            field = dict(field)
            field["catalog"] = [
                {**item, **dict(availability.get(item["id"], {}))}
                for item in normalize_trainings_config(field, active_only=True)
            ]
            if field.get("max_total_amount") not in (None, ""):
                field["max_total_formatted"] = format_price_pln(
                    field.get("max_total_amount"),
                    field.get("currency"),
                )
        normalized.append(field)
    return normalized
