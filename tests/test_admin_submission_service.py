from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from services.admin_submission_service import (
    admin_status_label,
    build_submission_detail_sections,
    filter_submissions,
    sort_submissions,
    submission_value,
)


def test_admin_status_label_uses_workflow_label_before_catalog():
    form = SimpleNamespace(
        definition_json={
            "fields": [],
            "workflow": {
                "statuses": [
                    {"id": "CUSTOM_REVIEW", "label": "Weryfikacja specjalna"},
                ]
            }
        }
    )

    assert admin_status_label("CUSTOM_REVIEW", form) == "Weryfikacja specjalna"


def test_admin_status_label_falls_back_for_unknown_status():
    assert admin_status_label("ODD_HISTORY_STATUS") == "Nieznany status: ODD_HISTORY_STATUS"


def test_submission_filter_and_sort_use_flat_and_json_values():
    first = SimpleNamespace(
        submission_id="A-1",
        email="anna@example.com",
        nazwisko="Kowalska",
        process_status="FORM_SUBMITTED",
        officer_decision="",
        data_json={"city": "Lublin"},
        created_at=None,
    )
    second = SimpleNamespace(
        submission_id="B-2",
        email="jan@example.com",
        nazwisko="Nowak",
        process_status="OFFICER_REJECTED",
        officer_decision="rejected",
        data_json={"city": "Warszawa"},
        created_at=None,
    )

    filtered = filter_submissions([first, second], {"field": "city", "operator": "contains", "value": "lublin"})

    assert filtered == [first]
    assert submission_value(first, "city") == "Lublin"
    assert sort_submissions([first, second], "nazwisko", "asc") == [first, second]


def test_submission_detail_sections_use_labels_and_formatted_training_snapshot():
    form = SimpleNamespace(
        definition_json={
            "fields": [
                {"type": "text", "name": "imiona", "label": "Imiona"},
                {"type": "checkbox", "name": "osw_rodo", "label": "Zgoda RODO"},
            ],
            "documents": {
                "declaration": {
                    "enabled": True,
                    "fields": [
                        {"type": "training_selection", "name": "selected_trainings", "label": "Szkolenia"}
                    ],
                }
            },
        }
    )
    submission = SimpleNamespace(
        __table__=SimpleNamespace(
            columns=[
                SimpleNamespace(name="id"),
                SimpleNamespace(name="submission_id"),
                SimpleNamespace(name="form_name"),
                SimpleNamespace(name="created_at"),
                SimpleNamespace(name="imiona"),
                SimpleNamespace(name="email"),
                SimpleNamespace(name="osw_rodo"),
                SimpleNamespace(name="selected_trainings"),
                SimpleNamespace(name="data_json"),
            ]
        ),
        id=1,
        submission_id="ABC",
        form_name="Formularz",
        created_at=None,
        imiona="Anna",
        email="anna@example.com",
        osw_rodo=True,
        selected_trainings='[{"id":"python","name":"Python","price":"1200.00","currency":"PLN","code":"PY"}]',
        data_json={"custom": "true"},
    )

    detail = build_submission_detail_sections(form, submission)

    assert detail["sections"][0]["title"] == "Dane podstawowe"
    assert {"label": "Imiona", "value": "Anna"} in detail["sections"][0]["items"]
    assert detail["trainings"][0]["price_formatted"] == "1 200,00 zł"
    assert detail["technical_items"]
