from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import abort, current_app, request


@dataclass(frozen=True)
class ParticipantSubmissionAccess:
    submission: dict
    credential: str


def participant_credential() -> str:
    """Return an existing participant credential without creating a new one."""
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(
        request.form.get("access_token")
        or request.form.get("token")
        or request.args.get("access_token")
        or request.args.get("token")
        or ""
    ).strip()


def resolve_participant_submission_access(
    submission_id: str,
    *,
    slug: str | None = None,
    submission: dict | None = None,
    submission_loader: Callable[[str], dict | None] | None = None,
) -> ParticipantSubmissionAccess | None:
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
        return None

    row = submission.get("row") or submission
    if str(row.get("submission_id") or submission.get("submission_id") or "") != str(submission_id):
        return None
    actual_slug = str(submission.get("form_slug") or row.get("form_slug") or "")
    if slug is not None and actual_slug != str(slug):
        return None

    credential = participant_credential()
    if not current_app.extensions["services"].access_token_service.verify_required_token(
        row,
        credential,
    ):
        return None
    return ParticipantSubmissionAccess(submission=submission, credential=credential)


def require_participant_submission_access(
    submission_id: str,
    *,
    slug: str | None = None,
    submission: dict | None = None,
    submission_loader: Callable[[str], dict | None] | None = None,
) -> ParticipantSubmissionAccess:
    access = resolve_participant_submission_access(
        submission_id,
        slug=slug,
        submission=submission,
        submission_loader=submission_loader,
    )
    if access is None:
        abort(404)
    return access


def require_public_csrf() -> None:
    # Imported lazily to keep the public-form route module independent.
    from routes.public_forms import _valid_public_csrf

    valid, _missing = _valid_public_csrf(request.form)
    if not valid:
        abort(400, description="Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.")
