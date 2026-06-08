from types import SimpleNamespace

from models import FormSubmission
from services.admin_mail_context_service import (
    build_mail_context,
    mail_template_type_score,
    preview_mail_context,
    render_mail_text,
)


def test_build_mail_context_adds_document_urls_when_builders_are_available():
    form = SimpleNamespace(name="Form", slug="formularz")
    submission = FormSubmission(
        submission_id="abc",
        form_slug="formularz",
        form_name="Form",
        pdf_filename="abc.pdf",
        access_token="token",
        data_json={},
    )

    context = build_mail_context(
        form,
        submission,
        [],
        documents_to_sign_url_builder=lambda item: f"/do-podpisania?submission_id={item.submission_id}",
        document_url_builder=lambda item, filename: f"/download/{filename}",
    )

    assert context["podpisz_url"] == "/do-podpisania?submission_id=abc"
    assert context["document_url"] == "/download/abc.pdf"
    assert context["pobierz_url"] == "/download/abc.pdf"


def test_render_and_preview_mail_context_defaults():
    form = SimpleNamespace(name="Form", slug="formularz")

    assert render_mail_text("Witaj {{ imiona }}", {"imiona": "Jan"}) == "Witaj Jan"
    preview = preview_mail_context(form)
    assert preview["imiona"] == "Jan"
    assert preview["form_slug"] == "formularz"


def test_mail_template_type_score_matches_legacy_rules():
    submission = SimpleNamespace(
        officer_decision="accepted",
        process_status="FORM_SUBMITTED",
        declaration_signed="",
        agreement_signed="",
    )
    template = SimpleNamespace(template_type="accepted")

    assert mail_template_type_score(template, submission) == 3
    assert mail_template_type_score(SimpleNamespace(template_type="unknown"), submission) == 0
