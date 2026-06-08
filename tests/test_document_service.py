import json

from services.document_service import DocumentService


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
