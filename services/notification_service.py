from __future__ import annotations

import logging
from typing import Any

from services.email_service import _send_email


logger = logging.getLogger(__name__)


class NotificationService:
    """Compatibility facade for retired workflow notification hooks.

    Automatic delivery is intentionally centralized in
    MailDispatchService.dispatch_submission_received().
    """

    def __init__(self, submission_repository=None, audit_log_service=None, storage=None, smtp_sender=None) -> None:
        self.submission_repository = submission_repository
        self.audit_log_service = audit_log_service
        self.storage = storage
        self.smtp_sender = smtp_sender or _send_email

    def notify_event(
        self,
        event_type: str,
        submission: dict,
        form_config: dict,
        context_extra: dict[str, Any] | None = None,
    ) -> list[dict]:
        logger.info("automatic_notification_disabled event=%s", event_type)
        return []

    def notify_event_once(
        self,
        event_type: str,
        submission: dict,
        form_config: dict,
        *,
        sent_field: str,
        idempotency_key: str | None = None,
        context_extra: dict[str, Any] | None = None,
    ) -> list[dict]:
        logger.info("automatic_notification_disabled event=%s", event_type)
        return []

    def send_decision_email(self, submission_id: str, decision: str) -> bool:
        logger.info("automatic_decision_email_disabled submission_id=%s", submission_id)
        return False
