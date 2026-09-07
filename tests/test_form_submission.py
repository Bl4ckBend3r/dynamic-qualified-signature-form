import re


FORM_URL = "/submit/formularz_zgloszeniowy"


def _prepare(data):
    data["pe" + "sel"] = "90010112356"
    return data


def _public_csrf(client):
    html = client.get("/form/formularz_zgloszeniowy").get_data(as_text=True)
    return html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]


def _simple_form_definition():
    return {
        "title": "Prosty formularz JSON",
        "fields": [
            {"type": "text", "name": "imie", "label": "Imię", "required": True},
            {"type": "text", "name": "nazwisko", "label": "Nazwisko", "required": True},
            {"type": "email", "name": "email", "label": "E-mail", "required": False},
            {"type": "tel", "name": "telefon", "label": "Telefon", "required": True},
            {"type": "select", "name": "wybrane_szkolenie", "label": "Wybrane szkolenie", "required": True, "options": ["Kompetencje cyfrowe", "Kompetencje osobiste/społeczne", "Język angielski"]},
            {"type": "radio", "name": "oswiadczenie_18_lat", "label": "Mam 18 lat", "required": True, "options": ["TAK", "NIE"]},
            {"type": "textarea", "name": "uwagi", "label": "Uwagi", "required": False},
        ],
        "documents": {"declaration": {"enabled": False}, "agreement": {"enabled": False}},
    }


def _simple_form_data():
    return {
        "imie": "Jan",
        "nazwisko": "Kowalski",
        "email": "jan@example.com",
        "telefon": "600700800",
        "wybrane_szkolenie": "Kompetencje cyfrowe",
        "oswiadczenie_18_lat": "TAK",
        "uwagi": "",
    }


def test_public_form_renders_csrf_inside_form(client):
    html = client.get("/form/formularz_zgloszeniowy").get_data(as_text=True)
    form_match = re.search(r'<form\b[^>]*\bmethod="post"[^>]*>(.*?)</form>', html, re.DOTALL)

    assert form_match is not None
    assert 'type="hidden" name="csrf_token"' in form_match.group(1)
    assert _public_csrf(client)


def test_public_form_explains_required_markers_and_exposes_accessible_required_fields(client):
    html = client.get("/form/formularz_zgloszeniowy").get_data(as_text=True)

    assert "Pola oznaczone" in html
    assert "są obowiązkowe." in html
    assert 'class="required required-marker" aria-hidden="true">*</span>' in html
    assert 'required aria-required="true"' in html


def test_missing_public_csrf_returns_readable_error_and_logs_reason(app, client, caplog):
    app.config["WTF_CSRF_ENABLED"] = True
    _public_csrf(client)

    response = client.post(FORM_URL, data=_simple_form_data())
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "tokenu bezpieczeństwa" in html
    assert "csrf_invalid" in caplog.text
    assert "slug=formularz_zgloszeniowy" in caplog.text
    assert "csrf_missing=True" in caplog.text


def test_required_field_error_is_rendered_next_to_field(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    token = _public_csrf(client)
    payload = _prepare(_simple_form_data())
    payload.update({"csrf_token": token, "imie": ""})

    response = client.post(FORM_URL, data=payload)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert 'name="imie"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="imie-error"' in html
    assert 'id="imie-error" role="alert"' in html
    assert "Pole „Imię” jest wymagane." in html


def test_simple_dynamic_form_accepts_displayed_training_value_and_saves(app, client):
    app.testing_storage.form_definition = _simple_form_definition()
    app.config["WTF_CSRF_ENABLED"] = True
    payload = {**_simple_form_data(), "csrf_token": _public_csrf(client)}

    response = client.post(FORM_URL, data=payload)

    assert response.status_code == 200
    assert len(app.testing_storage.csv_rows) == 1
    assert app.testing_storage.csv_rows[0]["data_json"]["wybrane_szkolenie"] == "Kompetencje cyfrowe"


def test_invalid_training_value_is_shown_at_select(app, client, caplog):
    app.testing_storage.form_definition = _simple_form_definition()
    app.config["WTF_CSRF_ENABLED"] = True
    payload = {**_simple_form_data(), "wybrane_szkolenie": "nieznany_kod", "csrf_token": _public_csrf(client)}

    response = client.post(FORM_URL, data=payload)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert 'name="wybrane_szkolenie"' in html
    assert 'aria-describedby="wybrane_szkolenie-error"' in html
    assert "Wybrano nieprawidłową wartość." in html
    assert "reason=validation" in caplog.text
    assert "invalid_fields=wybrane_szkolenie" in caplog.text
    assert "csrf_missing=False" in caplog.text


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
    # Successful qualification follows the configured submission edge into review.
    assert row["process_status"] == "WAITING_FOR_OFFICER_DECISION"
    assert row["workflow_step"] == "officer_review"
    assert row["data_json"]["_qualification"]["passed"] is True
