from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from services.process_service import ProcessStatus


class RulesService:
    """Small rule engine for workflow field/status updates."""

    LEGACY_BLOCKING_FIELDS = {
        "deklaracja_18_lat",
        "deklaracja_lubuskie",
        "deklaracja_brak_dzialalnosci",
        "deklaracja_brak_ksztalcenia",
        "deklaracja_umiejetnosci_podstawowe",
    }

    def apply_rules(
        self,
        submission: Mapping[str, Any],
        form_config: Mapping[str, Any],
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = {**dict(submission or {}), **dict(updates or {})}
        result: dict[str, Any] = {}
        rules = form_config.get("rules") or []

        if not rules:
            return self.apply_legacy_fallback(state)

        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            if self.matches(rule.get("when") or {}, state):
                for action in rule.get("then") or []:
                    result.update(self.apply_action(action, state, result))

        if self._rules_manage_agreement_block(rules) and "agreement_blocked" not in result:
            result["agreement_blocked"] = ""
            result["agreement_block_reason"] = ""

        return result

    def matches(self, condition: Any, state: Mapping[str, Any]) -> bool:
        if not isinstance(condition, Mapping):
            return bool(condition)

        if "any" in condition:
            return any(self.matches(item, state) for item in condition.get("any") or [])

        if "all" in condition:
            return all(self.matches(item, state) for item in condition.get("all") or [])

        field_name = condition.get("field")
        if field_name:
            actual = state.get(str(field_name), "")
            for operator in ("equals", "not_equals", "in", "not_in"):
                if operator in condition:
                    return self._compare(actual, operator, condition.get(operator))

        return False

    def apply_action(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
        current_updates: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(action, Mapping):
            return {}

        action_name = action.get("action")
        if action_name == "set_field":
            field = str(action.get("field") or "").strip()
            if not field:
                return {}
            return {field: deepcopy(action.get("value", ""))}

        if action_name == "set_status":
            return {"process_status": action.get("value", "")}

        if action_name == "block_document":
            document_id = str(action.get("document_id") or action.get("document") or "").strip()
            return self._document_block_updates(document_id, blocked=True, reason=action.get("reason", ""))

        if action_name == "unblock_document":
            document_id = str(action.get("document_id") or action.get("document") or "").strip()
            return self._document_block_updates(document_id, blocked=False, reason="")

        return {}

    def apply_legacy_fallback(self, state: Mapping[str, Any]) -> dict[str, str]:
        blocked = any(
            str(state.get(field_name) or "").strip().lower() == "nie"
            for field_name in self.LEGACY_BLOCKING_FIELDS
        )
        if not blocked:
            return {"agreement_blocked": "", "agreement_block_reason": ""}
        return {
            "agreement_blocked": "Tak",
            "agreement_block_reason": "Warunki nie zostały spełnione na podstawie deklaracji uczestnika.",
            "process_status": ProcessStatus.AGREEMENT_BLOCKED.value,
        }

    def _compare(self, actual: Any, operator: str, expected: Any) -> bool:
        actual_text = str(actual or "").strip()
        if operator == "equals":
            return actual_text == str(expected or "").strip()
        if operator == "not_equals":
            return actual_text != str(expected or "").strip()
        if operator in {"in", "not_in"}:
            expected_values = expected if isinstance(expected, list) else [expected]
            normalized_expected = {str(value or "").strip() for value in expected_values}
            result = actual_text in normalized_expected
            return result if operator == "in" else not result
        return False

    def _document_block_updates(self, document_id: str, *, blocked: bool, reason: Any) -> dict[str, Any]:
        if document_id in {"agreement", "training_agreement"}:
            return {
                "agreement_blocked": "Tak" if blocked else "",
                "agreement_block_reason": str(reason or "") if blocked else "",
                **({"process_status": ProcessStatus.AGREEMENT_BLOCKED.value} if blocked else {}),
            }
        suffix = f"{document_id}_" if document_id else "document_"
        return {
            f"{suffix}blocked": "Tak" if blocked else "",
            f"{suffix}block_reason": str(reason or "") if blocked else "",
        }

    def _rules_manage_agreement_block(self, rules: list[dict]) -> bool:
        for rule in rules:
            for action in (rule.get("then") or []) if isinstance(rule, Mapping) else []:
                if not isinstance(action, Mapping):
                    continue
                action_name = action.get("action")
                field = action.get("field")
                document_id = action.get("document_id") or action.get("document")
                if field in {"agreement_blocked", "agreement_block_reason"}:
                    return True
                if action_name in {"block_document", "unblock_document"} and document_id in {"agreement", "training_agreement"}:
                    return True
        return False
