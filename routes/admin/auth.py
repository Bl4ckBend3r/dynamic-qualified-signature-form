from __future__ import annotations

import secrets
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash

from models import User

from . import bp, db_session_factory


def csrf_token() -> str:
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


def validate_csrf() -> None:
    if request.method != "POST":
        return
    expected = session.get("admin_csrf_token")
    provided = request.form.get("csrf_token")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_user_id"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user or user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def permission_required(permission: str, *, message: str | None = None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from services.permission_service import PermissionService
            with db_session_factory()() as db:
                user = db.get(User, g.admin_user.id) if g.admin_user else None
                if not PermissionService().has_permission(db, user, permission):
                    abort(
                        403,
                        description=message or "Nie masz uprawnień do wykonania tej operacji.",
                    )
            return view(*args, **kwargs)
        return wrapped
    return decorator


def get_current_user() -> User | None:
    user_id = session.get("admin_user_id")
    if not user_id:
        return None
    with db_session_factory()() as db:
        user = db.get(User, int(user_id))
        if not user or user.is_blocked or not user.is_active:
            session.pop("admin_user_id", None)
            return None
        return user


@bp.before_request
def load_current_user():
    g.admin_user = get_current_user()
    if request.method == "POST":
        validate_csrf()


@bp.app_context_processor
def inject_admin_helpers():
    def template_has_permission(permission: str, form=None, submission=None) -> bool:
        from services.permission_service import PermissionService
        with db_session_factory()() as db:
            user = db.get(User, g.admin_user.id) if g.admin_user else None
            return PermissionService().has_permission(db, user, permission, form=form, submission=submission)

    return {
        "admin_csrf_token": csrf_token,
        "admin_is_active": admin_is_active,
        "has_permission": template_has_permission,
    }


def admin_is_active(*endpoints: str) -> bool:
    return request.endpoint in endpoints


def safe_local_redirect_target(value: str | None) -> str | None:
    target = str(value or "").strip()
    if not target or any(ord(char) < 32 for char in target) or "\\" in target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment))


@bp.get("/")
def admin_index():
    if session.get("admin_user_id"):
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/")
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    client_address = str(request.remote_addr or "unknown")
    limiter = current_app.extensions["services"].admin_login_rate_limit_service
    with db_session_factory()() as db:
        if limiter.is_limited(db, client_address=client_address, email=email):
            flash("Nieprawidlowy login lub haslo.", "error")
            return (
                render_template("admin/login.html", email=email),
                429,
                {"Retry-After": str(limiter.policy.short_window_seconds)},
            )
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user or user.is_blocked or not user.is_active or not check_password_hash(user.password_hash, password):
            limited = limiter.record_failure(
                db,
                client_address=client_address,
                email=email,
            )
            db.commit()
            flash("Nieprawidlowy login lub haslo.", "error")
            status_code = 429 if limited else 401
            if limited:
                return (
                    render_template("admin/login.html", email=email),
                    status_code,
                    {"Retry-After": str(limiter.policy.short_window_seconds)},
                )
            return render_template("admin/login.html", email=email), status_code
        limiter.clear_failures(db, client_address=client_address, email=email)
        db.commit()
        user_id = user.id
    session.clear()
    session["admin_user_id"] = user_id
    csrf_token()
    return redirect(safe_local_redirect_target(request.args.get("next")) or url_for("admin.dashboard"))


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("Wylogowano.", "success")
    return redirect(url_for("admin.admin_index"))
