import json
import logging

from flask import Flask

from services.observability import (
    ContextFilter,
    JsonLogFormatter,
    configure_logging,
    redact_log_text,
    register_observability,
)


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
