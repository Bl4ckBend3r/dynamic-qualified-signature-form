from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


QUALIFICATION_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "is_empty",
    "is_not_empty",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "in",
    "not_in",
}
QUALIFICATION_FAILURE_ACTIONS = {"auto_reject"}


class QualificationConditionService:
    def normalize_config(self, raw_config: Any, fields: list[dict] | None = None) -> dict:
        source = dict(raw_config) if isinstance(raw_config, Mapping) else {}
        known_fields = {
            str(field.get("name") or "").strip(): dict(field)
            for field in (fields or [])
            if isinstance(field, Mapping) and str(field.get("name") or "").strip()
        }
        conditions = []
        used_ids: set[str] = set()
        for index, raw_condition in enumerate(source.get("conditions") or []):
            if not isinstance(raw_condition, Mapping):
                continue
            field_name = str(raw_condition.get("field_name") or "").strip()
            condition_id = str(raw_condition.get("id") or f"condition_{index + 1}").strip()
            if not condition_id or condition_id in used_ids:
                condition_id = f"condition_{index + 1}"
            used_ids.add(condition_id)
            operator = str(raw_condition.get("operator") or "equals").strip()
            failure_action = str(raw_condition.get("failure_action") or "auto_reject").strip()
            expected_value = raw_condition.get("expected_value", "")
            if operator in {"in", "not_in"}:
                expected_value = self._normalize_expected_list(expected_value)
            elif expected_value is None:
                expected_value = ""
            conditions.append(
                {
                    "id": condition_id,
                    "field_name": field_name,
                    "field_label": str(
                        raw_condition.get("field_label")
                        or known_fields.get(field_name, {}).get("label")
                        or field_name
                    ).strip(),
                    "operator": operator,
                    "expected_value": expected_value,
                    "failure_action": failure_action,
                    "user_message": str(raw_condition.get("user_message") or "").strip(),
                    "officer_message": str(raw_condition.get("officer_message") or "").strip(),
                    "is_active": self._as_bool(raw_condition.get("is_active", True)),
                    "case_sensitive": self._as_bool(raw_condition.get("case_sensitive", False)),
                }
            )
        return {"enabled": self._as_bool(source.get("enabled", False)), "conditions": conditions}

    def validate_config(self, config: Any, fields: list[dict] | None = None) -> list[str]:
        normalized = self.normalize_config(config, fields)
        known_fields = {
            str(field.get("name") or "").strip()
            for field in (fields or [])
            if isinstance(field, Mapping) and str(field.get("name") or "").strip()
        }
        errors = []
        for index, condition in enumerate(normalized["conditions"]):
            label = f"Warunek {index + 1}"
            if not condition["field_name"] or (known_fields and condition["field_name"] not in known_fields):
                errors.append(f"{label}: wybierz istniejące pole formularza.")
            if condition["operator"] not in QUALIFICATION_OPERATORS:
                errors.append(f"{label}: wybierz prawidłowy operator.")
            if condition["failure_action"] not in QUALIFICATION_FAILURE_ACTIONS:
                errors.append(f"{label}: wybierz prawidłową akcję przy niespełnieniu.")
            if condition["operator"] not in {"is_empty", "is_not_empty"} and self._is_empty(
                condition["expected_value"]
            ):
                errors.append(f"{label}: podaj wartość oczekiwaną.")
        return errors

    def evaluate(self, config: Any, submission_data: Mapping[str, Any], fields: list[dict] | None = None) -> dict:
        normalized = self.normalize_config(config, fields)
        evaluated_at = datetime.now(timezone.utc).isoformat()
        results = []
        if normalized["enabled"]:
            field_types = {
                str(field.get("name") or "").strip(): str(field.get("type") or "text")
                for field in (fields or [])
                if isinstance(field, Mapping)
            }
            for condition in normalized["conditions"]:
                if not condition["is_active"]:
                    continue
                actual_value = submission_data.get(condition["field_name"])
                passed = self._matches(
                    actual_value,
                    condition["expected_value"],
                    condition["operator"],
                    field_type=field_types.get(condition["field_name"], "text"),
                    case_sensitive=condition["case_sensitive"],
                )
                results.append(
                    {
                        **condition,
                        "actual_value": actual_value,
                        "passed": passed,
                    }
                )
        failed = [item for item in results if not item["passed"]]
        user_messages = list(dict.fromkeys(item["user_message"] for item in failed if item["user_message"]))
        return {
            "enabled": normalized["enabled"],
            "passed": not failed,
            "evaluated_at": evaluated_at,
            "results": results,
            "failed_conditions": failed,
            "user_message": " ".join(user_messages),
        }

    def _matches(
        self,
        actual: Any,
        expected: Any,
        operator: str,
        *,
        field_type: str,
        case_sensitive: bool,
    ) -> bool:
        if operator == "is_empty":
            return self._is_empty(actual)
        if operator == "is_not_empty":
            return not self._is_empty(actual)

        if operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
            left, right = self._ordered_values(actual, expected, field_type)
            if left is None or right is None:
                return False
            return {
                "greater_than": left > right,
                "greater_than_or_equal": left >= right,
                "less_than": left < right,
                "less_than_or_equal": left <= right,
            }[operator]

        if operator in {"in", "not_in"}:
            choices = self._normalize_expected_list(expected)
            normalized_choices = {self._normalize_text(item, case_sensitive) for item in choices}
            if isinstance(actual, (list, tuple, set)):
                actual_values = list(actual)
            elif field_type in {"training_selection", "multi_select", "multiselect"}:
                actual_values = self._normalize_expected_list(actual)
            else:
                actual_values = [actual]
            matched = any(
                self._normalize_text(item, case_sensitive) in normalized_choices
                for item in actual_values
            )
            return matched if operator == "in" else not matched

        if isinstance(actual, (list, tuple, set)):
            actual_values = [self._normalize_text(item, case_sensitive) for item in actual]
            expected_text = self._normalize_text(expected, case_sensitive)
            equals = expected_text in actual_values
            contains = any(expected_text in item for item in actual_values)
        else:
            actual_text = self._normalize_text(actual, case_sensitive)
            expected_text = self._normalize_text(expected, case_sensitive)
            equals = actual_text == expected_text
            contains = expected_text in actual_text
        return {
            "equals": equals,
            "not_equals": not equals,
            "contains": contains,
            "not_contains": not contains,
        }.get(operator, False)

    @staticmethod
    def _ordered_values(actual: Any, expected: Any, field_type: str):
        if field_type == "date":
            try:
                return date.fromisoformat(str(actual)), date.fromisoformat(str(expected))
            except (TypeError, ValueError):
                return None, None
        try:
            return Decimal(str(actual).strip()), Decimal(str(expected).strip())
        except (InvalidOperation, TypeError, ValueError):
            return None, None

    @staticmethod
    def _normalize_text(value: Any, case_sensitive: bool) -> str:
        if isinstance(value, bool):
            value = "TAK" if value else "NIE"
        text = str(value or "").strip()
        return text if case_sensitive else text.casefold()

    @staticmethod
    def _normalize_expected_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value or "").replace("\r", "\n").replace(",", "\n").split("\n") if item.strip()]

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, set, dict)):
            return not value
        return False

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "tak", "yes", "on"}
