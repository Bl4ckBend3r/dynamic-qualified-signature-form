from __future__ import annotations

import base64
import hashlib
import smtplib
import ssl
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from models import SystemMailSettings
from services.mail_template_service import sanitize_content_html


MAIL_MODES = {"system", "custom", "disabled"}
LOGO_POSITIONS = {"none", "header", "before_content", "after_content"}
LOGO_ALIGNMENTS = {"left", "center", "right"}
DEFAULT_LAYOUT = {
    "platform_name": "Platforma formularzy",
    "primary_color": "#1d2e5b",
    "accent_color": "#c8a35d",
    "footer_html": "",
    "logo_id": None,
    "logo_position": "none",
    "logo_alignment": "center",
    "logo_height_px": 64,
}


class MailSettingsService:
    def __init__(self, secret_key: str) -> None:
        digest = hashlib.sha256(str(secret_key or "").encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt_password(self, value: str) -> str | None:
        password = str(value or "")
        return self._fernet.encrypt(password.encode("utf-8")).decode("ascii") if password else None

    def decrypt_password(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    def update_form(self, form, form_data) -> None:
        mode = str(form_data.get("mail_mode") or "system").strip()
        form.mail_mode = mode if mode in MAIL_MODES else "system"
        form.smtp_config = self.config_from_form(form_data, prefix="form_smtp_")
        password = str(form_data.get("form_smtp_password") or "")
        if password:
            form.smtp_password_encrypted = self.encrypt_password(password)
        elif form_data.get("form_smtp_clear_password") == "on":
            form.smtp_password_encrypted = None

    def get_or_create_system_settings(self, db) -> SystemMailSettings:
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        if settings is None:
            settings = SystemMailSettings(smtp_config={}, layout_config=dict(DEFAULT_LAYOUT))
            db.add(settings)
            db.flush()
        return settings

    def update_system(self, settings: SystemMailSettings, form_data, *, updated_by_user_id: int | None) -> None:
        settings.smtp_config = self.config_from_form(form_data, prefix="system_smtp_")
        password = str(form_data.get("system_smtp_password") or "")
        if password:
            settings.smtp_password_encrypted = self.encrypt_password(password)
        elif form_data.get("system_smtp_clear_password") == "on":
            settings.smtp_password_encrypted = None
        settings.layout_config = self.layout_from_form(form_data)
        settings.updated_by_user_id = updated_by_user_id

    def resolve_smtp(self, db, form, app_config: Mapping[str, Any]) -> dict[str, Any] | None:
        mode = str(getattr(form, "mail_mode", "system") or "system")
        if mode == "disabled":
            return None
        if mode == "custom":
            return self._resolved_config(
                getattr(form, "smtp_config", None),
                self.decrypt_password(getattr(form, "smtp_password_encrypted", None)),
            )
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        if settings and settings.smtp_config:
            return self._resolved_config(
                settings.smtp_config,
                self.decrypt_password(settings.smtp_password_encrypted),
            )
        legacy = {
            "host": app_config.get("SMTP_HOST", ""),
            "port": app_config.get("SMTP_PORT", 587),
            "user": app_config.get("SMTP_USER", ""),
            "mail_from": app_config.get("MAIL_FROM", ""),
            "sender_name": app_config.get("MAIL_SENDER_NAME", ""),
            "use_tls": app_config.get("SMTP_USE_TLS", True),
            "use_ssl": app_config.get("SMTP_USE_SSL", False),
            "timeout": app_config.get("SMTP_TIMEOUT", 30),
            "reply_to": app_config.get("MAIL_REPLY_TO", ""),
        }
        return self._resolved_config(legacy, str(app_config.get("SMTP_PASSWORD") or ""))

    def get_layout(self, db) -> dict[str, Any]:
        settings = db.execute(select(SystemMailSettings).order_by(SystemMailSettings.id)).scalars().first()
        layout = dict(DEFAULT_LAYOUT)
        if settings and isinstance(settings.layout_config, dict):
            layout.update(settings.layout_config)
        if layout.get("logo_position") not in LOGO_POSITIONS:
            layout["logo_position"] = DEFAULT_LAYOUT["logo_position"]
        if layout.get("logo_alignment") not in LOGO_ALIGNMENTS:
            layout["logo_alignment"] = DEFAULT_LAYOUT["logo_alignment"]
        layout["logo_height_px"] = _bounded_int(
            layout.get("logo_height_px"), DEFAULT_LAYOUT["logo_height_px"], 16, 200
        )
        return layout

    def config_from_form(self, form_data, *, prefix: str) -> dict[str, Any]:
        return {
            "host": str(form_data.get(f"{prefix}host") or "").strip(),
            "port": _bounded_int(form_data.get(f"{prefix}port"), 587, 1, 65535),
            "user": str(form_data.get(f"{prefix}user") or "").strip(),
            "mail_from": str(form_data.get(f"{prefix}mail_from") or "").strip(),
            "sender_name": str(form_data.get(f"{prefix}sender_name") or "").strip(),
            "use_tls": form_data.get(f"{prefix}use_tls") == "on",
            "use_ssl": form_data.get(f"{prefix}use_ssl") == "on",
            "timeout": _bounded_int(form_data.get(f"{prefix}timeout"), 30, 1, 120),
            "reply_to": str(form_data.get(f"{prefix}reply_to") or "").strip(),
        }

    def layout_from_form(self, form_data) -> dict[str, Any]:
        primary = _safe_color(form_data.get("layout_primary_color"), DEFAULT_LAYOUT["primary_color"])
        accent = _safe_color(form_data.get("layout_accent_color"), DEFAULT_LAYOUT["accent_color"])
        logo_id = str(form_data.get("layout_logo_id") or "").strip()
        logo_position = str(form_data.get("layout_logo_position") or DEFAULT_LAYOUT["logo_position"]).strip()
        logo_alignment = str(form_data.get("layout_logo_alignment") or DEFAULT_LAYOUT["logo_alignment"]).strip()
        return {
            "platform_name": str(form_data.get("layout_platform_name") or DEFAULT_LAYOUT["platform_name"]).strip()[:255],
            "primary_color": primary,
            "accent_color": accent,
            "footer_html": sanitize_content_html(str(form_data.get("layout_footer_html") or "").strip())[:50_000],
            "logo_id": int(logo_id) if logo_id.isdigit() else None,
            "logo_position": logo_position if logo_position in LOGO_POSITIONS else DEFAULT_LAYOUT["logo_position"],
            "logo_alignment": logo_alignment if logo_alignment in LOGO_ALIGNMENTS else DEFAULT_LAYOUT["logo_alignment"],
            "logo_height_px": _bounded_int(
                form_data.get("layout_logo_height_px"), DEFAULT_LAYOUT["logo_height_px"], 16, 200
            ),
        }

    def test_connection(self, config: Mapping[str, Any]) -> None:
        host = str(config.get("smtp_host") or config.get("host") or "").strip()
        port = _bounded_int(config.get("smtp_port") or config.get("port"), 587, 1, 65535)
        timeout = _bounded_int(config.get("timeout"), 30, 1, 120)
        if not host:
            raise ValueError("Podaj host SMTP.")
        smtp_class = smtplib.SMTP_SSL if config.get("use_ssl") else smtplib.SMTP
        with smtp_class(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if config.get("use_tls") and not config.get("use_ssl"):
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            user = str(config.get("smtp_user") or config.get("user") or "").strip()
            password = str(config.get("smtp_password") or "")
            if user and password:
                smtp.login(user, password)

    @staticmethod
    def public_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
        source = value if isinstance(value, Mapping) else {}
        return {
            "host": str(source.get("host") or ""),
            "port": _bounded_int(source.get("port"), 587, 1, 65535),
            "user": str(source.get("user") or ""),
            "mail_from": str(source.get("mail_from") or ""),
            "sender_name": str(source.get("sender_name") or ""),
            "use_tls": bool(source.get("use_tls")),
            "use_ssl": bool(source.get("use_ssl")),
            "timeout": _bounded_int(source.get("timeout"), 30, 1, 120),
            "reply_to": str(source.get("reply_to") or ""),
        }

    @staticmethod
    def _resolved_config(value: Mapping[str, Any] | None, password: str) -> dict[str, Any] | None:
        config = MailSettingsService.public_config(value)
        if not config["host"] or not config["mail_from"]:
            return None
        return {
            "smtp_host": config["host"],
            "smtp_port": config["port"],
            "smtp_user": config["user"],
            "smtp_password": password,
            "mail_from": config["mail_from"],
            "sender_name": config["sender_name"],
            "use_tls": config["use_tls"],
            "use_ssl": config["use_ssl"],
            "timeout": config["timeout"],
            "reply_to": config["reply_to"],
        }


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _safe_color(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip()
    if len(candidate) == 7 and candidate.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in candidate[1:]):
        return candidate
    return fallback
