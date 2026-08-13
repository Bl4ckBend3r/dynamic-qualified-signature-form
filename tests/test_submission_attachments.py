from __future__ import annotations

from io import BytesIO

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from werkzeug.datastructures import FileStorage, MultiDict

from models import Base, FormSubmission, SubmissionFile
from repositories.submission_repository import PostgresSubmissionRepository
from services.submission_attachment_service import SubmissionAttachmentService
from validators.form_config_validator import FormConfigValidator
from form_loader import evaluate_visible_if, normalize_form_definition


PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


class MemoryStorage:
    output_dir = "output"

    def __init__(self):
        self.files = {}

    def mkdir(self, path):
        pass

    def write_bytes(self, path, content, content_type=None):
        self.files[path] = bytes(content)

    def read_bytes(self, path):
        return self.files[path]

    def delete(self, path, missing_ok=False):
        self.files.pop(path, None)


def upload(name="certificate.pdf", content=PDF, mime="application/pdf"):
    return FileStorage(stream=BytesIO(content), filename=name, content_type=mime)


def config(**overrides):
    field = {
        "name": "certificate",
        "type": "file",
        "label": "Zaświadczenie",
        "required": True,
        "allowed_extensions": ["pdf"],
        "allowed_mime_types": ["application/pdf"],
        "max_size_mb": 10,
        "max_files": 1,
        "document_type": "employment_certificate",
        **overrides,
    }
    return {"fields": [{"name": "employment", "type": "text"}, field]}


def service_with_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = PostgresSubmissionRepository("sqlite://", session_factory=factory)
    storage = MemoryStorage()
    return SubmissionAttachmentService(repository, storage), factory, storage


def test_required_optional_and_conditional_file_validation():
    service, _, _ = service_with_database()
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict())[1]
    assert service.validate_uploads(config(required=False), {}, MultiDict())[1] == {}
    conditional = config(required=False, required_if={"field": "employment", "equals": "yes"})
    assert "certificate" in service.validate_uploads(conditional, {"employment": "yes"}, MultiDict())[1]
    assert service.validate_uploads(conditional, {"employment": "no"}, MultiDict())[1] == {}


def test_pdf_security_size_empty_count_and_path_traversal():
    service, _, _ = service_with_database()
    assert service.validate_uploads(config(), {}, MultiDict([("certificate", upload())]))[1] == {}
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict([("certificate", upload("virus.exe"))]))[1]
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict([("certificate", upload("../certificate.pdf"))]))[1]
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict([("certificate", upload(content=b"not a pdf"))]))[1]
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict([("certificate", upload(mime="text/html"))]))[1]
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict([("certificate", upload(content=b""))]))[1]
    assert "certificate" in service.validate_uploads(config(max_size_mb=0.000001), {}, MultiDict([("certificate", upload())]))[1]
    two = MultiDict([("certificate", upload("one.pdf")), ("certificate", upload("two.pdf"))])
    assert "certificate" in service.validate_uploads(config(max_files=1), {}, two)[1]
    assert service.validate_uploads(config(max_files=2), {}, MultiDict([("certificate", upload("one.pdf")), ("certificate", upload("two.pdf"))]))[1] == {}


def test_storage_sha256_and_attachment_versions_are_preserved():
    service, factory, storage = service_with_database()
    with factory() as db:
        submission = FormSubmission(submission_id="public-1", form_slug="safe-form")
        db.add(submission)
        db.commit()
        internal_id = submission.id

    prepared, errors = service.validate_uploads(config(), {}, MultiDict([("certificate", upload("first.pdf"))]))
    assert errors == {}
    service.persist(form_slug="safe-form", submission_id="public-1", attachments=prepared, workflow_step="submission")
    with factory() as db:
        first = db.execute(select(SubmissionFile).where(SubmissionFile.submission_id == internal_id)).scalar_one()
        assert first.checksum_sha256
        assert first.attachment_version == 1
        assert first.antivirus_status == "not_configured"
        assert first.original_filename == "first.pdf"
        assert first.filename != first.original_filename
        assert first.storage_path.startswith("output/safe-form/submissions/public-1/attachments/certificate/")
        assert storage.read_bytes(first.storage_path) == PDF
        first.status = "requires_correction"
        db.commit()

    prepared, errors = service.validate_uploads(
        config(), {}, MultiDict([("certificate", upload("second.pdf", PDF + b"\nnew"))]), submission_id="public-1"
    )
    assert errors == {}
    service.persist(form_slug="safe-form", submission_id="public-1", attachments=prepared, workflow_step="returned_for_correction")
    with factory() as db:
        rows = db.execute(
            select(SubmissionFile).where(SubmissionFile.submission_id == internal_id).order_by(SubmissionFile.attachment_version)
        ).scalars().all()
        assert [row.attachment_version for row in rows] == [1, 2]
        assert [row.status for row in rows] == ["superseded", "active"]
        assert rows[1].workflow_step_at_upload == "returned_for_correction"
        assert rows[0].storage_path in storage.files


def test_required_file_for_one_submission_does_not_use_another_submission_attachment():
    service, factory, _ = service_with_database()
    with factory() as db:
        one = FormSubmission(submission_id="one", form_slug="safe-form")
        two = FormSubmission(submission_id="two", form_slug="safe-form")
        db.add_all([one, two])
        db.commit()
    prepared, _ = service.validate_uploads(config(), {}, MultiDict([("certificate", upload())]))
    service.persist(form_slug="safe-form", submission_id="one", attachments=prepared)
    assert "certificate" in service.validate_uploads(config(), {}, MultiDict(), submission_id="two")[1]


def test_attachment_alias_and_condition_operators_share_form_evaluator():
    normalized = normalize_form_definition({"title": "Test", "fields": [{"type": "attachment", "name": "proof"}]})
    assert normalized["fields"][0]["type"] == "file"
    values = {"status": "yes", "empty": ""}
    assert evaluate_visible_if({"field": "status", "equals": "yes"}, values)
    assert evaluate_visible_if({"field": "status", "not_equals": "no"}, values)
    assert evaluate_visible_if({"field": "status", "in": ["yes", "maybe"]}, values)
    assert evaluate_visible_if({"field": "status", "not_in": ["no"]}, values)
    assert evaluate_visible_if({"field": "empty", "is_empty": True}, values)
    assert evaluate_visible_if({"field": "status", "is_not_empty": True}, values)


def test_admin_configuration_rejects_unsafe_attachment_types():
    definition = config(allowed_extensions=["pdf", "exe"])
    definition["title"] = "Test"
    errors = FormConfigValidator(skip_template_check=True).validate(definition)
    assert any("unsafe types: exe" in error for error in errors)


def test_antivirus_extension_point_rejects_infected_file():
    repository_service, _, _ = service_with_database()
    repository_service.antivirus_scanner = type("Scanner", (), {"scan": lambda self, content, mime: "infected"})()
    _, errors = repository_service.validate_uploads(config(), {}, MultiDict([("certificate", upload())]))
    assert "certificate" in errors


def test_public_submit_accepts_multipart_attachment(client, app, form_definition, valid_form_data):
    form_definition["fields"].append(
        {
            "type": "file", "name": "certificate", "label": "Zaświadczenie",
            "required": True, "allowed_extensions": ["pdf"], "max_size_mb": 10,
        }
    )
    response = client.post(
        "/submit/formularz_zgloszeniowy",
        data={**valid_form_data, "certificate": (BytesIO(PDF), "certificate.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    attachment_paths = [path for path in app.testing_storage.direct_files if "/attachments/certificate/" in path]
    assert len(attachment_paths) == 1
    assert app.testing_storage.direct_files[attachment_paths[0]] == PDF
    assert app.testing_storage.csv_rows[0]["data_json"]["_attachments"][0]["checksum_sha256"]
