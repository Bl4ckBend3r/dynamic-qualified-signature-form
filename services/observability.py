from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from flask import Flask, Response, current_app, g, has_request_context, request
from flask.logging import default_handler
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from werkzeug.exceptions import HTTPException


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PESEL = re.compile(r"(?<!\d)\d{11}(?!\d)")
_SENSITIVE_PAIR = re.compile(
    r"(?i)(authorization|cookie|csrf|password|passwd|token|secret|pesel|database_url|"
    r"nextcloud_app_password|smtp_password)(\s*[=:]\s*)([^\s,;&]+)"
)
_SENSITIVE_QUERY = re.compile(r"(?i)([?&](?:token|access_token|csrf|signature_token)=)[^&\s]+")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def redact_log_text(value: object) -> str:
    text = str(value)
    text = _SENSITIVE_PAIR.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _SENSITIVE_QUERY.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    return _PESEL.sub("[REDACTED_PESEL]", text)


def safe_identifier_hash(value: str) -> str:
    return hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()[:16]


def _request_context() -> dict[str, object]:
    if not has_request_context():
        return {}
    request_id = getattr(g, "request_id", None)
    correlation_id = getattr(g, "correlation_id", None)
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "method": request.method,
        "endpoint": request.endpoint or "unmatched",
    }


class ContextFilter(logging.Filter):
    def __init__(self, *, environment: str, service_name: str) -> None:
        super().__init__()
        self.environment = environment
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _request_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "environment"):
            record.environment = self.environment
        if not hasattr(record, "service"):
            record.service = self.service_name
        return True


class JsonLogFormatter(logging.Formatter):
    _standard_fields = (
        "request_id",
        "correlation_id",
        "method",
        "endpoint",
        "status_code",
        "duration_ms",
        "environment",
        "service",
        "operation",
        "document_type",
        "workflow_step",
        "form_id",
        "submission_pk",
        "user_id",
        "exception_type",
        "decision_category",
        "selected_count",
        "failure_reason",
    )

    def format(self, record: logging.LogRecord) -> str:
        message = redact_log_text(record.getMessage())
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "event": redact_log_text(getattr(record, "event", message)),
        }
        for field in self._standard_fields:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = redact_log_text(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["stack_trace"] = redact_log_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class TextLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def configure_logging(app: Flask) -> None:
    root = logging.getLogger()
    root.setLevel(str(app.config.get("LOG_LEVEL") or "INFO").upper())
    # The application hook below is the canonical access log. Framework access
    # loggers include the raw request target (and therefore query strings).
    logging.getLogger("werkzeug").disabled = True
    logging.getLogger("gunicorn.access").disabled = True
    handler = next((item for item in root.handlers if getattr(item, "_application_log_handler", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._application_log_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    formatter: logging.Formatter
    if str(app.config.get("LOG_FORMAT") or "text").lower() == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = TextLogFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    for existing_filter in tuple(handler.filters):
        if isinstance(existing_filter, ContextFilter):
            handler.removeFilter(existing_filter)
    handler.addFilter(
        ContextFilter(
            environment=str(app.config.get("ENV") or "development"),
            service_name=str(app.config.get("SERVICE_NAME") or app.config.get("APP_NAME") or "application"),
        )
    )
    app.logger.setLevel(root.level)
    if default_handler in app.logger.handlers:
        app.logger.removeHandler(default_handler)
    app.logger.propagate = True


class ApplicationMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "app_http_requests_total",
            "HTTP requests completed by endpoint and status class.",
            ("method", "endpoint", "status_class"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "app_http_request_duration_seconds",
            "HTTP request duration by endpoint.",
            ("method", "endpoint"),
            registry=self.registry,
        )
        self.operation_duration = Histogram(
            "app_operation_duration_seconds",
            "Duration of low-cardinality application operations.",
            ("operation", "kind"),
            registry=self.registry,
        )
        self.operation_failures = Counter(
            "app_operation_failures_total",
            "Failures of low-cardinality application operations.",
            ("operation", "kind"),
            registry=self.registry,
        )
        self.training_capacity_rejections = Counter(
            "app_training_capacity_rejections_total",
            "Training capacity conflicts rejected by the application.",
            registry=self.registry,
        )
        self.readiness_failures = Counter(
            "app_readiness_failures_total",
            "Readiness checks returning a failure.",
            registry=self.registry,
        )

    def observe_request(self, method: str, endpoint: str, status_code: int, duration_seconds: float) -> None:
        status_class = f"{int(status_code) // 100}xx"
        self.http_requests.labels(method=method, endpoint=endpoint, status_class=status_class).inc()
        self.http_duration.labels(method=method, endpoint=endpoint).observe(duration_seconds)

    @contextmanager
    def operation(self, operation: str, kind: str = "default") -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        except Exception:
            self.operation_failures.labels(operation=operation, kind=kind).inc()
            raise
        finally:
            self.operation_duration.labels(operation=operation, kind=kind).observe(time.perf_counter() - started)


def get_metrics(app: Flask | None = None) -> ApplicationMetrics | None:
    target = app or (current_app if has_request_context() else None)
    return target.extensions.get("observability_metrics") if target is not None else None


def _trusted_forwarded_id(header_name: str) -> str | None:
    if int(current_app.config.get("TRUSTED_PROXY_HOPS") or 0) <= 0 and not current_app.config.get("PROXY_FIX"):
        return None
    candidate = str(request.headers.get(header_name) or "").strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else None


def _metrics_authorized() -> bool:
    configured_token = str(current_app.config.get("METRICS_TOKEN") or "")
    if configured_token:
        supplied = str(request.headers.get("X-Metrics-Token") or "")
        authorization = str(request.headers.get("Authorization") or "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_token)
    return request.remote_addr in {"127.0.0.1", "::1"}


def register_observability(app: Flask) -> None:
    metrics = ApplicationMetrics()
    app.extensions["observability_metrics"] = metrics

    @app.before_request
    def observability_request_started() -> None:
        g.request_started_at = time.perf_counter()
        g.request_id = _trusted_forwarded_id("X-Request-ID") or str(uuid.uuid4())
        g.correlation_id = _trusted_forwarded_id("X-Correlation-ID") or g.request_id

    @app.after_request
    def observability_request_completed(response: Response) -> Response:
        duration_seconds = max(0.0, time.perf_counter() - getattr(g, "request_started_at", time.perf_counter()))
        endpoint = request.endpoint or "unmatched"
        response.headers["X-Request-ID"] = g.request_id
        metrics.observe_request(request.method, endpoint, response.status_code, duration_seconds)
        current_app.logger.info(
            "http_request_completed",
            extra={
                "event": "http_request_completed",
                "endpoint": endpoint,
                "status_code": response.status_code,
                "duration_ms": round(duration_seconds * 1000, 3),
            },
        )
        return response

    @app.errorhandler(Exception)
    def observability_unhandled_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        current_app.logger.exception(
            "http_request_failed",
            extra={
                "event": "http_request_failed",
                "endpoint": request.endpoint or "unmatched",
                "exception_type": type(exc).__name__,
            },
        )
        request_id = getattr(g, "request_id", "unknown")
        body = (
            "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\"><title>Błąd serwera</title></head>"
            f"<body><h1>Wystąpił błąd serwera</h1><p>Identyfikator błędu: {request_id}</p></body></html>"
        )
        return Response(body, status=500, content_type="text/html; charset=utf-8")

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        if not app.config.get("METRICS_ENABLED", True):
            return Response(status=404)
        if not _metrics_authorized():
            return Response("Forbidden\n", status=403, content_type="text/plain; charset=utf-8")
        return Response(generate_latest(metrics.registry), content_type=CONTENT_TYPE_LATEST)
