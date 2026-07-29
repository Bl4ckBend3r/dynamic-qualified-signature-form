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
    "active",
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
OFFICE_CONFIRMATION_STEP_IDS = frozenset(
    {"stage_10", "stage_11", "beneficiary_agreement_review", "office_agreement_signature"}
)
OFFICE_CONFIRMATION_STATUSES = frozenset(
    {"AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE", "AGREEMENT_SIGNED_BY_OFFICE"}
)

STATUS_TO_STAGE_ALIASES = {
    "FORM_SUBMITTED": ("submission",),
    "WAITING_FOR_OFFICER_DECISION": ("officer_review",),
    "OFFICER_REVIEW": ("officer_review",),
    "DECLARATION_READY": ("declaration",),
    "DECLARATION_WAITING_FOR_SIGNATURE": ("declaration_signature",),
    "AGREEMENT_READY": ("agreement", "training_agreements"),
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE": ("agreement_signature", "training_agreements_signature"),
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE": ("office_agreement_signature", "beneficiary_agreement_review"),
    "AGREEMENT_SIGNED_BY_OFFICE": ("office_agreement_signature",),
    "RETURNED_FOR_CORRECTION": ("waiting_for_correction", "agreement_correction"),
    "PROCESS_COMPLETED": ("completed",),
}


def normalize_decision_assignments(decisions: Any, steps: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Resolve historic decision-to-stage fields into the canonical ``step_id``."""
    by_id = {str(step.get("id") or "").strip(): step for step in steps}
    by_status = {str(step.get("status") or step.get("status_code") or "").strip(): step for step in steps}
    result = []
    for raw in decisions or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        candidate = ""
        for key in ("assigned_stage", "stage_id", "workflow_stage_id", "decision_stage", "step_id"):
            value = str(item.get(key) or "").strip()
            if value:
                candidate = value
                break
        if candidate not in by_id:
            for key in ("trigger_status", "status"):
                status = str(item.get(key) or "").strip()
                if status in by_status:
                    candidate = str(by_status[status].get("id") or "")
                    break
                for alias in STATUS_TO_STAGE_ALIASES.get(status, ()):
                    if alias in by_id:
                        candidate = alias
                        break
                if candidate in by_id:
                    break
        item["step_id"] = candidate if candidate in by_id else ""
        if item["step_id"]:
            item["assigned_stage"] = item["step_id"]
        explicit_active = next((item[key] for key in ("is_active", "active", "enabled") if key in item), None)
        item["active"] = (
            bool(item["step_id"])
            if explicit_active is None
            else explicit_active is True or str(explicit_active).strip().lower() in {"1", "true", "yes", "tak", "on"}
        )
        item["assignment_error"] = "" if item["step_id"] else (
            f"Nie znaleziono aktywnego etapu dla przypisania „{candidate}”." if candidate else "Brak przypisanego etapu."
        )
        result.append(item)
    return result


def repair_agreement_confirmation_path(workflow: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Ensure an enabled agreement workflow includes the office-signature branch.

    Imported forms used a few different identifiers for the beneficiary signature
    step.  The repair deliberately works from statuses as well as identifiers so
    it is safe for both the visual editor and older JSON definitions.
    """
    source = dict(workflow or {})
    steps = [dict(step) for step in source.get("steps") or [] if isinstance(step, Mapping)]
    by_id = {str(step.get("id") or "").strip(): step for step in steps}
    changed = False
    confirmation_required = bool(source.get("requires_contract")) and bool(
        source.get(
            "requires_agreement_confirmation",
            source.get("requires_office_agreement_signature_confirmation", False),
        )
    )
    if not confirmation_required:
        # Preserve the legacy editor behaviour when the optional branch is
        # disabled: the beneficiary signature goes directly to completion.
        for step in steps:
            if step.get("id") in {"agreement_signature", "training_agreements_signature"} and str(step.get("next") or "") in OFFICE_CONFIRMATION_STEP_IDS:
                step["next"] = "completed"
                changed = True
        source["steps"] = steps
        return source, changed

    signature_steps = [
        step for step in steps
        if step.get("id") in {"agreement_signature", "training_agreements_signature"}
        or str(step.get("status") or step.get("status_code") or "")
        == "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE"
    ]
    # A missing agreement branch is a configuration error, not something this
    # focused repair should mask by adding only its final office stage.
    if not signature_steps:
        source["steps"] = steps
        return source, changed
    # The old training workflow has two office steps: review then explicit
    # signature confirmation.  Keep that meaningful distinction intact.
    if {"stage_10", "stage_11", "completed"}.issubset(by_id):
        for signature in signature_steps:
            if str(signature.get("next") or "").strip() == "completed":
                signature["next"] = "stage_10"
                changed = True
        if str(by_id["stage_10"].get("next") or "").strip() != "stage_11":
            by_id["stage_10"]["next"] = "stage_11"
            changed = True
        if str(by_id["stage_11"].get("next") or "").strip() != "completed":
            by_id["stage_11"]["next"] = "completed"
            changed = True
        source["steps"] = steps
        return source, changed
    office_step = next(
        (
            step for step in steps
            if step.get("id") in OFFICE_CONFIRMATION_STEP_IDS
            or str(step.get("status") or step.get("status_code") or "")
            == "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE"
        ),
        None,
    )
    if office_step is None:
        office_id = "office_agreement_signature"
        suffix = 2
        while office_id in by_id:
            office_id = f"office_agreement_signature_{suffix}"
            suffix += 1
        office_step = {
            "id": office_id,
            "type": "manual_decision",
            "label": "Umowa oczekuje na podpis po stronie urzędu",
            "admin_label": "Umowa oczekuje na podpis po stronie urzędu",
            "user_label": "Umowa oczekuje na podpis po stronie urzędu",
            "status": "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",
            "description": "Umowa została podpisana przez beneficjenta i oczekuje na podpis oraz potwierdzenie po stronie urzędu.",
            "next_action": "Oczekuj na podpis i potwierdzenie po stronie urzędu.",
            "requires_officer_action": True,
            "decisions": {"accepted": "completed"},
            "next": "completed",
        }
        completed_index = next((index for index, step in enumerate(steps) if step.get("id") == "completed"), len(steps))
        steps.insert(completed_index, office_step)
        by_id[office_id] = office_step
        changed = True
    office_id = str(office_step["id"])
    if str(office_step.get("next") or "").strip() in {"", "completed"} and str(office_step.get("next") or "").strip() != "completed":
        office_step["next"] = "completed"
        changed = True
    decisions = dict(office_step.get("decisions") or {})
    if decisions.get("accepted") != "completed":
        decisions["accepted"] = "completed"
        office_step["decisions"] = decisions
        changed = True
    for signature in signature_steps:
        if str(signature.get("next") or "").strip() in {"", "completed"}:
            signature["next"] = office_id
            changed = True
    source["steps"] = steps
    return source, changed


def repair_form_definition_agreement_confirmation(
    form_definition: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Return a form definition with only the known office-confirmation shortcut repaired."""
    definition = dict(form_definition or {})
    repaired_workflow, changed = repair_agreement_confirmation_path(definition.get("workflow"))
    if changed:
        definition["workflow"] = repaired_workflow
    return definition, changed


def is_workflow_step_active(step: Mapping[str, Any], workflow: Mapping[str, Any]) -> bool:
    step_id = str(step.get("id") or "").strip()
    status = str(step.get("status") or step.get("status_code") or "").strip()
    step_type = str(step.get("type") or "").strip()
    document_id = str(step.get("document_id") or "").strip()
    requires_declaration = bool(workflow.get("requires_declaration"))
    requires_contract = bool(workflow.get("requires_contract"))
    requires_confirmation = requires_contract and bool(workflow.get("requires_agreement_confirmation"))
    allow_correction = bool(workflow.get("allow_correction", True))

    if step_id in OFFICE_CONFIRMATION_STEP_IDS or status in OFFICE_CONFIRMATION_STATUSES:
        return requires_confirmation
    is_correction = (
        "correction" in step_id
        or "correction" in step_type
        or status in {"WAITING_FOR_CORRECTION", "CORRECTION_REQUIRED", "RETURNED_FOR_CORRECTION"}
    )
    is_agreement = (
        "agreement" in step_id
        or "contract" in step_id
        or document_id in {"agreement", "training_agreement"}
        or status.startswith("AGREEMENT_")
    )
    if is_correction:
        return allow_correction and (requires_contract if is_agreement else True)
    if (
        "declaration" in step_id
        or document_id == "declaration"
        or status.startswith("DECLARATION_")
    ):
        return requires_declaration
    if is_agreement:
        return requires_contract
    return bool(step.get("active", True))


def active_workflow_steps(workflow: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(step)
        for step in workflow.get("steps") or []
        if isinstance(step, Mapping) and is_workflow_step_active(step, workflow)
    ]


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
            source.get(
                "requires_agreement_confirmation",
                source.get("requires_office_agreement_signature_confirmation", source["requires_contract"]),
            )
        )
        source["send_email_notifications"] = bool(source.get("send_email_notifications", False))
        source["allow_correction"] = bool(source.get("allow_correction", True))
        source["electronic_signature_required"] = bool(source.get("electronic_signature_required", True))
        source["signed_document_uploader"] = str(source.get("signed_document_uploader") or "beneficiary")
        raw_decisions = self._mapping_list(source.get("decision_settings"))
        source["email_notifications"] = self._mapping_list(source.get("email_notifications"))
        source["steps"] = [self.normalize_step(step, index) for index, step in enumerate(raw_steps) if isinstance(step, Mapping)]
        source["decision_settings"] = normalize_decision_assignments(raw_decisions, source["steps"])
        for decision in source["decision_settings"]:
            if decision.get("label"):
                decision["label"] = _modern_workflow_label(str(decision["label"]).strip())
        source, _repaired = repair_agreement_confirmation_path(source)
        for step in source["steps"]:
            step["active"] = is_workflow_step_active(step, source)
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
                "active": bool(item.get("active", True)),
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
            "contract_generation_mode", "contract_filename_pattern", "contract_number_pattern",
            "contract_show_all_trainings_total", "managed_documents",
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
        all_steps = config["steps"]
        steps = active_workflow_steps(config)
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
        all_ids = {step["id"] for step in all_steps}
        if config["initial_step"] and config["initial_step"] not in all_ids:
            errors.append("Status początkowy wskazuje nieistniejący etap.")
            errors.append(
                f"Nie istnieje etap o ID „{config['initial_step']}”. Wybierz istniejący etap z listy „Etap początkowy”."
            )
        for step in steps:
            if step.get("next") and step["next"] not in all_ids:
                errors.append(
                    f"Etap „{step['admin_label']}” prowadzi do nieistniejącego etapu „{step['next']}”. "
                    "Wybierz istniejący kolejny etap albo oznacz ten etap jako końcowy."
                )
            for decision, target in step.get("decisions", {}).items():
                if not str(decision).strip() or not str(target).strip():
                    errors.append(f"Etap „{step['admin_label']}” zawiera pustą decyzję.")
                elif target not in all_ids:
                    errors.append(
                        f"Decyzja „{decision}” w etapie „{step['admin_label']}” prowadzi do nieistniejącego "
                        f"etapu „{target}”. Wskaż istniejący etap docelowy w karcie tej decyzji."
                    )
        statuses = {step["status"] for step in steps}
        steps_by_id = {step["id"]: step for step in steps}
        all_steps_by_id = {step["id"]: step for step in all_steps}
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
        signature_step = next(
            (step for step in steps if step.get("id") == "training_agreements_signature"),
            None,
        )
        if (
            agreement_decisions_active
            and signature_step
            and signature_step.get("next") == "completed"
        ):
            errors.append(
                "Włączono potwierdzenie podpisania umowy przez urząd, ale etap "
                "„Umowa oczekuje na podpis beneficjenta” prowadzi bezpośrednio do zakończenia procesu. "
                "Ustaw kolejny etap na „Oczekuje na podpis urzędu” albo wyłącz wymaganie "
                "potwierdzenia podpisu przez urząd."
            )
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
            if decision_id == "declaration_confirmation" and not config["requires_declaration"]:
                continue
            if decision_id == "correction_required" and not config["allow_correction"]:
                continue
            step_id = str(decision.get("step_id") or "").strip()
            if not step_id:
                continue
            step = steps_by_id.get(step_id)
            if step is None:
                if step_id in all_steps_by_id:
                    continue
                errors.append(
                    f"Decyzja „{decision.get('label') or decision.get('id')}” wskazuje nieistniejący etap "
                    f"„{step_id}”. Wybierz etap z listy dostępnych etapów albo wyłącz tę decyzję."
                )
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
