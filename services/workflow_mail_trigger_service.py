from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.form_config_service import TRIGGER_DESCRIPTIONS
from services.workflow_config_service import (
    WorkflowConfigNormalizer,
    active_workflow_steps,
    workflow_status_label,
)


EVENT_LABELS = {
    "submission_created": "Potwierdzenie złożenia wniosku dla osoby z listy",
    "correction_accepted": "Poprawiony wniosek zaakceptowany",
    "manual": "Wiadomość wysyłana ręcznie",
    "manual_bulk": "Wiadomość zbiorcza",
    "application_submitted": "Wniosek złożony",
    "submission_received": "Potwierdzenie odebrania wniosku",
    "officer_accepted": "Wniosek zaakceptowany przez urzędnika",
    "officer_rejected": "Wniosek odrzucony przez urzędnika",
    "auto_rejected_by_condition": "Wniosek odrzucony automatycznie",
    "returned_for_correction": "Wniosek wysłany do poprawy",
    "additional_fields_completed": "Uzupełniono dodatkowe pola",
    "document_generated": "Dokument został wygenerowany",
    "document_uploaded": "Dokument został wgrany",
    "declaration_signed": "Deklaracja została podpisana",
    "agreement_ready": "Umowa jest gotowa",
    "agreement_signed_by_user": "Umowa podpisana przez beneficjenta",
    "agreement_signed_by_office": "Umowa podpisana przez urząd",
    "stage_rollback": "Etap procesu został cofnięty",
    "email_requested": "Workflow wymaga wysłania wiadomości",
    "sla_before_deadline": "Przypomnienie przed terminem SLA",
    "sla_deadline": "Termin SLA przypada teraz",
    "sla_overdue": "Termin SLA został przekroczony",
    "sla_escalation": "Eskalacja przekroczonego SLA",
}

SAFE_SYSTEM_EVENTS = ("manual", "manual_bulk", "application_submitted", "submission_received")

STATUS_EVENT_MAP = {
    "FORM_SUBMITTED": "application_submitted",
    "SUBMITTED": "application_submitted",
    "APPLICATION_SUBMITTED": "application_submitted",
    "OFFICER_ACCEPTED": "officer_accepted",
    "OFFICER_REJECTED": "officer_rejected",
    "AUTO_REJECTED_BY_CONDITION": "auto_rejected_by_condition",
    "WAITING_FOR_CORRECTION": "returned_for_correction",
    "CORRECTION_REQUIRED": "returned_for_correction",
    "ADDITIONAL_FIELDS_COMPLETED": "additional_fields_completed",
    "DECLARATION_SIGNED": "declaration_signed",
    "AGREEMENT_READY": "agreement_ready",
    "AGREEMENT_SIGNED_BY_USER": "agreement_signed_by_user",
    "AGREEMENT_SIGNED_BY_OFFICE": "agreement_signed_by_office",
}

DECISION_LABELS = {
    "accepted": "Wniosek zaakceptowany",
    "rejected": "Wniosek odrzucony",
    "correction": "Wniosek skierowany do poprawy",
    "yes": "Decyzja pozytywna",
    "no": "Decyzja negatywna",
}


class WorkflowMailTriggerService:
    MARKER = "workflow_stage"

    def stage_options(self, form) -> list[dict[str, Any]]:
        definition = getattr(form, "definition_json", None) or {}
        workflow = (definition.get("workflow") or {}) if isinstance(definition, Mapping) else {}
        if not isinstance(workflow, Mapping) or not workflow.get("steps"):
            return []
        workflow = WorkflowConfigNormalizer().normalize(workflow)
        catalog = self.options_for_form(form)
        return [
            {
                "value": str(step.get("id") or ""),
                "label": self._stage_label(step),
                "status": str(step.get("status") or step.get("status_code") or ""),
                "events": [str(value) for value in step.get("triggers") or []],
                "decisions": [item for item in catalog["decisions"]
                              if item.get("stage_id") == str(step.get("id") or "")],
            }
            for step in active_workflow_steps(workflow) if step.get("id")
        ]

    def binding_for_template(self, template, stages: list[dict]) -> dict[str, Any]:
        """Interpret old status/event bindings only when they identify one active stage."""
        event = str(getattr(template, "trigger_event", "") or "")
        status = str(getattr(template, "trigger_status", "") or "")
        decision = str(getattr(template, "trigger_decision", "") or "")
        if event == self.MARKER:
            stage = next((item for item in stages if item["value"] == status), None)
            valid = bool(stage and (not decision or any(d["value"] == decision for d in stage["decisions"])))
            return {"automatic": bool(getattr(template, "is_default_for_status", False)),
                    "stage": status if valid else "", "decision": decision if valid else "",
                    "warning": "Powiązanie nie należy do aktualnego workflow. Wybierz etap i decyzję ponownie."
                    if not valid and getattr(template, "is_default_for_status", False) else ""}
        if not any((event, status, decision, getattr(template, "is_default_for_status", False))):
            return {"automatic": False, "stage": "", "decision": "", "warning": ""}
        event_stages = {item["value"] for item in stages if event in item["events"] or
                        STATUS_EVENT_MAP.get(item["status"].upper()) == event}
        matches = [item for item in stages if
                   (not status or item["status"] == status)
                   and (not event or item["value"] in event_stages)
                   and (not decision or any(d["value"] == decision for d in item["decisions"]))]
        if len(matches) != 1 or not (status or event):
            return {"automatic": False, "stage": "", "decision": "",
                    "warning": "Historyczne wyzwalanie wymaga ponownego przypisania do etapu workflow."}
        return {"automatic": True, "stage": matches[0]["value"], "decision": decision,
                "warning": ""}

    def options_for_form(self, form) -> dict[str, Any]:
        definition = getattr(form, "definition_json", None) or {}
        raw_workflow = definition.get("workflow") if isinstance(definition, Mapping) else None
        if not isinstance(raw_workflow, Mapping) or not raw_workflow.get("steps"):
            return {
                "has_workflow": False,
                "message": (
                    "Formularz nie ma aktywnych etapów workflow. "
                    "Najpierw skonfiguruj workflow."
                ),
                "events": [self._event(value, source="system") for value in SAFE_SYSTEM_EVENTS],
                "statuses": [],
                "decisions": [],
            }

        workflow = WorkflowConfigNormalizer().normalize(raw_workflow)
        steps = active_workflow_steps(workflow)
        active_step_ids = {str(step.get("id") or "").strip() for step in steps}
        events: list[dict[str, str]] = []
        if any(isinstance(field, Mapping) and (field.get('submission_confirmation') or {}).get('enabled')
               for field in definition.get('fields') or []):
            events.append(self._event('submission_created', source='system'))
        statuses: list[dict[str, str]] = []
        decisions: list[dict[str, str]] = []
        if workflow.get("allow_correction"):
            events.append(self._event("correction_accepted", source="workflow"))
        if any(step.get("sla") or step.get("deadline") for step in steps):
            events.extend(
                self._event(value, source="workflow")
                for value in ("sla_before_deadline", "sla_deadline", "sla_overdue", "sla_escalation")
            )

        for step in steps:
            stage_id = str(step.get("id") or "").strip()
            stage_label = self._stage_label(step)
            status = str(step.get("status") or step.get("status_code") or "").strip()
            if status:
                statuses.append(
                    {
                        "value": status,
                        "label": workflow_status_label(status),
                        "description": f"Status aktywnego etapu „{stage_label}”.",
                        "stage_id": stage_id,
                        "workflow_stage": stage_id,
                        "source": "workflow",
                    }
                )
                mapped_event = STATUS_EVENT_MAP.get(status.upper())
                if mapped_event:
                    events.append(self._event(mapped_event, stage_id=stage_id, stage_label=stage_label))

            for trigger in step.get("triggers") or []:
                value = str(trigger or "").strip()
                if value:
                    events.append(self._event(value, stage_id=stage_id, stage_label=stage_label))

            step_decisions = step.get("decisions")
            if workflow.get("flow_mode") != "explicit" and isinstance(step_decisions, Mapping):
                for value in step_decisions:
                    code = str(value or "").strip()
                    if code:
                        decisions.append(self._decision(code, stage_id, stage_label))

        for collection in (
            definition.get("notifications") if isinstance(definition, Mapping) else None,
            workflow.get("notifications"),
            workflow.get("email_notifications"),
        ):
            for notification in collection if isinstance(collection, list) else []:
                if not isinstance(notification, Mapping) or not self._is_enabled(notification):
                    continue
                stage_id = self._assigned_stage(notification)
                if stage_id and stage_id not in active_step_ids:
                    continue
                value = str(notification.get("event") or notification.get("trigger") or "").strip()
                if value:
                    stage = next((item for item in steps if item.get("id") == stage_id), None)
                    events.append(
                        self._event(
                            value,
                            stage_id=stage_id,
                            stage_label=self._stage_label(stage or {}),
                        )
                    )

        if workflow.get("flow_mode") == "explicit":
            for decision in workflow.get("decision_types") or []:
                if not isinstance(decision, Mapping) or not self._is_enabled(decision):
                    continue

                stage_id = str(decision.get("step_id") or "").strip()
                code = str(decision.get("code") or "").strip()

                if not code or stage_id not in active_step_ids:
                    continue

                stage = next(
                    (item for item in steps if item.get("id") == stage_id),
                    {},
                )

                if stage.get("stage_type") != "decision":
                    continue

                entry = self._decision(
                    code,
                    stage_id,
                    self._stage_label(stage),
                )
                entry["label"] = str(
                    decision.get("label") or code
                ).strip()

                decisions.append(entry)

        legacy_decisions = (
            workflow.get("decision_settings") or []
            if workflow.get("flow_mode") != "explicit"
            else []
        )

        for decision in legacy_decisions:
            if not isinstance(decision, Mapping) or not self._is_enabled(decision):
                continue
            stage_id = str(decision.get("step_id") or "").strip()
            if not stage_id or stage_id not in active_step_ids:
                continue
            stage = next((item for item in steps if item.get("id") == stage_id), {})
            stage_label = self._stage_label(stage)
            decision_label = str(
                decision.get("display_name")
                or decision.get("label")
                or decision.get("technical_name")
                or decision.get("id")
                or "Decyzja urzędnika"
            ).strip()
            outcomes = decision.get("outcomes") if isinstance(decision.get("outcomes"), list) else []
            if outcomes:
                for outcome in outcomes:
                    if not isinstance(outcome, Mapping) or not self._is_enabled(outcome):
                        continue
                    raw_code = str(outcome.get("value") or outcome.get("code") or "").strip()
                    code = {"yes": "accepted", "no": "rejected"}.get(raw_code, raw_code)
                    if not code:
                        continue
                    outcome_label = str(outcome.get("label") or DECISION_LABELS.get(code) or self._humanize(code))
                    decisions.append(
                        {
                            "value": code,
                            "label": f"{decision_label} — {outcome_label}",
                            "description": f"Aktywna decyzja na etapie „{stage_label}”.",
                            "stage_id": stage_id,
                            "workflow_stage": stage_id,
                            "source": "workflow",
                        }
                    )
            else:
                code = str(decision.get("technical_name") or decision.get("id") or "").strip()
                if code:
                    decisions.append(
                        {
                            "value": code,
                            "label": decision_label,
                            "description": f"Aktywna decyzja na etapie „{stage_label}”.",
                            "stage_id": stage_id,
                            "workflow_stage": stage_id,
                            "source": "workflow",
                        }
                    )

        return {
            "has_workflow": True,
            "message": "",
            "events": self._unique(events),
            "statuses": self._unique(statuses),
            "decisions": self._unique(decisions, by_stage=True),
        }

    def _event(
        self,
        value: str,
        *,
        source: str = "workflow",
        stage_id: str = "",
        stage_label: str = "",
    ) -> dict[str, str]:
        description = TRIGGER_DESCRIPTIONS.get(value) or (
            f"Zdarzenie aktywnego etapu „{stage_label}”." if stage_label else "Bezpieczne zdarzenie systemowe."
        )
        return {
            "value": value,
            "label": EVENT_LABELS.get(value, self._humanize(value)),
            "description": description,
            "stage_id": stage_id,
            "workflow_stage": stage_id,
            "source": source,
        }

    def _decision(self, value: str, stage_id: str, stage_label: str) -> dict[str, str]:
        return {
            "value": value,
            "label": DECISION_LABELS.get(value, self._humanize(value)),
            "description": f"Aktywna decyzja na etapie „{stage_label}”.",
            "stage_id": stage_id,
            "workflow_stage": stage_id,
            "source": "workflow",
        }

    @staticmethod
    def _assigned_stage(item: Mapping[str, Any]) -> str:
        for key in ("step_id", "stage_id", "workflow_stage", "assigned_stage"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _is_enabled(item: Mapping[str, Any]) -> bool:
        for key in ("active", "is_active", "enabled"):
            if key in item:
                value = item[key]
                return value is True or str(value).strip().lower() in {"1", "true", "yes", "tak", "on"}
        return True

    @staticmethod
    def _stage_label(step: Mapping[str, Any]) -> str:
        return str(
            step.get("admin_label")
            or step.get("label")
            or step.get("name")
            or step.get("id")
            or "etap workflow"
        ).strip()

    @staticmethod
    def _unique(items: list[dict[str, str]], *, by_stage: bool = False) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[Any] = set()
        for item in items:
            value = (item.get("stage_id", ""), item["value"]) if by_stage else item["value"]
            if value and value not in seen:
                seen.add(value)
                result.append(item)
        return result

    @staticmethod
    def _humanize(value: str) -> str:
        return str(value or "").replace("_", " ").strip().capitalize()
