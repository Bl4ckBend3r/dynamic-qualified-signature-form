from services.qualification_condition_service import QualificationConditionService


FIELDS = [
    {"name": "age", "label": "Wiek", "type": "number"},
    {"name": "region", "label": "Województwo", "type": "text"},
    {"name": "consent", "label": "Zgoda", "type": "checkbox"},
    {"name": "trainings", "label": "Szkolenia", "type": "training_selection"},
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
