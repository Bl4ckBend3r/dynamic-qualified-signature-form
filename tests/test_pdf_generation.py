FORM_URL = "/submit/formularz_zgloszeniowy"


def _linked_pdf_context(form_definition, valid_form_data):
    from copy import deepcopy

    from form_loader import build_consents_view, build_submission_view

    definition = deepcopy(form_definition)
    definition["fields"][-2]["pdf_text"] = (
        'Przeczytałem <a href="https://example.com/dokument">Dokument</a>.'
    )
    values = dict(valid_form_data)
    values["specjalne_potrzeby"] = (
        '<a href="https://example.com" onclick="alert(1)" title="bad">Dokument</a>'
        '<script>alert(2)</script><style>body{display:none}</style>'
        '<img src=x onerror="alert(3)">'
        '<a href="javascript:alert(4)">Zły link</a>'
        '<a href="javascript&#58;alert(5)">Zakodowany</a>'
        '<a href="data:text/html,evil">Dane</a>'
    )
    return {
        "form_definition": definition,
        "submission_view": build_submission_view(definition, values),
        "submission_id": "example-id",
        "consents_view": build_consents_view(definition, values),
    }


def test_pdf_html_sanitizes_link_values_before_chromium(app, form_definition, valid_form_data, tmp_path, monkeypatch):
    import pdf_generator

    captured = {}

    def capture_html(html_string, output_path, footer_image_url=None):
        captured["html"] = html_string
        return output_path

    monkeypatch.setattr(pdf_generator, "write_pdf_from_html", capture_html)
    context = _linked_pdf_context(form_definition, valid_form_data)
    pdf_generator.generate_pdf(app, "pdf_template.html", context, tmp_path / "form.pdf")

    html = captured["html"]
    assert '<a href="https://example.com">Dokument</a>' in html
    assert '<a href="https://example.com/dokument">Dokument</a>' in html
    assert "Imię" in html and "Jan" in html
    assert "&lt;a href=" not in html
    assert "<script" not in html and "alert(2)" not in html
    assert "<style>body{display:none}</style>" not in html
    assert "<img" not in html and "onerror" not in html
    assert "onclick" not in html and "title=\"bad\"" not in html
    assert "javascript:" not in html and "data:text/html" not in html
    assert "Zły link" in html and "Zakodowany" in html and "Dane" in html


def test_pdf_plain_values_remain_escaped_and_urls_are_not_auto_linked(app, form_definition, valid_form_data):
    from flask import render_template
    from form_loader import build_submission_view

    values = dict(valid_form_data)
    values["specjalne_potrzeby"] = "2 < 3 & https://example.com <b>dosłownie</b>"
    with app.app_context():
        html = render_template(
            "pdf_template.html",
            form_definition=form_definition,
            submission_view=build_submission_view(form_definition, values),
            submission_id="example-id",
            consents_view=[],
        )

    assert "2 &lt; 3 &amp; https://example.com &lt;b&gt;dosłownie&lt;/b&gt;" in html
    assert '<a href="https://example.com">' not in html


def test_pdf_renders_each_visible_field_once_and_omits_hidden_consent(app):
    from flask import render_template
    from form_loader import build_consents_view, build_submission_view

    definition = {"title": "Formularz", "fields": [
        {"type": "section", "label": "Oświadczenia"},
        {"type": "checkbox", "name": "ordinary", "label": "Kompetencje Cyfrowe"},
        {"type": "checkbox", "name": "consent", "label": "Akceptacja", "consent": True},
        {"type": "checkbox", "name": "conditional", "label": "Zgoda warunkowa", "consent": True,
         "visible_if": {"field": "answer", "operator": "equals", "value": "Tak"}},
    ]}
    for answer in ("Nie", "Tak"):
        values = {"answer": answer, "ordinary": "Tak", "consent": "Tak", "conditional": "Tak"}
        with app.app_context():
            html = render_template(
                "pdf_template.html", form_definition=definition,
                submission_view=build_submission_view(definition, values),
                consents_view=build_consents_view(definition, values), submission_id="example-id",
            )
        assert html.count("Kompetencje Cyfrowe") == 1
        assert html.count("Akceptacja") == 1
        assert html.count("Zgoda warunkowa") == (1 if answer == "Tak" else 0)


def test_chromium_pdf_contains_clickable_link(app, form_definition, valid_form_data, tmp_path):
    import os
    from pathlib import Path

    import pypdf
    import pytest

    from pdf_generator import generate_pdf

    browser_dir = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        or (
            Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
            if os.name == "nt"
            else Path.home() / ".cache" / "ms-playwright"
        )
    )
    if not any(browser_dir.glob("chromium_headless_shell-*")):
        pytest.skip("Playwright Chromium is not installed")

    output_path = tmp_path / "linked-form.pdf"
    generate_pdf(
        app,
        "pdf_template.html",
        _linked_pdf_context(form_definition, valid_form_data),
        output_path,
    )

    reader = pypdf.PdfReader(str(output_path))
    assert any("Dokument" in (page.extract_text() or "") for page in reader.pages)
    uris = [
        annotation.get_object().get("/A", {}).get("/URI")
        for page in reader.pages
        for annotation in page.get("/Annots", [])
    ]
    assert "https://example.com" in {uri.rstrip("/") for uri in uris}
    assert "https://example.com/dokument" in uris
    assert all("<a href" not in (page.extract_text() or "") for page in reader.pages)


def test_submit_valid_form_creates_pdf_file(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200
    assert app.testing_storage.saved_pdfs

    pdf_content = next(iter(app.testing_storage.saved_pdfs.values()))
    assert len(pdf_content) > 0


def test_generated_pdf_has_pdf_header(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200

    pdf_content = next(iter(app.testing_storage.saved_pdfs.values()))
    assert pdf_content.startswith(b"%PDF")
