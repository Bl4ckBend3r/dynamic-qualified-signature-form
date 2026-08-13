from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


LEGACY_INITIAL_STAGE = "initial_submission"
LEGACY_AFTER_ACCEPTANCE_STAGE = "after_officer_acceptance"


class FieldAvailabilityError(ValueError):
    pass


class FieldAvailabilityService:
    """Resolve field permissions against the workflow embedded in one form version."""

    @staticmethod
    def workflow_steps(form_definition: Mapping[str, Any]) -> list[dict]:
        workflow = form_definition.get("workflow") or {}
        steps = workflow.get("steps") or []
        return [dict(step) for step in steps if isinstance(step, Mapping) and step.get("active", True) is not False and str(step.get("id") or "").strip()]

    def step_ids(self, form_definition: Mapping[str, Any]) -> list[str]:
        return [str(step["id"]).strip() for step in self.workflow_steps(form_definition)]

    def initial_step(self, form_definition: Mapping[str, Any]) -> str:
        workflow = form_definition.get("workflow") or {}
        ids = self.step_ids(form_definition)
        configured = str(workflow.get("initial_step") or "").strip()
        if configured in ids:
            return configured
        return ids[0] if ids else LEGACY_INITIAL_STAGE

    def legacy_after_acceptance_step(self, form_definition: Mapping[str, Any]) -> str:
        workflow = form_definition.get("workflow") or {}
        explicit = str((workflow.get("legacy_field_stage_mapping") or {}).get(LEGACY_AFTER_ACCEPTANCE_STAGE) or "").strip()
        ids = self.step_ids(form_definition)
        if not ids:
            return LEGACY_AFTER_ACCEPTANCE_STAGE
        if explicit in ids:
            return explicit
        initial = self.initial_step(form_definition)
        for step in self.workflow_steps(form_definition):
            step_id = str(step.get("id") or "").strip()
            if step_id != initial and bool(step.get("requires_user_action")):
                return step_id
        return ids[1] if len(ids) > 1 else initial

    def resolve_step(self, form_definition: Mapping[str, Any], step: str | None) -> str:
        value = str(step or "").strip()
        if not self.step_ids(form_definition):
            return value
        if value == LEGACY_INITIAL_STAGE:
            return self.initial_step(form_definition)
        if value == LEGACY_AFTER_ACCEPTANCE_STAGE:
            return self.legacy_after_acceptance_step(form_definition)
        return value

    def normalize_field(self, field: Mapping[str, Any], form_definition: Mapping[str, Any]) -> dict:
        normalized = deepcopy(dict(field))
        ids = self.step_ids(form_definition)
        raw = normalized.get("availability")
        entries: list[dict] = []
        if isinstance(raw, list):
            seen: set[str] = set()
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                step = self.resolve_step(form_definition, str(item.get("step") or ""))
                if not step or step in seen:
                    continue
                seen.add(step)
                visible = bool(item.get("visible", True))
                editable = visible and bool(item.get("editable", True))
                required = bool(item.get("required", False))
                entries.append({"step": step, "visible": visible, "editable": editable, "required": required})
        if not entries:
            legacy_step = self.resolve_step(form_definition, str(normalized.get("stage") or LEGACY_INITIAL_STAGE))
            if ids and legacy_step not in ids:
                legacy_step = self.initial_step(form_definition)
            visible = not bool(normalized.get("hidden"))
            editable = visible and not bool(normalized.get("readonly")) and not bool(normalized.get("system"))
            entries = [{
                "step": legacy_step,
                "visible": visible,
                "editable": editable,
                "required": editable and bool(normalized.get("required", False)),
            }]
        normalized["availability"] = entries
        return normalized

    def permission(self, field: Mapping[str, Any], form_definition: Mapping[str, Any], step: str) -> dict:
        wanted = self.resolve_step(form_definition, step)
        normalized = self.normalize_field(field, form_definition)
        return next(
            (dict(item) for item in normalized["availability"] if item.get("step") == wanted),
            {"step": wanted, "visible": False, "editable": False, "required": False},
        )

    def fields_for_step(self, form_definition: Mapping[str, Any], step: str, *, mode: str = "visible") -> list[dict]:
        result: list[dict] = []
        pending_sections: list[dict] = []
        for raw_field in form_definition.get("fields") or []:
            if not isinstance(raw_field, Mapping):
                continue
            field = self.normalize_field(raw_field, form_definition)
            if field.get("type") in {"section", "static_text"}:
                pending_sections.append(field)
                continue
            permission = self.permission(field, form_definition, step)
            allowed = permission["visible"] if mode == "visible" else permission["editable"]
            if not allowed:
                continue
            result.extend(pending_sections)
            pending_sections = []
            field["required"] = bool(permission["required"])
            field["readonly"] = not bool(permission["editable"])
            result.append(field)
        return result

    def visible_fields(self, form_definition: Mapping[str, Any], step: str) -> list[dict]:
        return self.fields_for_step(form_definition, step, mode="visible")

    def editable_fields(self, form_definition: Mapping[str, Any], step: str) -> list[dict]:
        return self.fields_for_step(form_definition, step, mode="editable")

    def required_fields(self, form_definition: Mapping[str, Any], step: str) -> list[dict]:
        return [field for field in self.editable_fields(form_definition, step) if field.get("required")]

    def can_update(self, field: Mapping[str, Any], form_definition: Mapping[str, Any], step: str) -> bool:
        return bool(self.permission(field, form_definition, step)["editable"])

    def validate_config(self, form_definition: Mapping[str, Any]) -> list[str]:
        errors: list[str] = []
        ids = set(self.step_ids(form_definition))
        for field in form_definition.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            name = str(field.get("name") or field.get("label") or "?")
            normalized = self.normalize_field(field, form_definition)
            for item in normalized["availability"]:
                step = str(item.get("step") or "")
                if step not in ids:
                    errors.append(f'Pole "{name}" odwołuje się do nieistniejącego etapu "{step}".')
                if item.get("required") and (not item.get("visible") or not item.get("editable")):
                    errors.append(f'Pole "{name}" nie może być wymagane i jednocześnie ukryte lub tylko do odczytu w etapie "{step}".')
        return errors
