from __future__ import annotations

import string
import re
from pathlib import Path

from form_loader import SUPPORTED_FIELD_STAGES, SUPPORTED_FIELD_TYPES
from services.field_availability_service import FieldAvailabilityService
from services.form_config_service import TRIGGER_DESCRIPTIONS
from services.qualification_condition_service import QualificationConditionService
from services.workflow_config_service import WorkflowConfigValidator, is_workflow_step_active
from services.upload_validation import SAFE_ATTACHMENT_EXTENSIONS, SAFE_ATTACHMENT_MIME_TYPES


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
    def __init__(self, template_root: str | Path | None = None, skip_template_check: bool = False) -> None:
        self.template_root = Path(template_root) if template_root else Path.cwd() / "templates"
        self.skip_template_check = skip_template_check

    def validate(self, form_config: dict) -> list[str]:
        errors: list[str] = []
        if not str(form_config.get("title") or "").strip():
            errors.append("title is required")
        if not isinstance(form_config.get("fields"), list):
            errors.append("fields must be a list")
        else:
            self._validate_fields(form_config["fields"], errors)
        documents = form_config.get("documents") or []
        if not isinstance(documents, list):
            errors.append("documents must be a list")
            documents = []
        document_ids = self._validate_documents(documents, errors)
        self._validate_workflow(form_config.get("workflow") or {}, document_ids, errors)
        if (form_config.get("workflow") or {}).get("flow_mode") == "explicit":
            errors.extend(WorkflowConfigValidator().validate(form_config["workflow"], form_config))
        errors.extend(FieldAvailabilityService().validate_config(form_config))
        self._validate_rules(form_config.get("rules") or [], errors)
        self._validate_notifications(form_config.get("notifications") or [], errors)
        errors.extend(
            QualificationConditionService().validate_config(
                form_config.get("qualification_conditions"),
                form_config.get("fields") or [],
            )
        )
        return errors

    def _validate_fields(self, fields: list[dict], errors: list[str]) -> None:
        names: set[str] = set()
        all_root_names = {
            str(field.get("name") or "").strip()
            for field in fields if isinstance(field, dict) and str(field.get("name") or "").strip()
        }
        for index, field in enumerate(fields):
            field_type = field.get("type")
            if field_type not in SUPPORTED_FIELD_TYPES:
                errors.append(f"fields[{index}].type is unsupported: {field_type}")
            field_name = str(field.get("name") or "").strip()
            if field_type not in {"section", "static_text"} and not field_name:
                errors.append(f"fields[{index}].name is required")
            if not field.get("availability") and field.get("stage", "initial_submission") not in SUPPORTED_FIELD_STAGES:
                errors.append(f"fields[{index}].stage is unsupported: {field.get('stage')}")
            if field_name:
                if field_name in names:
                    errors.append(f"fields[{index}].name is duplicated: {field_name}")
                names.add(field_name)
            if field_type in {"file", "attachment"}:
                self._validate_file_field(field, index, errors)
            if field_type == "repeatable_group":
                children = field.get("fields")
                confirmation = field.get('submission_confirmation')
                if confirmation is not None:
                    if not isinstance(confirmation, dict) or not isinstance(confirmation.get('enabled', False), bool):
                        errors.append(f"fields[{index}].submission_confirmation must contain a boolean enabled")
                    elif confirmation.get('enabled'):
                        email_field = confirmation.get('email_field') or field.get('decision_contact_email_field')
                        if not any(isinstance(child, dict) and child.get('name') == email_field and child.get('type') == 'email' for child in (children if isinstance(children, list) else [])):
                            errors.append(f"fields[{index}].submission_confirmation.email_field must reference an email field")
                if not isinstance(children, list):
                    errors.append(f"fields[{index}].fields must be a list")
                else:
                    self._validate_repeatable_children(children, all_root_names, index, errors)

        for index, field in enumerate(fields):
            condition = field.get("required_if")
            if condition:
                if not isinstance(condition, dict) or str(condition.get("field") or "") not in names:
                    errors.append(f"fields[{index}].required_if.field must reference an existing field")
                operators = {"equals", "not_equals", "in", "not_in", "is_empty", "is_not_empty"}
                operator = condition.get("operator") or next((key for key in operators if key in condition), None)
                if operator not in operators:
                    errors.append(f"fields[{index}].required_if operator is unsupported")

    @staticmethod
    def _validate_repeatable_children(children: list[dict], root_names: set[str], group_index: int, errors: list[str]) -> None:
        child_names = {str(item.get("name") or "") for item in children if isinstance(item, dict)}
        for child_index, child in enumerate(children):
            if not isinstance(child, dict):
                errors.append(f"fields[{group_index}].fields[{child_index}] must be an object")
                continue
            for condition_name in ("visible_if", "required_if"):
                condition = child.get(condition_name)
                if not condition:
                    continue
                if not isinstance(condition, dict):
                    errors.append(f"fields[{group_index}].fields[{child_index}].{condition_name} must be an object")
                    continue
                scope = str(condition.get("scope") or "current")
                if scope not in {"root", "current"}:
                    errors.append(f"fields[{group_index}].fields[{child_index}].{condition_name}.scope is unsupported")
                allowed_names = root_names if scope == "root" else child_names
                if str(condition.get("field") or "") not in allowed_names:
                    errors.append(f"fields[{group_index}].fields[{child_index}].{condition_name}.field references an unknown {scope} field")

    def _validate_file_field(self, field: dict, index: int, errors: list[str]) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", str(field.get("name") or "")):
            errors.append(f"fields[{index}].name is unsafe for attachment storage")
        extensions = field.get("allowed_extensions", ["pdf"])
        if not isinstance(extensions, list) or not extensions:
            errors.append(f"fields[{index}].allowed_extensions must be a non-empty list")
        else:
            unsafe = [str(item).lower().lstrip(".") for item in extensions if str(item).lower().lstrip(".") not in SAFE_ATTACHMENT_EXTENSIONS]
            if unsafe:
                errors.append(f"fields[{index}].allowed_extensions contains unsafe types: {', '.join(unsafe)}")
        mime_types = field.get("allowed_mime_types", [])
        if not isinstance(mime_types, list) or any(str(item).lower() not in SAFE_ATTACHMENT_MIME_TYPES for item in mime_types):
            errors.append(f"fields[{index}].allowed_mime_types contains unsupported types")
        try:
            size = float(field.get("max_size_mb", 10))
            if size <= 0 or size > 25:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"fields[{index}].max_size_mb must be between 0 and 25")
        try:
            count = int(field.get("max_files", 1))
            if count < 1 or count > 10:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"fields[{index}].max_files must be between 1 and 10")
        for key in ("document_type", "category"):
            value = str(field.get(key) or "")
            if value and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
                errors.append(f"fields[{index}].{key} contains unsupported characters")

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
            if kind == "generated_pdf" and not (document.get("template") or document.get("template_html") or document.get("template_source") == "builder" and document.get("builder_document")):
                errors.append(f"documents[{index}].template is required for generated_pdf")
            if kind == "generated_pdf" and document.get("template"):
                self._validate_template_exists(document.get("template"), f"documents[{index}].template", errors)
            if document.get("repeat_over") and not document.get("repeat_item_alias"):
                errors.append(f"documents[{index}].repeat_item_alias is required when repeat_over is set")
            self._validate_filename_pattern(document.get("filename_pattern", ""), f"documents[{index}].filename_pattern", errors)
        return ids

    def _validate_workflow(self, workflow: dict, document_ids: set[str], errors: list[str]) -> None:
        steps = workflow.get("steps") or []
        if not isinstance(steps, list):
            errors.append("workflow.steps must be a list")
            return
        if steps and not str(workflow.get("initial_step") or "").strip():
            errors.append("workflow.initial_step is required")
        step_ids: set[str] = set()
        for index, step in enumerate(steps):
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                errors.append(f"workflow.steps[{index}].id is required")
            elif step_id in step_ids:
                errors.append(f"workflow.steps[{index}].id is duplicated: {step_id}")
            step_ids.add(step_id)

        initial_step = str(workflow.get("initial_step") or "").strip()
        if initial_step and initial_step not in step_ids:
            errors.append(f"workflow.initial_step references unknown step: {initial_step}")

        active_steps = [step for step in steps if is_workflow_step_active(step, workflow)]
        known_triggers = set(TRIGGER_DESCRIPTIONS)
        for index, step in enumerate(steps):
            if not is_workflow_step_active(step, workflow):
                continue
            for key in ("next",):
                target = step.get(key)
                if target and target not in step_ids:
                    errors.append(f"workflow.steps[{index}].{key} references unknown step: {target}")
            for decision, target in (step.get("decisions") or {}).items():
                if target not in step_ids:
                    errors.append(f"workflow.steps[{index}].decisions.{decision} references unknown step: {target}")
            transitions = step.get("transitions") or []
            if transitions and not isinstance(transitions, list):
                errors.append(f"workflow.steps[{index}].transitions must be a list")
                transitions = []
            for transition_index, transition in enumerate(transitions):
                if not isinstance(transition, dict) or not isinstance(transition.get("when"), dict):
                    errors.append(f"workflow.steps[{index}].transitions[{transition_index}] requires when")
                    continue
                target = str(transition.get("next") or "")
                if target not in step_ids:
                    errors.append(f"workflow.steps[{index}].transitions[{transition_index}].next references unknown step: {target}")
            triggers = step.get("triggers") or []
            if isinstance(triggers, str):
                triggers = [triggers]
            if not isinstance(triggers, list):
                errors.append(f"workflow.steps[{index}].triggers must be a list")
                triggers = []
            for trigger in triggers:
                if trigger not in known_triggers:
                    errors.append(f"workflow.steps[{index}].triggers contains unsupported trigger: {trigger}")
            document_id = step.get("document_id")
            if document_id and document_id not in document_ids:
                errors.append(f"workflow.steps[{index}].document_id references unknown document: {document_id}")
            if step.get("repeat_over") and not step.get("document_id"):
                errors.append(f"workflow.steps[{index}].repeat_over requires document_id")
        if workflow.get("requires_declaration"):
            source = str(workflow.get("declaration_template_source") or "html")
            docx_metadata = workflow.get("declaration_docx_template") or {}
            builder_document = (
                workflow.get("declaration_builder_active_document")
                or workflow.get("declaration_builder_document")
                or {}
            )
            has_template = (
                bool(builder_document.get("blocks"))
                if source == "builder"
                else bool(docx_metadata.get("html") or docx_metadata.get("storage_path"))
                if source == "docx"
                else bool(str(workflow.get("declaration_template_html") or "").strip())
            )
            if not has_template:
                errors.append("workflow.declaration_template_html is required when declaration is required")
            if source == "docx" and docx_metadata.get("unknown_variables"):
                errors.append(
                    "Szablon DOCX deklaracji zawiera nieznane zmienne: "
                    + ", ".join(str(item) for item in docx_metadata["unknown_variables"])
                    + "."
                )
        if workflow.get("requires_contract"):
            source = str(workflow.get("contract_template_source") or "html")
            docx_metadata = workflow.get("contract_docx_template") or {}
            builder_document = (
                workflow.get("contract_builder_active_document")
                or workflow.get("contract_builder_document")
                or {}
            )
            has_template = (
                bool(builder_document.get("blocks"))
                if source == "builder"
                else bool(docx_metadata.get("html") or docx_metadata.get("storage_path"))
                if source == "docx"
                else bool(str(workflow.get("contract_template_html") or "").strip())
            )
            if not has_template:
                errors.append("Brak szablonu umowy dla tego formularza.")
            if source == "docx" and docx_metadata.get("unknown_variables"):
                errors.append(
                    "Szablon DOCX zawiera nieznane zmienne: "
                    + ", ".join(str(item) for item in docx_metadata["unknown_variables"])
                    + "."
                )
        signature_step = next(
            (step for step in active_steps if step.get("id") == "training_agreements_signature"),
            None,
        )
        if (
            workflow.get("flow_mode") != "explicit"
            and workflow.get("requires_contract")
            and workflow.get("requires_agreement_confirmation")
            and signature_step
            and signature_step.get("next") == "completed"
        ):
            errors.append(
                "Włączono potwierdzenie podpisania umowy przez urząd, ale etap "
                "„Umowa oczekuje na podpis beneficjenta” prowadzi bezpośrednio do zakończenia procesu. "
                "Ustaw kolejny etap na „Oczekuje na podpis urzędu” albo wyłącz wymaganie "
                "potwierdzenia podpisu przez urząd."
            )
        self._validate_reachable_steps(workflow, steps, step_ids, initial_step, errors)

    def _validate_reachable_steps(
        self,
        workflow: dict,
        steps: list[dict],
        step_ids: set[str],
        initial_step: str,
        errors: list[str],
    ) -> None:
        if not initial_step or initial_step not in step_ids:
            return
        by_id = {step.get("id"): step for step in steps}
        reachable = {initial_step}
        pending = [initial_step]
        while pending:
            step = by_id.get(pending.pop()) or {}
            targets = []
            if step.get("next"):
                targets.append(step.get("next"))
            targets.extend((step.get("decisions") or {}).values())
            targets.extend(option.get("target_step") for option in workflow.get("decision_types", []) if option.get("active", True) and option.get("step_id") == step.get("id"))
            if workflow.get("flow_mode") == "explicit" and step.get("completion_next"):
                targets.append(step["completion_next"])
            targets.extend(
                transition.get("next")
                for transition in step.get("transitions") or []
                if isinstance(transition, dict)
            )
            for target in targets:
                if target in step_ids and target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        for step in steps:
            step_id = str(step.get("id") or "").strip()
            if step_id in reachable or not is_workflow_step_active(step, workflow):
                continue
            label = str(step.get("admin_label") or step.get("label") or step_id).strip()
            errors.append(f"Etap „{label}” nie jest połączony z główną ścieżką workflow.")

    def _validate_notifications(self, notifications: list[dict], errors: list[str]) -> None:
        if not isinstance(notifications, list):
            errors.append("notifications must be a list")
            return
        for index, notification in enumerate(notifications):
            if not notification.get("event"):
                errors.append(f"notifications[{index}].event is required")
            if not str(notification.get("subject") or notification.get("template") or "").strip():
                errors.append(f"notifications[{index}].subject or template is required")
            if not str(notification.get("body") or notification.get("html_body") or notification.get("template") or "").strip():
                errors.append(f"notifications[{index}].body or template is required")
            recipients = notification.get("to", [])
            if recipients and not isinstance(recipients, list):
                errors.append(f"notifications[{index}].to must be a list")
            template = notification.get("template")
            if template:
                self._validate_template_exists(template, f"notifications[{index}].template", errors)

    def _validate_rules(self, rules: list[dict], errors: list[str]) -> None:
        if not isinstance(rules, list):
            errors.append("rules must be a list")
            return
        for index, rule in enumerate(rules):
            if not rule.get("id"):
                errors.append(f"rules[{index}].id is required")
            if not isinstance(rule.get("when"), dict):
                errors.append(f"rules[{index}].when must be an object")
            actions = rule.get("then")
            if not isinstance(actions, list):
                errors.append(f"rules[{index}].then must be a list")
                continue
            for action_index, action in enumerate(actions):
                action_name = action.get("action") if isinstance(action, dict) else None
                if action_name not in {"set_field", "set_status", "block_document", "unblock_document"}:
                    errors.append(f"rules[{index}].then[{action_index}].action is unsupported: {action_name}")

    def _validate_filename_pattern(self, pattern: str, path: str, errors: list[str]) -> None:
        if not pattern:
            return
        formatter = string.Formatter()
        for _, field_name, _, _ in formatter.parse(pattern):
            if field_name and field_name not in ALLOWED_FILENAME_PLACEHOLDERS:
                errors.append(f"{path} contains unsupported placeholder: {field_name}")

    def _validate_template_exists(self, template: str, path: str, errors: list[str]) -> None:
        if self.skip_template_check:
            return
        if not (self.template_root / template).exists():
            errors.append(f"{path} does not exist: {template}")
