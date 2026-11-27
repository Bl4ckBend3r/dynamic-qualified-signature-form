import json
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from form_loader import evaluate_scoped_condition, evaluate_visible_if, extract_submission_data, normalize_form_definition, validate_submission
from validators.form_config_validator import FormConfigValidator


@pytest.mark.parametrize("operator, expected, current, result", [
    ("contains", "inne", [], False),
    ("contains", "inne", ["ai"], False),
    ("contains", "inne", ["inne"], True),
    ("contains", "inne", ["ai", "inne"], True),
    ("contains", "inne", ["inne_szkolenie"], False),
    ("contains", "inne", ["inne", "inne"], True),
    ("not_contains", "inne", [], True),
    ("not_contains", "inne", ["ai", "inne"], False),
    ("contains_any", ["inne", "ai"], ["ai"], True),
    ("contains_any", ["inne", "ai"], [], False),
    ("contains_all", ["inne", "ai"], ["ai", "inne", "ai"], True),
    ("contains_all", ["inne", "ai"], ["ai"], False),
    ("contains_none", ["inne", "ai"], [], True),
    ("contains_none", ["inne", "ai"], ["inne_szkolenie"], True),
    ("contains_none", ["inne", "ai"], ["ai"], False),
    ("equals", "Tak", "Tak", True),
    ("not_equals", "Tak", "Nie", True),
    ("in", ["Tak", "Nie"], "Tak", True),
    ("not_in", ["Tak"], "Nie", True),
    ("is_empty", None, [], True),
    ("is_not_empty", None, ["ai"], True),
    ("unknown", "inne", ["inne"], False),
    ("contains", "inne", "inne", False),
    ("contains_any", [], ["inne"], False),
    ("contains_all", "inne", ["inne"], False),
    ("contains_none", [1], ["inne"], False),
])
def test_field_condition_operators(operator, expected, current, result):
    assert evaluate_visible_if({"field": "choice", "operator": operator, "value": expected}, {"choice": current}) is result


def test_conditional_checkbox_one_option_and_http_validation():
    definition = normalize_form_definition({"fields": [
        {"name": "choice", "type": "checkbox", "required": False, "options": [{"value": "inne", "label": "Inne szkolenie"}]},
        {"name": "detail", "type": "text", "required": False,
         "required_if": {"field": "choice", "operator": "contains", "value": "inne"}},
    ]})
    checked = extract_submission_data(definition, MultiDict([("choice", "inne")]))
    assert checked["choice"] == ["inne"]
    assert "detail" in validate_submission(definition, checked)
    assert "detail" not in validate_submission(definition, {"choice": []})
    assert "detail" not in validate_submission(definition, {"choice": ["inne_szkolenie"]})
    assert "choice" in validate_submission(definition, {"choice": ["forged"]})
    consent = normalize_form_definition({"fields": [{"name": "consent", "type": "checkbox", "consent": True, "options": [{"value": "Tak"}]}]})
    assert extract_submission_data(consent, MultiDict([("consent", "Tak")]))["consent"] == "Tak"


def test_scopes_and_invalid_configuration():
    rule = {"scope": "root", "field": "choice", "operator": "contains", "value": "inne"}
    assert evaluate_scoped_condition(rule, root_data={"choice": ["inne"]}, current_data={"choice": []})
    assert not evaluate_scoped_condition({**rule, "scope": "invalid"}, root_data={"choice": ["inne"]})
    fields = [
        {"name": "choice", "type": "checkbox", "options": [{"value": "inne", "label": "Inne"}]},
        {"name": "detail", "type": "text", "visible_if": rule},
    ]
    validator = FormConfigValidator(skip_template_check=True)
    assert not any("condition" in error for error in validator.validate({"title": "Form", "fields": fields}))
    fields[1]["visible_if"] = {**rule, "value": "Inne"}
    assert any("unknown option value" in error for error in validator.validate({"title": "Form", "fields": fields}))
    fields[1]["visible_if"] = {**rule, "operator": "bogus"}
    assert any("unsupported" in error for error in validator.validate({"title": "Form", "fields": fields}))
    fields[1]["visible_if"] = {"field": "detail", "operator": "equals", "value": "x"}
    assert any("cyclic" in error for error in validator.validate({"title": "Form", "fields": fields}))


def test_json_normalization_round_trip_preserves_conditions():
    definition = {"title": "Form", "fields": [
        {"name": "choice", "type": "checkbox", "options": ["inne|Inne", "ai|AI"]},
        {"name": "detail", "type": "text", "visible_if": {"field": "choice", "operator": "contains", "value": "inne"},
         "required_if": {"field": "choice", "operator": "contains_any", "value": ["inne", "ai"]}},
    ]}
    first = normalize_form_definition(definition)
    second = normalize_form_definition(json.loads(json.dumps(first)))
    assert second["fields"][1]["visible_if"] == definition["fields"][1]["visible_if"]
    assert second["fields"][1]["required_if"] == definition["fields"][1]["required_if"]


def test_browser_predicate_matches_python():
    playwright = pytest.importorskip("playwright.sync_api")
    source = (Path(__file__).resolve().parents[1] / "static/js/field_conditions.js").read_text(encoding="utf-8")
    try:
        with playwright.sync_playwright() as engine:
            browser = engine.chromium.launch(headless=True)
            page = browser.new_page()
            page.add_script_tag(content=source)
            cases = [
                ({"field": "x", "operator": operator, "value": expected}, current)
                for operator, expected, current in [
                    ("contains", "inne", []), ("contains", "inne", ["ai", "inne"]),
                    ("contains", "inne", ["inne_szkolenie"]),
                    ("not_contains", "inne", []), ("contains_any", ["ai", "inne"], ["inne"]),
                    ("contains_all", ["ai", "inne"], ["inne", "ai"]),
                    ("contains_none", ["ai", "inne"], []),
                    ("equals", "Tak", "Tak"), ("not_equals", "Tak", "Nie"),
                    ("in", ["Tak"], "Tak"), ("not_in", ["Tak"], "Nie"),
                    ("is_empty", None, []), ("is_not_empty", None, ["ai"]),
                    ("unknown", "x", ["x"]),
                ]
            ]
            for rule, current in cases:
                assert page.evaluate("([rule, current]) => window.fieldConditionMatches(rule, current)", [rule, current]) == evaluate_visible_if(rule, {"x": current})
            browser.close()
    except playwright.Error as exception:
        pytest.skip(f"Brak przeglądarki Playwright: {exception}")
