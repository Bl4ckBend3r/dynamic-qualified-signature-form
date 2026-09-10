from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.instruction_html_service import sanitize_instruction_html
from services.process_service import ProcessStatus
from services.status_catalog import LEGACY_STATUS_MAP, ProcessStatusCode, get_status_label


DEFAULT_INSTRUCTION_TITLE = "Instrukcja dalszego postępowania"
MAX_STAGES = 100
DEFAULT_STATUS_INSTRUCTIONS = {
    ProcessStatus.AGREEMENT_UPLOADED_BY_BENEFICIARY.value: {
        "key": "agreement-uploaded-by-beneficiary",
        "label": "Podpisana umowa wgrana przez beneficjenta",
        "description": "Podpisana umowa została wgrana. Oczekuje na podpis i potwierdzenie po stronie urzędu.",
        "next_action": "Nie musisz teraz wykonywać dodatkowych czynności.",
        "final": False,
        "rejected": False,
    },
    ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value: {
        "key": "agreement-waiting-for-office-signature",
        "label": "Umowa oczekuje na podpis po stronie urzędu",
        "description": "Podpisana umowa została wgrana. Oczekuje na podpis i potwierdzenie po stronie urzędu. Nie musisz wykonywać dodatkowych czynności.",
        "next_action": "Poczekaj na podpis i potwierdzenie po stronie urzędu.",
        "final": False,
        "rejected": False,
    },
    ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value: {
        "key": "agreement-signed-by-office",
        "label": "Umowa podpisana przez urząd",
        "description": "Umowa została podpisana przez urząd. Informacja została wysłana na adres e-mail wskazany w formularzu.",
        "next_action": "Nie musisz wykonywać dodatkowych czynności.",
        "final": True,
        "rejected": False,
    },
    ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value: {
        "key": "agreement-correction",
        "label": "Umowa wymaga poprawy",
        "description": "Umowa wymaga poprawy. Wgraj poprawny podpisany dokument zgodnie z uwagami urzędu.",
        "next_action": "Popraw umowę, podpisz ją i wgraj ponownie zgodnie z uwagami urzędu.",
        "final": False,
        "rejected": True,
    },
}

# Historyczne statusy otrzymują aktualne komunikaty, bez zmiany zapisanych danych.
DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.AGREEMENT_UPLOADED.value] = {
    **DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE.value],
    "key": "legacy-agreement-waiting-for-office-signature",
}
DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.BENEFICIARY_AGREEMENT_CONFIRMED.value] = {
    **DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.AGREEMENT_SIGNED_BY_OFFICE.value],
    "key": "legacy-agreement-signed-by-office",
}
DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.BENEFICIARY_AGREEMENT_REJECTED.value] = {
    **DEFAULT_STATUS_INSTRUCTIONS[ProcessStatus.AGREEMENT_REJECTED_BY_OFFICE.value],
    "key": "legacy-agreement-rejected-by-office",
}


def instruction_status_options() -> list[dict[str, str]]:
    """Return all workflow statuses that may be assigned in the admin editor."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in [*(status.value for status in ProcessStatus), *(status.value for status in ProcessStatusCode)]:
        if value in seen:
            continue
        seen.add(value)
        result.append({"value": value, "label": get_status_label(value)})
    return result


def normalize_instruction_config(
    value: Mapping[str, Any] | None,
    *,
    legacy_description: str | None = None,
) -> dict[str, Any]:
    """Normalize untrusted JSON to the safe instruction schema."""
    source = value if isinstance(value, Mapping) else {}
    title = _plain_text(source.get("title"), limit=255)
    description = sanitize_instruction_html(source.get("description"))
    if not description:
        description = sanitize_instruction_html(legacy_description)

    raw_stages = source.get("stages")
    if not isinstance(raw_stages, list):
        raw_stages = []

    stages: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for index, raw_stage in enumerate(raw_stages[:MAX_STAGES], start=1):
        if not isinstance(raw_stage, Mapping):
            continue
        label = _plain_text(raw_stage.get("label") or raw_stage.get("name"), limit=255)
        if not label:
            continue
        key = _unique_key(_stage_key(raw_stage.get("key"), label, index), used_keys)
        raw_codes = raw_stage.get("status_codes")
        if isinstance(raw_codes, str):
            raw_codes = [raw_codes]
        status_codes: list[str] = []
        if isinstance(raw_codes, list):
            for raw_code in raw_codes:
                code = _plain_text(raw_code, limit=128)
                if code and code not in status_codes:
                    status_codes.append(code)
        stages.append(
            {
                "key": key,
                "label": label,
                "description": sanitize_instruction_html(raw_stage.get("description")),
                "next_action": sanitize_instruction_html(raw_stage.get("next_action")),
                "status_codes": status_codes,
                "final": _as_bool(raw_stage.get("final")),
                "rejected": _as_bool(raw_stage.get("rejected")),
                "active": _as_bool(raw_stage.get("active", True)),
                "inactive_reason": _plain_text(raw_stage.get("inactive_reason"), limit=500),
                "sort_order": len(stages) + 1,
            }
        )

    has_instruction = bool(description or stages)
    return {
        "title": title or (DEFAULT_INSTRUCTION_TITLE if has_instruction else ""),
        "description": description,
        "stages": stages,
    }


def build_process_instruction_view(
    process_status: str | None,
    *,
    instruction_config: Mapping[str, Any] | None = None,
    legacy_description: str | None = None,
    workflow: Mapping[str, Any] | None = None,
    step_id: str | None = None,
    document_substate: str | None = None,
    **_legacy_arguments: Any,
) -> dict[str, Any]:
    """Build the public instruction exclusively from the form configuration."""
    raw_status = _plain_text(process_status, limit=128)
    config = normalize_instruction_config(instruction_config, legacy_description=legacy_description)
    if document_substate and workflow:
        steps = [s for s in workflow.get("steps", []) if s.get("active", True)]
        step = next((s for s in steps if s.get("id") == step_id), None)
        if step:
            content = (step.get("document_instructions") or {}).get(document_substate) or {}
            following = next((s for s in steps if s.get("id") == step.get("next")), {})
            instruction = {
                "title": config["title"] or DEFAULT_INSTRUCTION_TITLE, "description": config["description"], "has_instruction": True,
                "current_stage_key": step_id, "current_stage_label": step.get("user_label") or step.get("admin_label"),
                "current_stage_description": sanitize_instruction_html(content.get("description") or step.get("description")),
                "next_action": sanitize_instruction_html(content.get("next_action") or step.get("next_action")),
                "next_stage_key": following.get("id"), "next_stage_label": following.get("user_label") or following.get("admin_label"),
                "stages": [{"key": s["id"], "label": s.get("user_label") or s.get("admin_label"), "current": s["id"] == step_id, "completed": False} for s in steps],
            }
            return {"instruction": instruction, "form_instruction": instruction["description"], "has_form_instruction": True,
                    "current_step": step_id, "current_step_label": instruction["current_stage_label"], "next_action": instruction["next_action"],
                    "next_action_label": "Co dalej?", "next_stage_label": instruction["next_stage_label"], "instruction_steps": instruction["stages"]}
    configured_stages = [stage for stage in config["stages"] if stage.get("active", True)]
    default_stage = DEFAULT_STATUS_INSTRUCTIONS.get(raw_status)
    current_index = _find_current_stage(configured_stages, raw_status)
    if current_index is None and default_stage:
        configured_stages = [
            *configured_stages,
            {
                **default_stage,
                "status_codes": [raw_status],
                "sort_order": len(configured_stages) + 1,
            },
        ]
        current_index = len(configured_stages) - 1
    has_instruction = bool(config["description"] or configured_stages)

    stages: list[dict[str, Any]] = []
    for index, configured in enumerate(configured_stages):
        stage = dict(configured)
        stage["completed"] = current_index is not None and index < current_index
        stage["current"] = current_index == index
        stages.append(stage)

    current_stage: dict[str, Any] | None = stages[current_index] if current_index is not None else None
    if configured_stages and current_stage is None:
        # A safe, non-failing representation for a status not assigned by the officer.
        fallback = {
            "key": "current-status",
            "label": get_status_label(raw_status) if raw_status else "Aktualny status wniosku",
            "description": "",
            "next_action": "",
            "status_codes": [raw_status] if raw_status else [],
            "completed": False,
            "current": True,
            "final": False,
            "rejected": False,
            "sort_order": len(stages) + 1,
            "fallback": True,
        }
        stages.append(fallback)
        current_stage = fallback

    next_stage = None
    if current_index is not None:
        next_stage = next((item for item in stages[current_index + 1 :] if item.get("active", True)), None)

    instruction = {
        "title": config["title"] or (DEFAULT_INSTRUCTION_TITLE if has_instruction else ""),
        "description": config["description"],
        "has_instruction": has_instruction,
        "current_stage_key": current_stage["key"] if current_stage else None,
        "current_stage_label": current_stage["label"] if current_stage else None,
        "current_stage_description": current_stage["description"] if current_stage else "",
        "next_action": current_stage["next_action"] if current_stage else "",
        "next_stage_key": next_stage["key"] if next_stage else None,
        "next_stage_label": next_stage["label"] if next_stage else None,
        "stages": stages,
    }
    # Keep the original flat keys until existing API clients migrate to `instruction`.
    return {
        "instruction": instruction,
        "form_instruction": instruction["description"] or None,
        "has_form_instruction": instruction["has_instruction"],
        "current_step": instruction["current_stage_key"],
        "current_step_label": instruction["current_stage_label"],
        "next_action": instruction["next_action"],
        "next_action_label": "Co dalej?",
        "next_stage_label": instruction["next_stage_label"],
        "instruction_steps": instruction["stages"],
    }


def reconcile_instruction_config(
    value: Mapping[str, Any] | None,
    workflow: Mapping[str, Any] | None,
    *,
    active_stages: list[Mapping[str, Any]] | None = None,
    title: object | None = None,
    description: object | None = None,
) -> dict[str, Any]:
    """Mark removed workflow instructions inactive and optionally replace active stages."""
    current = normalize_instruction_config(value)
    steps = [step for step in (workflow or {}).get("steps", []) if isinstance(step, Mapping)]
    active_keys = {str(step.get("id") or "").strip() for step in steps}
    active_statuses = {str(step.get("status") or step.get("status_code") or "").strip() for step in steps}

    def belongs_to_workflow(stage: Mapping[str, Any]) -> bool:
        key = str(stage.get("key") or "").strip()
        statuses = {str(code or "").strip() for code in stage.get("status_codes", [])}
        return bool((key and key in active_keys) or (statuses - {""}) & active_statuses)

    if active_stages is None:
        stages = current["stages"]
    else:
        stages = [dict(stage) for stage in active_stages]
        stages.extend(stage for stage in current["stages"] if not belongs_to_workflow(stage))

    classified = []
    for stage in stages:
        item = dict(stage)
        item["active"] = belongs_to_workflow(item)
        item["inactive_reason"] = "" if item["active"] else "Ten etap nie występuje już w workflow."
        classified.append(item)
    return normalize_instruction_config(
        {
            "title": current["title"] if title is None else title,
            "description": current["description"] if description is None else description,
            "stages": classified,
        }
    )


def _find_current_stage(stages: list[dict[str, Any]], raw_status: str) -> int | None:
    if not raw_status:
        return None
    candidates = {raw_status.casefold()}
    try:
        candidates.add(ProcessStatusCode(raw_status).value.casefold())
    except ValueError:
        mapped = LEGACY_STATUS_MAP.get(raw_status)
        if mapped:
            candidates.add(mapped.value.casefold())
    for index, stage in enumerate(stages):
        if any(str(code).casefold() in candidates for code in stage["status_codes"]):
            return index
    return None


def _plain_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    # Content is deliberately plain text. NUL/control bytes are discarded and all
    # rendering sites use textContent/Jinja escaping, so markup is never executed.
    text = str(value).replace("\x00", "")
    text = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    return text.strip()[:limit]


def _stage_key(value: Any, label: str, index: int) -> str:
    candidate = _plain_text(value, limit=80).lower()
    if not candidate:
        candidate = label.lower()
    candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate).strip("-_")
    return candidate or f"stage-{index}"


def _unique_key(candidate: str, used: set[str]) -> str:
    result = candidate
    suffix = 2
    while result in used:
        result = f"{candidate}-{suffix}"
        suffix += 1
    used.add(result)
    return result


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "tak"}
