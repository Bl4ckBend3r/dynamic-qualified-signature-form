import json
from types import SimpleNamespace

import pytest
from flask import Flask, render_template_string

from services.document_service import DocumentService, normalize_selected_items
from services.document_naming_service import resolve_pdf_storage_path
from testing_support import InMemoryStorage


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


def test_forced_declaration_generation_preserves_existing_pdf_bytes(monkeypatch):
    class PdfStorage:
        output_dir = "output"

        def __init__(self):
            self.files = {}

        def pdf_storage_path(self, slug, filename, document_type=None, signed=None):
            return resolve_pdf_storage_path(
                output_dir=self.output_dir, slug=slug, filename=filename,
                document_type=document_type, signed=signed,
            )

        def exists(self, path):
            return path in self.files

        def save_pdf(self, slug, filename, pdf_bytes, document_type=None, signed=None):
            self.files[self.pdf_storage_path(slug, filename, document_type, signed)] = pdf_bytes

        def get_pdf_bytes(self, slug, filename):
            return self.files[self.pdf_storage_path(slug, filename, "declaration", False)]

        def read_bytes(self, path):
            return self.files[path]

        def delete(self, path, missing_ok=False):
            self.files.pop(path, None)

    storage = PdfStorage()
    old_path = storage.pdf_storage_path("form", "notice.pdf", "declaration", False)
    storage.files[old_path] = b"old PDF"
    recorded = []
    service = DocumentService(
        storage=storage,
        pdf_render_service=SimpleNamespace(render_document_pdf_bytes=lambda **kwargs: b"new PDF"),
        submission_document_service=SimpleNamespace(record_generated_document=lambda **kwargs: recorded.append(kwargs) or True),
    )
    monkeypatch.setattr(service, "get_document_by_id", lambda *args: {"id": "declaration", "enabled": True, "fields": []})
    monkeypatch.setattr(service, "build_filename_for_document", lambda *args: "notice.pdf")
    monkeypatch.setattr(service, "resolve_document_template", lambda *args: "<p>Declaration</p>")
    monkeypatch.setattr(service, "resolve_pdf_image_url", lambda *args: None)
    monkeypatch.setattr("services.document_service.build_document_pdf_context", lambda **kwargs: {})
    monkeypatch.setattr("services.document_service.build_declaration_render_context", lambda context, **kwargs: context)
    submission = {"submission_id": "SUB1", "form_slug": "form", "row": {
        "submission_id": "SUB1", "form_slug": "form", "data_json": {},
        "declaration_generated": "Tak", "declaration_filename": "notice.pdf",
    }}

    with Flask(__name__).app_context():
        generated = service.generate_document(submission, {"fields": []}, "declaration", force=True)

    assert generated["filename"] == "notice-2.pdf"
    assert storage.files[old_path] == b"old PDF"
    assert storage.files[recorded[0]["storage_path"]] == b"new PDF"
    assert recorded[0]["storage_path"] != old_path


@pytest.mark.parametrize("source,document,expected", [
    ("html", {"template_source": "html", "template_html": "<p>Fresh HTML</p>"}, "Fresh HTML"),
    ("builder", {"template_source": "builder", "builder_document": {"version": 1,
        "blocks": [{"id": "body", "type": "paragraph", "content": "Fresh builder"}]}}, "Fresh builder"),
    ("docx", {"template_source": "docx", "template_metadata": {"storage_path": "current.docx"}}, "Fresh DOCX"),
])
def test_revision_uses_current_html_builder_or_docx_template(monkeypatch, source, document, expected):
    storage = InMemoryStorage({})
    recorded = []
    rendered = []
    service = DocumentService(storage=storage,
        pdf_render_service=SimpleNamespace(render_document_pdf_bytes=lambda **kwargs:
            rendered.append(kwargs["template_html"]) or b"%PDF-1.4\ncurrent\n%%EOF"),
        submission_document_service=SimpleNamespace(record_generated_document=lambda **kwargs:
            recorded.append(kwargs) or True, list_documents=lambda _id: []))
    monkeypatch.setattr(service, "get_document_by_id", lambda *_: {"id": "declaration", "enabled": True,
        "fields": [], **document})
    monkeypatch.setattr(service, "build_filename_for_document", lambda *_: "current.pdf")
    monkeypatch.setattr(service, "resolve_pdf_image_url", lambda *_: None)
    monkeypatch.setattr("services.document_service.build_document_pdf_context", lambda **kwargs: {})
    monkeypatch.setattr("services.document_service.build_declaration_render_context", lambda context, **kwargs: context)
    if source == "docx":
        monkeypatch.setattr("services.document_service.parse_stored_agreement_docx",
                            lambda *_: SimpleNamespace(html="<p>Fresh DOCX</p>"))
    submission = {"submission_id": "SUB1", "form_slug": "form", "data_json": {}}
    metadata = {"form_version_id": 2, "revision": 2, "template_hash": "new-template"}
    with Flask(__name__).app_context():
        generated = service.generate_document(submission, {"fields": []}, "declaration",
                                              revision_metadata=metadata)
    assert expected in rendered[0]
    assert "-v2-r2" in generated["filename"]
    assert recorded[0]["revision_metadata"] == metadata


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


@pytest.mark.parametrize("document_type", ["agreement", "declaration"])
def test_production_template_resolution_uses_builder_inline_run_renderer(document_type):
    phrase = "Regulaminu projektu"
    builder_document = {
        "version": 1,
        "document_type": document_type,
        "blocks": [{
            "type": "paragraph",
            "runs": [
                {"text": "Treść przed "},
                {"text": phrase, "bold": True, "italic": True, "underline": True},
                {"text": " po treści."},
            ],
        }],
    }

    template = DocumentService().resolve_document_template({
        "id": document_type,
        "template_source": "builder",
        "builder_document": builder_document,
    })

    assert (
        '<strong><em><span class="document-text-underline">'
        f"{phrase}</span></em></strong>"
    ) in template
