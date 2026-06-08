import io


def test_index_lists_available_forms(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Formularz" in response.get_data(as_text=True)


def test_form_page_loads(client):
    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Formularz" in html
    assert 'name="imie"' in html
    assert 'name="pesel"' in html


def test_initial_form_hides_after_acceptance_fields(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "fields": [
            *app.testing_storage.form_definition["fields"],
            {
                "type": "text",
                "name": "post_acceptance_note",
                "label": "Dodatkowa informacja",
                "required": True,
                "stage": "after_officer_acceptance",
            },
        ],
    }

    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="post_acceptance_note"' not in html


def test_form_page_sanitizes_configured_html(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "fields": [
            {
                "type": "static_text",
                "label": '<script>alert("x")</script><strong>Bezpieczny opis</strong>',
            },
            *app.testing_storage.form_definition["fields"],
        ],
    }

    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'alert("x")' not in html
    assert "<strong>Bezpieczny opis</strong>" in html


def test_unknown_form_returns_404(client):
    response = client.get("/form/brak-formularza")

    assert response.status_code == 404


def test_submit_empty_form_returns_validation_errors(client):
    response = client.post("/submit/formularz_zgloszeniowy", data={})
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Formularz" in html
    assert "wymagane" in html


def test_submit_invalid_email_returns_validation_error(client, valid_form_data):
    valid_form_data["email"] = "invalid-email"

    response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "adres e-mail" in html


def test_submit_valid_form_generates_pdf_and_csv_row(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Formularz" in html

    storage = app.testing_storage
    assert len(storage.csv_rows) == 1
    assert storage.csv_rows[0]["form_slug"] == "formularz_zgloszeniowy"
    assert storage.csv_rows[0]["imie"] == valid_form_data["imie"]
    assert storage.csv_rows[0]["pdf_filename"].endswith(".pdf")
    assert storage.saved_pdfs


def test_submit_creates_submission_through_service(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    service = app.extensions["submission_service"]
    original = service.create_submission
    calls = []

    def spy_create_submission(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    service.create_submission = spy_create_submission
    try:
        response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    finally:
        service.create_submission = original

    assert response.status_code == 200
    assert calls
    assert calls[0]["args"][0] == "formularz_zgloszeniowy"


def test_show_result_for_existing_submission(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    submission_id = app.testing_storage.csv_rows[0]["submission_id"]
    response = client.get(f"/result/formularz_zgloszeniowy/{submission_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert submission_id in html
    assert "Formularz" in html


def test_show_result_generates_pdf_link_with_access_token(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    response = client.get(f"/result/formularz_zgloszeniowy/{row['submission_id']}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f"token={row['access_token']}" in html


def test_download_pdf_returns_generated_pdf(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    response = client.get(
        f"/downloads/pdfs/formularz_zgloszeniowy/{row['pdf_filename']}?token={row['access_token']}"
    )

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-1.4")


def test_download_pdf_returns_403_without_valid_token(client, app, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    submit_response = client.post("/submit/formularz_zgloszeniowy", data=valid_form_data)
    assert submit_response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    response = client.get(f"/downloads/pdfs/formularz_zgloszeniowy/{row['pdf_filename']}?token=invalid")

    assert response.status_code == 403


def test_download_pdf_rejects_token_from_other_submission(client, app):
    app.testing_storage.csv_rows = [
        {
            "submission_id": "first",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "access_token": "first-token",
            "pdf_filename": "first.pdf",
        },
        {
            "submission_id": "second",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "access_token": "second-token",
            "pdf_filename": "second.pdf",
        },
    ]
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/second.pdf"] = b"%PDF-1.4\n"

    response = client.get("/downloads/pdfs/formularz_zgloszeniowy/second.pdf?token=first-token")

    assert response.status_code == 403


def test_download_signed_pdf_requires_valid_token(client, app):
    row = {
        "submission_id": "signed-1",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "access_token": "secret-token",
        "signed_pdf_filename": "formularz_zgloszeniowy-signed-1-signed.pdf",
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs[
        "output/formularz_zgloszeniowy/pdf/formularz_zgloszeniowy-signed-1-signed.pdf"
    ] = b"%PDF-1.4\n"

    ok_response = client.get(
        "/downloads/signed/formularz_zgloszeniowy/formularz_zgloszeniowy-signed-1-signed.pdf?token=secret-token"
    )
    bad_response = client.get(
        "/downloads/signed/formularz_zgloszeniowy/formularz_zgloszeniowy-signed-1-signed.pdf?token=wrong"
    )

    assert ok_response.status_code == 200
    assert bad_response.status_code == 403


def test_upload_signed_pdf_rejects_file_without_pdf_header(client, app):
    app.testing_storage.csv_rows = [
        {
            "submission_id": "signed-1",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "access_token": "secret-token",
            "pdf_filename": "formularz_zgloszeniowy-signed-1.pdf",
        }
    ]

    response = client.post(
        "/upload-signed/formularz_zgloszeniowy/signed-1",
        data={"signed_pdf": (io.BytesIO(b"not a pdf"), "signed.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert "output/formularz_zgloszeniowy/pdf/formularz_zgloszeniowy-signed-1-signed.pdf" not in app.testing_storage.saved_pdfs


def test_download_pdf_without_token_for_new_submission_is_forbidden(client, app):
    row = {
        "submission_id": "new-no-token",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "pdf_filename": "formularz_zgloszeniowy-new-no-token.pdf",
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs[
        "output/formularz_zgloszeniowy/pdf/formularz_zgloszeniowy-new-no-token.pdf"
    ] = b"%PDF-1.4\n"

    response = client.get("/downloads/pdfs/formularz_zgloszeniowy/formularz_zgloszeniowy-new-no-token.pdf")

    assert response.status_code == 403
    assert app.testing_storage.csv_rows[0]["access_token"]


def test_documents_to_sign_get_loads(client):
    response = client.get("/do-podpisania")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "podpisania" in html


def test_documents_to_sign_shows_declaration_and_training_agreements(client, app):
    import json

    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True},
            "agreement": {"enabled": True},
        },
    }
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "declaration_required": "Tak",
        "declaration_generated": "Tak",
        "declaration_filename": "deklaracja.pdf",
        "declaration_signature_valid": "",
        "agreement_required": "Tak",
        "agreement_generated": "Tak",
        "agreement_generated_at": "2026-05-25",
        "selected_trainings": json.dumps([{"id": "excel", "name": "Excel", "price": 1200}]),
        "training_agreements": json.dumps(
            [
                {
                    "id": "excel",
                    "training_name": "Excel",
                    "filename": "excel-umowa.pdf",
                    "signature_valid": False,
                }
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/deklaracja.pdf"] = b"%PDF-1.4\n"
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/excel-umowa.pdf"] = b"%PDF-1.4\n"

    declaration_response = client.post("/do-podpisania", data={"submission_id": "abc", "akceptacja": "Tak"})
    declaration_html = declaration_response.get_data(as_text=True)

    assert declaration_response.status_code == 200
    assert "deklaracja.pdf" in declaration_html
    assert "token=secret-token" in declaration_html

    row["declaration_signature_valid"] = "Tak"
    agreement_response = client.post("/do-podpisania", data={"submission_id": "abc", "akceptacja": "Tak"})
    agreement_html = agreement_response.get_data(as_text=True)

    assert agreement_response.status_code == 200
    assert "excel-umowa.pdf" in agreement_html
    assert "token=secret-token" in agreement_html


def test_documents_to_sign_get_with_submission_id_shows_current_submission(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True},
            "agreement": {"enabled": True},
        },
    }
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "declaration_required": "Tak",
        "declaration_generated": "Tak",
        "declaration_filename": "deklaracja.pdf",
        "agreement_required": "Tak",
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/deklaracja.pdf"] = b"%PDF-1.4\n"

    response = client.get("/do-podpisania?submission_id=abc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "deklaracja.pdf" in html
    assert "abc" in html


def test_documents_to_sign_requires_additional_fields_before_declaration(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "fields": [
            *app.testing_storage.form_definition["fields"],
            {
                "type": "text",
                "name": "post_acceptance_note",
                "label": "Dodatkowa informacja",
                "required": True,
                "stage": "after_officer_acceptance",
            },
        ],
        "documents": {"declaration": {"enabled": True}},
    }
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "declaration_required": "Tak",
        "process_status": "accepted_waiting_for_additional_fields",
    }
    app.testing_storage.csv_rows = [row]

    response = client.get("/do-podpisania?submission_id=abc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="post_acceptance_note"' in html
    assert "Pobierz deklarację" not in html


def test_additional_fields_unlock_declaration_download(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "fields": [
            *app.testing_storage.form_definition["fields"],
            {
                "type": "text",
                "name": "post_acceptance_note",
                "label": "Dodatkowa informacja",
                "required": True,
                "stage": "after_officer_acceptance",
            },
        ],
        "documents": {"declaration": {"enabled": True}},
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "abc",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "email": "jan.kowalski@example.com",
            "access_token": "secret-token",
            "officer_decision": "TAK",
            "declaration_required": "Tak",
            "process_status": "accepted_waiting_for_additional_fields",
        }
    ]

    save_response = client.post(
        "/additional-fields/formularz_zgloszeniowy/abc",
        data={"post_acceptance_note": "Uzupełniono"},
    )
    assert save_response.status_code == 302
    assert app.testing_storage.csv_rows[0]["process_status"] == "additional_fields_completed"
    html = client.get("/do-podpisania?submission_id=abc").get_data(as_text=True)

    assert app.testing_storage.csv_rows[0]["additional_fields_completed"] == "Tak"
    assert "Pobierz deklarację" in html


def test_generate_agreements_uses_today_and_redirects_to_current_submission(client, app, monkeypatch):
    from datetime import date

    captured = {}
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "declaration_signature_valid": "Tak",
        "selected_trainings": '[{"id": "excel", "name": "Excel", "price": 1200}]',
    }
    app.testing_storage.csv_rows = [row]

    def fake_generate_documents_for_collection(*args, **kwargs):
        captured["context_extra"] = kwargs["context_extra"]
        return []

    monkeypatch.setattr(
        app.extensions["document_service"],
        "generate_documents_for_collection",
        fake_generate_documents_for_collection,
    )

    response = client.post(
        "/agreements/formularz_zgloszeniowy/abc/generate",
        data={"agreement_generated_at": "2000-01-01"},
    )

    assert response.status_code == 302
    assert response.location.endswith("/do-podpisania?submission_id=abc")
    assert captured["context_extra"]["generated_date"] == date.today().isoformat()


def test_upload_participant_signed_training_agreement_notifies_with_default_nextcloud_config(client, app, monkeypatch):
    import json

    notified = []
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "agreement_required": "Tak",
        "agreement_generated": "Tak",
        "agreement_signature_valid": "",
        "training_agreements": json.dumps(
            [
                {
                    "id": "excel",
                    "training_name": "Excel",
                    "filename": "excel-umowa.pdf",
                    "signature_valid": False,
                }
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]
    app.config["FORM_NOTIFICATION_EMAILS"] = ["koordynator@example.com"]

    def fake_upload_signed_document(submission, document_id, uploaded_file, instance_id=None):
        agreements = json.loads(row["training_agreements"])
        agreements[0].update(
            {
                "signed": True,
                "signature_valid": True,
                "signed_filename": "excel-umowa-signed.pdf",
                "signature_type": "mszafir",
            }
        )
        app.extensions["submission_repository"].update(
            "abc",
            {
                "training_agreements": json.dumps(agreements),
                "agreement_signed": "Tak",
                "agreement_signature_valid": "Tak",
                "agreement_signed_filename": "excel-umowa-signed.pdf",
                "process_status": "AGREEMENT_SIGNED",
            },
        )
        return {
            "is_signed": True,
            "is_valid": True,
            "source_filename": "excel-umowa.pdf",
            "signed_filename": "excel-umowa-signed.pdf",
            "verification": {"signature_type": "mszafir"},
        }

    def fake_notify_event_once(event_type, submission, form_config, **kwargs):
        notified.append(
            {
                "event_type": event_type,
                "submission": submission,
                "form_config": form_config,
                "kwargs": kwargs,
            }
        )
        return [{"event": event_type, "to": ["koordynator@example.com"]}]

    monkeypatch.setattr(app.extensions["document_service"], "upload_signed_document", fake_upload_signed_document)
    monkeypatch.setattr(app.extensions["notification_service"], "notify_event_once", fake_notify_event_once)

    response = client.post(
        "/agreements/formularz_zgloszeniowy/abc/excel/upload",
        data={"signed_agreement_pdf": (io.BytesIO(b"%PDF-1.4"), "signed.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.location.endswith("/do-podpisania?submission_id=abc")
    assert notified
    assert notified[0]["event_type"] == "AGREEMENT_SIGNED"
    assert notified[0]["form_config"]["notifications"][0]["to"] == ["form_notifications"]
    assert notified[0]["form_config"]["notifications"][0]["template"] == "Template/Mail/agreement_signed.html"
    assert notified[0]["kwargs"]["sent_field"] == "agreement_success_email_sent"
    assert notified[0]["kwargs"]["idempotency_key"] == "all"
    assert notified[0]["kwargs"]["context_extra"]["signed_filename"] == "excel-umowa-signed.pdf"
    assert notified[0]["kwargs"]["context_extra"]["signed_by"] == "participant"


def test_acceptance_status_missing_submission(client):
    response = client.get("/api/submissions/brak-id/acceptance-status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["exists"] is False
    assert payload["can_sign_documents"] is False


def test_acceptance_status_refresh_does_not_send_decision_email(client, app):
    sent = []
    row = {
        "submission_id": "abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan.kowalski@example.com",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "process_status": "OFFICER_ACCEPTED",
        "decision_email_sent": "",
        "decision_email_sent_for": "",
    }
    app.testing_storage.csv_rows = [row]
    service = app.extensions["notification_service"]
    original_sender = service.smtp_sender

    def fake_sender(**kwargs):
        sent.append(kwargs)

    service.smtp_sender = fake_sender
    try:
        first_response = client.get("/api/submissions/abc/acceptance-status")
        second_response = client.get("/api/submissions/abc/acceptance-status")
    finally:
        service.smtp_sender = original_sender

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    payload = first_response.get_json()
    assert payload["normalized_process_status"] == "REVIEW_ACCEPTED"
    assert payload["process_status_label"] == "Wniosek zaakceptowany"
    assert payload["is_rejected"] is False
    assert sent == []
    assert app.testing_storage.csv_rows[0]["decision_email_sent"] == ""
    assert app.testing_storage.csv_rows[0]["decision_email_sent_for"] == ""
