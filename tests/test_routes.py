def test_index_lists_available_forms(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Formularz zgłoszeniowy" in response.get_data(as_text=True)


def test_form_page_loads(client):
    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Formularz zgłoszeniowy" in html
    assert 'name="imie"' in html
    assert 'name="pesel"' in html


def test_unknown_form_returns_404(client):
    response = client.get("/form/brak-formularza")

    assert response.status_code == 404


def test_submit_empty_form_returns_validation_errors(client):
    response = client.post("/submit/formularz_zgloszeniowy", data={})
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Formularz zawiera błędy" in html
    assert "Pole „Imię” jest wymagane." in html


def test_submit_invalid_email_returns_validation_error(client, valid_form_data):
    valid_form_data["email"] = "invalid-email"

    response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Podaj poprawny adres e-mail." in html


def test_submit_valid_form_generates_pdf_and_csv_row(client, app, valid_form_data):
    response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Wynik operacji" in html
    assert "Formularz zgłoszeniowy" in html

    storage = app.testing_storage
    assert len(storage.csv_rows) == 1
    assert storage.csv_rows[0]["form_slug"] == "formularz_zgloszeniowy"
    assert storage.csv_rows[0]["imie"] == "Jan"
    assert storage.csv_rows[0]["pdf_filename"].endswith(".pdf")
    assert storage.saved_pdfs


def test_show_result_for_existing_submission(client, app, valid_form_data):
    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    submission_id = app.testing_storage.csv_rows[0]["submission_id"]
    response = client.get(f"/result/formularz_zgloszeniowy/{submission_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert submission_id in html
    assert "Formularz zgłoszeniowy" in html


def test_download_pdf_returns_generated_pdf(client, app, valid_form_data):
    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    response = client.get(f"/download/formularz_zgloszeniowy/{row['pdf_filename']}")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-1.4")


def test_documents_to_sign_get_loads(client):
    response = client.get("/do-podpisania")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "podpisania" in html


def test_acceptance_status_missing_submission(client):
    response = client.get("/api/submissions/brak-id/acceptance-status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["exists"] is False
    assert payload["can_sign_documents"] is False
