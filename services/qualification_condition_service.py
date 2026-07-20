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
EMPTY_VALUE_OPERATORS = {"is_empty", "is_not_empty"}
LIST_VALUE_OPERATORS = {"in", "not_in"}
MULTI_VALUE_FIELD_TYPES = {
    "multi_select",
    "multiselect",
    "multiple_select",
    "checkbox_group",
    "checkbox-group",
    "training_selection",
}
BOOLEAN_FIELD_TYPES = {"boolean", "bool", "checkbox"}
NUMBER_FIELD_TYPES = {"number", "integer", "float", "decimal"}


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
            field_definition = known_fields.get(field_name, {})
            expected_value = self._normalize_expected_value(
                raw_condition.get("expected_value"),
                operator,
                field_definition,
            )
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
            str(field.get("name") or "").strip(): dict(field)
            for field in (fields or [])
            if isinstance(field, Mapping) and str(field.get("name") or "").strip()
        }
        errors = []
        for index, condition in enumerate(normalized["conditions"]):
            label = f"Warunek {index + 1}"
            field = known_fields.get(condition["field_name"])
            if not condition["field_name"] or (known_fields and field is None):
                errors.append(f"{label}: wybierz istniejące pole formularza.")
            if condition["operator"] not in QUALIFICATION_OPERATORS:
                errors.append(f"{label}: wybierz prawidłowy operator.")
            if condition["failure_action"] not in QUALIFICATION_FAILURE_ACTIONS:
                errors.append(f"{label}: wybierz prawidłową akcję przy niespełnieniu.")
            if condition["operator"] not in EMPTY_VALUE_OPERATORS and self._is_empty(
                condition["expected_value"]
            ):
                errors.append(f"{label}: podaj wartość oczekiwaną.")
                continue
            if condition["operator"] in LIST_VALUE_OPERATORS and not isinstance(
                condition["expected_value"], list
            ):
                errors.append(f"{label}: podaj listę wartości oczekiwanych.")
            if condition["operator"] in {
                "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"
            } and not self._valid_ordered_value(condition["expected_value"], field or {}):
                errors.append(f"{label}: wartość oczekiwana nie pasuje do porównania liczbowego lub daty.")
            if field and condition["operator"] not in EMPTY_VALUE_OPERATORS:
                invalid_options = self._invalid_option_values(
                    condition["expected_value"],
                    field,
                    case_sensitive=condition["case_sensitive"],
                )
                if invalid_options:
                    errors.append(
                        f"{label}: wybierz wartość zdefiniowaną w polu formularza."
                    )
        return errors

    def evaluate(self, config: Any, submission_data: Mapping[str, Any], fields: list[dict] | None = None) -> dict:
        normalized = self.normalize_config(config, fields)
        evaluated_at = datetime.now(timezone.utc).isoformat()
        results = []
        if normalized["enabled"]:
            field_types = {
                str(field.get("name") or "").strip(): str(field.get("type") or "text").strip().lower()
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
            normalized_choices = {
                self._normalize_comparable(item, case_sensitive, field_type) for item in choices
            }
            if isinstance(actual, (list, tuple, set)):
                actual_values = list(actual)
            elif field_type in {"training_selection", "multi_select", "multiselect"}:
                actual_values = self._normalize_expected_list(actual)
            else:
                actual_values = [actual]
            matched = any(
                self._normalize_comparable(item, case_sensitive, field_type) in normalized_choices
                for item in actual_values
            )
            return matched if operator == "in" else not matched

        if isinstance(actual, (list, tuple, set)):
            actual_values = [self._normalize_comparable(item, case_sensitive, field_type) for item in actual]
            if isinstance(expected, (list, tuple, set)):
                expected_values = [
                    self._normalize_comparable(item, case_sensitive, field_type)
                    for item in expected
                ]
                equals = set(actual_values) == set(expected_values)
                contains = all(item in actual_values for item in expected_values)
            else:
                expected_text = self._normalize_comparable(expected, case_sensitive, field_type)
                equals = expected_text in actual_values
                contains = any(expected_text in item for item in actual_values)
        else:
            actual_text = self._normalize_comparable(actual, case_sensitive, field_type)
            expected_source = expected[0] if isinstance(expected, (list, tuple, set)) and len(expected) == 1 else expected
            expected_text = self._normalize_comparable(expected_source, case_sensitive, field_type)
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

    @classmethod
    def _normalize_comparable(cls, value: Any, case_sensitive: bool, field_type: str) -> str:
        if field_type in BOOLEAN_FIELD_TYPES:
            boolean_value = cls._boolean_value(value)
            if boolean_value is not None:
                return "__true__" if boolean_value else "__false__"
        if field_type in NUMBER_FIELD_TYPES:
            try:
                number = Decimal(str(value).strip().replace(",", "."))
            except (InvalidOperation, TypeError, ValueError):
                pass
            else:
                return f"__number__:{number.normalize()}"
        return cls._normalize_text(value, case_sensitive)

    @staticmethod
    def _boolean_value(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        text = str(value or "").strip().casefold()
        if text in {"tak", "yes", "true", "1", "on"}:
            return True
        if text in {"nie", "no", "false", "0", "off"}:
            return False
        return None

    @classmethod
    def _normalize_expected_value(
        cls,
        value: Any,
        operator: str,
        field: Mapping[str, Any],
    ) -> Any:
        if operator in EMPTY_VALUE_OPERATORS:
            return None
        field_type = str(field.get("type") or "text").strip().lower()
        if operator in LIST_VALUE_OPERATORS:
            return [cls._coerce_scalar(item, field_type) for item in cls._normalize_expected_list(value)]
        if value is None:
            return ""
        return cls._coerce_scalar(value, field_type)

    @staticmethod
    def _coerce_scalar(value: Any, field_type: str) -> Any:
        if field_type not in NUMBER_FIELD_TYPES or isinstance(value, (int, float)):
            return value
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            number = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return value
        return int(number) if number == number.to_integral_value() else float(number)

    @classmethod
    def _field_options(cls, field: Mapping[str, Any]) -> list[str]:
        raw_options = field.get("options") or field.get("choices") or field.get("catalog") or []
        if isinstance(raw_options, Mapping):
            raw_options = list(raw_options.values())
        result = []
        for option in raw_options if isinstance(raw_options, (list, tuple)) else []:
            if isinstance(option, Mapping):
                value = option.get("value", option.get("id", option.get("name", option.get("label", ""))))
            else:
                value = option
            text = "" if value is None else str(value).strip()
            if text:
                result.append(text)
        return result

    @classmethod
    def _invalid_option_values(
        cls,
        expected: Any,
        field: Mapping[str, Any],
        *,
        case_sensitive: bool,
    ) -> list[Any]:
        field_type = str(field.get("type") or "text").strip().lower()
        if field_type in BOOLEAN_FIELD_TYPES:
            values = expected if isinstance(expected, list) else [expected]
            return [value for value in values if cls._boolean_value(value) is None]
        options = cls._field_options(field)
        if not options:
            return []
        allowed = {cls._normalize_text(option, case_sensitive) for option in options}
        values = expected if isinstance(expected, list) else [expected]
        return [
            value for value in values
            if cls._normalize_text(value, case_sensitive) not in allowed
        ]

    @staticmethod
    def _valid_ordered_value(expected: Any, field: Mapping[str, Any]) -> bool:
        field_type = str(field.get("type") or "text").strip().lower()
        if field_type == "date":
            try:
                date.fromisoformat(str(expected))
                return True
            except (TypeError, ValueError):
                return False
        try:
            Decimal(str(expected).strip().replace(",", "."))
            return True
        except (InvalidOperation, TypeError, ValueError):
            return False

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
