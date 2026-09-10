from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass(frozen=True)
class EndpointCase:
    name: str
    method: str
    path: str
    data: dict = field(default_factory=dict)


CASES = (
    EndpointCase("documents GET", "GET", "/do-podpisania?submission_id=case-b&token={token}"),
    EndpointCase(
        "documents POST",
        "POST",
        "/do-podpisania",
        {"submission_id": "case-b", "akceptacja": "Tak", "access_token": "{token}"},
    ),
    EndpointCase("result", "GET", "/result/formularz_zgloszeniowy/case-b?token={token}"),
    EndpointCase("declaration GET", "GET", "/declaration/formularz_zgloszeniowy/case-b?token={token}"),
    EndpointCase(
        "declaration POST",
        "POST",
        "/declaration/formularz_zgloszeniowy/case-b",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "signed declaration upload",
        "POST",
        "/upload-declaration-signed/formularz_zgloszeniowy/case-b",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "additional fields",
        "POST",
        "/additional-fields/formularz_zgloszeniowy/case-b",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "agreement generate",
        "POST",
        "/agreements/formularz_zgloszeniowy/case-b/generate",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "training agreement upload",
        "POST",
        "/agreements/formularz_zgloszeniowy/case-b/excel/upload",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "training agreements batch upload",
        "POST",
        "/agreements/formularz_zgloszeniowy/case-b/upload-all",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "legacy agreement upload",
        "POST",
        "/agreement/formularz_zgloszeniowy/case-b/upload",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "signed submission upload",
        "POST",
        "/upload-signed/formularz_zgloszeniowy/case-b",
        {"access_token": "{token}"},
    ),
    EndpointCase(
        "PDF download",
        "GET",
        "/downloads/pdfs/formularz_zgloszeniowy/case-b.pdf?token={token}",
    ),
    EndpointCase(
        "signed PDF download",
        "GET",
        "/downloads/signed/formularz_zgloszeniowy/case-b-signed.pdf?token={token}",
    ),
    EndpointCase(
        "acceptance API",
        "GET",
        "/api/submissions/case-b/acceptance-status?token={token}",
    ),
    EndpointCase(
        "workflow API",
        "GET",
        "/api/submissions/case-b/workflow-status?token={token}",
    ),
)


def _request(client, case: EndpointCase, token: str):
    path = case.path.format(token=token)
    data = {
        key: (value.format(token=token) if isinstance(value, str) else value)
        for key, value in case.data.items()
    }
    return client.open(path, method=case.method, data=data)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_participant_endpoint_access_matrix(client, app, case):
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True, "template_html": "<p>Deklaracja</p>"},
            "agreement": {"enabled": True, "template_html": "<p>Umowa</p>"},
        },
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "case-a",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Sprawa A",
            "access_token": "token-a",
        },
        {
            "submission_id": "case-b",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "Sprawa B",
            "access_token": "SECRET_PARTICIPANT_TOKEN_DO_NOT_RENDER",
            "officer_decision": "TAK",
            "acceptance_required": "TAK",
            "process_status": "OFFICER_ACCEPTED",
            "pdf_filename": "case-b.pdf",
            "signed_pdf_filename": "case-b-signed.pdf",
            "declaration_generated": "Tak",
            "declaration_filename": "declaration-b.pdf",
            "declaration_signature_valid": "Tak",
            "training_agreements": "[]",
        },
    ]
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/case-b.pdf"] = b"%PDF-1.4\n"
    app.testing_storage.saved_pdfs["output/formularz_zgloszeniowy/pdf/case-b-signed.pdf"] = b"%PDF-1.4\n"

    missing = _request(client, case, "")
    wrong = _request(client, case, "wrong-token")
    cross_submission = _request(client, case, "token-a")
    allowed = _request(client, case, "SECRET_PARTICIPANT_TOKEN_DO_NOT_RENDER")

    assert missing.status_code == wrong.status_code == cross_submission.status_code == 404
    assert allowed.status_code != 404
    if case.name == "result":
        assert b"SECRET_PARTICIPANT_TOKEN_DO_NOT_RENDER" not in allowed.data
        assert b"Przejd" not in allowed.data


def test_participant_routes_use_central_fail_closed_authorization():
    documents_source = Path("routes/documents.py").read_text(encoding="utf-8")
    public_forms_source = Path("routes/public_forms.py").read_text(encoding="utf-8")
    api_source = Path("routes/api.py").read_text(encoding="utf-8")

    assert "require_participant_submission_access" in documents_source
    assert "require_participant_submission_access" in public_forms_source
    assert "resolve_participant_submission_access" in api_source
    assert "verify_token(" not in documents_source
    assert "verify_token(" not in public_forms_source
    assert "verify_token(" not in api_source
    assert "_provided_access_token" not in api_source


@pytest.mark.parametrize(
    "case",
    tuple(case for case in CASES if case.method == "POST"),
    ids=lambda case: case.name,
)
def test_participant_posts_require_csrf_separately_from_access_token(client, app, case):
    app.config["WTF_CSRF_ENABLED"] = True
    app.testing_storage.form_definition = {
        **app.testing_storage.form_definition,
        "documents": {
            "declaration": {"enabled": True, "template_html": "<p>Deklaracja</p>"},
            "agreement": {"enabled": True, "template_html": "<p>Umowa</p>"},
        },
    }
    app.testing_storage.csv_rows = [
        {
            "submission_id": "case-b",
            "form_slug": "formularz_zgloszeniowy",
            "form_name": "CSRF",
            "access_token": "csrf-participant-token",
            "officer_decision": "TAK",
            "acceptance_required": "TAK",
            "declaration_generated": "Tak",
            "declaration_signature_valid": "Tak",
            "training_agreements": "[]",
        }
    ]

    response = _request(client, case, "csrf-participant-token")

    assert response.status_code == 400
