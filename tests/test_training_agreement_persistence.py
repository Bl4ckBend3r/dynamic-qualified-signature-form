import json

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


def agreement_config(template_html="<main class=\"document\">ADMIN TEMPLATE {{ training.name }}</main>"):
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


@pytest.mark.parametrize("training_count", [1, 2])
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
        assert all("/umowy/niepodpisane/" in item.storage_path for item in files)
        assert all(item.storage_path in storage.files for item in files)

    updated = repository.get_by_id("public-uuid")
    assert [item["filename"] for item in json.loads(updated["training_agreements"])] == [
        item["filename"] for item in agreements
    ]
    assert all(call["template_html"].startswith("<main") for call in renderer.calls)


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


def test_single_agreement_mode_creates_one_agreement_submission_file(tmp_path):
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
    with session_factory() as db:
        file_row = db.query(SubmissionFile).one()
        assert file_row.document_id == "agreement"
        assert file_row.document_type == SubmissionDocumentType.AGREEMENT
        assert file_row.agreement_number == "U/public-uuid/2026-07-16"
        assert file_row.storage_path in storage.files
    assert renderer.calls[0]["template_html"].startswith("<main>One agreement")
