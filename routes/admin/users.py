from __future__ import annotations

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from werkzeug.security import generate_password_hash

from models import (
    AccessRole, AccessRolePermission, EmailLog, Form, FormPermission, FormSubmission, FormUserRole, Logo,
    Permission, RoleAssignmentAudit,
    SubmissionAssignmentHistory, SubmissionInternalNote, SubmissionInternalNoteRevision,
    User, UserGlobalRole,
)
from services.permission_service import PermissionService

from . import ROLE_FORM_MANAGER, ROLES, bp, db_session_factory, login_required, permission_required


@bp.route("/users")
@login_required
@permission_required("can_manage_users")
def users_list():
    with db_session_factory()() as db:
        users = db.execute(select(User).order_by(User.email)).scalars().all()
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        return render_template("admin/users/list.html", users=users, forms=forms)


@bp.post("/users/<int:user_id>/toggle-block")
@login_required
@permission_required("can_manage_users")
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
@permission_required("can_manage_users")
def user_delete(user_id: int):
    with db_session_factory()() as db:
        user = db.get(User, user_id) or abort(404)
        if user.id == g.admin_user.id:
            flash("Nie mozna usunac aktualnie zalogowanego uzytkownika.", "error")
            return redirect(url_for("admin.users_list"))
        assignment_references = db.execute(
            select(func.count(SubmissionAssignmentHistory.id)).where(
                or_(
                    SubmissionAssignmentHistory.assigned_to_user_id == user.id,
                    SubmissionAssignmentHistory.assigned_by_user_id == user.id,
                    SubmissionAssignmentHistory.previous_user_id == user.id,
                )
            )
        ).scalar() or 0
        current_cases = db.execute(
            select(func.count(FormSubmission.id)).where(
                or_(FormSubmission.assigned_to_user_id == user.id, FormSubmission.assigned_by_user_id == user.id)
            )
        ).scalar() or 0
        note_references = db.execute(
            select(func.count(SubmissionInternalNote.id)).where(
                or_(SubmissionInternalNote.author_user_id == user.id, SubmissionInternalNote.archived_by_user_id == user.id)
            )
        ).scalar() or 0
        revision_references = db.execute(
            select(func.count(SubmissionInternalNoteRevision.id)).where(
                SubmissionInternalNoteRevision.edited_by_user_id == user.id
            )
        ).scalar() or 0
        role_audit_references = db.execute(
            select(func.count(RoleAssignmentAudit.id)).where(RoleAssignmentAudit.target_user_id == user.id)
        ).scalar() or 0
        if assignment_references or current_cases or note_references or revision_references or role_audit_references:
            flash("Nie można usunąć użytkownika powiązanego z historią spraw, notatek lub audytem uprawnień.", "error")
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
@permission_required("can_manage_users")
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
            policy_result = current_app.extensions["services"].password_policy_service.validate(password)
            if not policy_result.valid:
                flash(policy_result.errors[0], "error")
                return render_template("admin/users/password.html", user=user), 400
            user.password_hash = generate_password_hash(password)
            db.commit()
            flash("Haslo zostalo zmienione.", "success")
            return redirect(url_for("admin.users_list"))
        return render_template("admin/users/password.html", user=user)


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
@permission_required("can_manage_roles")
def user_edit(user_id: int | None = None):
    with db_session_factory()() as db:
        user = db.get(User, user_id) if user_id else User(email="", password_hash="", role=ROLE_FORM_MANAGER)
        if not user:
            abort(404)
        forms = db.execute(select(Form).order_by(Form.name)).scalars().all()
        form_roles = db.execute(select(AccessRole).where(
            AccessRole.scope == "form", AccessRole.is_active.is_(True)
        ).order_by(AccessRole.name)).scalars().all()
        global_roles = db.execute(select(AccessRole).where(
            AccessRole.scope == "global", AccessRole.is_active.is_(True)
        ).order_by(AccessRole.name)).scalars().all()
        if request.method == "POST":
            previous_role = user.role
            user.email = request.form.get("email", "").strip().lower()
            user.role = request.form.get("role", ROLE_FORM_MANAGER)
            if user.role not in ROLES:
                abort(400)
            if user.id == g.admin_user.id and previous_role == "super_admin" and user.role != "super_admin":
                flash("Nie można odebrać sobie roli superadministratora.", "error")
                return redirect(url_for("admin.user_edit", user_id=user.id))
            password = request.form.get("password", "")
            if password:
                policy_result = current_app.extensions["services"].password_policy_service.validate(password)
                if not policy_result.valid:
                    flash(policy_result.errors[0], "error")
                    return render_template(
                        "admin/users/edit.html",
                        user=user,
                        roles=sorted(ROLES),
                        forms=forms,
                        form_roles=form_roles,
                        global_roles=global_roles,
                        assigned_role_ids={},
                        assigned_global_role_ids=set(),
                    ), 400
                user.password_hash = generate_password_hash(password)
            if not user.password_hash:
                flash("Haslo jest wymagane dla nowego uzytkownika.", "error")
                return render_template(
                    "admin/users/edit.html",
                    user=user,
                    roles=sorted(ROLES),
                    forms=forms,
                    form_roles=form_roles, global_roles=global_roles,
                    assigned_role_ids={}, assigned_global_role_ids=set(),
                ), 400
            user.is_active = request.form.get("is_active") == "on"
            user.is_blocked = request.form.get("is_blocked") == "on"
            if user.id == g.admin_user.id and (not user.is_active or user.is_blocked):
                flash("Nie można zablokować ani dezaktywować aktualnie zalogowanego użytkownika.", "error")
                return redirect(url_for("admin.user_edit", user_id=user.id))
            db.add(user)
            db.flush()
            permission_service = PermissionService()
            for form in forms:
                role_ids = [int(item) for item in request.form.getlist(f"form_role_ids_{form.id}") if item.isdigit()]
                permission_service.replace_form_roles(
                    db, target=user, form=form, role_ids=role_ids, actor=g.admin_user
                )
            global_role_ids = [int(item) for item in request.form.getlist("global_role_ids") if item.isdigit()]
            permission_service.replace_global_roles(
                db, target=user, role_ids=global_role_ids, actor=g.admin_user
            )
            db.commit()
            flash("Uzytkownik zostal zapisany.", "success")
            return redirect(url_for("admin.users_list"))
        assigned_role_ids: dict[int, set[int]] = {}
        for assignment in db.execute(select(FormUserRole).where(FormUserRole.user_id == user.id)).scalars():
            assigned_role_ids.setdefault(assignment.form_id, set()).add(assignment.role_id)
        assigned_global_role_ids = set(db.execute(select(UserGlobalRole.role_id).where(UserGlobalRole.user_id == user.id)).scalars())
        return render_template(
            "admin/users/edit.html", user=user, roles=sorted(ROLES), forms=forms,
            form_roles=form_roles, global_roles=global_roles,
            assigned_role_ids=assigned_role_ids, assigned_global_role_ids=assigned_global_role_ids,
        )


@bp.get("/roles")
@login_required
@permission_required("can_manage_roles")
def roles_list():
    with db_session_factory()() as db:
        roles = db.execute(select(AccessRole).order_by(AccessRole.scope, AccessRole.name)).scalars().all()
        return render_template("admin/users/roles.html", roles=roles)


@bp.route("/roles/new", methods=["GET", "POST"])
@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_roles")
def role_edit(role_id: int | None = None):
    with db_session_factory()() as db:
        role = db.get(AccessRole, role_id) if role_id else AccessRole(key="", name="", scope="form")
        if not role:
            abort(404)
        if request.method == "POST":
            if role.is_system:
                abort(403)
            scope = str(request.form.get("scope") or "form")
            if scope not in {"form", "global"}:
                abort(400)
            if role.id and scope != role.scope:
                flash("Zakresu istniejącej roli nie można zmienić.", "error")
                return redirect(request.url)
            key = "".join(char if char.isalnum() else "_" for char in str(request.form.get("key") or "").lower()).strip("_")
            name = str(request.form.get("name") or "").strip()
            if not key or not name:
                flash("Klucz i nazwa roli są wymagane.", "error")
                return redirect(request.url)
            duplicate = db.execute(select(AccessRole).where(AccessRole.key == key, AccessRole.id != (role.id or 0))).scalar_one_or_none()
            if duplicate:
                flash("Rola o takim kluczu już istnieje.", "error")
                return redirect(request.url)
            role.key, role.name, role.scope = key, name, scope
            role.is_active = request.form.get("is_active") == "on"
            db.add(role)
            db.flush()
            permission_ids = {int(item) for item in request.form.getlist("permission_ids") if item.isdigit()}
            allowed = set(db.execute(select(Permission.id).where(Permission.scope == scope, Permission.is_active.is_(True))).scalars())
            if not permission_ids <= allowed:
                abort(400)
            db.execute(AccessRolePermission.__table__.delete().where(AccessRolePermission.role_id == role.id))
            for permission_id in sorted(permission_ids):
                db.add(AccessRolePermission(role_id=role.id, permission_id=permission_id))
            db.commit()
            flash("Rola została zapisana.", "success")
            return redirect(url_for("admin.roles_list"))
        permissions = db.execute(select(Permission).where(Permission.is_active.is_(True)).order_by(Permission.category, Permission.name)).scalars().all()
        selected_ids = {link.permission_id for link in role.permission_links} if role.id else set()
        return render_template("admin/users/role_edit.html", role=role, permissions=permissions, selected_ids=selected_ids)
