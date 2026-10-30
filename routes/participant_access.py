from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from flask import abort, current_app, request

from models import FormSubmission
from sqlalchemy import select
from services.form_version_service import FormVersionError

logger = logging.getLogger(__name__)


def _access_denied(category: str) -> None:
    logger.info("participant_access_denied category=%s", category)


@dataclass(frozen=True)
class ParticipantSubmissionAccess:
    submission: dict
    credential: str


def participant_credential() -> str | None:
    """Return one unambiguous participant credential without creating a new one."""
    authorization = str(request.headers.get("Authorization") or "").strip()
    credentials: list[str] = []
    if authorization.lower().startswith("bearer "):
        credentials.append(authorization[7:].strip())
    credentials.extend(
        str(value or "").strip()
        for value in (
            request.form.get("access_token"),
            request.form.get("token"),
            request.args.get("access_token"),
            request.args.get("token"),
        )
    )
    distinct = {credential for credential in credentials if credential}
    if not distinct:
        return ""
    if len(distinct) != 1:
        return None
    return distinct.pop()


def resolve_participant_submission_access(
    submission_id: str,
    *,
    slug: str | None = None,
    submission: dict | None = None,
    submission_loader: Callable[[str], dict | None] | None = None,
) -> ParticipantSubmissionAccess | None:
    loaded_from_service = submission is None and submission_loader is None
    if submission is None:
        if submission_loader is None:
            services = current_app.extensions["services"]
            submission_loader = lambda public_id: services.submission_service.get_submission_context(
                public_id,
                form_config_service=services.form_config_service,
                storage=services.storage,
            )
        submission = submission_loader(submission_id)
    if not submission:
        _access_denied("submission_not_found")
        return None

    row = submission.get("row") or submission
    if str(row.get("submission_id") or submission.get("submission_id") or "") != str(submission_id):
        _access_denied("submission_mismatch")
        return None
    actual_slug = str(submission.get("form_slug") or row.get("form_slug") or "")
    if slug is not None and actual_slug != str(slug):
        _access_denied("form_mismatch")
        return None

    credential = participant_credential()
    if not credential:
        _access_denied("ambiguous_credential" if credential is None else "missing_credential")
        return None
    if not current_app.extensions["services"].access_token_service.verify_required_token(
        row,
        credential,
    ):
        _access_denied("invalid_credential")
        return None
    services = current_app.extensions["services"]
    session_factory = getattr(getattr(services, "submission_repository", None), "session_factory", None)
    if current_app.config.get("DATABASE_URL") and session_factory and hasattr(services, "form_version_service"):
        try:
            with session_factory.begin() as db:
                record = db.execute(select(FormSubmission).where(
                    FormSubmission.submission_id == submission_id,
                    FormSubmission.form_slug == actual_slug,
                ).with_for_update()).scalar_one_or_none()
                if record is None:
                    _access_denied("submission_not_found")
                    return None
                version_service = services.form_version_service
                if record.form_version_id:
                    version_service.upgrade_submission(db, record)
                    version_service.refresh_submission_completion(db, record)
            if loaded_from_service:
                submission = services.submission_service.get_submission_context(
                    submission_id,
                    form_config_service=services.form_config_service,
                    storage=services.storage,
                ) or submission
        except FormVersionError as exc:
            logger.warning("participant_version_upgrade_conflict submission_id=%s reason=%s", submission_id, str(exc))
            abort(409, description="Aktualizacja formularza wymaga interwencji administratora.")
    return ParticipantSubmissionAccess(submission=submission, credential=credential)


def require_participant_submission_access(
    submission_id: str,
    *,
    slug: str | None = None,
    submission: dict | None = None,
    submission_loader: Callable[[str], dict | None] | None = None,
    allow_incomplete: bool = False,
) -> ParticipantSubmissionAccess:
    access = resolve_participant_submission_access(
        submission_id,
        slug=slug,
        submission=submission,
        submission_loader=submission_loader,
    )
    if access is None:
        abort(404)
    if request.method == "POST" and not allow_incomplete:
        services = current_app.extensions["services"]
        session_factory = getattr(getattr(services, "submission_repository", None), "session_factory", None)
        if current_app.config.get("DATABASE_URL") and session_factory and hasattr(services, "form_version_service"):
            with session_factory.begin() as db:
                record = db.execute(select(FormSubmission).where(
                    FormSubmission.submission_id == submission_id,
                    FormSubmission.form_slug == str(access.submission.get("form_slug") or ""),
                ).with_for_update()).scalar_one_or_none()
                if record is None:
                    abort(404)
                try:
                    services.form_version_service.require_submission_data_complete(db, record)
                except FormVersionError as exc:
                    abort(409, description=str(exc))
    return access


def require_public_csrf() -> None:
    # Imported lazily to keep the public-form route module independent.
    from routes.public_forms import _valid_public_csrf

    valid, _missing = _valid_public_csrf(request.form)
    if not valid:
        abort(400, description="Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.")
