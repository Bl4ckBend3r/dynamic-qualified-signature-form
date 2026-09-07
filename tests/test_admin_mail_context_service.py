from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

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


def test_office_signed_agreements_are_used_in_final_agreement_mail_list():
    form = SimpleNamespace(name="Form", slug="formularz")
    submission = FormSubmission(
        submission_id="abc",
        form_slug="formularz",
        form_name="Form",
        access_token="token",
        data_json={},
    )

    context = build_mail_context(
        form,
        submission,
        [{
            "filename": "final-python.pdf",
            "document_type": "agreement_signed_by_office",
            "signed": True,
        }],
        document_url_builder=lambda item, filename: f"/download/{filename}",
    )

    assert "final-python.pdf" in context["signed_agreements_list"]
    assert "/download/final-python.pdf" in context["signed_agreements_list"]


def test_render_and_preview_mail_context_defaults():
    form = SimpleNamespace(name="Form", slug="formularz")

    assert render_mail_text("Witaj {{ imiona }}", {"imiona": "Jan"}) == "Witaj Jan"
    preview = preview_mail_context(form)
    assert preview["imiona"] == "Jan"
    assert preview["form_slug"] == "formularz"


def test_missing_nested_variable_keeps_resolved_values_and_diagnoses(caplog):
    rendered = render_mail_text('Dzień dobry {{ participant.first_name }}. Numer {{ submission_id }}. {{ absent.value }}',
                                {'participant': {'first_name': 'Jan'}, 'submission_id': 'ABC'})
    assert rendered == 'Dzień dobry Jan. Numer ABC. '
    assert 'missing_template_variable=absent.value' in caplog.text


def test_root_context_preserves_dynamic_values_when_model_column_is_empty():
    submission = FormSubmission(submission_id='ABC', data_json={'email': 'dynamic@example.org', 'imiona': 'Jan'})
    context = build_mail_context(SimpleNamespace(name='Form', slug='form'), submission)
    assert context['email'] == 'dynamic@example.org'
    assert context['imiona'] == 'Jan'
    assert context['submitted_at'] == submission.created_at


def test_text_fallback_keeps_resolved_special_link():
    from services.mail_template_service import render_platform_mail_text
    template = SimpleNamespace(name='Status', html_body='<a href="{{ public_status_url }}">Sprawdź status</a>')
    text = render_platform_mail_text(template, {'public_status_url': 'https://example.org/status/ABC'})
    assert 'https://example.org/status/ABC' in text
    assert '{{' not in text and '<a' not in text


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
