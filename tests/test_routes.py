import io
import json
from pathlib import Path

from flask import url_for


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


def test_form_page_pesel_autofill_locks_dependent_fields(client):
    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "lockAutoField" in html
    assert 'mirror.type = "hidden"' in html
    assert 'el.addEventListener("blur", updateFromPesel)' in html


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


def test_public_form_renders_admin_logo_under_title_without_static_header(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "logo_url": "/assets/logos/1/admin-logo.png",
        "logo_alignment": "left",
        "header_image": "Logo/static-big-logo.png",
    }

    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("<h2>Formularz") < html.index('src="/assets/logos/1/admin-logo.png"')
    assert "form-logo-row form-logo-row--left" in html
    assert "form-header-image" not in html
    assert "Logo/static-big-logo.png" not in html


def test_public_form_without_logo_does_not_render_logo_container(client, app):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "logo_url": "",
    }

    response = client.get("/form/formularz_zgloszeniowy")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "form-logo-row" not in html


def test_public_form_logo_alignment_classes(client, app):
    for alignment in ["left", "center", "right"]:
        app.testing_storage.form_definition = {
            **app.testing_storage.form_definition,
            "logo_url": f"/assets/logos/1/{alignment}.png",
            "logo_alignment": alignment,
        }

        html = client.get("/form/formularz_zgloszeniowy").get_data(as_text=True)

        assert f"form-logo-row form-logo-row--{alignment}" in html


def test_public_form_logo_css_keeps_fixed_height_and_alignment_rules():
    stylesheet = Path("static/css/style.css").read_text(encoding="utf-8")
    logo_block = stylesheet.split(".form-logo {", 1)[1].split("}", 1)[0]

    assert "height: 84px;" in logo_block
    assert "width: auto;" in logo_block
    assert "object-fit: contain;" in logo_block
    assert "justify-content: flex-start;" in stylesheet
    assert "justify-content: center;" in stylesheet
    assert "justify-content: flex-end;" in stylesheet


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


def test_submit_overwrites_tampered_pesel_derived_values(client, app, valid_form_data):
    tampered = {
        **valid_form_data,
        "pesel": "90010112356",
        "data_urodzenia": "2001-02-03",
        "plec": "Kobieta",
        "wiek": "7",
    }

    response = client.post("/submit/formularz_zgloszeniowy", data=tampered)

    assert response.status_code == 200
    row = app.testing_storage.csv_rows[0]
    assert row["data_urodzenia"] == "1990-01-01"
    assert row["plec"] == "Mężczyzna"
    assert row["wiek"] == "36"
    assert row["data_json"]["data_urodzenia"] == "1990-01-01"
    assert row["data_json"]["plec"] == "Mężczyzna"
    assert row["data_json"]["wiek"] == "36"


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


def test_document_endpoint_names_stay_registered(app):
    from routes.documents import bp as documents_bp

    assert documents_bp.name == "documents"
    with app.test_request_context():
        assert url_for("documents.documents_to_sign") == "/do-podpisania"
        assert url_for("documents.declaration_form", slug="sample", submission_id="abc") == "/declaration/sample/abc"
        assert url_for("documents.save_additional_fields", slug="sample", submission_id="abc") == "/additional-fields/sample/abc"
        assert url_for("documents.generate_training_agreements", slug="sample", submission_id="abc") == "/agreements/sample/abc/generate"
        assert url_for("documents.upload_signed_declaration", slug="sample", submission_id="abc") == "/upload-declaration-signed/sample/abc"
        assert url_for("documents.upload_signed_pdf", slug="sample", submission_id="abc") == "/upload-signed/sample/abc"
        assert (
            url_for("documents.upload_signed_training_agreement", slug="sample", submission_id="abc", agreement_id="excel")
            == "/agreements/sample/abc/excel/upload"
        )
        assert url_for("documents.upload_signed_training_agreements", slug="sample", submission_id="abc") == "/agreements/sample/abc/upload-all"
        assert url_for("documents.download_pdf", slug="sample", filename="file.pdf") == "/downloads/pdfs/sample/file.pdf"
        assert url_for("documents.download_signed_pdf", slug="sample", filename="file.pdf") == "/downloads/signed/sample/file.pdf"


def test_documents_to_sign_shows_declaration_and_training_agreements(client, app):
    import json

    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True},
            "agreement": {"enabled": True, "template_html": "<main class=\"document\">Umowa</main>"},
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
                    "participant_status": "agreement_waiting_for_beneficiary_signature",
                    "agreement_downloaded": True,
                },
                {
                    "id": "english",
                    "training_name": "English",
                    "filename": "english-umowa.pdf",
                    "signature_valid": False,
                    "participant_status": "agreement_waiting_for_beneficiary_signature",
                    "agreement_downloaded": True,
                },
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/deklaracja.pdf"] = b"%PDF-1.4\n"
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/excel-umowa.pdf"] = b"%PDF-1.4\n"
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/english-umowa.pdf"] = b"%PDF-1.4\n"

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
    assert "english-umowa.pdf" in agreement_html
    assert "token=secret-token" in agreement_html
    assert agreement_html.count("data-bulk-agreement-upload") == 1
    assert "data-bulk-agreement-files" in agreement_html
    assert "signed_agreement_pdf_1" not in agreement_html
    assert "signed_agreement_pdf_2" not in agreement_html


def test_all_training_agreement_downloads_return_pdf(client, app, monkeypatch):
    import json
    import routes.documents as document_routes

    filenames = ["Jan_Kowalski-excel-umowa.pdf", "Jan_Kowalski-english-umowa.pdf"]
    marked_downloads = []
    monkeypatch.setattr(
        document_routes,
        "_mark_training_agreement_downloaded",
        lambda submission_id, agreement_key, filename: marked_downloads.append((submission_id, agreement_key, filename)) or True,
    )
    app.testing_storage.csv_rows = [
        {
            "submission_id": "training-download",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz",
            "access_token": "training-token",
            "agreement_filename": filenames[0],
            "training_agreements": json.dumps(
                [
                    {"id": "excel", "filename": filenames[0]},
                    {"id": "english", "filename": filenames[1]},
                ]
            ),
        }
    ]
    for filename in filenames:
        app.testing_storage.saved_pdfs[f"output/formularz_zgloszeniowy/pdf/{filename}"] = b"%PDF-1.4\ntraining"

    for filename in filenames:
        response = client.get(
            f"/downloads/pdfs/formularz_zgloszeniowy/{filename}?token=training-token"
        )
        assert response.status_code == 200
        assert response.data.startswith(b"%PDF-1.4")
    assert [item[2] for item in marked_downloads] == filenames


def test_documents_to_sign_does_not_render_dead_training_agreement_link(client, app):
    import json

    filename = "missing-training-agreement.pdf"
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True, "template_html": "<p>Deklaracja</p>"},
            "agreement": {"enabled": True, "template_html": "<p>Umowa</p>"},
        },
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "missing-agreement",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz",
            "access_token": "secret-token",
            "officer_decision": "TAK",
            "declaration_signature_valid": "Tak",
            "agreement_generated": "Tak",
            "agreement_filename": filename,
            "selected_trainings": json.dumps([{"id": "excel", "name": "Excel", "price": "1200.00"}]),
            "training_agreements": json.dumps([{"id": "excel", "training_name": "Excel", "filename": filename}]),
        }
    ]

    response = client.get("/do-podpisania?submission_id=missing-agreement")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f'href="/downloads/pdfs/formularz_zgloszeniowy/{filename}' not in html


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
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "agreement": {
                "enabled": True,
                "template_html": "<main class=\"document\">Umowa</main>",
            }
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
    assert notified == []


def test_upload_all_training_agreements_matches_filenames_and_supports_partial_success(client, app, monkeypatch):
    row = {
        "submission_id": "batch-abc",
        "form_slug": "formularz_zgloszeniowy",
        "form_name": "Formularz zgłoszeniowy",
        "email": "jan@example.com",
        "access_token": "secret-token",
        "officer_decision": "TAK",
        "acceptance_required": "TAK",
        "agreement_generated": "Tak",
        "training_agreements": json.dumps(
            [
                {"id": "excel", "training_name": "Excel", "number": "1/2026", "filename": "excel-umowa.pdf"},
                {"id": "kadry", "training_name": "Kadry", "number": "2/2026", "filename": "kadry-umowa.pdf"},
            ]
        ),
    }
    app.testing_storage.csv_rows = [row]
    calls = []

    def fake_upload(*, submission, document_id, uploaded_file, instance_id=None):
        calls.append((instance_id, uploaded_file.filename))
        if instance_id == "kadry":
            raise ValueError("Niepoprawny podpis")
        return {"is_signed": True, "is_valid": True}

    monkeypatch.setattr(app.extensions["services"].document_signing_service, "upload_signed_document", fake_upload)
    response = client.post(
        "/agreements/formularz_zgloszeniowy/batch-abc/upload-all",
        data={
            "access_token": "secret-token",
            "signed_agreement_files": [
                (io.BytesIO(b"%PDF-1.4 kadry"), "kadry-umowa_signed.pdf"),
                (io.BytesIO(b"%PDF-1.4 excel"), "excel-umowa.pdf"),
            ],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert calls == [("kadry", "kadry-umowa_signed.pdf"), ("excel", "excel-umowa.pdf")]
    assert payload["uploaded"] == 1
    assert payload["failed"] == 1
    assert [item["status"] for item in payload["results"]] == ["rejected", "uploaded"]
    assert payload["pending_agreements"] == [{"filename": "kadry-umowa.pdf", "id": "kadry", "training_name": "Kadry"}]


def test_upload_all_training_agreements_reports_unmatched_and_existing_signed_file(client, app, monkeypatch):
    row = {
        "submission_id": "batch-existing",
        "form_slug": "formularz_zgloszeniowy",
        "access_token": "token-existing",
        "training_agreements": json.dumps([
            {"id": "excel", "filename": "excel-umowa.pdf", "signed_filename": "excel-umowa-signed.pdf", "signature_valid": True},
        ]),
    }
    app.testing_storage.csv_rows = [row]
    calls = []
    monkeypatch.setattr(
        app.extensions["services"].document_signing_service,
        "upload_signed_document",
        lambda **kwargs: calls.append(kwargs),
    )

    response = client.post(
        "/agreements/formularz_zgloszeniowy/batch-existing/upload-all",
        data={
            "access_token": "token-existing",
            "signed_agreement_files": [
                (io.BytesIO(b"%PDF-1.4 unknown"), "unknown.pdf"),
                (io.BytesIO(b"%PDF-1.4 excel"), "excel-umowa-podpisana.pdf"),
            ],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert calls == []
    assert [item["status"] for item in payload["results"]] == ["unmatched", "rejected"]
    assert payload["results"][1]["message"] == "Podpisany plik dla tej umowy został już wgrany."


def test_upload_all_training_agreements_accepts_one_matching_file(client, app, monkeypatch):
    app.testing_storage.csv_rows = [{
        "submission_id": "batch-single",
        "form_slug": "formularz_zgloszeniowy",
        "access_token": "single-token",
        "officer_decision": "TAK",
        "declaration_signature_valid": "Tak",
        "training_agreements": json.dumps([{
            "id": "excel",
            "training_name": "Excel",
            "filename": "excel-umowa.pdf",
            "participant_status": "agreement_waiting_for_beneficiary_signature",
            "agreement_downloaded": True,
        }]),
    }]
    calls = []

    def fake_upload(**kwargs):
        calls.append((kwargs["instance_id"], kwargs["uploaded_file"].filename))
        return {"is_signed": True, "is_valid": True}

    monkeypatch.setattr(app.extensions["services"].document_signing_service, "upload_signed_document", fake_upload)
    response = client.post(
        "/agreements/formularz_zgloszeniowy/batch-single/upload-all",
        data={
            "access_token": "single-token",
            "signed_agreement_files": [(io.BytesIO(b"%PDF-1.4 excel"), "excel-umowa-podpisana.pdf")],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert calls == [("excel", "excel-umowa-podpisana.pdf")]
    assert payload["uploaded"] == 1
    assert payload["failed"] == 0
    assert payload["pending_agreements"] == []


def test_upload_all_training_agreements_requires_public_access_token(client, app):
    app.testing_storage.csv_rows = [{
        "submission_id": "batch-token",
        "form_slug": "formularz_zgloszeniowy",
        "access_token": "required-token",
        "training_agreements": "[]",
    }]

    response = client.post(
        "/agreements/formularz_zgloszeniowy/batch-token/upload-all",
        data={"signed_agreement_files": [(io.BytesIO(b"%PDF-1.4"), "umowa.pdf")]},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 403


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


def test_acceptance_status_returns_form_instruction_steps_and_next_action(client, app):
    instruction = "Pobierz deklarację.\nPodpisz ją elektronicznie."
    app.testing_storage.form_definition["user_instruction"] = instruction
    app.testing_storage.form_definition["user_instruction_config"] = {
        "title": "Instrukcja dla tego formularza",
        "description": instruction,
        "stages": [
            {
                "key": "sent",
                "label": "Wysłano własny formularz",
                "status_codes": ["FORM_SUBMITTED"],
                "description": "Etap zakończony.",
                "next_action": "Czekaj.",
            },
            {
                "key": "declaration",
                "label": "Własna deklaracja",
                "status_codes": ["OFFICER_ACCEPTED", "REVIEW_ACCEPTED"],
                "description": "Opis aktualnego etapu.",
                "next_action": "Wykonaj czynność skonfigurowaną przez urzędnika.",
            },
        ],
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "instruction-1",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "officer_decision": "TAK",
            "process_status": "OFFICER_ACCEPTED",
            "user_instruction": "Ta wartość ze zgłoszenia ma być ignorowana.",
        },
        {
            "submission_id": "instruction-empty",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "officer_decision": "TAK",
            "process_status": "OFFICER_ACCEPTED",
            "user_instruction": "Instrukcja pojedynczego zgłoszenia",
        },
    ]

    with_instruction = client.get(
        "/api/submissions/instruction-1/acceptance-status",
        headers={"X-Forwarded-Prefix": "/aplikacja"},
    ).get_json()
    assert with_instruction["form_instruction"] == instruction
    assert with_instruction["has_form_instruction"] is True
    assert with_instruction["current_step"] == "declaration"
    assert with_instruction["current_step_label"] == "Własna deklaracja"
    assert with_instruction["next_action"] == "Wykonaj czynność skonfigurowaną przez urzędnika."
    assert len(with_instruction["instruction_version"]) == 64
    current = [step for step in with_instruction["instruction_steps"] if step["current"]]
    assert len(current) == 1
    assert current[0]["key"] == "declaration"
    assert current[0]["description"] == "Opis aktualnego etapu."
    assert with_instruction["instruction"]["title"] == "Instrukcja dla tego formularza"
    assert with_instruction["instruction"]["current_stage_key"] == "declaration"

    app.testing_storage.csv_rows[1]["process_status"] = "DECLARATION_WAITING_FOR_SIGNATURE"
    changed_status = client.get("/api/submissions/instruction-empty/acceptance-status").get_json()
    assert changed_status["instruction_version"] != with_instruction["instruction_version"]

    app.testing_storage.form_definition["user_instruction"] = ""
    app.testing_storage.form_definition["user_instruction_config"] = {}
    without_instruction = client.get("/api/submissions/instruction-empty/acceptance-status").get_json()
    assert without_instruction["form_instruction"] is None
    assert without_instruction["has_form_instruction"] is False
    assert without_instruction["next_action"] == ""
    assert without_instruction["instruction"]["has_instruction"] is False
    assert "user_instruction" not in without_instruction


def test_blocked_agreement_status_has_no_positive_signing_message_or_actions(client, app):
    app.testing_storage.form_definition["user_instruction"] = "Możesz przejść do podpisywania dokumentów."
    app.testing_storage.form_definition["user_instruction_config"] = {
        "title": "Stara instrukcja",
        "description": "Możesz przejść do podpisywania dokumentów.",
        "stages": [
            {
                "key": "agreement",
                "label": "Umowa",
                "status_codes": ["AGREEMENT_READY"],
                "next_action": "Podpisz umowę.",
            }
        ],
    }
    app.testing_storage.form_definition["documents"] = {
        "declaration": {"enabled": True, "template_html": "<p>Deklaracja</p>"},
        "agreement": {"enabled": True, "template_html": "<p>Umowa</p>"},
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "blocked-agreement",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Formularz zgłoszeniowy",
            "officer_decision": "TAK",
            "acceptance_required": "TAK",
            "process_status": "AGREEMENT_READY",
            "declaration_required": "Tak",
            "declaration_signature_valid": "Tak",
            "agreement_required": "Tak",
            "agreement_blocked": "Tak",
            "agreement_block_reason": "Warunki nie zostały spełnione na podstawie deklaracji uczestnika.",
            "agreement_generated": "Tak",
            "agreement_filename": "umowa.pdf",
        }
    ]

    payload = client.get("/api/submissions/blocked-agreement/acceptance-status").get_json()

    assert payload["process_status"] == "AGREEMENT_BLOCKED"
    assert payload["status_title"] == "Umowa nie może zostać wygenerowana"
    assert payload["can_sign_documents"] is False
    assert payload["can_view_status_details"] is True
    assert payload["can_download_agreement"] is False
    assert payload["can_upload_signed_agreement"] is False
    assert payload["blocking_reason"] == "Warunki nie zostały spełnione na podstawie deklaracji uczestnika."
    assert payload["next_action"].startswith("Na tym etapie nie możesz")
    assert payload["instruction"]["next_action"] == payload["next_action"]
    assert "Możesz przejść do podpisywania dokumentów" not in payload["message"]
    assert sum(
        "Etap zakończony poprawnie" in item["text"]
        for item in payload["status_messages"]
    ) == 1

    response = client.get("/do-podpisania?submission_id=blocked-agreement")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Umowa nie może zostać wygenerowana" in html
    assert "Warunki nie zostały spełnione na podstawie deklaracji uczestnika." in html
    assert html.count("Etap zakończony poprawnie") == 1
    assert "Pobierz umowę PDF" not in html
    assert "Wyślij podpisaną umowę" not in html
    assert "Wygeneruj umowę" not in html
