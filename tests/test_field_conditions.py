import json
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from form_loader import evaluate_scoped_condition, evaluate_visible_if, extract_submission_data, normalize_form_definition, validate_submission, values_for_rerender
from validators.form_config_validator import FormConfigValidator
from services.admin_form_service import normalize_admin_form_definition, parse_uploaded_form_definition


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
        {"name": "choice", "type": "checkbox", "required": False, "multiple": True, "options": [{"value": "inne", "label": "Inne szkolenie"}]},
        {"name": "detail", "type": "text", "required": False,
         "required_if": {"field": "choice", "operator": "contains", "value": "inne"}},
    ]})
    checked = extract_submission_data(definition, MultiDict([("choice", "inne")]))
    assert checked["choice"] == ["inne"]
    assert values_for_rerender(definition, MultiDict([("choice", "inne")]), saved_values={"choice": ["ai"]})["choice"] == ["inne"]
    assert "detail" in validate_submission(definition, checked)
    assert "detail" not in validate_submission(definition, {"choice": []})
    assert "detail" not in validate_submission(definition, {"choice": ["inne_szkolenie"]})
    assert "choice" in validate_submission(definition, {"choice": ["forged"]})
    consent = normalize_form_definition({"fields": [{"name": "consent", "type": "checkbox", "consent": True, "options": [{"value": "Tak"}]}]})
    assert extract_submission_data(consent, MultiDict([("consent", "Tak")]))["consent"] == "Tak"


def test_scopes_and_invalid_configuration():
    rule = {"scope": "root", "field": "choice", "operator": "contains", "value": "inne"}
    assert evaluate_scoped_condition(rule, root_data={"choice": ["inne"]}, current_data={"choice": []})
    assert not evaluate_scoped_condition({**rule, "scope": "current"}, root_data={"choice": ["inne"]}, current_data={})
    assert not evaluate_scoped_condition({**rule, "scope": "invalid"}, root_data={"choice": ["inne"]})
    assert not evaluate_visible_if({}, {"choice": ["inne"]})
    assert not evaluate_visible_if([], {"choice": ["inne"]})
    fields = [
        {"name": "choice", "type": "checkbox", "multiple": True, "options": [{"value": "inne", "label": "Inne"}]},
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


@pytest.mark.parametrize("rule, message", [
    ({"field": "choice", "operator": "contains_any", "value": []}, "non-empty array"),
    ({"field": "choice", "operator": "contains_all", "value": "inne"}, "non-empty array"),
    ({"field": "choice", "operator": "contains", "value": "label"}, "unknown option value"),
    ({"field": "text", "operator": "contains", "value": "inne"}, "multiple-choice"),
    ({"field": "missing", "operator": "equals", "value": "inne"}, "unknown"),
    ({"field": "choice", "operator": "contains", "value": "inne", "scope": "invalid"}, "scope"),
])
def test_condition_configuration_rejects_invalid_rules(rule, message):
    fields = [
        {"name": "choice", "type": "checkbox", "multiple": True, "options": [{"value": "inne", "label": "label"}]},
        {"name": "text", "type": "text"},
        {"name": "detail", "type": "text", "visible_if": rule},
    ]
    errors = FormConfigValidator(skip_template_check=True).validate({"title": "Form", "fields": fields})
    assert any(message in error for error in errors)


def test_repeatable_condition_scope_and_cycle_validation():
    fields = [
        {"name": "choice", "type": "checkbox", "multiple": True, "options": ["inne|Inne"]},
        {"name": "people", "type": "repeatable_group", "fields": [
            {"name": "local", "type": "text", "visible_if": {"scope": "root", "field": "choice", "operator": "contains", "value": "inne"}},
            {"name": "other", "type": "text", "required_if": {"scope": "current", "field": "local", "operator": "is_not_empty"}},
        ]},
    ]
    validator = FormConfigValidator(skip_template_check=True)
    assert not any("condition" in error for error in validator.validate({"title": "Form", "fields": fields}))
    fields[1]["fields"][0]["visible_if"] = {"scope": "current", "field": "other", "operator": "is_not_empty"}
    assert any("cyclic" in error for error in validator.validate({"title": "Form", "fields": fields}))


def test_json_import_export_import_preserves_conditions():
    definition = {"title": "Form", "fields": [
        {"name": "choice", "type": "checkbox", "options": ["inne|Inne", "ai|AI"]},
        {"name": "detail", "type": "text", "visible_if": {"field": "choice", "operator": "contains", "value": "inne"},
         "required_if": {"field": "choice", "operator": "contains_any", "value": ["inne", "ai"]}},
    ]}
    first = normalize_admin_form_definition(parse_uploaded_form_definition(json.dumps(definition).encode(), "form.json"))
    second = normalize_admin_form_definition(parse_uploaded_form_definition(json.dumps(first).encode(), "form.json"))
    assert second["fields"][1]["visible_if"] == definition["fields"][1]["visible_if"]
    assert second["fields"][1]["required_if"] == definition["fields"][1]["required_if"]


def test_public_form_renders_single_option_group_condition(app, client):
    definition = normalize_form_definition({"title": "Warunki", "fields": [
        {"name": "choice", "type": "checkbox", "multiple": True, "required": False,
         "options": [{"value": "inne", "label": "Inne szkolenie"}]},
        {"name": "detail", "type": "text", "required": False,
         "visible_if": {"field": "choice", "operator": "contains", "value": "inne"},
         "required_if": {"field": "choice", "operator": "contains", "value": "inne"}},
    ]})
    app.testing_storage.form_definition = definition
    response = client.get("/form/formularz_zgloszeniowy")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-checkbox-group="true"' in html
    assert '"operator": "contains"' in html or '&#34;operator&#34;: &#34;contains&#34;' in html
    assert "js/field_conditions.js" in html


def test_http_cannot_skip_conditional_required_field(app, client, valid_form_data):
    definition = dict(app.testing_storage.form_definition)
    definition["fields"] = [*definition["fields"],
        {"name": "choice", "type": "checkbox", "required": False,
         "options": [{"value": "inne", "label": "Inne"}, {"value": "inne_szkolenie", "label": "Inne szkolenie"}]},
        {"name": "detail", "type": "text", "required": False,
         "required_if": {"field": "choice", "operator": "contains", "value": "inne"}},
    ]
    app.testing_storage.form_definition = definition
    missing = client.post("/form/formularz_zgloszeniowy/review", data={**valid_form_data, "choice": "inne"})
    assert missing.status_code == 400
    assert "detail" in missing.json["errors"]
    different = client.post("/form/formularz_zgloszeniowy/review", data={**valid_form_data, "choice": "inne_szkolenie"})
    assert different.status_code == 200
    assert "detail" not in different.json.get("errors", {})


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


def test_javascript_predicate_matches_python_without_browser():
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    source = Path(__file__).resolve().parents[1] / "static/js/field_conditions.js"
    cases = [
        ({"field": "x", "operator": operator, "value": expected}, current)
        for operator, expected, current in [
            ("contains", "inne", []), ("contains", "inne", ["ai", "inne"]),
            ("contains", "inne", ["inne_szkolenie"]), ("not_contains", "inne", []),
            ("contains_any", ["inne", "ai"], ["ai"]),
            ("contains_all", ["inne", "ai"], ["ai", "inne", "ai"]),
            ("contains_none", ["inne", "ai"], ["other"]),
            ("equals", "Tak", "Tak"), ("not_equals", "Tak", "Nie"),
            ("in", ["Tak"], "Tak"), ("not_in", ["Tak"], "Nie"),
            ("is_empty", None, []), ("is_not_empty", None, ["ai"]),
            ("unknown", "inne", ["inne"]), ("contains", "inne", "inne"),
            ("contains_all", [], ["inne"]), ("contains_none", [1], ["inne"]),
            ("equals", True, 1),
        ]
    ]
    cases.extend([({}, ["inne"]), ([], ["inne"]), ({"operator": "equals", "value": ""}, "")])
    script = "const fs=require('fs'),vm=require('vm');const context={window:{}};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(([rule,current])=>context.window.fieldConditionMatches(rule,current))));"
    result = subprocess.run(["node", "-e", script, str(source)], input=json.dumps(cases), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [evaluate_visible_if(rule, {"x": current}) for rule, current in cases]


def test_public_checkbox_options_keep_values_separate_from_labels():
    from routes.public_forms import normalize_field_options

    assert normalize_field_options("checkbox", ["inne|Inne szkolenie"]) == [
        {"value": "inne", "label": "Inne szkolenie"}
    ]
