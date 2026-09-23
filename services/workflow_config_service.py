from __future__ import annotations

import re
from copy import deepcopy
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
    "document_lifecycle", "document_options", "document_instructions",
    "explicit_stage", "action", "decision_name", "decision_scope", "decision_group",
    "completion_policy", "completion_next", "decision_email", "allow_loop",
    "id",
    "type",
    "label",
    "admin_label",
    "user_label",
    "status",
    "status_code",
    "next",
    "transitions",
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
    "stage_type",
    "instruction",
    "side_effects",
    "sla",
    "deadline",
    "reminders",
    "escalation",
    "business_calendar_id",
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
        item["requested_step_id"] = candidate if candidate and candidate not in by_id else ""
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
    if workflow.get("flow_mode") == "explicit":
        return step.get("active", True) is not False
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


# Legacy document phases are interpreted only when converting an editable draft.
_LEGACY_DOCUMENT_PHASES = {
    **{f"{role}_{suffix}": (document, phase)
       for role, document in (("DECLARATION", "declaration"), ("AGREEMENT", "agreement"))
       for suffix, phase in {
           "REQUIRED": "generating", "READY": "ready", "GENERATED": "ready",
           "WAITING_FOR_SIGNATURE": "awaiting_signature", "UPLOADED": "verifying",
           "SIGNED": "participant_signed", "SIGNATURE_INVALID": "verification_failed",
       }.items()},
    "AGREEMENT_WAITING_FOR_BENEFICIARY_SIGNATURE": ("agreement", "awaiting_signature"),
    "WAITING_FOR_AGREEMENT_SIGNATURE": ("agreement", "awaiting_signature"),
    "AGREEMENT_UPLOADED_BY_BENEFICIARY": ("agreement", "participant_signed"),
    "AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE": ("agreement", "awaiting_office_signature"),
    "AGREEMENT_SIGNED_BY_OFFICE": ("agreement", "office_signed"),
    "BENEFICIARY_AGREEMENT_CONFIRMED": ("agreement", "office_signed"),
}
_DOCUMENT_ACTION_PHASES = {
    "generate_document": "generating", "generate_documents": "generating",
    "await_signature": "awaiting_signature", "signature_upload": "upload",
    "signature_upload_many": "upload", "upload_document": "upload",
}


def collapse_draft_document_stages(definition: Mapping[str, Any]) -> dict:
    """Collapse complete linear document chains in a draft, including split composites.

    Versioned runtime reads never call this adapter. Business decisions and
    incompatible phase-specific permissions remain independent stages.
    """
    from services.documents.document_workflow_service import document_policy, is_document_step
    from services.form_config_service import FormConfigService

    result = deepcopy(definition)
    # Normalize supported legacy references before the workflow normalizer fills defaults.
    for step in (result.get("workflow") or {}).get("steps", []):
        if "next" not in step and "next_step" in step:
            step["next"] = step["next_step"]
    workflow = WorkflowConfigNormalizer().normalize(result.get("workflow") or {})
    documents = {d["id"]: d for d in FormConfigService().normalize_documents_config(result) if d.get("enabled", True)}
    steps = workflow["steps"]
    by_id = {s["id"]: s for s in steps}
    assigned = {o.get("step_id") for o in workflow.get("decision_types", []) if o.get("active", True)}
    phases = {}
    for step in steps:
        if not step.get("active", True) or step.get("final"):
            continue
        if step.get("stage_type") == "decision" or step.get("type") == "manual_decision" or step["id"] in assigned:
            continue
        if step.get("transitions") or step.get("decisions"):
            continue
        if any(step.get(key) for key in ("side_effects", "sla", "deadline", "reminders", "escalation")):
            continue
        role, phase = _LEGACY_DOCUMENT_PHASES.get(str(step.get("status") or "").upper(), ("", ""))
        document_id = step.get("document_id") or role
        if document_id == "agreement" and document_id not in documents and "training_agreement" in documents:
            document_id = "training_agreement"
        if document_id not in documents:
            continue
        action = step.get("action") if step.get("action") not in {None, "none"} else step.get("type")
        phase = phase or _DOCUMENT_ACTION_PHASES.get(action, "")
        if not phase and not is_document_step(step) and step.get("type") != "document" and step.get("stage_type") != "document":
            continue
        phases[step["id"]] = (document_id, phase)

    aliases, replacements, removed = {}, {}, set()
    for first in steps:
        first_id = first["id"]
        if first_id not in phases or first_id in removed:
            continue
        if any(s.get("next") == first_id and phases.get(s["id"], (None,))[0] == phases[first_id][0] for s in steps):
            continue
        chain = [first]
        while chain[-1].get("next") in phases:
            following = by_id[chain[-1]["next"]]
            if phases[following["id"]][0] != phases[first_id][0] or following in chain:
                break
            chain.append(following)
        chain_ids = {s["id"] for s in chain}
        exit_id = chain[-1].get("next")
        if not exit_id or exit_id in chain_ids:
            continue
        if len(chain) == 1 and is_document_step(first):
            continue

        def conflicting_availability(value):
            if isinstance(value, list):
                return any(conflicting_availability(v) for v in value)
            if not isinstance(value, dict):
                return False
            rows = [a for a in value.get("availability", []) if a.get("step") in chain_ids]
            if rows and (len(rows) != len(chain_ids) or any({k: v for k, v in a.items() if k != "step"} != {k: v for k, v in rows[0].items() if k != "step"} for a in rows)):
                return True
            return any(conflicting_availability(v) for k, v in value.items() if k != "availability")
        if conflicting_availability(result.get("fields", [])) or conflicting_availability(result.get("consents", [])):
            continue
        document = documents[phases[first_id][0]]
        canonical_id = "agreement" if document["id"] == "training_agreement" else document["id"]
        # A separate business stage may already own that ID.
        if canonical_id in by_id and canonical_id not in chain_ids:
            canonical_id = first_id
        if canonical_id in aliases.values():
            canonical_id = first_id
        policy = document_policy(first, document)
        instructions = {}
        triggers = []
        for phase_step in chain:
            substate = phases[phase_step["id"]][1]
            # Capabilities enabled in any former phase belong to the merged lifecycle.
            for key, enabled in (phase_step.get("document_options") or {}).items():
                if key in policy:
                    policy[key] = policy[key] or enabled
            if substate in {"awaiting_office_signature", "office_signed"}:
                policy["office_signature"] = True
            if substate in {"awaiting_signature", "participant_signed"}:
                policy["participant_signature"] = policy["upload_required"] = True
            for key, value in (phase_step.get("document_instructions") or {}).items():
                destination = instructions.setdefault(key, {})
                for part, text in value.items():
                    if text and text != destination.get(part):
                        destination[part] = "\n".join(v for v in (destination.get(part), text) if v)
            instruction = {key: phase_step.get(key) or phase_step.get("instruction", {}).get(key, "") for key in ("description", "next_action")}
            if substate and any(instruction.values()):
                destination = instructions.setdefault(substate, {})
                for part, text in instruction.items():
                    if text and text != destination.get(part):
                        destination[part] = "\n".join(v for v in (destination.get(part), text) if v)
            for trigger in phase_step.get("triggers") or []:
                if trigger not in triggers:
                    triggers.append(trigger)
        for instruction in instructions.values():
            instruction.setdefault("description", "")
            instruction.setdefault("next_action", "")
        label = {"declaration": "Deklaracja", "agreement": "Umowa", "training_agreement": "Umowa"}.get(document["id"], document.get("label") or document["id"])
        merged = deepcopy(first)
        merged.update(id=canonical_id, type="document", stage_type="document", document_lifecycle="composite", action="none",
                      document_id=document["id"], document_options=policy, document_instructions=instructions,
                      admin_label=label, label=label, next=exit_id, requires_user_action=False, requires_officer_action=False,
                      triggers=triggers)
        if "next_step" in merged:
            merged["next_step"] = exit_id
        replacements[first_id] = merged
        for phase_step in chain:
            aliases[phase_step["id"]] = canonical_id
        removed.update(chain_ids - {first_id})

    workflow["steps"] = [replacements.get(s["id"], s) for s in steps if s["id"] not in removed]
    # Rewrite reference fields only: never replace IDs inside instructions or templates.
    reference_keys = {"next", "next_step", "initial_step", "step", "stage", "step_id", "target_step", "target_step_id",
                      "completion_next", "assigned_stage", "return_step", "return_to_step", "correction_step", "correction_target",
                      "available_at_step", "workflow_step", "requested_step_id", "stage_id", "workflow_stage_id", "decision_stage"}
    target_status_keys = {"yes_status", "no_status", "correction_status", "status_on_yes", "status_on_no", "target_status"}
    def remap(value):
        if isinstance(value, list):
            return [remap(v) for v in value]
        if not isinstance(value, dict):
            return value
        mapped = {}
        for key, val in value.items():
            if key in reference_keys | target_status_keys and isinstance(val, str):
                mapped[key] = aliases.get(val, val)
            elif key in {"step_ids", "steps_allowed"} and isinstance(val, list):
                mapped[key] = list(dict.fromkeys(aliases.get(v, v) for v in val))
            elif key == "decisions" and isinstance(val, dict):
                mapped[key] = {k: aliases.get(v, v) if isinstance(v, str) else remap(v) for k, v in val.items()}
            elif key in {"diagram_positions", "node_positions"} and isinstance(val, dict):
                positions = {}
                for old_id, position in val.items():
                    positions.setdefault(aliases.get(old_id, old_id), position)
                mapped[key] = positions
            elif key == "diagram_layout" and isinstance(val, dict):
                nodes = {}
                for old_id, position in (val.get("nodes") or {}).items():
                    nodes.setdefault(aliases.get(old_id, old_id), position)
                mapped[key] = {**val, "nodes": nodes}
            else:
                mapped[key] = remap(val)
        if isinstance(mapped.get("availability"), list):
            mapped["availability"] = [a for i, a in enumerate(mapped["availability"]) if a not in mapped["availability"][:i]]
        return mapped
    result["workflow"] = workflow
    return remap(result)


class WorkflowConfigNormalizer:
    def normalize(self, workflow: Mapping[str, Any] | None) -> dict[str, Any]:
        source = dict(workflow or {})
        raw_steps = source.get("steps") if isinstance(source.get("steps"), list) else []
        source["name"] = str(source.get("name") or source.get("label") or "Workflow").strip()
        source["schema_version"] = 2
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
        raw_decision_types = self._mapping_list(source.get("decision_types"))
        source["email_notifications"] = self._mapping_list(source.get("email_notifications"))
        source["steps"] = [self.normalize_step(step, index) for index, step in enumerate(raw_steps) if isinstance(step, Mapping)]
        if source.get("flow_mode") == "explicit":
            source["initial_step"] = str(workflow.get("initial_step") or "").strip()
            for step, raw in zip(source["steps"], [s for s in raw_steps if isinstance(s, Mapping)]):
                for key in ("id", "admin_label", "user_label"):
                    step[key] = str(raw.get(key) or "").strip()
                # Preserve invalid input for validation instead of silently repairing it.
                raw_transitions = raw.get("transitions", [])
                if not isinstance(raw_transitions, list) or any(not isinstance(t, Mapping) or not isinstance(t.get("when", {}), Mapping) for t in raw_transitions):
                    step["invalid_transitions"] = True

        source["decision_settings"] = normalize_decision_assignments(raw_decisions, source["steps"])
        source["decision_types"] = [
            {
                **item,
                "code": str(item.get("code") or "").strip(),
                "label": str(item.get("label") or item.get("code") or "").strip(),
                "semantic_category": str(item.get("semantic_category") or "neutral").strip(),
                "require_reason": bool(item.get("reason_required", item.get("require_reason", False))),
                "scope": str(item.get("scope") or "both"),
                "step_id": str(item.get("step_id") or "").strip(),
                "target_step": str(item.get("target_step") or "").strip(),
                "active": bool(item.get("active", True)),
            }
            for item in raw_decision_types if item.get("code")
        ]
        if "diagram_layout" in source:
            source["diagram_layout"] = self._normalize_diagram_layout(source.get("diagram_layout"))
        for decision in source["decision_settings"]:
            if decision.get("label"):
                decision["label"] = _modern_workflow_label(str(decision["label"]).strip())
            self._normalize_decision(decision)
        if source.get("flow_mode") != "explicit":
            source, _repaired = repair_agreement_confirmation_path(source)
        for step in source["steps"]:
            if source.get("flow_mode") == "explicit":
                step["explicit_stage"] = True
            step["active"] = is_workflow_step_active(step, source)
        return source

    def normalize_step(self, step: Mapping[str, Any], index: int) -> dict[str, Any]:
        item = dict(step)
        if isinstance(item.get("document_instructions"), Mapping):
            item["document_instructions"] = {key: {**value, "description": sanitize_instruction_html(value.get("description")),
                "next_action": sanitize_instruction_html(value.get("next_action"))} if isinstance(value, Mapping) else value
                for key, value in item["document_instructions"].items()}
        instruction = item.get("instruction") if isinstance(item.get("instruction"), Mapping) else {}
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
                "transitions": [
                    {
                        "when": dict(transition["when"]) if isinstance(transition.get("when"), Mapping) else {},
                        "next": str(transition.get("next") or "").strip(),
                    }
                    for transition in item.get("transitions") or []
                    if isinstance(transition, Mapping)
                ],
                "final": bool(item.get("final") or item.get("type") in {"end", "final"} or item.get("stage_type") == "final"),
                "rejected": bool(item.get("rejected", "reject" in step_id)),
                "requires_user_action": bool(item.get("requires_user_action", self._user_action(step_id))),
                "requires_officer_action": bool(item.get("requires_officer_action", item.get("type") == "manual_decision")),
                "active": bool(item.get("active", True)),
                "stage_type": str(item.get("stage_type") or self._stage_type(item, step_id)).strip(),
                "instruction": {
                    "title": str(instruction.get("title") or item.get("user_label") or label).strip(),
                    "description": sanitize_instruction_html(
                        instruction.get("description", item.get("description"))
                    ),
                    "next_action": sanitize_instruction_html(
                        instruction.get("next_action", item.get("next_action"))
                    ),
                    "links": list(instruction.get("links") or []),
                    "conditional_message": sanitize_instruction_html(
                        instruction.get("conditional_message")
                    ),
                },
                "side_effects": list(item.get("side_effects") or []),
            }
        )
        if not item.get("label"):
            item["label"] = label
        if not isinstance(item.get("decisions"), Mapping):
            item["decisions"] = {}
        else:
            item["decisions"] = dict(item["decisions"])
        return item

    @staticmethod
    def _stage_type(item: Mapping[str, Any], step_id: str) -> str:
        if item.get("final") or item.get("type") == "end":
            return "final"
        if item.get("type") in {"manual_decision", "decision"}:
            return "decision"
        if item.get("document_id") or any(token in step_id for token in ("declaration", "agreement")):
            return "document"
        if item.get("type") == "manual_decision" or item.get("requires_officer_action"):
            return "officer_action"
        if item.get("requires_user_action"):
            return "user_action"
        return "system"

    @staticmethod
    def _normalize_decision(decision: dict[str, Any]) -> None:
        decision["technical_name"] = str(
            decision.get("technical_name") or decision.get("id") or ""
        ).strip()
        decision["display_name"] = str(
            decision.get("display_name") or decision.get("label") or decision["technical_name"]
        ).strip()
        decision["require_reason"] = bool(decision.get("require_reason", False))
        decision["user_message"] = sanitize_instruction_html(decision.get("user_message"))
        decision["system_action"] = str(decision.get("system_action") or "").strip()
        decision["email_template"] = str(decision.get("email_template") or "").strip()
        if not isinstance(decision.get("outcomes"), list):
            outcomes = []
            if decision.get("yes_status"):
                outcomes.append(
                    {"code": "yes", "label": "Tak", "target_status": str(decision["yes_status"])}
                )
            if decision.get("no_status"):
                outcomes.append(
                    {"code": "no", "label": "Nie", "target_status": str(decision["no_status"])}
                )
            if decision.get("correction_status"):
                outcomes.append(
                    {
                        "code": "correction",
                        "label": "Do poprawy",
                        "target_status": str(decision["correction_status"]),
                    }
                )
            decision["outcomes"] = outcomes

    def advanced_elements(self, workflow: Mapping[str, Any] | None) -> list[str]:
        normalized = self.normalize(workflow)
        result: list[str] = []
        known_workflow = {
            "name", "label", "initial_step", "steps", "requires_declaration", "requires_contract",
            "requires_agreement_confirmation", "send_email_notifications", "allow_correction",
            "electronic_signature_required", "signed_document_uploader", "decision_settings",
            "email_notifications", "declaration_template_html", "contract_template_html",
            "declaration_template_source", "declaration_docx_template", "declaration_builder_document",
            "declaration_builder_active_document", "declaration_builder_status", "declaration_builder_updated_at",
            "declaration_builder_updated_by", "declaration_template_updated_at", "declaration_template_updated_by",
            "declaration_template_updated_source", "contract_template_source", "contract_docx_template",
            "contract_builder_document", "contract_builder_active_document", "contract_builder_status",
            "contract_builder_updated_at", "contract_builder_updated_by", "contract_template_updated_at",
            "contract_template_updated_by", "contract_template_updated_source",
            "declaration_filename_pattern", "declaration_generation_mode",
            "contract_generation_mode", "contract_filename_pattern", "contract_number_pattern",
            "contract_show_all_trainings_total", "managed_documents",
            "schema_version", "diagram_layout", "flow_mode", "decision_types",
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
    def _normalize_diagram_layout(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        raw_nodes = value.get("nodes")
        if not isinstance(raw_nodes, Mapping):
            return {}
        nodes: dict[str, dict[str, float]] = {}
        for raw_id, raw_position in raw_nodes.items():
            node_id = str(raw_id or "").strip()
            if not node_id or not isinstance(raw_position, Mapping):
                continue
            try:
                x = float(raw_position.get("x"))
                y = float(raw_position.get("y"))
            except (TypeError, ValueError):
                continue
            if not (-10000 <= x <= 10000 and -10000 <= y <= 10000):
                continue
            nodes[node_id] = {"x": round(x, 2), "y": round(y, 2)}
        return {"nodes": nodes} if nodes else {}

    @staticmethod
    def _step_id(value: Any, index: int) -> str:
        raw = str(value or "").strip().lower()
        cleaned = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")
        return cleaned or f"stage_{index + 1}"

    @staticmethod
    def _user_action(step_id: str) -> bool:
        return any(token in step_id for token in ("signature", "correction", "declaration", "agreement")) and "review" not in step_id


class WorkflowConfigValidator:
    def validate(
        self,
        workflow: Mapping[str, Any] | None,
        form_config: Mapping[str, Any] | None = None,
    ) -> list[str]:
        raw_workflow = dict(workflow or {})
        strict_graph_validation = raw_workflow.get("flow_mode") == "explicit" or int(raw_workflow.get("schema_version") or 1) >= 2
        explicit = raw_workflow.get("flow_mode") == "explicit"
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
            from services.documents.document_workflow_service import is_document_step, validate_document_step
            if is_document_step(step) and form_config is not None:
                errors.extend(f"Etap „{step['admin_label']}” ({step['id']}): {error}"
                              for error in validate_document_step(step, form_config))
            if not step["admin_label"]:
                errors.append(f"Etap {index} nie ma nazwy dla administratora.")
            if not step["user_label"]:
                errors.append(f"Etap {index} nie ma nazwy dla użytkownika.")
            if not step["status"]:
                errors.append(f"Etap {index} nie ma przypisanego statusu.")
            if step["id"] in ids:
                errors.append(f"Identyfikator etapu „{step['id']}” występuje więcej niż raz.")
            ids.add(step["id"])
            sla = step.get("sla") if isinstance(step.get("sla"), Mapping) else {}
            deadline = sla.get("deadline") if isinstance(sla.get("deadline"), Mapping) else step.get("deadline")
            if deadline is not None:
                if not isinstance(deadline, Mapping):
                    errors.append(f"SLA etapu „{step['admin_label']}” musi zawierać obiekt deadline.")
                else:
                    try:
                        deadline_value = int(deadline.get("value"))
                    except (TypeError, ValueError):
                        deadline_value = -1
                    if deadline_value < 0:
                        errors.append(f"SLA etapu „{step['admin_label']}” ma nieprawidłową wartość terminu.")
                    if str(deadline.get("unit") or "") not in {"hours", "calendar_days", "business_days"}:
                        errors.append(f"SLA etapu „{step['admin_label']}” ma nieobsługiwaną jednostkę terminu.")
        all_ids = {step["id"] for step in (steps if explicit else all_steps)}
        if config["initial_step"] and config["initial_step"] not in all_ids:
            errors.append("Status początkowy wskazuje nieistniejący etap.")
            errors.append(
                f"Nie istnieje etap o ID „{config['initial_step']}”. Wybierz istniejący etap z listy „Etap początkowy”."
            )
        for step in steps:
            options = [option for option in config.get("decision_types", []) if option.get("active", True) and option.get("step_id") == step["id"]]
            if step.get("final") and (step.get("next") or step.get("transitions") or step.get("decisions") or options or step.get("completion_next")):
                errors.append(f"Etap końcowy „{step['admin_label']}” nie może mieć kolejnego etapu ani decyzji.")
            if explicit:
                kind = step.get("stage_type")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", step["id"]):
                    errors.append(f"ID etapu „{step['id']}” musi być stabilnym identyfikatorem technicznym.")
                if kind not in {"user_action", "officer_action", "system", "automatic", "decision", "document", "final"}:
                    errors.append(f"Etap „{step['admin_label']}” ma nieobsługiwany typ.")
                if step.get("invalid_transitions"):
                    errors.append(f"Etap „{step['admin_label']}”: przejścia muszą być tablicą JSON.")
                if step.get("action", "none") not in {"none", "generate_document", "await_signature"}:
                    errors.append(f"Etap „{step['admin_label']}” ma nieobsługiwaną akcję.")
                if step.get("action") == "await_signature" and kind not in {"user_action", "document"}:
                    errors.append("Oczekiwanie na podpis wymaga etapu Dokument lub Akcja użytkownika.")
                if kind == "decision":
                    if step.get("decision_scope") not in {"submission", "item"}:
                        errors.append("Wybierz zakres decyzji.")
                    if any(o.get("scope") != step.get("decision_scope") for o in options):
                        errors.append("Zakres opcji musi być zgodny z zakresem etapu decyzji.")
                    if len({o.get("code") for o in options}) != len(options):
                        errors.append("Opcje decyzji muszą mieć unikalne kody w etapie.")
                    definition_ids = [str(o["definition_id"]) for o in options if o.get("definition_id") is not None]
                    if len(set(definition_ids)) != len(definition_ids):
                        errors.append("Ta sama opcja katalogowa (DecisionOption ID) może wystąpić w etapie tylko raz.")
                    if len({o.get('code') for o in options}) < 2:
                        errors.append(f"Decyzja w etapie „{step['admin_label']}” wymaga co najmniej 2 opcji.")
                    if not str(step.get("decision_name") or "").strip():
                        errors.append(f"Etap „{step['admin_label']}” wymaga nazwy decyzji.")
                    if step.get("next"):
                        errors.append(f"Decyzja „{step['admin_label']}” musi definiować cel osobno dla każdej opcji.")
                elif options:
                    errors.append(f"Opcje decyzji można przypisać wyłącznie do etapu typu Decyzja: „{step['admin_label']}”.")
                targets = [step.get("next"), step.get("completion_next"), *(t.get("next") for t in step.get("transitions", [])), *(o.get("target_step") for o in options)]
                if step["id"] in targets and not step.get("allow_loop"):
                    errors.append(f"Etap „{step['admin_label']}” wskazuje sam na siebie bez jawnej zgody na pętlę.")
                if kind == "decision" and step.get("decision_scope") == "item":
                    group = str(step.get("decision_group") or "")
                    if not group or step.get("completion_policy") != "ALL":
                        errors.append(f"Decyzja „{step['admin_label']}” wymaga grupy i polityki ALL.")
                    if form_config and group not in {f.get("name") for f in form_config.get("fields", []) if f.get("type") == "repeatable_group"}:
                        errors.append(f"Decyzja „{step['admin_label']}” wskazuje nieistniejącą grupę.")
                    if not step.get("completion_next") or step.get("completion_next") not in all_ids:
                        errors.append(f"Decyzja „{step['admin_label']}” wymaga celu po zakończeniu ALL.")
                email = step.get("decision_email") or {}
                if not isinstance(email, Mapping):
                    errors.append("Konfiguracja e-mail musi być obiektem JSON.")
                    email = {}
                if email.get("enabled") and not email.get("template_type"):
                    errors.append(f"Wybierz szablon e-mail dla decyzji „{step['admin_label']}”.")
            for transition in step.get("transitions", []):
                if transition.get("next") not in all_ids:
                    errors.append(f"Przejście warunkowe etapu „{step['admin_label']}” wskazuje nieistniejący etap.")
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
            if strict_graph_validation and not step.get("final") and not step.get("rejected") and not step.get("next") and not step.get("decisions") and not step.get("transitions") and not options:
                errors.append(
                    f"Etap '{step['admin_label']}' nie ma wyjścia i nie jest etapem końcowym."
                )
        statuses = {step["status"] for step in steps}
        steps_by_id = {step["id"]: step for step in steps}
        all_steps_by_id = {step["id"]: step for step in all_steps}
        document_ids = {str(step.get("document_id") or "") for step in steps}
        if not explicit and config["requires_declaration"] and not (
            any(status.startswith("DECLARATION_") for status in statuses) or "declaration" in document_ids
        ):
            errors.append("Proces wymaga deklaracji, ale nie ma etapu deklaracji.")
        agreement_required = config["requires_contract"] and not explicit
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
            if explicit:
                continue
            decision_id = str(decision.get("id") or "")
            if not decision.get("active"):
                continue
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
                requested_step_id = str(decision.get("requested_step_id") or "").strip()
                if requested_step_id:
                    errors.append(
                        f"Decyzja „{decision.get('label') or decision.get('id')}” wskazuje nieistniejący etap "
                        f"„{requested_step_id}”. Wybierz istniejący etap albo ją dezaktywuj."
                    )
                else:
                    errors.append(
                        f"Aktywna decyzja „{decision.get('label') or decision.get('id')}” nie ma przypisanego etapu. "
                        "Przypisz ją do etapu albo ją dezaktywuj."
                    )
                continue
            has_real_transition = any(
                str(decision.get(key) or "").strip()
                for key in ("yes_status", "no_status", "correction_status")
            ) or any(
                str(outcome.get("target_status") or outcome.get("target_step") or "").strip()
                for outcome in decision.get("outcomes") or []
                if isinstance(outcome, Mapping)
            )
            if (
                not has_real_transition
                and decision_id in AGREEMENT_DECISION_IDS
                and (steps_by_id.get(step_id) or {}).get("status") in ACTIVE_AGREEMENT_CONFIRMATION_STATUSES
            ):
                # The office agreement confirmation uses the built-in service
                # transitions when an administrator has not overridden them.
                has_real_transition = True
            if not has_real_transition:
                errors.append(
                    f"Aktywna decyzja „{decision.get('label') or decision.get('id')}” musi mieć "
                    "przynajmniej jedno realne przejście."
                )
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
            for target_key in ("yes_status", "no_status", "correction_status"):
                target_status = str(decision.get(target_key) or "").strip()
                if target_status and target_status not in STATUS_LABELS:
                    errors.append(
                        f"Decyzja „{decision.get('label') or decision.get('id')}” wskazuje nieznany status docelowy."
                    )
            for outcome in decision.get("outcomes") or []:
                if not str(outcome.get("target_status") or outcome.get("target_step") or "").strip():
                    errors.append(
                        f"Wynik „{outcome.get('label') or outcome.get('code')}” decyzji "
                        f"„{decision.get('display_name') or decision.get('id')}” nie ma zdefiniowanego celu."
                    )
        for decision in config.get("decision_types") or []:
            if explicit and not decision.get("active", True):
                continue
            if decision.get("scope", "both") not in {"both", "submission", "item"}:
                errors.append(f"Decyzja „{decision.get('code')}” ma nieprawidłowy zakres.")
            if decision.get("semantic_category") not in {"positive", "negative", "correction", "neutral"}:
                errors.append(f"Decyzja „{decision.get('code')}” ma nieprawidłową kategorię semantyczną.")
            if decision.get("step_id") not in all_ids:
                errors.append(f"Decyzja „{decision.get('code')}” wskazuje nieistniejący etap decyzji.")
            if decision.get("target_step") not in all_ids:
                errors.append(f"Decyzja „{decision.get('code')}” wskazuje nieistniejący etap docelowy.")
        for notification in config.get("email_notifications", []):
            if form_config is not None and notification.get("recipient_source") is not None:
                from services.mail_recipient_service import MailRecipientResolver
                errors.extend(MailRecipientResolver.validate_source(notification["recipient_source"], form_config))
            if notification.get("enabled") and not str(notification.get("template_type") or "").strip():
                errors.append(f"Wybierz szablon dla powiadomienia „{notification.get('label') or notification.get('id')}”.")
            if notification.get("manual_confirmation") and notification.get("automatic"):
                errors.append(
                    f"Powiadomienie „{notification.get('label') or notification.get('id')}” nie może być jednocześnie ręczne i automatyczne."
                )
        if form_config is not None:
            from services.mail_recipient_service import MailRecipientResolver
            for step in steps:
                source = (step.get("decision_email") or {}).get("recipient_source")
                if source is not None:
                    errors.extend(MailRecipientResolver.validate_source(source, form_config))
        if strict_graph_validation:
            errors.extend(self._validate_graph(config, steps))
        if form_config:
            errors.extend(self._validate_modules(config, steps, form_config))
        if explicit and config["initial_step"] not in {step["id"] for step in steps}:
            errors.append("Wybierz jeden aktywny etap początkowy.")
        if explicit and len([s for s in steps if s.get("initial")]) > 1:
            errors.append("Workflow nie może mieć dwóch etapów początkowych.")
        return errors

    @staticmethod
    def _validate_graph(config: Mapping[str, Any], steps: list[dict[str, Any]]) -> list[str]:
        if not steps:
            return []
        by_id = {step["id"]: step for step in steps}
        initial = str(config.get("initial_step") or "")
        if initial not in by_id:
            return []
        edges = {
            step_id: {
                target
                for target in [
                    step.get("next"),
                    step.get("completion_next"),
                    *(step.get("decisions") or {}).values(),
                    *(transition.get("next") for transition in step.get("transitions") or []),
                    *(decision.get("target_step") for decision in config.get("decision_types") or []
                      if decision.get("active", True) and decision.get("step_id") == step_id),
                ]
                if target in by_id
            }
            for step_id, step in by_id.items()
        }
        reachable: set[str] = set()
        stack = [initial]
        while stack:
            node = stack.pop()
            if node in reachable:
                continue
            reachable.add(node)
            stack.extend(edges[node] - reachable)
        errors = [
            f"Etap '{by_id[step_id]['admin_label']}' jest nieosiągalny z etapu początkowego."
            for step_id in by_id.keys() - reachable
        ]
        finals = {step_id for step_id, step in by_id.items() if step.get("final") or (config.get("flow_mode") != "explicit" and step.get("rejected"))}
        if not finals:
            errors.append("Workflow nie zawiera etapu końcowego.")
            return errors
        if not reachable & finals:
            errors.append("Workflow nie ma osiągalnej ścieżki zakończenia.")
        can_finish = set(finals)
        changed = True
        while changed:
            changed = False
            for node, targets in edges.items():
                if node not in can_finish and targets & can_finish:
                    can_finish.add(node)
                    changed = True
        for step_id in reachable - can_finish:
            step = by_id[step_id]
            if config.get("flow_mode") == "explicit" or ("correction" not in step_id and "correction" not in str(step.get("stage_type") or "")):
                errors.append(
                    f"Etap '{step['admin_label']}' należy do cyklu lub ślepej ścieżki bez zakończenia."
                )
        return errors

    @staticmethod
    def _validate_modules(
        config: Mapping[str, Any],
        steps: list[dict[str, Any]],
        form_config: Mapping[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        documents = form_config.get("documents") or {}
        if isinstance(documents, list):
            document_ids = {
                str(item.get("id") or "")
                for item in documents
                if isinstance(item, Mapping) and item.get("enabled", True)
            }
        else:
            document_ids = {
                str(key)
                for key, item in documents.items()
                if not isinstance(item, Mapping) or item.get("enabled", True)
            }
        if config.get("requires_declaration") and "declaration" not in document_ids:
            errors.append("Etap deklaracji jest włączony, ale dokument deklaracji nie jest skonfigurowany.")
        if config.get("requires_contract") and not document_ids & {"agreement", "training_agreement"}:
            errors.append("Etap umowy jest włączony, ale dokument umowy nie jest skonfigurowany.")
        if config.get("flow_mode") == "explicit":
            for step in steps:
                if step.get("action") in {"generate_document", "await_signature"} and step.get("document_id") not in document_ids:
                    errors.append(f"Etap „{step['admin_label']}” wymaga skonfigurowanego dokumentu.")
        training_enabled = any(
            isinstance(field, Mapping)
            and field.get("type") == "training_selection"
            and field.get("enabled", True)
            for field in form_config.get("fields") or []
        )
        training_stage = any(
            "training" in str(step.get("id") or "")
            or str(step.get("status") or "") == "TRAINING_SELECTION_OPEN"
            for step in steps
        )
        if config.get("flow_mode") != "explicit" and training_enabled and not training_stage:
            errors.append("Wybór szkoleń jest włączony, ale workflow nie zawiera etapu wyboru szkoleń.")
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
