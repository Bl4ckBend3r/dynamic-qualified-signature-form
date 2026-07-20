import json
from decimal import Decimal

import pytest
from flask import Flask
from sqlalchemy import create_engine

from database import create_session_factory
from models import Base, FormSubmission, SubmissionFile
from repositories.submission_repository import PostgresSubmissionRepository
from services.document_naming_service import resolve_pdf_storage_path
from services.document_service import DocumentService
from services.documents.agreement_flow_service import AgreementFlowService
from services.submission_document_service import SubmissionDocumentType


class PdfStorage:
    def __init__(self):
        self.output_dir = "output"
        self.files = {}

    def pdf_storage_path(self, slug, filename, document_type=None, signed=None):
        return resolve_pdf_storage_path(
            output_dir=self.output_dir,
            slug=slug,
            filename=filename,
            document_type=document_type,
            signed=signed,
        )

    def save_pdf(self, slug, filename, pdf_bytes, document_type=None, signed=None):
        path = self.pdf_storage_path(slug, filename, document_type=document_type, signed=signed)
        self.files[path] = pdf_bytes

    def exists(self, path):
        return path in self.files

    def read_bytes(self, path):
        return self.files[path]

    def get_pdf_bytes(self, slug, filename):
        for path, payload in self.files.items():
            if path.endswith(f"/{filename}") and f"/{slug}/pdf/" in path:
                return payload
        raise FileNotFoundError(filename)

    def read_text_or_empty(self, path):
        return "<main class=\"document\">LEGACY TEMPLATE</main>"


class CapturingRenderer:
    def __init__(self):
        self.calls = []

    def render_document_pdf_bytes(self, **kwargs):
        self.calls.append(kwargs)
        return b"%PDF-1.4\ntraining agreement"


def build_service(tmp_path, selected_trainings):
    database_url = f"sqlite:///{tmp_path / 'training-agreements.db'}"
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    repository = PostgresSubmissionRepository(database_url, session_factory=session_factory)
    repository.create(
        {
            "submission_id": "public-uuid",
            "form_slug": "sample",
            "form_name": "Sample",
            "imiona": "Jan",
            "nazwisko": "Kowalski",
            "selected_trainings": json.dumps(selected_trainings),
            "declaration_signature_valid": "Tak",
        }
    )
    storage = PdfStorage()
    renderer = CapturingRenderer()
    service = DocumentService(
        storage=storage,
        submission_repository=repository,
        pdf_render_service=renderer,
    )
    row = repository.get_by_id("public-uuid")
    submission = {"submission_id": "public-uuid", "form_slug": "sample", "row": row}
    return service, repository, session_factory, storage, renderer, submission


def agreement_config(
    template_html=(
        "<main class=\"document\"><table>{% for item in selected_trainings %}"
        "<tr><td>{{ item.name }}</td><td>{{ item.price_formatted }}</td></tr>"
        "{% endfor %}</table><strong>{{ selected_trainings_total_formatted }}</strong></main>"
    ),
):
    return {
        "title": "Sample",
        "fields": [],
        "documents": [
            {
                "id": "training_agreement",
                "kind": "generated_pdf",
                "enabled": True,
                "template_html": template_html,
                "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
                "repeat_over": "selected_trainings",
                "repeat_item_alias": "training",
                "numbering": {"number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"},
            }
        ],
    }


@pytest.mark.parametrize("training_count", [1, 3])
def test_training_agreement_generation_persists_every_pdf(tmp_path, training_count):
    trainings = [
        {"id": f"training-{index}", "name": f"Training {index}", "price": f"{index}200.00"}
        for index in range(1, training_count + 1)
    ]
    service, repository, session_factory, storage, renderer, submission = build_service(tmp_path, trainings)
    app = Flask(__name__)
    app.config.update(TEMP_DIR=tmp_path, NEXTCLOUD_FORMS_DIR="Formularze", NEXTCLOUD_OUTPUT_DIR="output")

    with app.test_request_context("/"):
        agreements = service.generate_documents_for_collection(
            submission,
            agreement_config(),
            "training_agreement",
            "selected_trainings",
            "training",
            context_extra={"generated_date": "2026-07-16"},
        )

    with session_factory() as db:
        db_submission = db.query(FormSubmission).filter_by(submission_id="public-uuid").one()
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert len(files) == training_count
        assert {item.submission_id for item in files} == {db_submission.id}
        assert {item.public_submission_id for item in files} == {"public-uuid"}
        assert all(item.document_id == "training_agreement" for item in files)
        assert all(item.document_type == SubmissionDocumentType.TRAINING_AGREEMENT for item in files)
        assert [item.filename for item in files] == [item["filename"] for item in agreements]
        assert [item.agreement_number for item in files] == [item["number"] for item in agreements]
        assert all(item["agreement_number"] == item["number"] for item in agreements)
        assert all("/umowy/niepodpisane/" in item.storage_path for item in files)
        assert all(item.storage_path in storage.files for item in files)

    updated = repository.get_by_id("public-uuid")
    assert [item["filename"] for item in json.loads(updated["training_agreements"])] == [
        item["filename"] for item in agreements
    ]
    assert all(call["template_html"].startswith("<main") for call in renderer.calls)
    assert len(renderer.calls) == training_count
    expected_total = sum((Decimal(training["price"]) for training in trainings), Decimal("0"))
    for index, call in enumerate(renderer.calls):
        context = call["context"]
        expected_training = context["training"]
        assert context["selected_trainings"] == [expected_training]
        assert context["selected_trainings_normalized"] == [expected_training]
        assert context["submission"]["selected_trainings"] == [expected_training]
        assert len(context["training_agreements"]) == 1
        assert context["training_agreement"]["sequence"] == index + 1
        assert context["agreement"] == context["training_agreement"]
        assert context["agreement_number"] == agreements[index]["number"]
        assert context["selected_trainings_total_formatted"] == expected_training["price_formatted"]
        assert context["agreement_training_price"] == Decimal(str(expected_training["price"]))
        assert context["agreement_training_price_formatted"] == expected_training["price_formatted"]
        assert [item["id"] for item in context["all_selected_trainings"]] == [
            item["id"] for item in trainings
        ]
        assert context["all_selected_trainings_total"] == expected_total
        assert "{{ all_selected_trainings_total_formatted|default(selected_trainings_total_formatted, true) }}" in call["template_html"]

        rendered_table = app.jinja_env.from_string(
            "<table>{% for item in selected_trainings %}<tr><td>{{ item.name }}</td>"
            "<td>{{ item.price_formatted }}</td></tr>{% endfor %}</table>"
            "<strong>{{ all_selected_trainings_total_formatted }}</strong>"
        ).render(**context)
        assert rendered_table.count("<tr>") == 1
        assert expected_training["name"] in rendered_table
        assert expected_training["price_formatted"] in rendered_table
        assert context["all_selected_trainings_total_formatted"] in rendered_table


def test_duplicate_training_names_get_unique_filenames_and_numbers(tmp_path):
    trainings = [
        {"id": "digital-a", "name": "Digital Skills", "price": "1000.00"},
        {"id": "digital-b", "name": "Digital Skills", "price": "1000.00"},
    ]
    service, repository, session_factory, storage, renderer, submission = build_service(tmp_path, trainings)
    config = agreement_config()
    config["documents"][0]["filename_pattern"] = "{first_name}_{last_name}-{training_name}-umowa.pdf"
    config["documents"][0]["numbering"] = {"number_pattern": "U/{submission_id}/{generated_date}"}
    app = Flask(__name__)
    app.config.update(TEMP_DIR=tmp_path, NEXTCLOUD_FORMS_DIR="Formularze", NEXTCLOUD_OUTPUT_DIR="output")

    with app.test_request_context("/"):
        agreements = service.generate_documents_for_collection(
            submission,
            config,
            "training_agreement",
            "selected_trainings",
            "training",
            context_extra={"generated_date": "2026-07-16"},
        )

    assert [item["filename"] for item in agreements] == [
        "Jan_Kowalski-Digital_Skills-1-umowa.pdf",
        "Jan_Kowalski-Digital_Skills-2-umowa.pdf",
    ]
    assert [item["number"] for item in agreements] == [
        "U/public-uuid/2026-07-16/1",
        "U/public-uuid/2026-07-16/2",
    ]
    assert len(storage.files) == 2
    assert len(renderer.calls) == 2
    with session_factory() as db:
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert [item.filename for item in files] == [item["filename"] for item in agreements]
        assert [item.agreement_number for item in files] == [item["number"] for item in agreements]
    persisted = json.loads(repository.get_by_id("public-uuid")["training_agreements"])
    assert persisted == agreements


def test_regeneration_does_not_overwrite_existing_agreement_pdf(tmp_path):
    service, repository, session_factory, storage, renderer, submission = build_service(
        tmp_path,
        [{"id": "excel", "name": "Excel", "price": "1200.00"}],
    )
    app = Flask(__name__)
    app.config.update(TEMP_DIR=tmp_path, NEXTCLOUD_FORMS_DIR="Formularze", NEXTCLOUD_OUTPUT_DIR="output")

    with app.test_request_context("/"):
        first = service.generate_documents_for_collection(
            submission,
            agreement_config(),
            "training_agreement",
            "selected_trainings",
            "training",
            context_extra={"generated_date": "2026-07-16"},
        )
        second = service.generate_documents_for_collection(
            submission,
            agreement_config(),
            "training_agreement",
            "selected_trainings",
            "training",
            context_extra={"generated_date": "2026-07-16"},
        )

    assert first[0]["filename"] == "Jan_Kowalski-excel-umowa.pdf"
    assert second[0]["filename"] == "Jan_Kowalski-excel-2-umowa.pdf"
    assert len(storage.files) == 2
    with session_factory() as db:
        files = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert [item.filename for item in files] == [first[0]["filename"], second[0]["filename"]]
    assert json.loads(repository.get_by_id("public-uuid")["training_agreements"]) == second


def test_inline_admin_agreement_template_wins_over_legacy_template_path(tmp_path):
    service, _, _, _, renderer, submission = build_service(
        tmp_path,
        [{"id": "excel", "name": "Excel", "price": "1200.00"}],
    )
    form_config = {
        "title": "Sample",
        "fields": [],
        "documents": [
            {
                "id": "training_agreement",
                "kind": "generated_pdf",
                "enabled": True,
                "template": "Template/umowa.html",
            },
            {
                "id": "agreement",
                "kind": "generated_pdf",
                "enabled": True,
                "template_html": "<main>ADMIN AGREEMENT {{ training.name }}</main>",
                "generation_mode": "per_training",
            },
        ],
    }
    app = Flask(__name__)
    app.config.update(TEMP_DIR=tmp_path, NEXTCLOUD_FORMS_DIR="Formularze", NEXTCLOUD_OUTPUT_DIR="output")

    with app.test_request_context("/"):
        result = AgreementFlowService().generate_training_agreements(
            submission=submission,
            form_config=form_config,
            document_service=service,
            generated_date="2026-07-16",
        )

    assert result.success is True
    assert renderer.calls[0]["template_html"].startswith("<main>ADMIN AGREEMENT")
    assert "LEGACY TEMPLATE" not in renderer.calls[0]["template_html"]


def test_agreement_generation_refuses_missing_template(tmp_path):
    service, _, _, _, renderer, submission = build_service(
        tmp_path,
        [{"id": "excel", "name": "Excel", "price": "1200.00"}],
    )

    result = AgreementFlowService().generate_training_agreements(
        submission=submission,
        form_config={"documents": [{"id": "agreement", "kind": "generated_pdf", "enabled": True}]},
        document_service=service,
    )

    assert result.success is False
    assert result.error_code == "agreement_template_missing"
    assert result.message == "Brak szablonu umowy dla tego formularza."
    assert renderer.calls == []


def test_single_agreement_mode_is_normalized_to_one_agreement_per_training(tmp_path):
    service, _, session_factory, storage, renderer, submission = build_service(
        tmp_path,
        [
            {"id": "excel", "name": "Excel", "price": "1200.00"},
            {"id": "english", "name": "English", "price": "1800.00"},
        ],
    )
    form_config = {
        "title": "Sample",
        "fields": [],
        "documents": [
            {
                "id": "agreement",
                "kind": "generated_pdf",
                "enabled": True,
                "template_html": "<main>One agreement {{ agreement_number }}</main>",
                "generation_mode": "single",
                "filename_pattern": "{first_name}_{last_name}-umowa.pdf",
                "numbering": {"number_pattern": "U/{submission_id}/{generated_date}"},
            }
        ],
    }
    app = Flask(__name__)
    app.config.update(TEMP_DIR=tmp_path, NEXTCLOUD_FORMS_DIR="Formularze", NEXTCLOUD_OUTPUT_DIR="output")

    with app.test_request_context("/"):
        result = AgreementFlowService().generate_training_agreements(
            submission=submission,
            form_config=form_config,
            document_service=service,
            generated_date="2026-07-16",
        )

    assert result.success is True
    assert len(result.agreements) == 2
    assert [item["number"] for item in result.agreements] == [
        "U/public-uuid/2026-07-16/1",
        "U/public-uuid/2026-07-16/2",
    ]
    assert [item["filename"] for item in result.agreements] == [
        "Jan_Kowalski-1-umowa.pdf",
        "Jan_Kowalski-2-umowa.pdf",
    ]
    with session_factory() as db:
        file_rows = db.query(SubmissionFile).order_by(SubmissionFile.id).all()
        assert len(file_rows) == 2
        assert all(file_row.document_id == "training_agreement" for file_row in file_rows)
        assert all(file_row.document_type == SubmissionDocumentType.TRAINING_AGREEMENT for file_row in file_rows)
        assert [file_row.agreement_number for file_row in file_rows] == [
            "U/public-uuid/2026-07-16/1",
            "U/public-uuid/2026-07-16/2",
        ]
        assert all(file_row.storage_path in storage.files for file_row in file_rows)
    assert all(call["template_html"].startswith("<main>One agreement") for call in renderer.calls)
