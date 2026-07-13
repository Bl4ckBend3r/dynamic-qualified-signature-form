import json

import pytest
from flask import render_template_string

from services.document_service import DocumentService, normalize_selected_items


def test_document_context_exposes_selected_trainings_list():
    context = {}
    row = {
        "selected_trainings": json.dumps(
            [
                {"id": "excel", "name": "Excel", "price": 1200},
                {"id": "angielski", "name": "Angielski", "price": 900},
            ],
            ensure_ascii=False,
        )
    }

    DocumentService()._add_collection_context(context, row)

    assert [training["name"] for training in context["selected_trainings"]] == ["Excel", "Angielski"]
    assert context["selected_trainings_total"] == 2100


@pytest.mark.parametrize(
    ("raw_value", "expected_names"),
    [
        ("Excel", ["Excel"]),
        (["Excel", "Angielski"], ["Excel", "Angielski"]),
        ([{"id": "excel", "name": "Excel", "price": 1200}], ["Excel"]),
        (json.dumps(["Excel", {"id": "angielski", "label": "Angielski"}]), ["Excel", "Angielski"]),
        ("", []),
        (None, []),
    ],
)
def test_normalize_selected_items_handles_mixed_training_values(raw_value, expected_names):
    normalized = normalize_selected_items(raw_value)

    assert [item["name"] for item in normalized] == expected_names
    assert all(isinstance(item, dict) for item in normalized)
    assert all({"name", "label", "value"}.issubset(item) for item in normalized)


@pytest.mark.parametrize(
    "raw_value",
    [
        "Excel",
        ["Excel", "Angielski"],
        [{"id": "excel", "name": "Excel", "price": 1200}],
        json.dumps(["Excel", {"id": "angielski", "label": "Angielski"}]),
        "",
        None,
    ],
)
def test_declaration_template_can_call_get_on_normalized_selected_trainings(app, raw_value):
    context = {}
    DocumentService()._add_collection_context(context, {"selected_trainings": raw_value})

    with app.test_request_context("/"):
        rendered = render_template_string(
            "{% for training in selected_trainings %}{{ training.get('name') }};{% endfor %}",
            **context,
        )

    assert "'str object' has no attribute 'get'" not in rendered


def test_collection_context_normalization_overrides_raw_context_extra_string():
    context = {"selected_trainings": "Excel"}
    render_row = {"selected_trainings": json.dumps([{"id": "excel", "name": "Excel", "price": 1200}])}

    context.update({"selected_trainings": render_row["selected_trainings"]})
    DocumentService()._add_collection_context(context, render_row)

    assert context["selected_trainings"][0]["id"] == "excel"
    assert context["selected_trainings"][0]["name"] == "Excel"
    assert context["selected_trainings"][0]["price"] == "1200.00"
    assert context["selected_trainings"][0]["price_formatted"].startswith("1 200,00")
    assert context["selected_trainings_normalized"] == context["selected_trainings"]
    assert context["selected_trainings_total"] == 1200
