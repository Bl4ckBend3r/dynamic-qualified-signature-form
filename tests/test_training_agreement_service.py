from werkzeug.datastructures import MultiDict

from services.documents.declaration_flow_service import DeclarationFlowService
from services.training_agreement_service import (
    build_training_agreement_number,
    extract_training_selection,
    get_training_selection_field,
)


def training_form_definition():
    return {
        "documents": {
            "declaration": {
                "enabled": True,
                "fields": [
                    {"type": "text", "name": "note"},
                    {
                        "type": "training_selection",
                        "name": "selected_trainings",
                        "required": True,
                        "catalog": [
                            {"id": "excel", "name": "Excel", "price": 1200},
                            {"id": "python", "name": "Python", "price": 1800},
                        ],
                    },
                ],
            }
        }
    }


def test_get_training_selection_field_returns_declaration_training_field():
    field = get_training_selection_field(training_form_definition())

    assert field["name"] == "selected_trainings"
    assert field["type"] == "training_selection"


def test_extract_training_selection_matches_selected_catalog_items():
    field = get_training_selection_field(training_form_definition())
    selected, error = extract_training_selection(field, MultiDict([("selected_trainings", "python")]))

    assert error is None
    assert selected[0]["id"] == "python"
    assert selected[0]["name"] == "Python"
    assert selected[0]["price"] == "1800.00"
    assert selected[0]["price_formatted"] == "1 800,00 zł"
    assert selected[0]["capacity"] is None
    assert selected[0]["dates"] == []


def test_extract_training_selection_keeps_required_error():
    field = get_training_selection_field(training_form_definition())
    selected, error = extract_training_selection(field, MultiDict())

    assert selected == []
    assert error == "Wybierz co najmniej jedno szkolenie."


def test_extract_training_selection_keeps_limit_error():
    field = {
        **get_training_selection_field(training_form_definition()),
        "max_total_amount": 1000,
        "currency": "PLN",
    }
    selected, error = extract_training_selection(field, MultiDict([("selected_trainings", "excel")]))

    assert selected[0]["price"] == "1200.00"
    assert error == "Łączna wartość szkoleń przekracza limit 1 000,00 zł."


def test_extract_training_selection_skips_inactive_catalog_items():
    field = {
        **get_training_selection_field(training_form_definition()),
        "catalog": [
            {"id": "excel", "name": "Excel", "price": "1200.00", "active": False},
            {"id": "python", "name": "Python", "price": "1800.00", "active": True},
        ],
    }

    selected, error = extract_training_selection(field, MultiDict([("selected_trainings", "excel")]))

    assert selected == []
    assert error == "Wybierz co najmniej jedno szkolenie."


def test_declaration_form_omits_training_selection():
    definition = DeclarationFlowService.build_declaration_form_definition(
        {
            "fields": [
                {"type": "section", "label": "Wybór szkoleń"},
                {"type": "section", "label": "Oświadczenia uczestnika"},
                {"type": "checkbox", "name": "osw_rodo"},
                {"type": "training_selection", "name": "selected_trainings", "catalog": []},
            ]
        }
    )

    assert [field.get("name") or field.get("label") for field in definition["fields"]] == [
        "Oświadczenia uczestnika",
        "osw_rodo",
    ]


def test_build_training_agreement_number_keeps_legacy_pattern():
    number = build_training_agreement_number(
        "abc",
        2,
        "2026-06-08",
        {"numbering": {"number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"}},
    )

    assert number == "abc/2/2026-06-08"


def test_legacy_training_agreement_number_wrapper_delegates(monkeypatch):
    import legacy_app

    calls = []

    def fake_builder(submission_id, sequence, generated_date, config):
        calls.append((submission_id, sequence, generated_date, config))
        return "delegated"

    monkeypatch.setattr(legacy_app, "service_build_training_agreement_number", fake_builder)

    assert legacy_app.build_training_agreement_number("abc", 1, "2026-06-08", {}) == "delegated"
    assert calls == [("abc", 1, "2026-06-08", {})]
