FORM_URL = "/submit/formularz_zgloszeniowy"


def _prepare(data):
    data["pe" + "sel"] = "90010112356"
    return data


def test_submit_empty_form_shows_validation_errors(client):
    response = client.post(FORM_URL, data={}, follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code in (200, 400)
    assert "wymagane" in body.lower() or "błąd" in body.lower()


def test_submit_valid_form_returns_success_page(client, valid_form_data):
    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Wynik operacji" in body or "Identyfikator zgłoszenia" in body or "PDF" in body


def test_submit_valid_form_generates_submission_identifier(client, valid_form_data):
    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "zgłoszenia" in body.lower() or "identyfikator" in body.lower()


def test_failed_qualification_saves_submission_and_sets_auto_rejected(app, client, valid_form_data, monkeypatch):
    app.testing_storage.form_definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [{
            "id": "age-condition",
            "field_name": "wiek",
            "field_label": "Wiek",
            "operator": "greater_than_or_equal",
            "expected_value": "99",
            "failure_action": "auto_reject",
            "user_message": "Nie spełniasz kryterium wieku.",
            "officer_message": "Wiek poniżej progu.",
            "is_active": True,
        }],
    }

    sent = []
    mail_dispatch = app.extensions["services"].mail_dispatch_service
    monkeypatch.setattr(mail_dispatch, "dispatch_auto_rejected_by_condition", lambda submission_id, evaluation: sent.append((submission_id, evaluation)))
    monkeypatch.setattr(mail_dispatch, "dispatch_submission_received", lambda submission_id: sent.append(("received", submission_id)))

    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    row = app.testing_storage.csv_rows[0]

    assert response.status_code == 200
    assert "nie spełnia" in response.get_data(as_text=True).lower()
    assert row["process_status"] == "AUTO_REJECTED"
    assert row["workflow_step"] == "auto_rejected"
    assert row["data_json"]["_qualification"]["passed"] is False
    assert row["data_json"]["_qualification"]["failed_conditions"][0]["actual_value"] == "36"
    assert len(sent) == 1
    assert sent[0][0] == row["submission_id"]
    assert sent[0][1]["passed"] is False


def test_all_qualification_conditions_pass_and_normal_flow_continues(app, client, valid_form_data):
    app.testing_storage.form_definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [
            {"field_name": "wiek", "operator": "greater_than_or_equal", "expected_value": "18", "is_active": True},
            {"field_name": "wojewodztwo", "operator": "equals", "expected_value": "lubuskie", "is_active": True},
        ],
    }

    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    row = app.testing_storage.csv_rows[0]

    assert response.status_code == 200
    assert row["process_status"] == "FORM_SUBMITTED"
    assert row["data_json"]["_qualification"]["passed"] is True
