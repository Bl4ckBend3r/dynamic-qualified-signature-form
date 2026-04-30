FORM_URL = "/submit/formularz_zgloszeniowy"


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
