from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from services.admin_submission_service import admin_status_label, filter_submissions, sort_submissions, submission_value


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
