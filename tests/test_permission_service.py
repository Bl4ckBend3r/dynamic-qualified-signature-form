from __future__ import annotations

import pytest
import importlib
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import (
    AccessRole, AccessRolePermission, Base, Form, FormPermission, FormSubmission,
    FormUserRole, Permission, RoleAssignmentAudit, User, UserGlobalRole,
)
from services.admin_submission_service import build_submission_detail_sections
from services.permission_service import FORM_PERMISSIONS, GLOBAL_PERMISSIONS, PermissionService


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _user(email: str, role: str = "form_manager") -> User:
    return User(email=email, password_hash="hash", role=role, is_active=True, is_blocked=False)


def _form(slug: str) -> Form:
    return Form(slug=slug, name=slug, title=slug, definition_json={})


def _grant(db, user: User, form: Form | None, *permission_keys: str, global_scope: bool = False):
    scope = "global" if global_scope else "form"
    role = AccessRole(key=f"role_{user.email}_{scope}_{len(permission_keys)}", name="Test", scope=scope, is_active=True)
    db.add(role)
    db.flush()
    for key in permission_keys:
        permission = db.execute(select(Permission).where(Permission.key == key)).scalar_one_or_none()
        if permission is None:
            permission = Permission(key=key, name=key, scope=scope, category=scope, is_active=True)
            db.add(permission)
            db.flush()
        db.add(AccessRolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()
    if global_scope:
        db.add(UserGlobalRole(user_id=user.id, role_id=role.id))
    else:
        db.add(FormUserRole(user_id=user.id, form_id=form.id, role_id=role.id))
    db.flush()
    return role


def test_form_role_allows_only_declared_permission_and_form(db):
    user, first, second = _user("reviewer@example.org"), _form("first"), _form("second")
    db.add_all([user, first, second])
    db.flush()
    _grant(db, user, first, "can_view_submissions", "can_review")
    service = PermissionService()

    assert service.has_permission(db, user, "can_review", form=first)
    assert not service.has_permission(db, user, "can_make_decision", form=first)
    assert not service.has_permission(db, user, "can_review", form=second)
    assert not service.has_permission(db, user, "can_manage_users", form=first)


def test_form_ids_with_permission_excludes_role_without_submission_view(db):
    user, visible, workflow_only = _user("scoped@example.org"), _form("visible"), _form("workflow-only")
    db.add_all([user, visible, workflow_only])
    db.flush()
    _grant(db, user, visible, "can_view_submissions")
    _grant(db, user, workflow_only, "can_edit_workflow", "can_edit_form")

    assert PermissionService().form_ids_with_permission(
        db, user, "can_view_submissions"
    ) == [visible.id]


@pytest.mark.parametrize("permission_key", FORM_PERMISSIONS)
def test_each_form_permission_has_allowed_and_denied_case(db, permission_key):
    allowed, denied, form = _user(f"allowed-{permission_key}@example.org"), _user(f"denied-{permission_key}@example.org"), _form(f"form-{permission_key}")
    db.add_all([allowed, denied, form])
    db.flush()
    _grant(db, allowed, form, permission_key)
    service = PermissionService()
    assert service.has_permission(db, allowed, permission_key, form=form)
    assert not service.has_permission(db, denied, permission_key, form=form)


@pytest.mark.parametrize("permission_key", GLOBAL_PERMISSIONS)
def test_each_global_permission_has_allowed_and_denied_case(db, permission_key):
    allowed, denied = _user(f"global-{permission_key}@example.org"), _user(f"none-{permission_key}@example.org")
    db.add_all([allowed, denied])
    db.flush()
    _grant(db, allowed, None, permission_key, global_scope=True)
    service = PermissionService()
    assert service.has_permission(db, allowed, permission_key)
    assert not service.has_permission(db, denied, permission_key)


def test_global_role_does_not_grant_form_access(db):
    user, form = _user("global@example.org"), _form("form")
    db.add_all([user, form])
    db.flush()
    _grant(db, user, None, "can_manage_users", "can_manage_roles", global_scope=True)
    service = PermissionService()

    assert service.has_permission(db, user, "can_manage_users")
    assert not service.has_permission(db, user, "can_view_submissions", form=form)


def test_super_admin_has_all_permissions_without_assignment_rows(db):
    user, form = _user("root@example.org", role="super_admin"), _form("form")
    db.add_all([user, form])
    db.flush()

    assert PermissionService().has_permission(db, user, "can_sign_office_agreement", form=form)
    assert PermissionService().has_permission(db, user, "can_manage_roles")


def test_legacy_flags_are_deterministic_fallback(db):
    user, form = _user("legacy@example.org"), _form("legacy")
    db.add_all([user, form])
    db.flush()
    db.add(FormPermission(
        user_id=user.id, form_id=form.id, can_manage=False, can_review=True,
        can_make_decision=False, can_view_sensitive_data=False,
    ))
    db.flush()
    service = PermissionService()

    assert service.has_permission(db, user, "can_view_submissions", form=form)
    assert service.has_permission(db, user, "can_review", form=form)
    assert not service.has_permission(db, user, "can_view_sensitive_data", form=form)


def test_role_replacement_is_audited_and_prevents_self_lockout(db):
    actor, target, form = _user("actor@example.org"), _user("target@example.org"), _form("form")
    db.add_all([actor, target, form])
    db.flush()
    role = _grant(db, actor, None, "can_manage_roles", global_scope=True)
    service = PermissionService()

    with pytest.raises(ValueError, match="odebrać sobie"):
        service.replace_global_roles(db, target=actor, role_ids=[], actor=actor)

    form_role = AccessRole(key="read_only_test", name="Read", scope="form", is_active=True)
    db.add(form_role)
    db.flush()
    service.replace_form_roles(db, target=target, form=form, role_ids=[form_role.id], actor=actor)
    db.flush()
    audit = db.execute(select(RoleAssignmentAudit)).scalar_one()
    assert (audit.action, audit.actor_user_id, audit.target_user_id, audit.form_id) == (
        "granted", actor.id, target.id, form.id,
    )
    assert role.id is not None
    audit.action = "tampered"
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_removing_last_rbac_role_does_not_restore_legacy_access(db):
    actor, target, form = _user("root@example.org", "super_admin"), _user("target2@example.org"), _form("revoke")
    db.add_all([actor, target, form])
    db.flush()
    db.add(FormPermission(user_id=target.id, form_id=form.id, can_manage=False, can_review=True))
    role = _grant(db, target, form, "can_view_submissions")
    service = PermissionService()
    service.replace_form_roles(db, target=target, form=form, role_ids=[], actor=actor)
    db.flush()

    assert db.execute(select(FormPermission).where(FormPermission.user_id == target.id)).scalar_one_or_none() is None
    assert not service.has_permission(db, target, "can_view_submissions", form=form)
    assert role.id is not None


def test_sensitive_values_are_masked_in_backend_view_context(db):
    form = Form(
        slug="sensitive", name="Sensitive", title="Sensitive",
        definition_json={"fields": [
            {"name": "numer_sprawy", "label": "Numer", "data_classification": "normal"},
            {"name": "diagnoza", "label": "Diagnoza", "data_classification": "sensitive"},
        ]},
    )
    submission = FormSubmission(
        submission_id="SUB-1", form_slug=form.slug, form_name=form.name,
        data_json={"numer_sprawy": "ABC", "diagnoza": "tajna diagnoza"}, access_token="token",
        pesel="90010112345", email="person@example.org",
    )
    db.add_all([form, submission])
    db.flush()

    view = build_submission_detail_sections(form, submission, include_sensitive=False)
    rendered = repr(view)
    assert "tajna diagnoza" not in rendered
    assert "90010112345" not in rendered
    assert "person@example.org" not in rendered
    assert "ABC" in rendered
    assert "Dane ukryte" in rendered
    assert "token" not in rendered


def test_rbac_migration_backfills_legacy_permissions_without_overgrant(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260818_0041_role_based_permissions")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("role", sa.String(64)))
    sa.Table("forms", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("form_fields", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("submission_files", metadata, sa.Column("id", sa.Integer, primary_key=True))
    legacy = sa.Table(
        "form_permissions", metadata,
        sa.Column("user_id", sa.Integer), sa.Column("form_id", sa.Integer),
        sa.Column("can_manage", sa.Boolean), sa.Column("can_review", sa.Boolean),
        sa.Column("can_make_decision", sa.Boolean), sa.Column("can_view_sensitive_data", sa.Boolean),
        sa.Column("can_assign_submissions", sa.Boolean), sa.Column("can_view_internal_notes", sa.Boolean),
        sa.Column("can_add_internal_notes", sa.Boolean), sa.Column("can_manage_internal_notes", sa.Boolean),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users(id, role) VALUES (1, 'form_manager')"))
        connection.execute(sa.text("INSERT INTO users(id, role) VALUES (2, 'admin')"))
        connection.execute(sa.text("INSERT INTO forms(id) VALUES (10)"))
        connection.execute(legacy.insert().values(
            user_id=1, form_id=10, can_manage=True, can_review=True, can_make_decision=False,
            can_view_sensitive_data=False, can_assign_submissions=False,
            can_view_internal_notes=True, can_add_internal_notes=False, can_manage_internal_notes=False,
        ))
        connection.execute(legacy.insert().values(
            user_id=2, form_id=10, can_manage=True, can_review=False, can_make_decision=False,
            can_view_sensitive_data=False, can_assign_submissions=False,
            can_view_internal_notes=False, can_add_internal_notes=False, can_manage_internal_notes=False,
        ))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        keys = set(connection.execute(sa.text("""
            SELECT p.key FROM form_user_roles fur
            JOIN access_role_permissions arp ON arp.role_id = fur.role_id
            JOIN permissions p ON p.id = arp.permission_id
            WHERE fur.user_id = 1 AND fur.form_id = 10
        """)).scalars())
        assert {"can_view_submissions", "can_review", "can_view_internal_notes"} <= keys
        assert "can_make_decision" not in keys
        assert "can_edit_form" not in keys
        assert "can_add_internal_notes" not in keys
        admin_keys = set(connection.execute(sa.text("""
            SELECT p.key FROM form_user_roles fur
            JOIN access_role_permissions arp ON arp.role_id = fur.role_id
            JOIN permissions p ON p.id = arp.permission_id
            WHERE fur.user_id = 2 AND fur.form_id = 10
        """)).scalars())
        assert set(migration.FORM_PERMISSIONS) <= admin_keys
