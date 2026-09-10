from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from models import MailFooter


logger = logging.getLogger(__name__)


class MailFooterResolver:
    """Single policy for selecting global and form-specific mail footers."""

    GLOBAL_MAIL_TYPES = {
        "submission_received",
        "submission_registered",
        "initial_submission",
        "system",
        "global",
    }

    def resolve(self, db, *, mail_type: str, form: Any | None = None, submission: Any | None = None) -> MailFooter | None:
        normalized_type = str(mail_type or "").strip().lower()
        form_id = getattr(form, "id", None)
        if form_id is None and submission is not None:
            form_id = getattr(submission, "form_id", None)

        if normalized_type not in self.GLOBAL_MAIL_TYPES and form_id is not None:
            form_footers = db.execute(
                select(MailFooter)
                .where(MailFooter.form_id == form_id)
                .order_by(MailFooter.is_default.desc(), MailFooter.id.asc())
            ).scalars().all()
            configured = next((footer for footer in form_footers if footer.is_default), None)
            configured = configured or (form_footers[0] if form_footers else None)
            if configured and not configured.use_global and configured.is_active:
                self._log("form", configured, normalized_type, form_id)
                return configured

        global_footer = db.execute(
            select(MailFooter)
            .where(MailFooter.form_id.is_(None), MailFooter.is_active.is_(True))
            .order_by(MailFooter.is_default.desc(), MailFooter.id.asc())
        ).scalars().first()
        self._log("global" if global_footer else "none", global_footer, normalized_type, form_id)
        return global_footer

    @staticmethod
    def _log(scope: str, footer: MailFooter | None, mail_type: str, form_id: int | None) -> None:
        logger.info(
            "mail_footer_resolved scope=%s footer_id=%s mail_type=%s form_id=%s",
            scope,
            getattr(footer, "id", None),
            mail_type,
            form_id,
        )
