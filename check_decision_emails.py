from __future__ import annotations

import logging

from app import app, get_forms, maybe_send_decision_email, storage
from services.process_service import (
    FIELD_OFFICER_DECISION_EMAIL_REQUESTED,
    FIELD_OFFICER_DECISION_EMAIL_SENT,
    LEGACY_FIELD_DECISION_EMAIL_SENT,
    LEGACY_FIELD_OFFICER_EMAIL_REQUESTED,
    get_officer_decision,
    normalize_yes_no,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def is_yes(value: object) -> bool:
    return normalize_yes_no(value) == "TAK"


def decision_email_requested(row: dict) -> bool:
    return is_yes(row.get(FIELD_OFFICER_DECISION_EMAIL_REQUESTED)) or is_yes(
        row.get(LEGACY_FIELD_OFFICER_EMAIL_REQUESTED)
    )


def decision_email_needs_sending(row: dict) -> bool:
    if not decision_email_requested(row):
        return False

    officer_decision = get_officer_decision(row)

    if not officer_decision.value:
        return False

    sent = is_yes(row.get(FIELD_OFFICER_DECISION_EMAIL_SENT)) or is_yes(
        row.get(LEGACY_FIELD_DECISION_EMAIL_SENT)
    )
    sent_for = str(row.get("decision_email_sent_for", "")).strip()

    return not sent or sent_for != officer_decision.value


def build_submission(slug: str, form_title: str, row: dict) -> dict:
    from services.process_service import build_process_state

    state = build_process_state(row)

    return {
        "row": row,
        "submission_id": str(row.get("submission_id", "")).strip(),
        "form_slug": slug,
        "form_title": form_title,
        "officer_decision": state.officer_decision.value,
        "process_status": state.status.value,
        "can_sign_documents": state.can_sign_documents,
    }


def main() -> None:
    checked = 0
    sent_attempts = 0

    with app.app_context():
        forms = get_forms()

        for form in forms:
            slug = str(form.get("slug", "")).strip()
            form_title = str(form.get("title", slug)).strip() or slug

            if not slug:
                continue

            try:
                rows = storage.read_csv_rows(slug)
            except Exception as exc:
                logger.exception("Nie można odczytać CSV dla formularza %s: %s", slug, exc)
                continue

            for row in rows:
                checked += 1
                submission_id = str(row.get("submission_id", "")).strip()

                if not submission_id:
                    continue

                if not decision_email_needs_sending(row):
                    continue

                submission = build_submission(slug, form_title, row)

                try:
                    maybe_send_decision_email(submission)
                    sent_attempts += 1
                except Exception as exc:
                    logger.exception(
                        "Nie udało się wysłać maila decyzji dla %s/%s: %s",
                        slug,
                        submission_id,
                        exc,
                    )

    logger.info("Sprawdzono wiersze CSV: %s, próby wysyłki: %s", checked, sent_attempts)


if __name__ == "__main__":
    main()
