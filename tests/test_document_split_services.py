import logging
import json
from types import SimpleNamespace

import pytest

from services.document_service import DocumentService, build_signed_filename
from services import declaration_service
from services.documents import document_generation_service
from services.documents.document_access_service import DocumentAccessService
from services.documents.agreement_flow_service import AgreementFlowService
from services.documents.declaration_flow_service import DeclarationFlowService
from services.documents.document_download_service import DocumentDownloadService
from services.documents.document_signing_service import DocumentSigningService
from services.documents.document_storage_service import DocumentStorageError, DocumentStorageService
from services.documents.document_view_service import DocumentViewService
from services.documents.pdf_render_service import PdfRenderError, PdfRenderService
from services.documents.signed_document_service import SignedDocumentError, SignedDocumentService


class DummyStorage:
    def __init__(self):
        self.direct = {"output/sample/deklaracje/plik.pdf": b"%PDF from metadata"}
        self.legacy = {("sample", "plik.pdf"): b"%PDF legacy"}
        self.read_paths = []
        self.legacy_calls = []
        self.saved = []
        self.updated = []

    def read_bytes(self, path):
        self.read_paths.append(path)
        return self.direct[path]

    def get_pdf_bytes(self, slug, filename):
        self.legacy_calls.append((slug, filename))
        return self.legacy[(slug, filename)]

    def save_pdf(self, slug, filename, document_bytes, **kwargs):
        self.saved.append((slug, filename, document_bytes, kwargs))

    def update_csv_row_by_submission_id(self, slug, submission_id, updates):
        self.updated.append((slug, submission_id, updates))
        return True


class FakePdfRenderer:
    def __init__(self):
        self.calls = []

    def render_document_pdf_bytes(self, **kwargs):
        self.calls.append(kwargs)
        return b"%PDF-1.4\nrendered\n"


class FakeDocumentStorageService:
    def __init__(self):
        self.saved = []

    def save_pdf(self, **kwargs):
        self.saved.append(kwargs)


class DummyRepository:
    def __init__(self, metadata):
        self.metadata = metadata
        self.updated = []
        self.recorded = []

    def get_file_metadata(self, submission_id, filename, *, signed):
        return self.metadata

    def update(self, submission_id, updates):
        self.updated.append((submission_id, updates))
        return True

    def record_file(self, submission_id, metadata):
        self.recorded.append((submission_id, metadata))
        return True

    def list_submission_files(self, submission_id):
        return [self.metadata] if self.metadata else []


class FakeDocumentService:
    def __init__(self):
        self.read_calls = []
        self.generated_collections = []
        self.generated_documents = []

    def verify_download_token(self, submission, token):
        return token == submission.get("access_token")

    def read_document_bytes_for_download(self, submission, filename, *, signed):
        self.read_calls.append((submission["submission_id"], filename, signed))
        return b"%PDF delegated"

    def get_document_by_id(self, form_config, document_id):
        return {"id": document_id, "repeat_over": "selected_trainings", "repeat_item_alias": "training"}

    def generate_documents_for_collection(self, *args, **kwargs):
        self.generated_collections.append((args, kwargs))
        return [{"filename": "umowa.pdf"}]

    def generate_document(self, *args, **kwargs):
        self.generated_documents.append((args, kwargs))
        return {"filename": "deklaracja.pdf", "created": True}


def test_document_service_prefers_submission_file_storage_path():
    storage = DummyStorage()
    service = DocumentService(
        storage=storage,
        submission_repository=DummyRepository({"storage_path": "output/sample/deklaracje/plik.pdf"}),
    )

    payload = service.read_document_bytes_for_download(
        {"submission_id": "abc", "form_slug": "sample"},
        "plik.pdf",
        signed=False,
    )

    assert payload == b"%PDF from metadata"
    assert storage.read_paths == ["output/sample/deklaracje/plik.pdf"]
    assert storage.legacy_calls == []


def test_document_storage_strict_mode_blocks_legacy_lookup():
    storage = DummyStorage()
    service = DocumentStorageService()

    with pytest.raises(DocumentStorageError):
        service.read_document_bytes(
            storage=storage,
            slug="sample",
            filename="plik.pdf",
            metadata=None,
            submission_id="abc",
            strict_metadata=True,
        )

    assert storage.legacy_calls == []


def test_document_storage_strict_logging_has_no_sensitive_form_data(caplog):
    storage = DummyStorage()
    service = DocumentStorageService()

    with caplog.at_level(logging.ERROR), pytest.raises(DocumentStorageError):
        service.read_document_bytes(
            storage=storage,
            slug="sample",
            filename="plik.pdf",
            metadata=None,
            submission_id="abc",
            strict_metadata=True,
        )

    assert "strict_document_metadata_missing" in caplog.text
    assert "submission_id=abc" in caplog.text
    assert "90010112346" not in caplog.text
    assert "Jan Kowalski" not in caplog.text


def test_document_storage_rollback_to_legacy_when_strict_disabled():
    storage = DummyStorage()
    service = DocumentStorageService()

    payload = service.read_document_bytes(
        storage=storage,
        slug="sample",
        filename="plik.pdf",
        metadata=None,
        submission_id="abc",
        strict_metadata=False,
    )

    assert payload == b"%PDF legacy"
    assert storage.legacy_calls == [("sample", "plik.pdf")]


def test_document_view_prefers_submission_file_metadata():
    service = DocumentViewService()

    view = service.build_documents_view(
        row={"process_status": "FORM_SUBMITTED", "declaration_filename": "legacy.pdf"},
        documents_config=[{"id": "declaration", "label": "Deklaracja"}],
        download_url_builder=lambda filename, signed=False: f"/d/{filename}",
        document_files=[
            {
                "document_type": "declaration",
                "filename": "z-metadanych.pdf",
                "signed": False,
                "signature_status": "",
                "generated_at": "2026-06-10",
            }
        ],
    )

    assert view["documents"][0]["filename"] == "z-metadanych.pdf"
    assert view["documents"][0]["source"] == "submission_file"
    assert view["documents"][0]["used_legacy_fallback"] is False


def test_document_view_legacy_fallback_is_marked_and_logged(caplog):
    service = DocumentViewService()

    with caplog.at_level(logging.WARNING):
        view = service.build_documents_view(
            row={"process_status": "FORM_SUBMITTED", "declaration_filename": "legacy.pdf"},
            documents_config=[{"id": "declaration", "label": "Deklaracja"}],
            download_url_builder=lambda filename, signed=False: f"/d/{filename}",
            document_files=[],
        )

    assert view["documents"][0]["filename"] == "legacy.pdf"
    assert view["documents"][0]["source"] == "legacy"
    assert view["documents"][0]["used_legacy_fallback"] is True
    assert "Legacy document view fallback" in caplog.text


def test_document_download_service_validates_access_and_reads_bytes():
    service = DocumentDownloadService(access_service=DocumentAccessService())
    document_service = FakeDocumentService()
    submission = {"submission_id": "abc", "form_slug": "sample", "access_token": "secret"}

    assert service.verify_access(
        document_service=document_service,
        submission=submission,
        token="secret",
    )
    download = service.prepare_download(
        document_service=document_service,
        submission=submission,
        filename="plik.pdf",
        signed=True,
    )

    assert download.pdf_bytes == b"%PDF delegated"
    assert download.download_name == "plik.pdf"
    assert document_service.read_calls == [("abc", "plik.pdf", True)]


def test_document_signing_service_saves_signed_submission_pdf(tmp_path):
    storage = DummyStorage()
    repository = DummyRepository(metadata=None)
    signing_service = DocumentSigningService(
        storage=storage,
        submission_repository=repository,
        submission_service=SimpleNamespace(
            build_signed_pdf_filename=lambda slug, submission_id: f"{slug}-{submission_id}-signed.pdf"
        ),
        verifier=lambda path: {"is_signed": True, "is_szafir_signature": True},
    )
    uploaded_file = SimpleNamespace(
        filename="signed.pdf",
        mimetype="application/pdf",
        read=lambda: b"%PDF-1.4\nsigned",
    )

    result = signing_service.upload_signed_submission_pdf(
        slug="sample",
        submission_id="abc",
        uploaded_file=uploaded_file,
        temp_dir=tmp_path,
    )

    assert result.signed_filename == "sample-abc-signed.pdf"
    assert storage.saved[0][0:3] == ("sample", "sample-abc-signed.pdf", b"%PDF-1.4\nsigned")
    assert storage.saved[0][3]["signed"] is True
    assert repository.updated == [("abc", {"signed_pdf_filename": "sample-abc-signed.pdf"})]
    assert repository.recorded[0][1]["filename"] == "sample-abc-signed.pdf"
    assert repository.recorded[0][1]["signed"] is True


def test_document_signing_service_delegates_declaration_and_agreement_uploads():
    calls = []

    class DocumentServiceStub:
        def upload_signed_document(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"is_signed": True, "is_valid": True}

    service = DocumentSigningService(document_service=DocumentServiceStub())
    result = service.upload_signed_document(
        submission={"submission_id": "abc"},
        document_id="declaration",
        uploaded_file=SimpleNamespace(filename="deklaracja.pdf"),
        instance_id="training-1",
    )

    assert result == {"is_signed": True, "is_valid": True}
    assert calls[0][0][1] == "declaration"
    assert calls[0][1]["instance_id"] == "training-1"


def test_declaration_flow_saves_additional_fields_and_legacy_status():
    repository = DummyRepository(metadata=None)
    submission = {"submission_id": "abc", "form_slug": "sample", "row": {"data_json": "{}"}}
    form_config = {
        "fields": [
            {
                "type": "text",
                "name": "post_acceptance_note",
                "label": "Dodatkowa informacja",
                "required": True,
                "stage": "after_officer_acceptance",
            }
        ]
    }

    result = DeclarationFlowService().save_additional_fields(
        submission_id="abc",
        submission=submission,
        form_config=form_config,
        form_data={"post_acceptance_note": "Gotowe"},
        submission_repository=repository,
    )

    assert result.success is True
    assert repository.updated[0][1]["post_acceptance_note"] == "Gotowe"
    assert repository.updated[0][1]["additional_fields_completed"] == "Tak"
    assert repository.updated[0][1]["process_status"] == "additional_fields_completed"


def test_declaration_flow_generates_pdf_with_current_training_selection():
    repository = DummyRepository(metadata=None)
    document_service = FakeDocumentService()
    submission = {
        "submission_id": "abc",
        "form_slug": "sample",
        "row": {"selected_trainings": json.dumps([{"id": "old", "name": "Stare", "price": 3000}])},
    }
    form_config = {
        "documents": [
            {
                "id": "declaration",
                "enabled": True,
                "fields": [
                    {
                        "type": "training_selection",
                        "name": "selected_trainings",
                        "required": True,
                        "catalog": [
                            {"id": "s1", "name": "Szkolenie 1", "price": 6200},
                            {"id": "s2", "name": "Szkolenie 2", "price": 800},
                        ],
                    }
                ],
            }
        ]
    }

    result = DeclarationFlowService().handle_declaration_post(
        submission_id="abc",
        submission=submission,
        form_config=form_config,
        declaration_config=form_config["documents"][0],
        form_data=SimpleNamespace(getlist=lambda name: ["s1", "s2"], keys=lambda: ["selected_trainings"], get=lambda name: ""),
        rules_service=SimpleNamespace(apply_rules=lambda row, config, data: {}),
        submission_repository=repository,
        document_service=document_service,
        refresh_submission=lambda submission_id: {
            "submission_id": submission_id,
            "form_slug": "sample",
            "row": {"selected_trainings": json.dumps([{"id": "old", "name": "Stare", "price": 3000}])},
        },
    )

    assert result.success is True
    context_extra = document_service.generated_documents[0][1]["context_extra"]
    assert json.loads(context_extra["selected_trainings"]) == [
        {"id": "s1", "name": "Szkolenie 1", "price": 6200.0},
        {"id": "s2", "name": "Szkolenie 2", "price": 800.0},
    ]
    assert json.loads(document_service.generated_documents[0][0][0]["row"]["selected_trainings"]) == [
        {"id": "s1", "name": "Szkolenie 1", "price": 6200.0},
        {"id": "s2", "name": "Szkolenie 2", "price": 800.0},
    ]


def test_agreement_flow_generates_collection_with_today_by_default():
    document_service = FakeDocumentService()
    submission = {
        "submission_id": "abc",
        "form_slug": "sample",
        "row": {"declaration_signature_valid": "Tak", "selected_trainings": "[]"},
    }

    result = AgreementFlowService().generate_training_agreements(
        submission=submission,
        form_config={"documents": []},
        document_service=document_service,
        generated_date="2026-06-10",
    )

    assert result.success is True
    assert result.agreements == [{"filename": "umowa.pdf"}]
    assert document_service.generated_collections[0][1]["context_extra"] == {"generated_date": "2026-06-10"}


def test_agreement_flow_blocks_generation_until_declaration_is_signed():
    result = AgreementFlowService().generate_training_agreements(
        submission={"submission_id": "abc", "form_slug": "sample", "row": {"declaration_signature_valid": ""}},
        form_config={},
        document_service=FakeDocumentService(),
    )

    assert result.success is False
    assert result.error_code == "declaration_signature_required"


def test_document_storage_uses_legacy_filename_fallback_only_without_metadata(caplog):
    storage = DummyStorage()
    service = DocumentStorageService()

    with caplog.at_level(logging.WARNING):
        payload = service.read_document_bytes(
            storage=storage,
            slug="sample",
            filename="../plik.pdf",
            metadata=None,
            submission_id="abc",
        )

    assert payload == b"%PDF legacy"
    assert storage.legacy_calls == [("sample", "plik.pdf")]
    assert "Legacy PDF lookup by filename" in caplog.text


def test_document_storage_blocks_path_traversal_in_metadata():
    with pytest.raises(DocumentStorageError):
        DocumentStorageService().read_document_bytes(
            storage=DummyStorage(),
            slug="sample",
            filename="plik.pdf",
            metadata={"storage_path": "output/sample/../secret.pdf"},
            submission_id="abc",
        )


def test_signed_document_service_rejects_non_pdf_bytes():
    with pytest.raises(SignedDocumentError):
        SignedDocumentService.validate_pdf_bytes(b"not a pdf")


def test_signed_document_filename_matches_legacy_format():
    assert build_signed_filename("Jan_Kowalski-deklaracja.pdf") == "Jan_Kowalski-deklaracja-signed.pdf"
    assert SignedDocumentService.build_signed_filename("") == "dokument-signed.pdf"


def test_pdf_render_service_uses_injected_template_renderer(tmp_path):
    calls = []

    def renderer(**kwargs):
        calls.append(kwargs["template_name"])
        kwargs["output_path"].write_bytes(b"%PDF-1.4\n")

    app = SimpleNamespace(config={"TEMP_DIR": tmp_path})
    payload = PdfRenderService(template_renderer=renderer).render_document_pdf_bytes(
        app=app,
        template_name="declaration_template.html",
        context={"name": "Jan"},
    )

    assert payload.startswith(b"%PDF")
    assert calls == ["declaration_template.html"]


def test_pdf_render_service_wraps_renderer_errors(tmp_path):
    def renderer(**kwargs):
        raise RuntimeError("boom")

    app = SimpleNamespace(config={"TEMP_DIR": tmp_path})
    with pytest.raises(PdfRenderError, match="Nie udalo sie wyrenderowac PDF"):
        PdfRenderService(template_renderer=renderer).render_document_pdf_bytes(
            app=app,
            template_name="declaration_template.html",
            context={},
        )


def test_document_view_model_exposes_backend_status_flags():
    view = DocumentViewService().build_documents_view(
        row={
            "process_status": "OFFICER_REJECTED",
            "declaration_filename": "deklaracja.pdf",
            "declaration_signature_valid": "Nie",
        },
        documents_config=[{"id": "declaration", "label": "Deklaracja"}],
        download_url_builder=lambda filename, signed=False: f"/downloads/{filename}",
    )

    assert view["is_rejected"] is True
    assert view["is_final"] is True
    assert view["documents"][0]["can_download"] is True
    assert view["documents"][0]["can_upload"] is True


def test_legacy_training_filename_wrapper_delegates(monkeypatch):
    import legacy_app

    calls = []

    def fake_builder(pattern, row, training, sequence):
        calls.append((pattern, row["submission_id"], training["id"], sequence))
        return "delegated.pdf"

    monkeypatch.setattr(
        legacy_app.legacy_document_generation_service,
        "build_training_agreement_filename",
        fake_builder,
    )

    assert legacy_app.build_training_agreement_filename("x", {"submission_id": "abc"}, {"id": "excel"}, 2) == "delegated.pdf"
    assert calls == [("x", "abc", "excel", 2)]


def test_declaration_orchestration_renders_saves_and_updates_legacy_fields():
    storage = DummyStorage()
    renderer = FakePdfRenderer()
    storage_service = FakeDocumentStorageService()
    row = {"submission_id": "abc", "imiona": "Jan", "nazwisko": "Kowalski"}
    submission = {"submission_id": "abc", "form_slug": "sample", "row": row}
    form_definition = {
        "title": "Form",
        "fields": [],
        "process": {
            "documents": {
                "declaration": {
                    "enabled": True,
                    "template": "Template/deklaracja.html",
                    "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
                }
            }
        },
    }

    result = declaration_service.ensure_declaration_generated(
        submission,
        app=SimpleNamespace(config={"TEMP_DIR": "tmp"}),
        storage=storage,
        get_form_definition=lambda slug: form_definition,
        resolve_template_html=lambda path: "<html>Deklaracja</html>",
        resolve_pdf_image_url=lambda definition: None,
        pdf_render_service=renderer,
        document_storage_service=storage_service,
    )

    assert result == {"enabled": True, "filename": "Jan_Kowalski-deklaracja.pdf", "created": True}
    assert renderer.calls[0]["template_html"] == "<html>Deklaracja</html>"
    assert storage_service.saved[0]["filename"] == "Jan_Kowalski-deklaracja.pdf"
    assert storage.updated[0][2]["declaration_generated"] == "Tak"
    assert row["declaration_filename"] == "Jan_Kowalski-deklaracja.pdf"


def test_training_agreement_orchestration_generates_many_agreements_and_updates_legacy_json():
    storage = DummyStorage()
    renderer = FakePdfRenderer()
    storage_service = FakeDocumentStorageService()
    row = {
        "submission_id": "abc",
        "imiona": "Jan",
        "nazwisko": "Kowalski",
        "selected_trainings": json.dumps(
            [
                {"id": "excel", "name": "Excel", "price": 1200},
                {"id": "angielski", "name": "Angielski", "price": 1000},
            ]
        ),
    }
    submission = {"submission_id": "abc", "form_slug": "sample", "row": row}

    agreements = document_generation_service.generate_training_agreements_for_submission(
        submission,
        app=SimpleNamespace(config={"TEMP_DIR": "tmp"}),
        storage=storage,
        get_form_definition=lambda slug: {"title": "Form", "fields": []},
        get_training_agreement_config=lambda definition: {
            "enabled": True,
            "template": "Template/umowa.html",
            "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
            "numbering": {"number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"},
        },
        parse_selected_trainings=lambda row: json.loads(row["selected_trainings"]),
        resolve_template_html=lambda path: "<html>Umowa</html>",
        resolve_pdf_image_url=lambda definition: None,
        generated_date="2026-06-09",
        pdf_render_service=renderer,
        document_storage_service=storage_service,
    )

    assert [item["number"] for item in agreements] == ["abc/1/2026-06-09", "abc/2/2026-06-09"]
    assert [item["filename"] for item in agreements] == [
        "Jan_Kowalski-excel-umowa.pdf",
        "Jan_Kowalski-angielski-umowa.pdf",
    ]
    assert [item["filename"] for item in json.loads(row["training_agreements"])] == [
        "Jan_Kowalski-excel-umowa.pdf",
        "Jan_Kowalski-angielski-umowa.pdf",
    ]
    assert len(storage_service.saved) == 2
    assert storage.updated[0][2]["agreement_generated"] == "Tak"


def test_legacy_declaration_and_agreement_wrappers_delegate(monkeypatch):
    import legacy_app

    declaration_calls = []
    agreement_calls = []

    def fake_declaration(submission, **kwargs):
        declaration_calls.append((submission["submission_id"], kwargs["force"]))
        return {"created": True}

    def fake_agreements(submission, **kwargs):
        agreement_calls.append((submission["submission_id"], kwargs["generated_date"]))
        return [{"filename": "umowa.pdf"}]

    monkeypatch.setattr(legacy_app.legacy_declaration_service, "ensure_declaration_generated", fake_declaration)
    monkeypatch.setattr(
        legacy_app.legacy_document_generation_service,
        "generate_training_agreements_for_submission",
        fake_agreements,
    )

    submission = {"submission_id": "abc", "form_slug": "sample", "row": {}}

    assert legacy_app.ensure_declaration_generated(submission, force=True) == {"created": True}
    assert legacy_app.generate_training_agreements_for_submission(submission, "2026-06-09") == [{"filename": "umowa.pdf"}]
    assert declaration_calls == [("abc", True)]
    assert agreement_calls == [("abc", "2026-06-09")]


def test_legacy_declaration_form_definition_wrapper_delegates(monkeypatch):
    import legacy_app

    calls = []

    def fake_definition(config):
        calls.append(config["form_title"])
        return {"title": "Delegated", "fields": []}

    monkeypatch.setattr(
        legacy_app.legacy_declaration_service,
        "build_declaration_form_definition",
        fake_definition,
    )

    assert legacy_app.build_declaration_form_definition({}, {"form_title": "Deklaracja"}) == {
        "title": "Delegated",
        "fields": [],
    }
    assert calls == ["Deklaracja"]


def test_legacy_pdf_adapter_respects_generate_document_pdf_bytes_monkeypatch(monkeypatch):
    import legacy_app

    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs["template_name"])
        return b"%PDF patched"

    monkeypatch.setattr(legacy_app, "generate_document_pdf_bytes", fake_generate)

    payload = legacy_app.LegacyPdfRenderAdapter().render_document_pdf_bytes(
        app=SimpleNamespace(config={"TEMP_DIR": "tmp"}),
        template_name="legacy_template.html",
        context={},
    )

    assert payload == b"%PDF patched"
    assert calls == ["legacy_template.html"]


def test_document_generation_service_builds_document_number():
    assert document_generation_service.build_document_number(
        {"numbering": {"number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"}},
        submission_id="abc",
        sequence=3,
        generated_date="2026-06-09",
    ) == "abc/3/2026-06-09"
