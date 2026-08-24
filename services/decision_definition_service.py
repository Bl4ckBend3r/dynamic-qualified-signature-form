from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from models import DecisionTypeDefinition, RepeatableGroupItemDecision


DECISION_CATEGORIES = {"positive", "negative", "correction", "neutral"}
DECISION_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class DecisionDefinitionError(ValueError):
    pass


class DecisionDefinitionService:
    """Extends workflow decisions with a shared catalog and immutable snapshots."""

    def list_catalog(self, db, *, include_inactive: bool = True) -> list[DecisionTypeDefinition]:
        query = select(DecisionTypeDefinition)
        if not include_inactive:
            query = query.where(DecisionTypeDefinition.is_active.is_(True))
        return list(db.execute(query.order_by(DecisionTypeDefinition.sort_order, DecisionTypeDefinition.label, DecisionTypeDefinition.id)).scalars())

    def save_definition(self, db, *, definition=None, code: str, label: str, description: str = "", category: str, sort_order: int = 0, active: bool = True, actor_id: int | None = None) -> DecisionTypeDefinition:
        code = str(code or "").strip().lower()
        label = str(label or "").strip()
        category = str(category or "").strip().lower()
        if not DECISION_CODE_RE.fullmatch(code):
            raise DecisionDefinitionError("Kod musi zaczynać się literą i zawierać tylko małe litery, cyfry oraz podkreślenia.")
        if not label:
            raise DecisionDefinitionError("Nazwa decyzji jest wymagana.")
        if category not in DECISION_CATEGORIES:
            raise DecisionDefinitionError("Nieprawidłowa kategoria semantyczna decyzji.")
        duplicate = db.execute(select(DecisionTypeDefinition).where(DecisionTypeDefinition.code == code)).scalar_one_or_none()
        if duplicate and duplicate is not definition:
            raise DecisionDefinitionError("Typ decyzji o tym kodzie już istnieje.")
        record = definition or DecisionTypeDefinition(code=code, created_by_user_id=actor_id)
        record.code = code
        record.label = label
        record.description = str(description or "").strip()
        record.semantic_category = category
        record.sort_order = int(sort_order)
        record.is_active = bool(active)
        db.add(record)
        db.flush()
        return record

    def assign_to_draft(self, draft, definition: DecisionTypeDefinition, *, step_id: str, target_step: str, enabled: bool = True) -> None:
        if draft.status != "draft":
            raise DecisionDefinitionError("Decyzje można przypisywać wyłącznie do wersji roboczej formularza.")
        if not definition.is_active:
            raise DecisionDefinitionError("Nieaktywnego typu decyzji nie można przypisać do nowej wersji.")
        workflow = deepcopy((draft.definition_json or {}).get("workflow") or {})
        steps = {str(item.get("id") or "") for item in workflow.get("steps") or [] if isinstance(item, dict)}
        target_step = str(target_step or "").strip()
        step_id = str(step_id or "").strip()
        if step_id not in steps or target_step not in steps:
            raise DecisionDefinitionError("Wybierz istniejący etap decyzji i etap docelowy workflow.")
        assignments = [dict(item) for item in workflow.get("decision_types") or [] if isinstance(item, dict)]
        assignments = [item for item in assignments if str(item.get("code") or "") != definition.code]
        assignments.append({
            "definition_id": definition.id,
            "code": definition.code,
            "label": definition.label,
            "description": definition.description,
            "semantic_category": definition.semantic_category,
            "sort_order": definition.sort_order,
            "target_step": target_step,
            "step_id": step_id,
            "active": bool(enabled and definition.is_active),
        })
        assignments.sort(key=lambda item: (int(item.get("sort_order") or 0), str(item.get("label") or "")))
        workflow["decision_types"] = assignments
        snapshot = deepcopy(draft.definition_json or {})
        snapshot["workflow"] = workflow
        draft.definition_json = snapshot
        draft.updated_at = datetime.now(timezone.utc)

    def available_for_submission(self, submission) -> list[dict]:
        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        workflow = definition.get("workflow") or {}
        current_step = str(submission.workflow_stage or submission.workflow_step or workflow.get("initial_step") or "submission")
        all_configured = [dict(item) for item in (workflow.get("decision_types") or []) if isinstance(item, dict) and item.get("active", True)]
        configured = [item for item in all_configured if str(item.get("step_id") or current_step) == current_step]
        if all_configured:
            return sorted(configured, key=lambda item: (int(item.get("sort_order") or 0), str(item.get("label") or "")))
        return [
            {"code": "accepted", "label": "Tak", "semantic_category": "positive", "target_step": ""},
            {"code": "rejected", "label": "Nie", "semantic_category": "negative", "target_step": "end_rejected"},
            {"code": "correction", "label": "Do poprawy", "semantic_category": "correction", "target_step": "waiting_for_correction"},
        ]

    def resolve(self, submission, code: str) -> dict:
        wanted = str(code or "").strip()
        for item in self.available_for_submission(submission):
            if str(item.get("code") or "") == wanted:
                return item
        raise DecisionDefinitionError("Ta decyzja nie jest dostępna dla wersji formularza przypisanej do zgłoszenia.")

    def repeatable_groups(self, submission) -> list[dict]:
        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        data = dict(submission.data_json or {})
        result = []
        for field in definition.get("fields") or []:
            if not isinstance(field, dict) or field.get("type") != "repeatable_group" or not field.get("name"):
                continue
            key = str(field["name"])
            nested = [item for item in field.get("fields") or [] if isinstance(item, dict)]
            contact_key = str(field.get("decision_contact_email_field") or "").strip()
            email_fields = [str(item.get("name")) for item in nested if item.get("type") == "email" and item.get("name")]
            if not contact_key and len(email_fields) == 1:
                contact_key = email_fields[0]
            records = []
            for record in data.get(key) or []:
                if not isinstance(record, dict) or not self._valid_item_id(record.get("id")):
                    continue
                identity = [str(record.get(item.get("name")) or "").strip() for item in nested[:3] if item.get("name")]
                records.append({"id": str(record["id"]), "values": record, "summary": " · ".join(item for item in identity if item) or str(record["id"]), "contact_email": str(record.get(contact_key) or "").strip() if contact_key else ""})
            result.append({"key": key, "label": str(field.get("label") or key), "contact_field": contact_key, "items": records})
        return result

    def decide_item(self, db, submission, *, group_key: str, item_id: str, decision_code: str, comment: str, actor) -> RepeatableGroupItemDecision:
        group = next((item for item in self.repeatable_groups(submission) if item["key"] == group_key), None)
        if not group or not any(item["id"] == item_id for item in group["items"]):
            raise DecisionDefinitionError("Element grupy nie należy do tego zgłoszenia.")
        decision = self.resolve(submission, decision_code)
        record = RepeatableGroupItemDecision(
            submission_id=submission.id,
            group_key=group_key,
            item_id=item_id,
            decision_type_id=decision.get("definition_id"),
            decision_code=str(decision["code"]),
            decision_label=str(decision.get("label") or decision["code"]),
            semantic_category=str(decision.get("semantic_category") or "neutral"),
            workflow_step=str(submission.workflow_stage or submission.workflow_step or ""),
            target_step=str(decision.get("target_step") or ""),
            comment=str(comment or "").strip(),
            decided_by_user_id=getattr(actor, "id", None),
        )
        db.add(record)
        db.flush()
        return record

    def item_history(self, db, submission_id: int) -> list[RepeatableGroupItemDecision]:
        return list(db.execute(select(RepeatableGroupItemDecision).where(RepeatableGroupItemDecision.submission_id == submission_id).order_by(RepeatableGroupItemDecision.decided_at.desc(), RepeatableGroupItemDecision.id.desc())).scalars())

    def contact_email(self, submission, group_key: str, item_id: str) -> str:
        group = next((item for item in self.repeatable_groups(submission) if item["key"] == group_key), None)
        item = next((entry for entry in (group or {}).get("items", []) if entry["id"] == item_id), None)
        email = str((item or {}).get("contact_email") or "").strip()
        if not EMAIL_RE.fullmatch(email):
            raise DecisionDefinitionError("Element nie ma poprawnie skonfigurowanego adresu e-mail.")
        return email

    @staticmethod
    def _valid_item_id(value) -> bool:
        try:
            return str(UUID(str(value))) == str(value)
        except (ValueError, TypeError, AttributeError):
            return False
