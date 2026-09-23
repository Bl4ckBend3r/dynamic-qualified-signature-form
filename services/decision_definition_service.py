from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from models import DecisionTypeDefinition, RepeatableGroupItemDecision, SubmissionWorkflowEvent
from form_loader import evaluate_scoped_condition


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

    def assign_to_draft(self, draft, definition: DecisionTypeDefinition, *, step_id: str, target_step: str, enabled: bool = True, require_reason: bool = False, sort_order: int | None = None, scope: str = "both", remove: bool = False) -> None:
        if draft.status != "draft":
            raise DecisionDefinitionError("Decyzje można przypisywać wyłącznie do wersji roboczej formularza.")
        if not definition.is_active and not remove:
            raise DecisionDefinitionError("Nieaktywnego typu decyzji nie można przypisać do nowej wersji.")
        workflow = deepcopy((draft.definition_json or {}).get("workflow") or {})
        steps = {str(item.get("id") or "") for item in workflow.get("steps") or [] if isinstance(item, dict)}
        target_step = str(target_step or "").strip()
        step_id = str(step_id or "").strip()
        if scope not in {"both", "submission", "item"}:
            raise DecisionDefinitionError("Nieprawidłowy zakres decyzji.")
        if step_id not in steps or target_step not in steps:
            raise DecisionDefinitionError("Wybierz istniejący etap decyzji i etap docelowy workflow.")
        assignments = [dict(item) for item in workflow.get("decision_types") or [] if isinstance(item, dict)]
        assignments = [item for item in assignments if (str(item.get("code") or ""), str(item.get("step_id") or "")) != (definition.code, step_id)]
        if not remove:
            assignments.append({
                "definition_id": definition.id,
                "code": definition.code,
                "label": definition.label,
                "description": definition.description,
                "semantic_category": definition.semantic_category,
                "sort_order": definition.sort_order if sort_order is None else int(sort_order),
                "require_reason": bool(require_reason),
                "scope": scope,
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

    def available_for_submission(self, submission, *, scope: str | None = None) -> list[dict]:
        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        workflow = definition.get("workflow") or {}
        current_step = str(submission.workflow_stage or submission.workflow_step or workflow.get("initial_step") or "submission")
        if workflow.get("flow_mode") == "explicit":
            step = next((s for s in workflow.get("steps", []) if s.get("id") == current_step), {})
            if step.get("stage_type") != "decision":
                return []
        all_configured = [dict(item) for item in (workflow.get("decision_types") or []) if isinstance(item, dict) and item.get("active", True)]
        configured = [item for item in all_configured if str(item.get("step_id") or current_step) == current_step]
        if scope:
            configured = [item for item in configured if item.get("scope", "both") in {"both", scope}]
        if all_configured or workflow.get("flow_mode") == "explicit":
            return sorted(configured, key=lambda item: (int(item.get("sort_order") or 0), str(item.get("label") or "")))
        return [
            {"code": "accepted", "label": "Tak", "semantic_category": "positive", "target_step": ""},
            {"code": "rejected", "label": "Nie", "semantic_category": "negative", "target_step": "end_rejected"},
            {"code": "correction", "label": "Do poprawy", "semantic_category": "correction", "target_step": "waiting_for_correction"},
        ]

    def resolve(self, submission, code: str, *, scope: str | None = None) -> dict:
        wanted = str(code or "").strip()
        for item in self.available_for_submission(submission, scope=scope):
            if str(item.get("code") or "") == wanted:
                return item
        raise DecisionDefinitionError("Ta decyzja nie jest dostępna dla wersji formularza przypisanej do zgłoszenia.")

    def repeatable_groups(self, submission, *, definition: dict | None = None) -> list[dict]:
        if definition is None:
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
            applicant_ids = dict(data.get("_applicant_record_ids") or {})
            applicant_id = str(applicant_ids.get(key) or data.get("_applicant_record_id") or "")
            for record in data.get(key) or []:
                record_uuid = str((record or {}).get("record_uuid") or (record or {}).get("id") or "")
                if not isinstance(record, dict) or not self._valid_item_id(record_uuid):
                    continue
                identity = [str(record.get(item.get("name")) or "").strip() for item in nested[:3] if item.get("name")]
                display_fields = [
                    {"name": str(child.get("name")), "label": str(child.get("label") or child.get("name")), "value": record.get(child.get("name"))}
                    for child in nested
                    if child.get("name")
                    and record.get(child.get("name")) not in (None, "", [])
                    and evaluate_scoped_condition(child.get("visible_if"), root_data=data, current_data=record)
                ]
                records.append({"id": record_uuid, "record_uuid": record_uuid, "values": record, "display_fields": display_fields, "summary": " · ".join(item for item in identity if item) or record_uuid, "contact_email": str(record.get(contact_key) or "").strip() if contact_key else "", "is_applicant": record_uuid == applicant_id})
            for item in records:
                item.update(self._item_presentation(field, item))
            decisions = self.available_for_submission(submission, scope="item")
            workflow = definition.get("workflow") or {}
            step = next((s for s in workflow.get("steps", []) if s.get("id") == str(submission.workflow_stage or submission.workflow_step or "")), {})
            if workflow.get("flow_mode") == "explicit" and step.get("decision_group") != key:
                decisions = []
            decision_steps = (field.get("decision_completion") or {}).get("step_ids") or []
            current = str(submission.workflow_stage or submission.workflow_step or "")
            if workflow.get("flow_mode") != "explicit" and decision_steps and current not in decision_steps:
                decisions = []
            result.append({"key": key, "label": str(field.get("label") or key), "contact_field": contact_key, "items": records, "config": field, "applicant_record_id": applicant_id, "decisions": decisions})
        return result

    @staticmethod
    def _item_presentation(config: dict, item: dict) -> dict:
        fields = item["display_fields"]
        mapping = config.get("item_context_fields") or {}
        identity_keys = {mapping.get("first_name", "imie"), mapping.get("last_name", "nazwisko")}
        identity = [entry for entry in fields if entry["name"] in identity_keys]
        used = {entry["name"] for entry in identity}
        groups = []
        for group in config.get("item_summary_groups") or []:
            selected = [entry for name in group.get("fields", []) for entry in fields if entry["name"] == name]
            if selected:
                groups.append({"label": group.get("label", ""), "values": [entry["value"] for entry in selected]})
                used.update(entry["name"] for entry in selected)
        types = {child.get("name"): child.get("type") for child in config.get("fields") or []}
        primary = [entry for entry in fields if entry["name"] not in used and types.get(entry["name"]) in {"text", "email", "tel"}][:4]
        used.update(entry["name"] for entry in primary)
        return {"display_name": " ".join(str(entry["value"]) for entry in identity) or item["summary"],
                "primary_fields": primary, "summary_groups": groups,
                "additional_fields": [entry for entry in fields if entry["name"] not in used]}

    def decide_item(self, db, submission, *, group_key: str, item_id: str, decision_code: str, comment: str, actor) -> RepeatableGroupItemDecision:
        group = next((item for item in self.repeatable_groups(submission) if item["key"] == group_key), None)
        item = next((entry for entry in (group or {}).get("items", []) if entry["id"] == item_id), None)
        if not group or not item:
            raise DecisionDefinitionError("Element grupy nie należy do tego zgłoszenia.")
        if not any(str(option["code"]) == str(decision_code) for option in group["decisions"]):
            raise DecisionDefinitionError("Decyzja dla tej osoby nie jest dostępna na bieżącym etapie.")
        decision = self.resolve(submission, decision_code, scope="item")
        normalized_comment = str(comment or "").strip()
        if decision.get("reason_required", decision.get("require_reason")) and not normalized_comment:
            raise DecisionDefinitionError("Uzasadnienie tej decyzji jest wymagane.")
        workflow_step = str(submission.workflow_stage or submission.workflow_step or "")
        latest = db.execute(
            select(RepeatableGroupItemDecision)
            .where(
                RepeatableGroupItemDecision.submission_id == submission.id,
                RepeatableGroupItemDecision.group_key == group_key,
                RepeatableGroupItemDecision.item_id == item_id,
                RepeatableGroupItemDecision.workflow_step == workflow_step,
            )
            .order_by(RepeatableGroupItemDecision.decided_at.desc(), RepeatableGroupItemDecision.id.desc())
        ).scalars().first()
        if latest and latest.decision_code == str(decision["code"]) and latest.comment == normalized_comment:
            latest.was_created = False
            return latest
        snapshot = self._participant_snapshot(group, item, decision, normalized_comment)
        snapshot["completes_decision"] = decision.get("completes_decision", True if ((submission.form_version.definition_json or {}).get("workflow") or {}).get("flow_mode") == "explicit" else decision.get("semantic_category") in {"positive", "negative"})
        record = RepeatableGroupItemDecision(
            submission_id=submission.id,
            group_key=group_key,
            item_id=item_id,
            decision_type_id=decision.get("definition_id"),
            decision_code=str(decision["code"]),
            decision_label=str(decision.get("label") or decision["code"]),
            semantic_category=str(decision.get("semantic_category") or "neutral"),
            workflow_step=workflow_step,
            target_step=str(decision.get("target_step") or ""),
            comment=normalized_comment,
            participant_snapshot_json=snapshot,
            decided_by_user_id=getattr(actor, "id", None),
        )
        db.add(record)
        db.flush()
        record.was_created = True
        db.add(SubmissionWorkflowEvent(
            submission_id=submission.id,
            public_submission_id=submission.submission_id,
            form_slug=submission.form_slug,
            previous_status=str(submission.process_status or ""),
            new_status=str(submission.process_status or ""),
            previous_step=workflow_step,
            new_step=workflow_step,
            actor_id=getattr(actor, "id", None),
            actor_email=str(getattr(actor, "email", "") or ""),
            actor_role=str(getattr(actor, "role", "") or "system"),
            reason=normalized_comment,
            decision_code=str(decision["code"]),
            side_effects={"event": "PERSON_DECISION_RECORDED", "group_name": group_key, "record_uuid": item_id, "decision_definition_id": decision.get("definition_id")},
            source="repeatable_item_decision",
        ))
        return record

    @staticmethod
    def participant_context(group: dict, item: dict) -> dict:
        values = dict(item.get("values") or {})
        mapping = dict((group.get("config") or {}).get("item_context_fields") or {})
        aliases = {
            "first_name": ("first_name", "imie", "imiona"),
            "last_name": ("last_name", "nazwisko"),
            "email": (group.get("contact_field"), "email", "adres_email"),
            "phone": ("phone", "telefon"),
        }
        participant = {"id": item["id"], "record_uuid": item["id"], "values": values}
        for target, candidates in aliases.items():
            configured = str(mapping.get(target) or "")
            keys = ([configured] if configured else []) + [str(key or "") for key in candidates]
            participant[target] = next((str(values.get(key) or "").strip() for key in keys if key and values.get(key) not in (None, "")), "")
        participant["full_name"] = " ".join(part for part in (participant["first_name"], participant["last_name"]) if part)
        return participant

    @classmethod
    def _participant_snapshot(cls, group: dict, item: dict, decision: dict, reason: str) -> dict:
        participant = cls.participant_context(group, item)
        participant.update({"decision": str(decision["code"]), "decision_label": str(decision.get("label") or decision["code"]), "decision_reason": reason})
        return participant

    def completion_state(self, db, submission, group_key: str) -> dict:
        group = next((item for item in self.repeatable_groups(submission) if item["key"] == group_key), None)
        if not group:
            raise DecisionDefinitionError("Nie znaleziono skonfigurowanej grupy powtarzalnej.")
        completion = dict((group.get("config") or {}).get("decision_completion") or {})
        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        workflow = definition.get("workflow") or {}
        if workflow.get("flow_mode") == "explicit":
            current = str(submission.workflow_stage or submission.workflow_step or "")
            step = next((s for s in workflow.get("steps", []) if s.get("id") == current), {})
            completion = {"policy": step.get("completion_policy"), "step_ids": [current], "next": step.get("completion_next")} if step.get("decision_group") == group_key else {}
        if str(completion.get("policy") or "").upper() != "ALL":
            return {"configured": False, "complete": False, "aggregate": None, "target_step": ""}
        current_step = str(submission.workflow_stage or submission.workflow_step or "")
        configured_steps = {str(value) for value in completion.get("step_ids") or []}
        if configured_steps and current_step not in configured_steps:
            return {"configured": True, "complete": False, "aggregate": None, "target_step": ""}
        active_ids = self._active_item_ids(submission, group)
        history = self.item_history(db, submission.id)
        latest: dict[str, RepeatableGroupItemDecision] = {}
        for entry in history:
            if entry.group_key == group_key and entry.workflow_step == current_step and entry.item_id in active_ids:
                latest.setdefault(entry.item_id, entry)
        complete = bool(active_ids) and active_ids <= set(latest) and all(
            (entry.participant_snapshot_json or {}).get("completes_decision", True) for entry in latest.values()
        )
        categories = [latest[item_id].semantic_category for item_id in active_ids if item_id in latest]
        aggregate = None
        if complete:
            aggregate = "ALL_ACCEPTED" if all(value == "positive" for value in categories) else "ALL_REJECTED" if all(value == "negative" for value in categories) else "PARTIALLY_ACCEPTED"
        return {"configured": True, "complete": complete, "aggregate": aggregate, "target_step": str(completion.get("next") or ""), "decided": len(latest), "total": len(active_ids)}

    @staticmethod
    def _active_item_ids(submission, group: dict) -> set[str]:
        data = dict(submission.data_json or {})
        signed = dict(data.get("_signed_declaration_snapshot") or {})
        signed_groups = dict(signed.get("repeatable_groups") or {})
        snapshot_ids = signed_groups.get(group["key"])
        values = snapshot_ids if isinstance(snapshot_ids, list) else [item["id"] for item in group["items"]]
        return {str(value) for value in values if DecisionDefinitionService._valid_item_id(value)}

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
