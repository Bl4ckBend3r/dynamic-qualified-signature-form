import json
import logging

from flask import Flask
from jinja2 import UndefinedError
import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from services.observability import (
    ContextFilter,
    JsonLogFormatter,
    configure_logging,
    redact_log_text,
    register_observability,
    safe_identifier_hash,
    trace_declaration_generation,
)
from test_admin_panel import admin_app, create_form, create_user, login


def _app(*, log_format="text", metrics_token=""):
    app = Flask(__name__)
    app.config.update(
        TESTING=False,
        LOG_FORMAT=log_format,
        LOG_LEVEL="INFO",
        ENV="test",
        SERVICE_NAME="observability-test",
        METRICS_ENABLED=True,
        METRICS_TOKEN=metrics_token,
        TRUSTED_PROXY_HOPS=0,
        PROXY_FIX=False,
    )
    configure_logging(app)
    register_observability(app)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/failure")
    def failure():
        raise RuntimeError("controlled failure")

    @app.get("/sensitive")
    def sensitive():
        app.logger.info("pesel=44051401458 token=secret-value")
        return {"ok": True}

    return app


def test_request_id_is_returned_and_added_to_completion_log(caplog):
    app = _app()
    with caplog.at_level(logging.INFO):
        response = app.test_client().get("/ok")

    request_id = response.headers["X-Request-ID"]
    assert request_id
    completed = next(record for record in caplog.records if record.getMessage() == "http_request_completed")
    assert completed.request_id == request_id
    assert completed.correlation_id == request_id
    assert completed.endpoint.endswith(".ok") or completed.endpoint == "ok"


def test_unhandled_error_is_correlated_without_stack_trace_in_response(caplog):
    app = _app()
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/failure")

    assert response.status_code == 500
    assert response.headers["X-Request-ID"].encode() in response.data
    assert b"Traceback" not in response.data
    failed = next(record for record in caplog.records if record.getMessage() == "http_request_failed")
    assert failed.request_id == response.headers["X-Request-ID"]
    assert failed.exception_type == "RuntimeError"


def test_json_formatter_has_required_fields_and_redacts_sensitive_values():
    record = logging.LogRecord(
        name="application.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="operation_failed pesel=44051401458 token=secret-value",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-1"
    record.correlation_id = "req-1"
    record.method = "GET"
    record.endpoint = "public.status"
    record.status_code = 500
    record.duration_ms = 12.5
    ContextFilter(environment="production", service_name="forms").filter(record)

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "ERROR"
    assert payload["logger"] == "application.test"
    assert payload["request_id"] == "req-1"
    assert payload["environment"] == "production"
    assert payload["service"] == "forms"
    serialized = json.dumps(payload)
    assert "44051401458" not in serialized
    assert "secret-value" not in serialized


def test_query_token_and_pesel_are_not_emitted_by_structured_formatter(caplog):
    app = _app(log_format="json")
    with caplog.at_level(logging.INFO):
        response = app.test_client().get("/sensitive?participant_token=query-secret")
    assert response.status_code == 200
    assert all("query-secret" not in redact_log_text(record.getMessage()) for record in caplog.records)
    assert "44051401458" not in redact_log_text("pesel=44051401458")


def test_metrics_are_local_by_default_and_have_low_cardinality_labels():
    app = _app()
    client = app.test_client()
    client.get("/ok?participant_token=never-a-label")

    forbidden = client.get("/metrics", environ_base={"REMOTE_ADDR": "203.0.113.10"})
    metrics = client.get("/metrics", environ_base={"REMOTE_ADDR": "127.0.0.1"})

    assert forbidden.status_code == 403
    assert metrics.status_code == 200
    body = metrics.get_data(as_text=True)
    assert "app_http_requests_total" in body
    assert 'endpoint="ok"' in body
    assert "never-a-label" not in body


def test_metrics_token_allows_remote_scrape():
    app = _app(metrics_token="monitoring-secret")
    response = app.test_client().get(
        "/metrics",
        headers={"Authorization": "Bearer monitoring-secret"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert response.status_code == 200


def test_declaration_trace_records_start_completion_and_safe_browser():
    app = _app(log_format="text")

    @app.get("/declaration-test")
    def declaration_test():
        with trace_declaration_generation("private-submission", "review", "ready") as trace:
            trace.stage = "pdf_conversion"
        return {"ok": True}

    response = app.test_client().get(
        "/declaration-test?token=query-secret",
        headers={"User-Agent": "Mozilla/5.0 Firefox/132.0 user@example.org token=agent-secret"},
    )
    assert response.status_code == 200
    records = [entry for entry in app.extensions["runtime_log_buffer"].snapshot(event_name="declaration_generation_")
               if json.loads(entry["rendered"]).get("submission_ref") == safe_identifier_hash("private-submission")]
    assert [entry["event"] for entry in reversed(records)] == [
        "declaration_generation_started", "declaration_generation_completed",
    ]
    started = json.loads(records[-1]["rendered"])
    completed = json.loads(records[0]["rendered"])
    assert started["request_id"] == response.headers["X-Request-ID"]
    assert started["method"] == "GET" and started["endpoint"].endswith("declaration_test")
    assert started["browser"] == "Firefox 132.0"
    assert started["document_type"] == "declaration"
    assert started["workflow_step"] == "review" and started["document_status"] == "ready"
    assert len(started["submission_ref"]) == 16 and started["submission_ref"] != "private-submission"
    assert completed["stage"] == "completed" and completed["duration_ms"] >= 0
    rendered = "".join(entry["rendered"] for entry in records)
    for secret in ("private-submission", "query-secret", "user@example.org", "agent-secret"):
        assert secret not in rendered


def test_declaration_trace_classifies_failure_without_exception_message():
    app = _app()
    with app.test_request_context("/declaration-test?token=query-secret",
                                  headers={"User-Agent": "Mozilla/5.0 Chrome/131.0"}):
        try:
            with trace_declaration_generation("private-submission", "paper") as trace:
                trace.stage = "pdf_conversion"
                raise RuntimeError("pesel=44051401458 token=secret-value")
        except RuntimeError:
            pass
    failed = next(entry for entry in app.extensions["runtime_log_buffer"].snapshot(
        event_name="declaration_generation_failed") if
        json.loads(entry["rendered"]).get("submission_ref") == safe_identifier_hash("private-submission") and
        entry["stage"] == "pdf_conversion")
    payload = json.loads(failed["rendered"])
    assert payload["browser"] == "Chrome 131.0"
    assert payload["stage"] == "pdf_conversion"
    assert payload["reason_code"] == "PDF_CONVERSION_FAILED"
    assert payload["exception_type"] == "RuntimeError"
    assert "44051401458" not in failed["rendered"]
    assert "secret-value" not in failed["rendered"]


@pytest.mark.parametrize("stage,error,reason,event", [
    ("validation", ValueError("private"), "DOCUMENT_VALIDATION_FAILED", "declaration_generation_validation_failed"),
    ("claim", ValueError("private"), "DOCUMENT_OPERATION_CLAIM_REJECTED", "declaration_generation_blocked"),
    ("template_load", FileNotFoundError("private"), "TEMPLATE_FILE_READ_FAILED", "declaration_generation_failed"),
    ("template_load", RequestsConnectionError("private"), "NEXTCLOUD_UNAVAILABLE", "declaration_generation_failed"),
    ("pdf_conversion", UndefinedError("private"), "TEMPLATE_SUBSTITUTION_FAILED", "declaration_generation_failed"),
    ("storage_write", OSError("private"), "DOCUMENT_STORAGE_WRITE_FAILED", "declaration_generation_failed"),
    ("workflow_update", RuntimeError("private"), "WORKFLOW_STATUS_UPDATE_FAILED", "declaration_generation_failed"),
])
def test_declaration_trace_distinguishes_failure_stage(stage, error, reason, event):
    app = _app()
    with app.app_context():
        with pytest.raises(type(error)):
            with trace_declaration_generation("private-submission") as trace:
                trace.stage = stage
                raise error
    entries = app.extensions["runtime_log_buffer"].snapshot(event_name=event)
    assert any(entry["reason_code"] == reason and entry["stage"] == stage for entry in entries)


def test_declaration_diagnostics_are_visible_only_to_superadmin(admin_app):
    create_user(admin_app)
    from models import FormSubmission, FormVersion
    form_id = create_form(admin_app, slug="diagnostic-version")
    with admin_app.extensions["services"].submission_repository.session_factory.begin() as db:
        version = FormVersion(form_id=form_id, version_major=1, version_minor=0,
            version_label="1.0", status="published", definition_json={"fields": []})
        db.add(version)
        db.flush()
        db.add(FormSubmission(submission_id="outdated-diagnostic-case", form_slug="diagnostic-version",
            form_version_id=None, data_json={"_form_version_upgrade": {"missing_fields": ["city"]}},
            document_states={"version_revisions": {"declaration": {"status": "pending"}}}))
    with admin_app.test_request_context("/document-steps/test", headers={"User-Agent": "Firefox/132.0"}):
        try:
            with trace_declaration_generation("diagnostic-submission", "paper") as trace:
                trace.stage = "template_load"
                raise FileNotFoundError("token=private")
        except FileNotFoundError:
            pass
    client = admin_app.test_client()
    login(client)
    page = client.get("/admin/diagnostics")
    assert page.status_code == 200
    assert "Generowanie dokumentów" in page.text
    assert "Stan schematu bazy danych" in page.text
    assert "Synchronizacja wniosków i dokumentów" in page.text
    assert safe_identifier_hash("outdated-diagnostic-case") in page.text
    assert "brakujące dane" in page.text and "declaration: pending" in page.text
    assert "TEMPLATE_FILE_READ_FAILED" in page.text
    assert "sqlite" in page.text
    assert "secret" not in page.text
    logs = client.get("/admin/runtime-logs?event=declaration_generation_")
    assert logs.status_code == 200
    assert "declaration_generation_failed" in logs.text
    assert "TEMPLATE_FILE_READ_FAILED" in logs.text
    assert "diagnostic-submission" not in logs.text
    create_user(admin_app, email="ordinary@example.com", role="admin")
    ordinary = admin_app.test_client()
    login(ordinary, email="ordinary@example.com")
    assert ordinary.get("/admin/diagnostics").status_code == 403
    assert ordinary.get("/admin/runtime-logs").status_code == 403
