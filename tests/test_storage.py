FORM_URL = "/submit/formularz_zgloszeniowy"


def test_submit_valid_form_creates_csv_file(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200
    assert len(app.testing_storage.csv_rows) == 1


def test_csv_contains_submitted_candidate_data(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    assert row["imie"] == valid_form_data["imie"]
    assert row["nazwisko"] == valid_form_data["nazwisko"]
    assert row["email"] == valid_form_data["email"]
