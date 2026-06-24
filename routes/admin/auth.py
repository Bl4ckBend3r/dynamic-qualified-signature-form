from __future__ import annotations

import secrets
from functools import wraps

from flask import abort, flash, g, redirect, render_template, request, session, url_for
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
    return {"admin_csrf_token": csrf_token, "admin_is_active": admin_is_active}


def admin_is_active(*endpoints: str) -> bool:
    return request.endpoint in endpoints


@bp.get("/")
def admin_index():
    if session.get("admin_user_id"):
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.post("/")
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    with db_session_factory()() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user or user.is_blocked or not user.is_active or not check_password_hash(user.password_hash, password):
            flash("Nieprawidlowy login lub haslo.", "error")
            return render_template("admin/login.html", email=email), 401
        session["admin_user_id"] = user.id
    return redirect(request.args.get("next") or url_for("admin.dashboard"))


@bp.get("/logout")
@login_required
def logout():
    session.pop("admin_user_id", None)
    flash("Wylogowano.", "success")
    return redirect(url_for("admin.admin_index"))
