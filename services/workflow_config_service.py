from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.instruction_html_service import sanitize_instruction_html
from services.status_catalog import WORKFLOW_STATUS_LABELS


STATUS_LABELS: dict[str, str] = {
    "FORM_SUBMITTED": "Wniosek złożony",
    "SUBMITTED": "Wniosek złożony",
    "APPLICATION_SUBMITTED": "Wniosek złożony",
    "application_submitted": "Wniosek złożony",
    "WAITING_FOR_OFFICER_DECISION": "Weryfikacja przez urzędnika",
    "WAITING_FOR_REVIEW": "Weryfikacja przez urzędnika",
    "OFFICER_REVIEW": "Weryfikacja przez urzędnika",
    "OFFICER_ACCEPTED": "Wniosek zaakceptowany",
    "officer_accepted": "Wniosek zaakceptowany",
    "OFFICER_REJECTED": "Wniosek odrzucony",
    "officer_rejected": "Wniosek odrzucony",
    "DECLARATION_REQUIRED": "Deklaracja wymagana",
    "declaration_required": "Deklaracja wymagana",
    "DECLARATION_READY": "Deklaracja gotowa",
    "DECLARATION_GENERATED": "Deklaracja wygenerowana",
    "DECLARATION_WAITING_FOR_SIGNATURE": "Deklaracja oczekuje na podpis",
    "DECLARATION_UPLOADED": "Podpisana deklaracja wgrana",
    "DECLARATION_SIGNED": "Deklaracja podpisana",
    "AGREEMENT_REQUIRED": "Umowa wymagana",
    "contract_required": "Umowa wymagana",
    "AGREEMENT_READY": "Umowa gotowa",
    "AGREEMENT_GENERATED": "Umowa wygenerowana",
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE": "Umowa oczekuje na podpis beneficjenta",
    "WAITING_FOR_AGREEMENT_SIGNATURE": "Umowa oczekuje na podpis beneficjenta",
    "AGREEMENT_UPLOADED_BY_BENEFICIARY": "Podpisana umowa wgrana przez beneficjenta",
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE": "Umowa oczekuje na podpis po stronie urzędu",
    "AGREEMENT_SIGNED_BY_OFFICE": "Umowa podpisana przez urząd",
    "AGREEMENT_REJECTED_BY_OFFICE": "Umowa wymaga poprawy",
    "AGREEMENT_WAITING_FOR_SIGNATURE": "Umowa oczekuje na podpis beneficjenta",
    "AGREEMENT_UPLOADED": "Umowa oczekuje na podpis po stronie urzędu",
    "BENEFICIARY_AGREEMENT_CONFIRMED": "Umowa podpisana przez urząd",
    "BENEFICIARY_AGREEMENT_REJECTED": "Umowa wymaga poprawy",
    "WAITING_FOR_CORRECTION": "Wymagana korekta",
    "CORRECTION_REQUIRED": "Wymagana korekta",
    "PROCESS_COMPLETED": "Proces zakończony",
    "COMPLETED": "Proces zakończony",
    "PROCESS_CANCELLED": "Proces anulowany",
    "CANCELLED": "Proces anulowany",
    "document_generated": "Dokument wygenerowany",
    "document_uploaded": "Dokument wgrany",
}
STATUS_LABELS = {**STATUS_LABELS, **WORKFLOW_STATUS_LABELS}

DEFAULT_STEP_STATUS = {
    "submission": "FORM_SUBMITTED",
    "officer_review": "WAITING_FOR_OFFICER_DECISION",
    "waiting_for_correction": "WAITING_FOR_CORRECTION",
    "declaration": "DECLARATION_READY",
    "declaration_signature": "DECLARATION_WAITING_FOR_SIGNATURE",
    "agreement": "AGREEMENT_READY",
    "training_agreements": "AGREEMENT_READY",
    "agreement_signature": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
    "training_agreements_signature": "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
    "beneficiary_agreement_review": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
    "office_agreement_signature": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
    "agreement_correction": "AGREEMENT_REJECTED_BY_OFFICE",
    "end_rejected": "OFFICER_REJECTED",
    "completed": "PROCESS_COMPLETED",
}

KNOWN_STEP_FIELDS = {
    "id",
    "type",
    "label",
    "admin_label",
    "user_label",
    "status",
    "status_code",
    "next",
    "final",
    "rejected",
    "requires_user_action",
    "requires_officer_action",
    "description",
    "next_action",
    "decisions",
    "document_id",
    "repeat_over",
    "repeat_item_alias",
    "triggers",
}


PREFERRED_WORKFLOW_STATUSES = (
    "FORM_SUBMITTED",
    "WAITING_FOR_OFFICER_DECISION",
    "OFFICER_ACCEPTED",
    "OFFICER_REJECTED",
    "AUTO_REJECTED",
    "RETURNED_FOR_CORRECTION",
    "DECLARATION_REQUIRED",
    "DECLARATION_GENERATED",
    "DECLARATION_WAITING_FOR_SIGNATURE",
    "DECLARATION_UPLOADED",
    "DECLARATION_SIGNED",
    "AGREEMENT_REQUIRED",
    "AGREEMENT_GENERATED",
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
    "AGREEMENT_UPLOADED_BY_BENEFICIARY",
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
    "AGREEMENT_SIGNED_BY_OFFICE",
    "AGREEMENT_REJECTED_BY_OFFICE",
    "CORRECTION_REQUIRED",
    "PROCESS_COMPLETED",
    "PROCESS_CANCELLED",
)

# Only these agreement statuses are produced by the current process. Historical
# statuses remain readable through STATUS_LABELS, but must never make a legacy
# agreement branch look complete during admin validation.
ACTIVE_AGREEMENT_STATUSES = frozenset(
    {
        "AGREEMENT_REQUIRED",
        "AGREEMENT_READY",
        "AGREEMENT_GENERATED",
        "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE",
        "AGREEMENT_UPLOADED_BY_BENEFICIARY",
        "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
        "AGREEMENT_SIGNED_BY_OFFICE",
        "AGREEMENT_REJECTED_BY_OFFICE",
    }
)
ACTIVE_AGREEMENT_CONFIRMATION_STATUSES = frozenset(
    {
        "AGREEMENT_UPLOADED_BY_BENEFICIARY",
        "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
    }
)
AGREEMENT_DECISION_IDS = frozenset({"agreement_confirmation", "agreement_rejection"})


def workflow_status_options(existing_codes: Any = None) -> list[dict[str, str]]:
    codes = list(PREFERRED_WORKFLOW_STATUSES)
    for raw_code in existing_codes or []:
        code = str(raw_code or "").strip()
        if code and code not in codes:
            codes.append(code)
    return [{"value": code, "label": workflow_status_label(code)} for code in codes]


def workflow_status_label(code: Any) -> str:
    raw = str(code or "").strip()
    return STATUS_LABELS.get(raw, raw.replace("_", " ").strip().capitalize() or "Nieznany status")


class WorkflowConfigNormalizer:
    def normalize(self, workflow: Mapping[str, Any] | None) -> dict[str, Any]:
        source = dict(workflow or {})
        raw_steps = source.get("steps") if isinstance(source.get("steps"), list) else []
        source["name"] = str(source.get("name") or source.get("label") or "Workflow").strip()
        source["initial_step"] = str(source.get("initial_step") or (raw_steps[0].get("id") if raw_steps else "")).strip()
        source["requires_declaration"] = bool(source.get("requires_declaration", False))
        source["requires_contract"] = bool(source.get("requires_contract", False))
        source["requires_agreement_confirmation"] = source["requires_contract"] and bool(
            source.get("requires_agreement_confirmation", source["requires_contract"])
        )
        source["send_email_notifications"] = bool(source.get("send_email_notifications", False))
        source["allow_correction"] = bool(source.get("allow_correction", True))
        source["electronic_signature_required"] = bool(source.get("electronic_signature_required", True))
        source["signed_document_uploader"] = str(source.get("signed_document_uploader") or "beneficiary")
        source["decision_settings"] = self._mapping_list(source.get("decision_settings"))
        for decision in source["decision_settings"]:
            if decision.get("label"):
                decision["label"] = _modern_workflow_label(str(decision["label"]).strip())
        source["email_notifications"] = self._mapping_list(source.get("email_notifications"))
        source["steps"] = [self.normalize_step(step, index) for index, step in enumerate(raw_steps) if isinstance(step, Mapping)]
        return source

    def normalize_step(self, step: Mapping[str, Any], index: int) -> dict[str, Any]:
        item = dict(step)
        step_id = self._step_id(item.get("id"), index)
        status = str(item.get("status") or item.get("status_code") or DEFAULT_STEP_STATUS.get(step_id) or "").strip()
        label = _modern_workflow_label(str(
            item.get("admin_label")
            or item.get("label")
            or item.get("name")
            or workflow_status_label(status)
            or f"Etap {index + 1}"
        ).strip())
        item.update(
            {
                "id": step_id,
                "admin_label": label,
                "user_label": _modern_workflow_label(str(item.get("user_label") or label).strip()),
                "status": status,
                "description": sanitize_instruction_html(item.get("description")),
                "next_action": sanitize_instruction_html(item.get("next_action")),
                "next": str(item.get("next") or "").strip(),
                "final": bool(item.get("final", item.get("type") == "end" and step_id == "completed")),
                "rejected": bool(item.get("rejected", "reject" in step_id)),
                "requires_user_action": bool(item.get("requires_user_action", self._user_action(step_id))),
                "requires_officer_action": bool(item.get("requires_officer_action", item.get("type") == "manual_decision")),
            }
        )
        if not item.get("label"):
            item["label"] = label
        if not isinstance(item.get("decisions"), Mapping):
            item["decisions"] = {}
        else:
            item["decisions"] = dict(item["decisions"])
        return item

    def advanced_elements(self, workflow: Mapping[str, Any] | None) -> list[str]:
        normalized = self.normalize(workflow)
        result: list[str] = []
        known_workflow = {
            "name", "label", "initial_step", "steps", "requires_declaration", "requires_contract",
            "requires_agreement_confirmation", "send_email_notifications", "allow_correction",
            "electronic_signature_required", "signed_document_uploader", "decision_settings",
            "email_notifications", "declaration_template_html", "contract_template_html",
            "declaration_filename_pattern", "declaration_generation_mode",
            "contract_generation_mode", "contract_filename_pattern", "contract_number_pattern", "managed_documents",
        }
        result.extend(str(key) for key in normalized if key not in known_workflow)
        for step in normalized["steps"]:
            extras = [key for key in step if key not in KNOWN_STEP_FIELDS]
            if extras:
                result.append(f"{step['id']}: {', '.join(extras)}")
        return result

    @staticmethod
    def _mapping_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            return [{"id": str(key), **(dict(item) if isinstance(item, Mapping) else {"enabled": bool(item)})} for key, item in value.items()]
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _step_id(value: Any, index: int) -> str:
        raw = str(value or "").strip().lower()
        cleaned = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")
        return cleaned or f"stage_{index + 1}"

    @staticmethod
    def _user_action(step_id: str) -> bool:
        return any(token in step_id for token in ("signature", "correction", "declaration", "agreement")) and "review" not in step_id


class WorkflowConfigValidator:
    def validate(self, workflow: Mapping[str, Any] | None) -> list[str]:
        raw_workflow = dict(workflow or {})
        config = WorkflowConfigNormalizer().normalize(workflow)
        steps = config["steps"]
        errors: list[str] = []
        if not str(raw_workflow.get("initial_step") or "").strip():
            errors.append("Wybierz status początkowy workflow.")
        if not steps:
            errors.append("Dodaj co najmniej jeden etap workflow.")
            return errors
        ids: set[str] = set()
        for index, step in enumerate(steps, start=1):
            if not step["admin_label"]:
                errors.append(f"Etap {index} nie ma nazwy dla administratora.")
            if not step["user_label"]:
                errors.append(f"Etap {index} nie ma nazwy dla użytkownika.")
            if not step["status"]:
                errors.append(f"Etap {index} nie ma przypisanego statusu.")
            if step["id"] in ids:
                errors.append(f"Identyfikator etapu „{step['id']}” występuje więcej niż raz.")
            ids.add(step["id"])
        if config["initial_step"] and config["initial_step"] not in ids:
            errors.append("Status początkowy wskazuje nieistniejący etap.")
        for step in steps:
            if step.get("next") and step["next"] not in ids:
                errors.append(f"Etap „{step['admin_label']}” prowadzi do nieistniejącego etapu.")
            for decision, target in step.get("decisions", {}).items():
                if not str(decision).strip() or not str(target).strip():
                    errors.append(f"Etap „{step['admin_label']}” zawiera pustą decyzję.")
                elif target not in ids:
                    errors.append(f"Decyzja „{decision}” prowadzi do nieistniejącego etapu „{target}”.")
        statuses = {step["status"] for step in steps}
        steps_by_id = {step["id"]: step for step in steps}
        document_ids = {str(step.get("document_id") or "") for step in steps}
        if config["requires_declaration"] and not (
            any(status.startswith("DECLARATION_") for status in statuses) or "declaration" in document_ids
        ):
            errors.append("Proces wymaga deklaracji, ale nie ma etapu deklaracji.")
        agreement_required = config["requires_contract"]
        if agreement_required and not (
            ACTIVE_AGREEMENT_STATUSES & statuses or {"agreement", "training_agreement"} & document_ids
        ):
            errors.append(
                "Proces wymaga umowy: formularz ma włączoną obsługę umów, ale brakuje aktywnego etapu umowy."
            )
        agreement_decisions_active = agreement_required and config["requires_agreement_confirmation"]
        if (
            agreement_decisions_active
            and not ACTIVE_AGREEMENT_CONFIRMATION_STATUSES & statuses
        ):
            errors.append(
                "Proces ma włączoną obsługę umów: dodaj etap „Potwierdzenie podpisania umowy przez urząd” "
                "ze statusem „Podpisana umowa wgrana przez beneficjenta” albo "
                "„Umowa oczekuje na podpis po stronie urzędu”."
            )
        decision_rules = {
            "application_decision": {
                "WAITING_FOR_OFFICER_DECISION", "WAITING_FOR_REVIEW", "OFFICER_REVIEW"
            },
            "application_rejection": {
                "WAITING_FOR_OFFICER_DECISION", "WAITING_FOR_REVIEW", "OFFICER_REVIEW"
            },
            "declaration_confirmation": {"DECLARATION_UPLOADED", "DECLARATION_SIGNED"},
            "agreement_confirmation": ACTIVE_AGREEMENT_CONFIRMATION_STATUSES,
            "agreement_rejection": ACTIVE_AGREEMENT_CONFIRMATION_STATUSES,
        }
        for decision in config.get("decision_settings", []):
            decision_id = str(decision.get("id") or "")
            # Stale agreement decisions are inert when agreement handling or
            # the optional office-confirmation branch is off.
            # They can remain in imported JSON for round-trip compatibility, but
            # they must not prevent the form from being saved.
            if decision_id in AGREEMENT_DECISION_IDS and not agreement_decisions_active:
                continue
            step_id = str(decision.get("step_id") or "").strip()
            if not step_id:
                continue
            step = steps_by_id.get(step_id)
            if step is None:
                errors.append(f"Decyzja „{decision.get('label') or decision.get('id')}” wskazuje nieistniejący etap.")
                continue
            allowed = decision_rules.get(decision_id)
            if allowed and step["status"] not in allowed:
                agreement_context = (
                    " Obsługa umów jest włączona."
                    if decision_id in AGREEMENT_DECISION_IDS
                    else ""
                )
                errors.append(
                    f"Decyzja „{decision.get('label') or decision.get('id')}” jest przypisana do etapu "
                    f"„{step['admin_label']}” ({workflow_status_label(step['status'])}), na którym nie może być wykonana."
                    f"{agreement_context}"
                )
            for target_key in ("yes_status", "no_status"):
                target_status = str(decision.get(target_key) or "").strip()
                if target_status and target_status not in STATUS_LABELS:
                    errors.append(
                        f"Decyzja „{decision.get('label') or decision.get('id')}” wskazuje nieznany status docelowy."
                    )
        for notification in config.get("email_notifications", []):
            if notification.get("enabled") and not str(notification.get("template_type") or "").strip():
                errors.append(f"Wybierz szablon dla powiadomienia „{notification.get('label') or notification.get('id')}”.")
            if notification.get("manual_confirmation") and notification.get("automatic"):
                errors.append(
                    f"Powiadomienie „{notification.get('label') or notification.get('id')}” nie może być jednocześnie ręczne i automatyczne."
                )
        return errors


LEGACY_VISIBLE_LABELS = {
    "Umowa podpisana przez beneficjenta": "Umowa podpisana przez urząd",
    "Potwierdzenie podpisanej umowy przez beneficjenta": "Potwierdzenie podpisania umowy przez urząd",
    "Umowa wgrana przez beneficjenta — do potwierdzenia": "Umowa oczekuje na podpis po stronie urzędu",
    "Umowa podpisana przez beneficjenta — do potwierdzenia": "Umowa oczekuje na podpis po stronie urzędu",
    "Umowa podpisana przez beneficjenta - do potwierdzenia": "Umowa oczekuje na podpis po stronie urzędu",
}


def _modern_workflow_label(value: str) -> str:
    return LEGACY_VISIBLE_LABELS.get(value, value)
