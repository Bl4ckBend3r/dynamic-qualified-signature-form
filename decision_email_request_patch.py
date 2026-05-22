from __future__ import annotations

import logging
import sys
from typing import Any

from services.nextcloud_storage import NextcloudStorage
from services.process_service import (
    FIELD_OFFICER_DECISION_EMAIL_REQUESTED,
    FIELD_OFFICER_DECISION_EMAIL_SENT,
    LEGACY_FIELD_DECISION_EMAIL_SENT,
    LEGACY_FIELD_OFFICER_EMAIL_REQUESTED,
    normalize_yes_no,
)

logger = logging.getLogger(__name__)
_original_update_csv_row = NextcloudStorage.update_csv_row_by_submission_id


def _app_module() -> Any:
    return sys.modules.get("app") or sys.modules.get("__main__")


def _is_yes(value: Any) -> bool:
    return normalize_yes_no(value) == "TAK"


def _find_row(storage: NextcloudStorage, slug: str, submission_id: str) -> dict:
    for row in storage.read_csv_rows(slug):
        if str(row.get("submission_id", "")).strip() == str(submission_id).strip():
            return row
    return {}


def _requested_changed_to_yes(previous_row: dict, updates: dict) -> bool:
    watched_fields = (
        FIELD_OFFICER_DECISION_EMAIL_REQUESTED,
        LEGACY_FIELD_OFFICER_EMAIL_REQUESTED,
    )

    for field_name in watched_fields:
        if field_name not in updates:
            continue

        previous_value = previous_row.get(field_name, "")
        new_value = updates.get(field_name, "")

        if not _is_yes(previous_value) and _is_yes(new_value):
            return True

    return False


def _reset_sent_flags_if_needed(updates: dict, should_send_after_update: bool) -> dict:
    if not should_send_after_update:
        return updates

    normalized_updates = dict(updates)
    normalized_updates[FIELD_OFFICER_DECISION_EMAIL_SENT] = ""
    normalized_updates[LEGACY_FIELD_DECISION_EMAIL_SENT] = ""
    normalized_updates["decision_email_sent_for"] = ""
    return normalized_updates


def _send_decision_email_after_update(slug: str, submission_id: str) -> None:
    app_module = _app_module()

    if app_module is None or not hasattr(app_module, "find_submission_acceptance_by_id"):
        logger.warning("Cannot send decision email: app module is not available.")
        return

    submission = app_module.find_submission_acceptance_by_id(submission_id)

    if not submission or submission.get("form_slug") != slug:
        logger.warning("Cannot send decision email: submission %s not found in form %s.", submission_id, slug)
        return

    try:
        app_module.maybe_send_decision_email(submission)
    except Exception as exc:
        logger.exception("Cannot send decision email after request flag changed to TAK: %s", exc)


def update_csv_row_by_submission_id_with_decision_email(
    self: NextcloudStorage,
    slug: str,
    submission_id: str,
    updates: dict,
) -> bool:
    previous_row = _find_row(self, slug, submission_id)
    should_send_after_update = _requested_changed_to_yes(previous_row, updates)
    final_updates = _reset_sent_flags_if_needed(updates, should_send_after_update)

    updated = _original_update_csv_row(self, slug, submission_id, final_updates)

    if updated and should_send_after_update:
        _send_decision_email_after_update(slug, submission_id)

    return updated


if not getattr(NextcloudStorage, "_decision_email_request_patch_applied", False):
    NextcloudStorage.update_csv_row_by_submission_id = update_csv_row_by_submission_id_with_decision_email
    NextcloudStorage._decision_email_request_patch_applied = True
