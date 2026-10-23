import json
from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from services.training_agreement_service import extract_training_selection
from services.training_availability_service import TrainingAvailabilityService
from services.documents.declaration_flow_service import DeclarationFlowService
from services.training_service import build_training_availability


class FakeRepository:
    def __init__(self, rows):
        self.rows = rows

    def list_by_form(self, form_slug):
        return list(self.rows)


def test_training_availability_counts_only_locked_or_binding_training_rows():
    field = {
        "catalog": [
            {"id": "excel", "name": "Excel", "price": "1200.00", "capacity": 2},
            {"id": "python", "name": "Python", "price": "1800.00", "capacity": 1},
        ]
    }
    rows = [
        {"submission_id": "a", "_submission_trainings": [{"training_id": "excel", "status": "agreement_uploaded_by_beneficiary", "is_locked": True}]},
        {"submission_id": "b", "_submission_trainings": [{"training_id": "excel", "status": "selected", "is_locked": False}]},
        {"submission_id": "c", "_submission_trainings": [{"training_id": "python", "status": "agreement_waiting_for_beneficiary_signature", "is_locked": False}]},
    ]

    availability = TrainingAvailabilityService(FakeRepository(rows)).availability_for_field(
        form_slug="sample",
        field=field,
        current_submission_id="",
    )

    assert availability["excel"]["available_seats"] == 1
    assert availability["python"]["available_seats"] == 1
    assert availability["python"]["is_available"] is True


def test_training_selection_rejects_training_without_available_seats():
    field = {
        "name": "selected_trainings",
        "required": True,
        "catalog": [{"id": "python", "name": "Python", "price": "1800.00", "capacity": 1}],
    }

    selected, error = extract_training_selection(
        field,
        MultiDict([("selected_trainings", "python")]),
        availability={"python": {"available_seats": 0, "is_available": False}},
    )

    assert selected == []
    assert error == "Brak wolnych miejsc dla szkolenia: Python."


def test_capacity_below_existing_occupancy_keeps_rows_and_blocks_new_selection():
    rows = [
        {
            "submission_id": f"participant-{index}",
            "_submission_trainings": [
                {
                    "training_id": "excel",
                    "status": "agreement_uploaded_by_beneficiary",
                    "is_locked": True,
                }
            ],
        }
        for index in range(20)
    ]
    field = {
        "name": "selected_trainings",
        "required": True,
        "catalog": [
            {"id": "excel", "name": "Excel", "price": "500.00", "capacity": 15}
        ],
    }
    service = TrainingAvailabilityService(FakeRepository(rows))

    availability = service.availability_for_field(form_slug="sample", field=field)
    selected, error = extract_training_selection(
        field,
        MultiDict([("selected_trainings", "excel")]),
        availability=availability,
    )

    assert len(rows) == 20
    assert availability["excel"]["occupied_seats"] == 20
    assert availability["excel"]["available_seats"] == 0
    assert availability["excel"]["is_available"] is False
    assert selected == []
    assert error == "Brak wolnych miejsc dla szkolenia: Excel."


def test_low_seats_comment_is_visible_only_for_one_to_five_available_seats():
    catalog = [
        {"id": "low", "name": "Low", "capacity": 5, "low_seats_comment": "Zostało niewiele miejsc."},
        {"id": "full", "name": "Full", "capacity": 1, "low_seats_comment": "Nie pokazuj"},
        {"id": "many", "name": "Many", "capacity": 9, "low_seats_comment": "Nie pokazuj"},
    ]

    availability = build_training_availability(catalog, {"full": 1, "many": 1})

    assert availability["low"]["low_seats_comment_visible"] == "Zostało niewiele miejsc."
    assert availability["full"]["low_seats_comment_visible"] == ""
    assert availability["many"]["low_seats_comment_visible"] == ""
    assert availability["low"]["available_seats_label"] == "Dostępne miejsca: 5"


def test_declaration_definition_omits_training_catalog():
    declaration = {
        "fields": [
            {
                "type": "training_selection",
                "name": "selected_trainings",
                "catalog": [
                    {"id": "open", "name": "Dostępne", "price": "100.00", "capacity": 3},
                    {"id": "closed", "name": "Nieaktywne", "price": "200.00", "capacity": 3, "active": False},
                ],
            }
        ]
    }
    availability = {
        "open": {
            "available_seats": 0,
            "available_seats_label": "Brak wolnych miejsc",
            "is_available": False,
        }
    }

    definition = DeclarationFlowService.build_declaration_form_definition(declaration, availability)
    assert definition["fields"] == []
