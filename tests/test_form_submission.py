def test_submit_empty_form_shows_validation_errors(client):
    response = client.post("/submit/sample_form", data={}, follow_redirects=True)

    body = response.get_data(as_text=True)

    assert response.status_code in (200, 400)
    assert (
        "wymagane" in body.lower()
        or "uzupełnij" in body.lower()
        or "validation" in body.lower()
        or "błąd" in body.lower()
    )


def test_submit_valid_form_returns_success_page(client, valid_form_data):
    response = client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert (
        "Wynik operacji" in body
        or "Formularz został przetworzony" in body
        or "Identyfikator zgłoszenia" in body
        or "PDF" in body
    )


def test_submit_valid_form_generates_submission_identifier(client, valid_form_data):
    response = client.post(
        "/submit/sample_form",
        data=valid_form_data,
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert (
        "submission" in body.lower()
        or "zgłoszenia" in body.lower()
        or "identyfikator" in body.lower()
    )