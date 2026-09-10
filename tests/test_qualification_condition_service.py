import pytest

from services.qualification_condition_service import QualificationConditionService


FIELDS = [
    {"name": "age", "label": "Wiek", "type": "number"},
    {"name": "region", "label": "Województwo", "type": "text"},
    {"name": "consent", "label": "Zgoda", "type": "checkbox"},
    {"name": "trainings", "label": "Szkolenia", "type": "training_selection"},
    {"name": "status", "label": "Status", "type": "select", "options": ["Aktywny", "Nieaktywny"]},
    {"name": "topics", "label": "Tematy", "type": "multi_select", "options": ["Excel", "Kadry", "Python"]},
]


def test_all_active_conditions_must_pass():
    service = QualificationConditionService()
    config = {
        "enabled": True,
        "conditions": [
            {"field_name": "age", "operator": "greater_than_or_equal", "expected_value": "18"},
            {"field_name": "region", "operator": "equals", "expected_value": "lubuskie"},
            {"field_name": "consent", "operator": "equals", "expected_value": "Tak"},
        ],
    }

    passed = service.evaluate(config, {"age": "21", "region": "Lubuskie", "consent": True}, FIELDS)
    failed = service.evaluate(config, {"age": "17", "region": "Lubuskie", "consent": True}, FIELDS)

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert [item["field_name"] for item in failed["failed_conditions"]] == ["age"]


def test_inactive_condition_and_disabled_configuration_do_not_reject():
    service = QualificationConditionService()
    condition = {"field_name": "age", "operator": "greater_than", "expected_value": "100", "is_active": False}

    assert service.evaluate({"enabled": True, "conditions": [condition]}, {"age": "18"}, FIELDS)["passed"] is True
    condition["is_active"] = True
    assert service.evaluate({"enabled": False, "conditions": [condition]}, {"age": "18"}, FIELDS)["passed"] is True


def test_failed_messages_and_actual_expected_values_are_preserved():
    result = QualificationConditionService().evaluate(
        {
            "enabled": True,
            "conditions": [{
                "id": "region-rule",
                "field_name": "region",
                "operator": "in",
                "expected_value": ["lubuskie", "wielkopolskie"],
                "user_message": "Projekt jest przeznaczony dla mieszkańców wskazanych województw.",
                "officer_message": "Niespełnione kryterium terytorialne.",
            }],
        },
        {"region": "mazowieckie"},
        FIELDS,
    )

    assert result["user_message"].startswith("Projekt jest")
    assert result["failed_conditions"][0]["actual_value"] == "mazowieckie"
    assert result["failed_conditions"][0]["expected_value"] == ["lubuskie", "wielkopolskie"]
    assert result["failed_conditions"][0]["officer_message"] == "Niespełnione kryterium terytorialne."


def test_validation_rejects_unknown_field_operator_and_missing_value():
    errors = QualificationConditionService().validate_config(
        {
            "enabled": True,
            "conditions": [
                {"field_name": "missing", "operator": "unknown", "expected_value": ""},
            ],
        },
        FIELDS,
    )

    assert len(errors) == 3


def test_one_of_operator_supports_multi_select_values():
    result = QualificationConditionService().evaluate(
        {
            "enabled": True,
            "conditions": [{
                "field_name": "trainings",
                "operator": "in",
                "expected_value": ["Excel", "Kadry"],
            }],
        },
        {"trainings": "Python, Excel"},
        FIELDS,
    )

    assert result["passed"] is True


@pytest.mark.parametrize(
    ("actual", "expected", "passed"),
    [
        (True, "TAK", True),
        ("yes", "TAK", True),
        ("1", "true", True),
        (False, "NIE", True),
        ("no", "false", True),
        ("0", "NIE", True),
        ("TAK", "NIE", False),
    ],
)
def test_boolean_equivalents_are_evaluated_consistently(actual, expected, passed):
    result = QualificationConditionService().evaluate(
        {
            "enabled": True,
            "conditions": [{"field_name": "consent", "operator": "equals", "expected_value": expected}],
        },
        {"consent": actual},
        FIELDS,
    )

    assert result["passed"] is passed


def test_list_operators_and_contains_support_multiple_submission_values():
    service = QualificationConditionService()
    base = {"enabled": True, "conditions": [{"field_name": "topics", "expected_value": ["Kadry", "Excel"]}]}

    base["conditions"][0]["operator"] = "in"
    assert service.evaluate(base, {"topics": ["Python", "Excel"]}, FIELDS)["passed"] is True
    base["conditions"][0]["operator"] = "not_in"
    assert service.evaluate(base, {"topics": ["Python"]}, FIELDS)["passed"] is True
    base["conditions"][0].update(operator="contains", expected_value="yth")
    assert service.evaluate(base, {"topics": ["Python", "Excel"]}, FIELDS)["passed"] is True
    base["conditions"][0].update(operator="contains", expected_value=["Python", "Excel"])
    assert service.evaluate(base, {"topics": ["Excel", "Python", "Kadry"]}, FIELDS)["passed"] is True
    base["conditions"][0].update(operator="equals", expected_value=["Python", "Excel"])
    assert service.evaluate(base, {"topics": ["Excel", "Python"]}, FIELDS)["passed"] is True


def test_normalization_preserves_typed_number_list_and_null_for_empty_operator():
    normalized = QualificationConditionService().normalize_config(
        {
            "enabled": True,
            "conditions": [
                {"field_name": "age", "operator": "equals", "expected_value": "18"},
                {"field_name": "topics", "operator": "in", "expected_value": "Excel, Python"},
                {"field_name": "region", "operator": "is_empty", "expected_value": "ignored"},
            ],
        },
        FIELDS,
    )

    assert normalized["conditions"][0]["expected_value"] == 18
    assert normalized["conditions"][1]["expected_value"] == ["Excel", "Python"]
    assert normalized["conditions"][2]["expected_value"] is None


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "passed"),
    [
        ("equals", "18.0", 18, True),
        ("not_equals", "18.00", 18, False),
        ("in", "2.0", [1, 2], True),
        ("not_in", "2.00", [1, 2], False),
    ],
)
def test_numeric_fields_compare_equivalent_number_formats(operator, actual, expected, passed):
    result = QualificationConditionService().evaluate(
        {
            "enabled": True,
            "conditions": [
                {"field_name": "age", "operator": operator, "expected_value": expected}
            ],
        },
        {"age": actual},
        FIELDS,
    )

    assert result["passed"] is passed


def test_select_option_with_numeric_zero_is_valid():
    fields = [
        {"name": "level", "label": "Poziom", "type": "select", "options": [0, 1]}
    ]

    errors = QualificationConditionService().validate_config(
        {
            "enabled": True,
            "conditions": [
                {"field_name": "level", "operator": "equals", "expected_value": "0"}
            ],
        },
        fields,
    )

    assert errors == []


def test_validation_rejects_value_outside_field_options_and_empty_list():
    service = QualificationConditionService()

    invalid_option = service.validate_config(
        {"enabled": True, "conditions": [{"field_name": "status", "operator": "equals", "expected_value": "Inny"}]},
        FIELDS,
    )
    empty_list = service.validate_config(
        {"enabled": True, "conditions": [{"field_name": "topics", "operator": "in", "expected_value": []}]},
        FIELDS,
    )

    assert any("wartość zdefiniowaną" in error for error in invalid_option)
    assert any("podaj wartość oczekiwaną" in error for error in empty_list)
