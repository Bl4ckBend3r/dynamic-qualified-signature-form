from __future__ import annotations

import logging
import hmac
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, send_file, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from database import create_session_factory
from form_loader import FIELD_STAGE_INITIAL, form_definition_for_stage, normalize_form_definition
from models import ContactPage, Form, FormDraft, FormField, FormRegulationVersion, FormSubmission, FormVersion, Logo, ServiceDocument
from services.form_draft_service import FormDraftError
from services.compliance_service import ComplianceError
from services.process_service import ProcessStatus
from services.contact_page_service import ensure_contact_defaults, normalized_phones
from services.site_document_service import SERVICE_DOCUMENT_TYPES
from services.nextcloud_storage import NextcloudStorageError

from services.form_access_service import verify_share_token
from routes.participant_access import require_participant_submission_access

logger = logging.getLogger(__name__)

bp = Blueprint("public_forms", __name__)
PUBLIC_CSRF_SESSION_KEY = "public_form_csrf_token"
FORM_VERSION_TOKEN_SALT = "public-form-version"


@bp.get("/sprawdz-status")
def public_status_page():
    return render_template("public_status.html")


def public_csrf_token() -> str:
    token = str(session.get(PUBLIC_CSRF_SESSION_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session[PUBLIC_CSRF_SESSION_KEY] = token
    return token


@bp.app_context_processor
def public_form_csrf_context() -> dict:
    return {"public_csrf_token": public_csrf_token}


def _public_csrf_enabled() -> bool:
    # Existing test/local configurations use this standard switch. Public CSRF
    # remains enabled by default when no explicit switch is present.
    if current_app.config.get("WTF_CSRF_ENABLED") is False:
        return False
    return bool(current_app.config.get("PUBLIC_CSRF_ENABLED", True))


def _provided_public_csrf_token(request_data) -> str:
    value = request.headers.get("X-CSRF-Token", "")
    if not value and hasattr(request_data, "get"):
        value = request_data.get("csrf_token", "")
    return str(value or "")


def _valid_public_csrf(request_data) -> tuple[bool, bool]:
    provided = _provided_public_csrf_token(request_data)
    missing = not bool(provided)
    if not _public_csrf_enabled():
        return True, missing
    expected = str(session.get(PUBLIC_CSRF_SESSION_KEY) or "")
    return bool(expected and provided and hmac.compare_digest(expected, provided)), missing


def get_services():
    return current_app.extensions["services"]


def db_session_factory():
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        return None
    return create_session_factory(database_url)


def _form_version_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=FORM_VERSION_TOKEN_SALT)


def _form_version_token(slug: str, version_id: int) -> str:
    return _form_version_serializer().dumps({"slug": slug, "version_id": version_id})


def _version_id_from_token(slug: str, token: str) -> int | None:
    try:
        payload = _form_version_serializer().loads(
            str(token or ""),
            max_age=int(current_app.config.get("FORM_VERSION_TOKEN_MAX_AGE", 86400)),
        )
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or str(payload.get("slug") or "") != slug:
        return None
    try:
        return int(payload.get("version_id"))
    except (TypeError, ValueError):
        return None


@bp.get("/")
def index():
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        forms = list_public_db_forms()
        return render_template("index.html", forms=forms)

    try:
        services.storage.ensure_base_structure()
        services.storage.ensure_outputs_for_all_forms()
        forms = services.form_config_service.list_forms(services.storage)
        return render_template("index.html", forms=forms)
    except NextcloudStorageError as exc:
        logger.exception("Błąd Nextcloud: %s", exc)
        return f"Błąd Nextcloud: {exc}", 500


@bp.get("/form/<slug>")
def form_page(slug: str):
    services = get_services()
    if current_app.config.get("DATABASE_URL"):
        share_access_token = str(
            request.args.get("access") or ""
        ).strip()

        form_meta, form_config, form_version = (
            get_public_db_form_versioned(
                slug,
                access_token=share_access_token,
            )
        )

        if not form_meta or not form_config:
            abort(404)

        form_action = url_for(
            "public_forms.submit",
            slug=slug,
            **(
                {"access": share_access_token}
                if share_access_token
                else {}
            ),
        )

        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(
                form_config,
                FIELD_STAGE_INITIAL,
            ),
            errors={},
            values={},
            form_version_id=(
                form_version.id
                if form_version
                else None
            ),
            form_version_token=(
                _form_version_token(
                    slug,
                    form_version.id,
                )
                if form_version
                else ""
            ),
            draft_enabled=True,
            form_action=form_action,
            share_access_token=share_access_token,
        )

    form_meta = services.form_config_service.get_form_meta(services.storage, slug)
    if not form_meta:
        abort(404)

    form_config = services.form_config_service.get_form_config(services.storage, slug)
    if not form_config:
        abort(404)

    return render_template(
        "form_page.html",
        slug=slug,
        form_meta=form_meta,
        form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
        errors={},
        values={},
        draft_enabled=False,
    )


@bp.post("/form/<slug>/draft")
def create_form_draft(slug: str):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    request_data = request.form
    csrf_valid, _csrf_missing = _valid_public_csrf(request_data)
    if not csrf_valid:
        abort(400, description="Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.")
    version_token = str(request_data.get("form_version_token") or "")
    share_access_token = str(
        request.args.get("access") or ""
    ).strip()
    form_meta, form_config, version = get_public_db_form_versioned(
        slug,
        version_token=version_token,
        require_version_token=True,
        access_token=share_access_token,
    )
    if not form_meta or not form_config or not version:
        abort(409, description="Wersja formularza nie jest dostępna. Otwórz formularz ponownie.")
    initial_config = form_definition_for_stage(form_config, FIELD_STAGE_INITIAL)
    with session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == slug)).scalar_one()
        try:
            created = get_services().form_draft_service.create(
                db, form=form, form_version=version, form_config=initial_config, request_data=request_data
            )
        except FormDraftError as exc:
            return render_template(
                "form_page.html", slug=slug, form_meta=form_meta, form_definition=initial_config,
                errors={"email": str(exc)}, values=request_data, form_version_id=version.id,
                form_version_token=version_token, form_error=str(exc), draft_enabled=True,
            ), 400
        draft_public_id = created.draft.public_id
        raw_token = created.raw_token
        db.commit()
    resume_url = url_for("public_forms.resume_form_draft", slug=slug, token=raw_token, _external=True)
    try:
        get_services().mail_dispatch_service.dispatch_form_draft_resume(draft_public_id, resume_url)
    except Exception as exc:
        current_app.logger.exception("form_draft_mail_failed slug=%s error=%s", slug, exc.__class__.__name__)
    return redirect(url_for("public_forms.resume_form_draft", slug=slug, token=raw_token, saved="1"))


@bp.get("/form/<slug>/draft/<token>")
def resume_form_draft(slug: str, token: str):
    resolved = _resolve_form_draft(slug, token)
    if not resolved:
        abort(404)
    form_meta, form_config, draft = resolved
    response = make_response(render_template(
        "form_page.html", slug=slug, form_meta=form_meta, form_definition=form_config,
        errors={}, values=dict(draft.data_json or {}), draft_enabled=True, draft_mode=True,
        draft_token=token,
        form_action=url_for("public_forms.submit_form_draft", slug=slug, token=token),
        autosave_url=url_for("public_forms.autosave_form_draft", slug=slug, token=token),
        draft_message=(
            f"Wczytano wersję roboczą zapisaną {draft.updated_at.strftime('%Y-%m-%d %H:%M')}."
            if request.args.get("saved") != "1"
            else "Wersja robocza została zapisana. Link do powrotu wysłaliśmy e-mailem."
        ),
    ))
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.post("/form/<slug>/draft/<token>/autosave")
def autosave_form_draft(slug: str, token: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Nieprawidłowe dane."}), 400
    csrf_valid, _csrf_missing = _valid_public_csrf(payload)
    if not csrf_valid:
        return jsonify({"ok": False, "error": "Sesja wygasła."}), 400
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == slug, Form.is_active.is_(True), Form.is_public.is_(True))).scalar_one_or_none()
        if not form:
            abort(404)
        draft = get_services().form_draft_service.get_by_token(db, form_id=form.id, raw_token=token)
        if not draft:
            abort(404)
        form_config = _definition_for_draft(db, form, draft)
        try:
            get_services().form_draft_service.autosave(db, draft=draft, form_config=form_config, request_data=payload)
        except FormDraftError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        db.commit()
        saved_at = draft.last_autosave_at.isoformat()
    return jsonify({"ok": True, "saved_at": saved_at})


@bp.post("/form/<slug>/draft/<token>/submit")
def submit_form_draft(slug: str, token: str):
    resolved = _resolve_form_draft(slug, token)
    if not resolved:
        abort(404)
    form_meta, form_config, draft = resolved
    request_data = request.form
    csrf_valid, _csrf_missing = _valid_public_csrf(request_data)
    if not csrf_valid:
        abort(400, description="Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.")
    session_factory = db_session_factory()
    with session_factory() as db:
        claimed = get_services().form_draft_service.claim_for_submit(db, draft_id=draft.id, raw_token=token)
    if not claimed:
        abort(409, description="Ta wersja robocza jest już wysyłana albo została wysłana.")
    existing_submission = get_services().submission_repository.get_by_id(claimed.submission_public_id)
    if existing_submission:
        with session_factory() as db:
            get_services().form_draft_service.complete(
                db, draft_id=draft.id, submission_internal_id=int(existing_submission["id"])
            )
        abort(409, description="Ta wersja robocza została już wysłana.")
    try:
        result = get_services().submission_service.submit_form(
            slug, form_config, request_data, form_version_id=draft.form_version_id,
            submission_id=claimed.submission_public_id,
        )
        if not result["ok"]:
            with session_factory() as db:
                get_services().form_draft_service.release_submit_claim(db, draft.id)
            return render_template(
                "form_page.html", slug=slug, form_meta=form_meta, form_definition=form_config,
                errors=result["errors"], values=result["values"], draft_enabled=True, draft_mode=True,
                draft_token=token, form_action=url_for("public_forms.submit_form_draft", slug=slug, token=token),
                autosave_url=url_for("public_forms.autosave_form_draft", slug=slug, token=token),
                form_error="Sprawdź pola oznaczone poniżej i popraw wskazane błędy.",
            ), 400
        submission_internal_id = int(result["submission"]["id"])
        with session_factory() as db:
            get_services().form_draft_service.complete(
                db, draft_id=draft.id, submission_internal_id=submission_internal_id
            )
        return render_template("result.html", result=result["result"])
    except Exception:
        with session_factory() as db:
            get_services().form_draft_service.release_submit_claim(db, draft.id)
        raise


@bp.post("/form/<slug>/draft/resend")
def resend_form_draft(slug: str):
    neutral = "Jeżeli istnieje aktywna wersja robocza dla tego adresu, wysłaliśmy link."
    request_data = request.form
    csrf_valid, _csrf_missing = _valid_public_csrf(request_data)
    if not csrf_valid:
        abort(400)
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    rotated = None
    with session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == slug, Form.is_active.is_(True), Form.is_public.is_(True))).scalar_one_or_none()
        if not form:
            abort(404)
        rotated = get_services().form_draft_service.rotate_for_email(
            db, form_id=form.id, email=str(request_data.get("draft_email") or "")
        )
        db.commit()
    if rotated:
        resume_url = url_for("public_forms.resume_form_draft", slug=slug, token=rotated.raw_token, _external=True)
        try:
            get_services().mail_dispatch_service.dispatch_form_draft_resume(rotated.draft.public_id, resume_url)
        except Exception as exc:
            current_app.logger.exception("form_draft_resend_failed slug=%s error=%s", slug, exc.__class__.__name__)
    flash(neutral, "success")
    return redirect(url_for("public_forms.form_page", slug=slug))


def _resolve_form_draft(slug: str, token: str):
    session_factory = db_session_factory()
    if not session_factory:
        return None
    with session_factory() as db:
        form = db.execute(select(Form).where(Form.slug == slug, Form.is_active.is_(True), Form.is_public.is_(True))).scalar_one_or_none()
        if not form:
            return None
        draft = get_services().form_draft_service.get_by_token(db, form_id=form.id, raw_token=token)
        if not draft:
            return None
        definition = _definition_for_draft(db, form, draft)
        meta = form_to_public_meta(form)
        db.expunge(draft)
        return meta, form_definition_for_stage(definition, FIELD_STAGE_INITIAL), draft


def _definition_for_draft(db, form: Form, draft: FormDraft) -> dict:
    if draft.form_version_id:
        version = db.get(FormVersion, draft.form_version_id)
        if not version or version.form_id != form.id:
            raise FormDraftError("Brak wersji formularza przypisanej do wersji roboczej.")
        return dict(version.definition_json or {})
    fields = db.execute(
        select(FormField).where(FormField.form_id == form.id, FormField.active.is_(True)).order_by(FormField.sort_order, FormField.id)
    ).scalars().all()
    return form_to_definition(form, fields)



@bp.post("/submit/<slug>")
def submit(slug: str):
    services = get_services()
    share_access_token = str(
        request.args.get("access") or ""
    ).strip()

    form_action = url_for(
        "public_forms.submit",
        slug=slug,
        **(
            {"access": share_access_token}
            if share_access_token
            else {}
        ),
    )
    request_data = request.get_json(silent=True) if request.is_json else request.form
    form_version = None
    form_version_token = str((request_data or {}).get("form_version_token") or "")
    if current_app.config.get("DATABASE_URL"):
        form_meta, form_config, form_version = get_public_db_form_versioned(
            slug,
            version_token=form_version_token,
            require_version_token=True,
            access_token=share_access_token,
        )
        if not form_meta or not form_config:
            abort(409, description="Wersja formularza wygasła albo nie jest już dostępna. Otwórz formularz ponownie.")
    else:
        form_meta = services.form_config_service.get_form_meta(services.storage, slug)
        if not form_meta:
            abort(404)

        form_config = services.form_config_service.get_form_config(services.storage, slug)
        if not form_config:
            abort(404)

    csrf_valid, csrf_missing = _valid_public_csrf(request_data or {})
    if not csrf_valid:
        logger.warning(
            "public_form_rejected slug=%s reason=csrf_invalid invalid_fields=%s csrf_missing=%s",
            slug,
            "csrf_token",
            csrf_missing,
        )
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={},
            values=request_data or {},
            form_version_id=form_version.id if form_version else None,
            form_version_token=form_version_token,
            form_error="Sesja formularza wygasła lub brakuje tokenu bezpieczeństwa. Odśwież stronę i spróbuj ponownie.",
            form_action=form_action,
            share_access_token=share_access_token,
        ), 400

    try:
        initial_form_config = form_definition_for_stage(form_config, FIELD_STAGE_INITIAL)
        submission_result = services.submission_service.submit_form(
            slug,
            initial_form_config,
            request_data or {},
            form_version_id=form_version.id if form_version else None,
        )
        if not submission_result["ok"]:
            invalid_fields = sorted(str(name) for name in submission_result["errors"])
            logger.warning(
                "public_form_rejected slug=%s reason=validation invalid_fields=%s csrf_missing=%s",
                slug,
                ",".join(invalid_fields) or "-",
                csrf_missing,
            )
            flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
            
            return render_template(
                "form_page.html",
                slug=slug,
                form_meta=form_meta,
                form_definition=initial_form_config,
                errors=submission_result["errors"],
                values=submission_result["values"],
                form_version_id=form_version.id if form_version else None,
                form_version_token=form_version_token,
                form_error="Sprawdź pola oznaczone poniżej i popraw wskazane błędy.",
                form_action=form_action,
                share_access_token=share_access_token,
            ), 400

        return render_template("result.html", result=submission_result["result"], form_action=form_action, share_access_token=share_access_token,)

    except ComplianceError as exc:
        logger.warning("public_form_rejected slug=%s reason=compliance", slug)
        flash(str(exc), "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={"compliance": str(exc)},
            values=request_data or request.form,
            form_version_id=form_version.id if form_version else None,
            form_version_token=form_version_token,
            form_error=str(exc),
            form_action=form_action,
            share_access_token=share_access_token,
        ), 400
    except Exception as exc:
        logger.exception("Błąd przetwarzania formularza: %s", exc)
        flash("Wystąpił błąd podczas przetwarzania formularza.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=form_definition_for_stage(form_config, FIELD_STAGE_INITIAL),
            errors={},
            values=request_data or request.form,
            form_version_id=form_version.id if form_version else None,
            form_version_token=form_version_token,
        ), 500


@bp.route("/form/<slug>/correction/<submission_id>", methods=["GET", "POST"])
def correct_submission(slug: str, submission_id: str):
    """Allow a participant to refill only a submission explicitly returned by an administrator."""
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    services = get_services()
    with session_factory() as db:
        form = db.execute(
            select(Form).where(
                Form.slug == slug,
                Form.is_active.is_(True),
                Form.is_public.is_(True),
            )
        ).scalar_one_or_none()
        submission = db.execute(
            select(FormSubmission).where(FormSubmission.submission_id == submission_id)
        ).scalar_one_or_none()
        if not form or not submission or submission.form_slug != slug:
            abort(404)
        access = require_participant_submission_access(
            submission_id,
            slug=slug,
            submission={
                "submission_id": submission.submission_id,
                "form_slug": submission.form_slug,
                "access_token": submission.access_token,
            },
        )
        token = access.credential
        if submission.process_status != ProcessStatus.RETURNED_FOR_CORRECTION.value:
            abort(404)
        version = services.form_version_service.resolve_for_submission(db, submission)
        if version:
            form_meta, form_config = version_to_public_form(db, form, version)
        else:
            fields = db.execute(
                select(FormField)
                .where(FormField.form_id == form.id, FormField.active.is_(True))
                .order_by(FormField.sort_order, FormField.id)
            ).scalars().all()
            form_meta = form_to_public_meta(form)
            form_config = form_to_definition(form, fields)
        stored_values = {
            key: value
            for key, value in dict(submission.data_json or {}).items()
            if not str(key).startswith("_")
        }
        correction_message = submission.correction_message

    initial_form_config = form_definition_for_stage(form_config, FIELD_STAGE_INITIAL)
    form_action = url_for(
        "public_forms.correct_submission",
        slug=slug,
        submission_id=submission_id,
        token=token,
    )
    if request.method == "GET":
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=initial_form_config,
            errors={},
            values=stored_values,
            form_action=form_action,
            correction_mode=True,
            correction_message=correction_message,
            access_token=token,
        )

    request_data = request.get_json(silent=True) if request.is_json else request.form
    csrf_valid, _csrf_missing = _valid_public_csrf(request_data or {})
    if not csrf_valid:
        abort(400, description="Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.")
    result = services.submission_service.submit_correction_form(
        slug,
        initial_form_config,
        request_data or {},
        submission_id=submission_id,
        access_token=token,
    )
    if not result["ok"]:
        flash("Formularz zawiera błędy. Popraw wskazane pola.", "error")
        return render_template(
            "form_page.html",
            slug=slug,
            form_meta=form_meta,
            form_definition=initial_form_config,
            errors=result["errors"],
            values=result["values"],
            form_action=form_action,
            correction_mode=True,
            correction_message=correction_message,
            access_token=token,
        ), 400
    return render_template("result.html", result=result["result"])


@bp.get("/kontakt")
def contact_page():
    session_factory = db_session_factory()
    page = None
    if session_factory:
        with session_factory() as db:
            page = db.execute(select(ContactPage).order_by(ContactPage.id)).scalar_one_or_none()
            if page:
                ensure_contact_defaults(page)
                page.phones = normalized_phones(page.phones)
    return render_template("contact.html", page=page)


@bp.get("/dokumenty/<document_type>")
def service_document_page(document_type: str):
    if document_type not in SERVICE_DOCUMENT_TYPES:
        abort(404)
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        document = db.execute(select(ServiceDocument).where(ServiceDocument.document_type == document_type)).scalar_one_or_none()
        if not document:
            return render_template("service_document.html", document=None, title=SERVICE_DOCUMENT_TYPES[document_type]), 404
        return render_template("service_document.html", document=document, title=document.title)


@bp.get("/dokumenty/<document_type>/plik")
def service_document_file(document_type: str):
    if document_type not in SERVICE_DOCUMENT_TYPES:
        abort(404)
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        document = db.execute(select(ServiceDocument).where(ServiceDocument.document_type == document_type)).scalar_one_or_none()
        if not document or not document.storage_path:
            abort(404)
        path = Path(document.storage_path)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=document.mime_type or None, download_name=document.original_filename)


@bp.get("/form/<slug>/regulamin")
def form_regulation_file(slug: str):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        form = db.execute(
            select(Form).where(
                Form.slug == slug,
                Form.is_active.is_(True),
                Form.is_public.is_(True),
            )
        ).scalar_one_or_none()
        if not form:
            abort(404)
        version_service = get_services().form_version_service
        if version_service.has_versions(db, form.id) and not version_service.resolve_published(db, form.id):
            abort(404)
        regulation = form.regulation
        if not regulation:
            abort(404)
        path = Path(regulation.storage_path)
        if not path.exists():
            abort(404)
        return send_file(path, mimetype=regulation.mime_type or None, download_name=regulation.original_filename)


@bp.get("/form/<slug>/regulamin/version/<int:regulation_version_id>")
def form_regulation_version_file(slug: str, regulation_version_id: int):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        regulation_version = db.get(FormRegulationVersion, regulation_version_id)
        if (
            not regulation_version
            or regulation_version.form.slug != slug
            or not regulation_version.form.is_active
            or not regulation_version.form.is_public
        ):
            abort(404)
        path = Path(regulation_version.storage_path)
        if not path.is_file():
            abort(404)
        return send_file(
            path,
            mimetype=regulation_version.mime_type or None,
            download_name=regulation_version.original_filename,
        )


@bp.get("/assets/logos/<int:logo_id>/<path:filename>")
def logo_asset(logo_id: int, filename: str):
    session_factory = db_session_factory()
    if not session_factory:
        abort(404)
    with session_factory() as db:
        logo = db.get(Logo, logo_id)
        if not logo or not logo.active or Path(logo.filename).name != Path(filename).name:
            abort(404)
        logo_path = Path(logo.storage_path)
        if not logo_path.exists():
            abort(404)
        return send_file(logo_path, mimetype=logo.mime_type or None)


def list_public_db_forms() -> list[dict]:
    session_factory = db_session_factory()
    if not session_factory:
        return []
    with session_factory() as db:
        forms = db.execute(
            select(Form)
            .where(Form.is_active.is_(True), Form.is_public.is_(True), Form.is_listed.is_(True))
            .order_by(Form.sort_order, Form.name)
        ).scalars().all()
        result = []
        version_service = get_services().form_version_service
        for form in forms:
            if version_service.has_versions(db, form.id):
                version = version_service.resolve_published(db, form.id)
                if not version:
                    continue
                meta, _definition = version_to_public_form(db, form, version)
                result.append(meta)
            else:
                result.append(form_to_public_meta(form))
        return result




def get_public_db_form(
    slug: str,
    access_token: str = "",
) -> tuple[dict | None, dict | None]:
    form_meta, form_config, _version = get_public_db_form_versioned(
        slug,
        access_token=access_token,
    )
    return form_meta, form_config


def get_public_db_form_versioned(
    slug: str,
    *,
    version_token: str = "",
    require_version_token: bool = False,
    access_token: str = "",
) -> tuple[dict | None, dict | None, FormVersion | None]:

    session_factory = db_session_factory()

    if not session_factory:
        return None, None, None

    with session_factory() as db:
        form = db.execute(
            select(Form).where(
                Form.slug == slug,
                Form.is_active.is_(True),
                Form.is_public.is_(True),
            )
        ).scalar_one_or_none()

        if not form:
            return None, None, None

        # Formularz typu "Tylko przez link"
        if not form.is_listed:
            if not verify_share_token(
                form.share_token_hash,
                access_token,
            ):
                return None, None, None

        version_service = get_services().form_version_service

        if version_service.has_versions(db, form.id):

            if version_token:
                version_id = _version_id_from_token(
                    slug,
                    version_token,
                )

                version = (
                    version_service.resolve_rendered_version(
                        db,
                        form,
                        version_id,
                    )
                    if version_id
                    else None
                )

            elif require_version_token:
                version = None

            else:
                version = version_service.resolve_published(
                    db,
                    form.id,
                )

            if not version:
                return None, None, None

            meta, definition = version_to_public_form(
                db,
                form,
                version,
            )

            return meta, definition, version

        fields = db.execute(
            select(FormField)
            .where(
                FormField.form_id == form.id,
                FormField.active.is_(True),
            )
            .order_by(
                FormField.sort_order,
                FormField.id,
            )
        ).scalars().all()

        return (
            form_to_public_meta(form),
            form_to_definition(form, fields),
            None,
        )

def version_to_public_form(db, form: Form, version: FormVersion) -> tuple[dict, dict]:
    definition = dict(version.definition_json or {})
    metadata = definition.get("_form_metadata") if isinstance(definition.get("_form_metadata"), dict) else {}
    logo_id = metadata.get("logo_id", form.logo_id)
    logo = db.get(Logo, logo_id) if logo_id else None
    project_logo_id = metadata.get("project_logo_id")
    project_logo = db.get(Logo, project_logo_id) if project_logo_id else None
    definition["logo_url"] = logo_url(logo)
    definition["project_logo_url"] = logo_url(project_logo)
    definition["regulation_url"] = (
        url_for(
            "public_forms.form_regulation_version_file",
            slug=form.slug,
            regulation_version_id=version.regulation_version_id,
        )
        if version.regulation_version_id
        else ""
    )
    definition["logo_alignment"] = normalize_logo_alignment(
        str(metadata.get("logo_alignment") or definition.get("logo_alignment") or "left")
    )
    normalized = normalize_form_definition(definition)
    return form_to_public_meta(form, definition=normalized, logo=logo), normalized


def form_to_public_meta(form: Form, *, definition: dict | None = None, logo: Logo | None = None) -> dict:
    definition = definition or {}
    logo_alignment = normalize_logo_alignment(str(definition.get("logo_alignment") or form.logo_alignment))
    return {
        "slug": form.slug,
        "title": definition.get("title") or form.title or form.name,
        "description": definition.get("description", form.description),
        "label_text": definition.get("label_text", form.label_text),
        "label_variant": definition.get("label_variant", form.label_variant),
        "label_color": definition.get("label_color", form.label_color),
        "label_background": definition.get("label_background", form.label_background),
        "logo_url": logo_url(logo if definition else form.logo),
        "project_logo_url": definition.get("project_logo_url") if definition else logo_url(form.project_logo),
        "logo_alignment": logo_alignment,
        "regulation_url": url_for("public_forms.form_regulation_file", slug=form.slug) if form.regulation else "",
    }


def form_to_definition(form: Form, fields: list[FormField]) -> dict:
    definition = dict(form.definition_json or {})
    logo_alignment = normalize_logo_alignment(form.logo_alignment)
    original_fields = {
        field.get("name"): dict(field)
        for field in definition.get("fields", [])
        if isinstance(field, dict) and field.get("name")
    }
    definition["title"] = form.title or form.name
    definition["description"] = form.description
    definition["fields"] = form_fields_to_definition(fields, original_fields)
    definition["logo_url"] = logo_url(form.logo)
    definition["project_logo_url"] = logo_url(form.project_logo)
    definition["label_text"] = form.label_text
    definition["label_color"] = form.label_color
    definition["label_background"] = form.label_background
    definition["regulation_url"] = url_for("public_forms.form_regulation_file", slug=form.slug) if form.regulation else ""
    definition["logo_alignment"] = logo_alignment
    return normalize_form_definition(definition)


def form_fields_to_definition(fields: list[FormField], original_fields: dict[str, dict]) -> list[dict]:
    rendered_fields = []
    current_section = None
    for field in fields:
        if field.section and field.section != current_section:
            rendered_fields.append({"type": "section", "label": field.section})
            current_section = field.section
        field_config = dict(original_fields.get(field.name, {}))
        field_config.update(
            {
                "name": field.name,
                "label": field.label or field.name,
                "type": field.type or "text",
                "required": bool(field.required),
                "options": normalize_field_options(field.type, field.options),
                "default": field.default_value,
                "stage": field.stage or FIELD_STAGE_INITIAL,
                "availability": list(field.availability_json or field_config.get("availability") or []),
            }
        )
        rendered_fields.append(field_config)
    return rendered_fields


def normalize_field_options(field_type: str, options) -> list:
    if not isinstance(options, list):
        return []
    if field_type == "checkbox":
        normalized = []
        for option in options:
            if isinstance(option, dict):
                value = str(option.get("value") or option.get("label") or "").strip()
                label = str(option.get("label") or value).strip()
            else:
                value = label = str(option or "").strip()
            if value:
                normalized.append({"value": value, "label": label})
        return normalized
    normalized = []
    for option in options:
        if isinstance(option, dict):
            value = str(option.get("value") or option.get("label") or "").strip()
        else:
            value = str(option or "").strip()
        if value:
            normalized.append(value)
    return normalized


def logo_url(logo: Logo | None) -> str:
    if not logo or not logo.active:
        return ""
    return url_for("public_forms.logo_asset", logo_id=logo.id, filename=Path(logo.filename).name)


def normalize_logo_alignment(value: str | None) -> str:
    return value if value in {"left", "center", "right"} else "left"
