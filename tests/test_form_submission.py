import re
from pathlib import Path

import pytest


FORM_URL = "/submit/formularz_zgloszeniowy"


@pytest.fixture(autouse=True)
def _stub_form_pdf(monkeypatch):
    """These tests cover submission flow; PDF rendering has separate tests."""
    monkeypatch.setattr(
        "services.submission_service.generate_pdf",
        lambda app, template_name, context, output_path: Path(output_path).write_bytes(b"%PDF-1.4\n"),
    )


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
    assert re.search(r'required\s+aria-required="true"', html)


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


def test_admin_notice_runs_only_after_successful_initial_submit(app, client, monkeypatch):
    app.testing_storage.form_definition = _simple_form_definition()
    app.testing_storage.form_definition["new_submission_notification"] = {
        "enabled": True, "recipient_email": "office@example.org"}
    app.config["WTF_CSRF_ENABLED"] = True
    submission_service = app.extensions["services"].submission_service
    dispatcher = app.extensions["services"].mail_dispatch_service
    notices = []
    monkeypatch.setattr(dispatcher, "dispatch_new_submission_notification",
                        lambda submission_id: notices.append((submission_id, bool(app.testing_storage.csv_rows[0].get("pdf_filename")))))
    csrf = _public_csrf(client)
    invalid = client.post(FORM_URL, data={**_simple_form_data(), "imie": "", "csrf_token": csrf})
    assert invalid.status_code == 400 and notices == []
    result = client.post(FORM_URL, data={**_simple_form_data(), "csrf_token": csrf})
    assert result.status_code == 200
    assert len(notices) == 1 and notices[0][1] is True

    monkeypatch.setattr(submission_service, "_generate_form_pdf", lambda **kwargs: (_ for _ in ()).throw(OSError("PDF failed")))
    failed = client.post(FORM_URL, data={**_simple_form_data(), "csrf_token": csrf})
    assert failed.status_code == 500
    assert len(notices) == 1


def test_admin_notice_failure_does_not_fail_successful_submission(app, client, monkeypatch):
    app.testing_storage.form_definition = _simple_form_definition()
    app.testing_storage.form_definition["new_submission_notification"] = {
        "enabled": True, "recipient_email": "office@example.org"}
    app.config["WTF_CSRF_ENABLED"] = True
    dispatcher = app.extensions["services"].mail_dispatch_service
    def failed_notice(_submission_id):
        raise OSError("SMTP unavailable")
    monkeypatch.setattr(dispatcher, "dispatch_new_submission_notification", failed_notice)
    result = client.post(FORM_URL, data={**_simple_form_data(), "csrf_token": _public_csrf(client)})
    assert result.status_code == 200
    assert len(app.testing_storage.csv_rows) == 1


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
    assert len(sent) == 2
    assert sent[0][0] == row["submission_id"]
    assert sent[0][1]["passed"] is False
    assert sent[1] == ("received", row["submission_id"])


def test_auto_rejected_result_shows_default_message_once(app, client, valid_form_data):
    app.testing_storage.form_definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [{
            "id": "age-condition",
            "field_name": "wiek",
            "operator": "greater_than_or_equal",
            "expected_value": "99",
            "failure_action": "auto_reject",
            "is_active": True,
        }],
    }
    default_message = "Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału."
    with client.session_transaction() as session:
        session["_flashes"] = [("info", default_message)]

    response = client.post(FORM_URL, data=_prepare(valid_form_data))

    assert response.status_code == 200
    assert "Odrzucenie zgłoszenia".encode() in response.data
    assert response.data.count(default_message.encode()) == 1
    assert app.testing_storage.csv_rows[0]["process_status"] == "AUTO_REJECTED"


def test_auto_rejected_result_shows_custom_message_once(app, client, valid_form_data):
    custom_message = "Nie spełniasz wymaganych kryteriów udziału w projekcie."
    app.testing_storage.form_definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [{
            "id": "age-condition",
            "field_name": "wiek",
            "operator": "greater_than_or_equal",
            "expected_value": "99",
            "failure_action": "auto_reject",
            "user_message": custom_message,
            "is_active": True,
        }],
    }
    with client.session_transaction() as session:
        session["_flashes"] = [("info", custom_message), ("info", "Osobna informacja.")]

    response = client.post(FORM_URL, data=_prepare(valid_form_data))

    assert response.status_code == 200
    assert response.data.count(custom_message.encode()) == 1
    assert response.data.count("Osobna informacja.".encode()) == 1
    assert "Zgłoszenie zostało zapisane, ale nie spełnia warunków udziału.".encode() not in response.data


def test_successful_submission_result_keeps_normal_content(app, client, valid_form_data):
    response = client.post(FORM_URL, data=_prepare(valid_form_data))

    assert response.status_code == 200
    assert "Formularz zgłoszeniowy".encode() in response.data
    assert "Identyfikator zgłoszenia".encode() in response.data
    assert "Status PDF".encode() in response.data
    assert "Odrzucenie zgłoszenia".encode() not in response.data
    assert app.testing_storage.csv_rows[0]["process_status"] != "AUTO_REJECTED"


@pytest.mark.parametrize("qualification_message", ["", "Nie spełniasz wymaganych kryteriów udziału w projekcie."])
def test_auto_rejected_template_uses_one_primary_message(app, qualification_message):
    from flask import render_template

    message = "Nie spełniasz wymaganych kryteriów udziału w projekcie."
    result = {
        "auto_rejected": True,
        "qualification_message": qualification_message,
        "message": message,
        "can_continue": False,
        "form_title": "Formularz zgłoszeniowy",
        "submission_id": "example-id",
    }
    with app.test_request_context("/"):
        html = render_template("result.html", result=result)

    assert html.count(message) == 1


def test_failed_qualification_requiring_officer_decision_still_sends_submission_received(
    app,
    client,
    valid_form_data,
    monkeypatch,
):
    app.testing_storage.form_definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [{
            "id": "age-officer-condition",
            "field_name": "wiek",
            "field_label": "Wiek",
            "operator": "greater_than_or_equal",
            "expected_value": "99",
            "failure_action": "officer_decision",
            "user_message": "Wniosek wymaga dodatkowej weryfikacji.",
            "officer_message": "Wiek poniżej progu.",
            "is_active": True,
        }],
    }

    sent = []
    mail_dispatch = app.extensions["services"].mail_dispatch_service
    monkeypatch.setattr(
        mail_dispatch,
        "dispatch_submission_received",
        lambda submission_id: sent.append(("received", submission_id)),
    )

    response = client.post(
        FORM_URL,
        data=_prepare(valid_form_data),
        follow_redirects=True,
    )

    row = app.testing_storage.csv_rows[0]

    assert response.status_code == 200
    assert row["process_status"] == "FORM_SUBMITTED"
    assert row["workflow_step"] == "officer_decision"
    assert row["officer_decision"] == ""
    assert "Wiek" in row["officer_decision_reason"]
    assert sent == [("received", row["submission_id"])]


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


def test_submission_skips_legacy_document_conditions_until_document_data_is_saved(app, client, valid_form_data):
    definition = app.testing_storage.form_definition
    definition["documents"] = {"declaration": {"enabled": True, "fields": [
        {"name": "deklaracja_18_lat", "type": "radio", "options": ["Tak", "Nie"]},
    ]}}
    definition["qualification_conditions"] = {"enabled": True, "conditions": [
        {"id": "main", "field_name": "wiek", "operator": "greater_than_or_equal", "expected_value": 18},
        {"id": "document", "field_name": "deklaracja_18_lat", "operator": "equals", "expected_value": "Tak", "failure_action": "auto_reject"},
    ]}
    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    assert response.status_code == 200
    row = app.testing_storage.csv_rows[0]
    assert row["process_status"] != "AUTO_REJECTED"
    evaluation = row["data_json"]["_qualification"]
    assert evaluation["passed"]
    assert [item["id"] for item in evaluation["results"]] == ["main"]


def test_explicit_workflow_rejects_all_failed_qualification_conditions_before_advancing(
    app, client, valid_form_data, monkeypatch,
):
    from services.workflow_service import WorkflowTransitionError
    import pytest

    definition = app.testing_storage.form_definition
    definition["workflow"] = {
        "flow_mode": "explicit", "initial_step": "intake",
        "steps": [
            {"id": "intake", "type": "form_submit", "stage_type": "user_action", "status": "FORM_SUBMITTED", "next": "review"},
            {"id": "review", "stage_type": "decision", "status": "WAITING_FOR_OFFICER_DECISION"},
        ],
    }
    definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [
            {"id": "age", "field_name": "wiek", "field_label": "Wiek", "operator": "greater_than_or_equal", "expected_value": "99", "failure_action": "auto_reject", "is_active": True},
            {"id": "region", "field_name": "wojewodztwo", "field_label": "Województwo", "operator": "equals", "expected_value": "inne", "failure_action": "auto_reject", "is_active": True},
        ],
    }

    events = []
    repository = app.extensions["services"].submission_repository
    monkeypatch.setattr(repository, "record_workflow_event", lambda submission_id, event: events.append(event) or True)
    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    row = app.testing_storage.csv_rows[0]

    assert response.status_code == 200
    assert row["process_status"] == "AUTO_REJECTED"
    assert row["workflow_step"] == row["workflow_stage"] == "auto_rejected"
    assert {item["id"] for item in row["data_json"]["_qualification"]["failed_conditions"]} == {"age", "region"}
    assert "Wiek" in row["officer_decision_reason"]
    assert "Województwo" in row["officer_decision_reason"]
    assert any(event["source"] == "auto_rejected_by_condition" for event in events)
    assert not any(event["new_step"] == "review" for event in events)
    with pytest.raises(WorkflowTransitionError):
        app.extensions["services"].workflow_service.transition_to(row["submission_id"], "review")


def test_explicit_workflow_advances_after_all_qualification_conditions_pass(app, client, valid_form_data):
    definition = app.testing_storage.form_definition
    definition["workflow"] = {
        "flow_mode": "explicit", "initial_step": "intake",
        "steps": [
            {"id": "intake", "type": "form_submit", "stage_type": "user_action", "status": "FORM_SUBMITTED", "next": "review"},
            {"id": "review", "stage_type": "decision", "status": "WAITING_FOR_OFFICER_DECISION"},
        ],
    }
    definition["qualification_conditions"] = {
        "enabled": True,
        "conditions": [
            {"field_name": "wiek", "operator": "greater_than_or_equal", "expected_value": "18", "is_active": True},
            {"field_name": "wojewodztwo", "operator": "equals", "expected_value": "lubuskie", "is_active": True},
        ],
    }

    response = client.post(FORM_URL, data=_prepare(valid_form_data), follow_redirects=True)
    row = app.testing_storage.csv_rows[0]

    assert response.status_code == 200
    assert row["data_json"]["_qualification"]["passed"] is True
    assert row["workflow_step"] == "review"
    assert row["process_status"] == "WAITING_FOR_OFFICER_DECISION"
