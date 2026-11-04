from __future__ import annotations

from typing import Any


def option_value(option: Any) -> str:
    if isinstance(option, dict):
        return str(option.get("value") or option.get("id") or option.get("label") or "").strip()
    raw = str(option or "").strip()
    value, separator, _label = raw.partition("|")
    return value.strip() if separator else raw


def option_label(option: Any) -> str:
    if isinstance(option, dict):
        value = option_value(option)
        return str(option.get("label") or option.get("name") or value).strip()
    raw = str(option or "").strip()
    value, separator, label = raw.partition("|")
    return (label.strip() or value.strip()) if separator else raw


def option_label_for_value(options: list | None, value: Any) -> str:
    raw_value = str(value or "").strip()
    for option in options or []:
        if option_value(option) == raw_value or str(option).strip() == raw_value:
            return option_label(option)
    return option_label(raw_value)
