from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.process_service import ProcessStatus
from services.status_catalog import LEGACY_STATUS_MAP, ProcessStatusCode, get_status_label


DEFAULT_INSTRUCTION_TITLE = "Instrukcja dalszego postępowania"
MAX_STAGES = 100


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
    """Normalize untrusted JSON to the plain-text instruction schema."""
    source = value if isinstance(value, Mapping) else {}
    title = _plain_text(source.get("title"), limit=255)
    description = _plain_text(source.get("description"), limit=50_000)
    if not description:
        description = _plain_text(legacy_description, limit=50_000)

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
                "description": _plain_text(raw_stage.get("description"), limit=50_000),
                "next_action": _plain_text(raw_stage.get("next_action"), limit=50_000),
                "status_codes": status_codes,
                "final": _as_bool(raw_stage.get("final")),
                "rejected": _as_bool(raw_stage.get("rejected")),
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
    **_legacy_arguments: Any,
) -> dict[str, Any]:
    """Build the public instruction exclusively from the form configuration."""
    raw_status = _plain_text(process_status, limit=128)
    config = normalize_instruction_config(instruction_config, legacy_description=legacy_description)
    configured_stages = config["stages"]
    has_instruction = bool(config["description"] or configured_stages)

    current_index = _find_current_stage(configured_stages, raw_status)
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

    instruction = {
        "title": config["title"],
        "description": config["description"],
        "has_instruction": has_instruction,
        "current_stage_key": current_stage["key"] if current_stage else None,
        "current_stage_label": current_stage["label"] if current_stage else None,
        "current_stage_description": current_stage["description"] if current_stage else "",
        "next_action": current_stage["next_action"] if current_stage else "",
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
        "instruction_steps": instruction["stages"],
    }


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
