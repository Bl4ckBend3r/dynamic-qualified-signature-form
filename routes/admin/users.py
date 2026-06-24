from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from models import EmailLog, Form, FormPermission, Logo, User

from . import ROLE_FORM_MANAGER, ROLE_SUPER_ADMIN, ROLES, bp, db_session_factory, login_required, role_required


@bp.route("/users")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def users_list():
    with db_session_factory()() as db:
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        return render_template("admin/users/list.html", users=users, forms=forms)


@bp.post("/users/<int:user_id>/toggle-block")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_toggle_block(user_id: int):
    with db_session_factory()() as db:
        user = db.get(User, user_id) or abort(404)
        if user.id == g.admin_user.id:
            flash("Nie mozna zablokowac aktualnie zalogowanego uzytkownika.", "error")
            return redirect(url_for("admin.users_list"))
        user.is_blocked = not user.is_blocked
        db.commit()
        is_blocked = user.is_blocked
    flash("Uzytkownik zostal zablokowany." if is_blocked else "Uzytkownik zostal odblokowany.", "success")
    return redirect(url_for("admin.users_list"))


@bp.post("/users/<int:user_id>/delete")
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_delete(user_id: int):
    with db_session_factory()() as db:
        user = db.get(User, user_id) or abort(404)
        if user.id == g.admin_user.id:
            flash("Nie mozna usunac aktualnie zalogowanego uzytkownika.", "error")
            return redirect(url_for("admin.users_list"))
        db.execute(Form.__table__.update().where(Form.created_by_id == user.id).values(created_by_id=None))
        db.execute(Logo.__table__.update().where(Logo.uploaded_by_user_id == user.id).values(uploaded_by_user_id=None))
        db.execute(EmailLog.__table__.update().where(EmailLog.sent_by_id == user.id).values(sent_by_id=None))
        db.execute(FormPermission.__table__.delete().where(FormPermission.user_id == user.id))
        db.delete(user)
        db.commit()
    flash("Uzytkownik zostal usuniety.", "success")
    return redirect(url_for("admin.users_list"))


@bp.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_change_password(user_id: int):
    with db_session_factory()() as db:
        user = db.get(User, user_id) or abort(404)
        if request.method == "POST":
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")
            if not password:
                flash("Podaj nowe haslo.", "error")
                return render_template("admin/users/password.html", user=user), 400
            if password != password_confirm:
                flash("Hasla nie sa takie same.", "error")
                return render_template("admin/users/password.html", user=user), 400
            user.password_hash = generate_password_hash(password)
            db.commit()
            flash("Haslo zostalo zmienione.", "success")
            return redirect(url_for("admin.users_list"))
        return render_template("admin/users/password.html", user=user)


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_SUPER_ADMIN)
def user_edit(user_id: int | None = None):
    with db_session_factory()() as db:
        user = db.get(User, user_id) if user_id else User(email="", password_hash="", role=ROLE_FORM_MANAGER)
        if not user:
            abort(404)
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        if request.method == "POST":
            user.email = request.form.get("email", "").strip().lower()
            user.role = request.form.get("role", ROLE_FORM_MANAGER)
            if user.role not in ROLES:
                abort(400)
            password = request.form.get("password", "")
            if password:
                user.password_hash = generate_password_hash(password)
            if not user.password_hash:
                flash("Haslo jest wymagane dla nowego uzytkownika.", "error")
                return render_template(
                    "admin/users/edit.html",
                    user=user,
                    roles=sorted(ROLES),
                    forms=forms,
                    assigned_form_ids=set(),
                ), 400
            user.is_active = request.form.get("is_active") == "on"
            user.is_blocked = request.form.get("is_blocked") == "on"
            db.add(user)
            db.flush()
            selected_form_ids = {int(item) for item in request.form.getlist("form_ids") if item.isdigit()}
            existing = {permission.form_id: permission for permission in user.permissions}
            for form in forms:
                if form.id in selected_form_ids and form.id not in existing:
                    db.add(FormPermission(user_id=user.id, form_id=form.id, can_manage=True))
                if form.id not in selected_form_ids and form.id in existing:
                    db.delete(existing[form.id])
            db.commit()
            flash("Uzytkownik zostal zapisany.", "success")
            return redirect(url_for("admin.users_list"))
        assigned_form_ids = {permission.form_id for permission in user.permissions}
        return render_template("admin/users/edit.html", user=user, roles=sorted(ROLES), forms=forms, assigned_form_ids=assigned_form_ids)
