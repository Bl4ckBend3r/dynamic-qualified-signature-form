from __future__ import annotations

import logging
import sys
from functools import wraps
from typing import Any, Callable

from flask import Flask, request

import email_footer_patch  # noqa: F401
from services.email_service import send_form_submission_notification_email

logger = logging.getLogger(__name__)
_previous_route = Flask.route


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _has_error_status(response: Any) -> bool:
    if isinstance(response, tuple) and len(response) >= 2 and isinstance(response[1], int):
        return response[1] >= 400
    status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and status_code >= 400


def _split_addresses(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    else:
        values = value
    return [str(item).strip() for item in values if str(item).strip()]


def _notification_addresses(app_module: Any, form_definition: dict) -> list[str]:
    return (
        _split_addresses(form_definition.get("notification_emails"))
        or list(app_module.app.config.get("FORM_NOTIFICATION_EMAILS", []))
    )


def _notify(app_module: Any, slug: str, form_definition: dict, row: dict) -> None:
    submission_id = str(row.get("submission_id", "")).strip()
    if not submission_id:
        return

    addresses = _notification_addresses(app_module, form_definition)
    if not addresses:
        return

    applicant_name = " ".join(
        value.strip()
        for value in (str(row.get("imiona", "")), str(row.get("nazwisko", "")))
        if value.strip()
    )

    try:
        send_form_submission_notification_email(
            smtp_host=app_module.app.config["SMTP_HOST"],
            smtp_port=app_module.app.config["SMTP_PORT"],
            smtp_user=app_module.app.config["SMTP_USER"],
            smtp_password=app_module.app.config["SMTP_PASSWORD"],
            mail_from=app_module.app.config["MAIL_FROM"],
            to_emails=addresses,
            submission_id=submission_id,
            form_title=row.get("form_name") or form_definition.get("title") or slug,
            applicant_name=applicant_name,
            applicant_email=str(row.get("email", "")).strip(),
            use_tls=app_module.app.config.get("SMTP_USE_TLS", True),
            use_ssl=app_module.app.config.get("SMTP_USE_SSL", False),
            timeout=app_module.app.config.get("SMTP_TIMEOUT", 30),
        )
        app_module.storage.update_csv_row_by_submission_id(
            slug,
            submission_id,
            {"form_notification_email_sent": "Tak", "form_notification_email_error": ""},
        )
    except Exception as exc:
        logger.exception("Form notification failed for %s: %s", submission_id, exc)
        app_module.storage.update_csv_row_by_submission_id(
            slug,
            submission_id,
            {"form_notification_email_sent": "Nie", "form_notification_email_error": str(exc)},
        )


def _wrap_submit(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        slug = kwargs.get("slug") or (args[0] if args else "")
        app_module = _app_module()
        existing_ids: set[str] = set()
        form_definition: dict = {}

        if app_module is not None and slug:
            try:
                existing_ids = {
                    str(row.get("submission_id", "")).strip()
                    for row in app_module.storage.read_csv_rows(slug)
                    if str(row.get("submission_id", "")).strip()
                }
                form_definition = app_module.get_form_definition(slug) or {}
            except Exception as exc:
                logger.warning("Cannot prepare form notification state: %s", exc)

        response = func(*args, **kwargs)

        if request.method == "POST" and app_module is not None and slug and not _has_error_status(response):
            try:
                for row in app_module.storage.read_csv_rows(slug):
                    submission_id = str(row.get("submission_id", "")).strip()
                    if submission_id and submission_id not in existing_ids:
                        _notify(app_module, slug, form_definition, row)
            except Exception as exc:
                logger.exception("Cannot process form notification: %s", exc)

        return response

    return wrapper


def _patched_route(self: Flask, rule: str, **options):
    decorator = _previous_route(self, rule, **options)

    def register(func: Callable):
        endpoint = options.get("endpoint") or func.__name__
        if endpoint == "submit" or func.__name__ == "submit":
            return decorator(_wrap_submit(func))
        return decorator(func)

    return register


if not getattr(Flask, "_form_notifications_patch_applied", False):
    Flask.route = _patched_route
    Flask._form_notifications_patch_applied = True
