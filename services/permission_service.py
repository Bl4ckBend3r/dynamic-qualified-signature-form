from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select

from models import (
    AccessRole,
    AccessRolePermission,
    Form,
    FormPermission,
    FormSubmission,
    FormUserRole,
    Permission,
    RoleAssignmentAudit,
    User,
    UserGlobalRole,
)


FORM_PERMISSIONS = (
    "can_view_submissions", "can_review", "can_make_decision", "can_return_for_correction",
    "can_assign_submissions", "can_manage_documents", "can_sign_office_agreement",
    "can_send_email", "can_export_data", "can_edit_form", "can_edit_workflow",
    "can_view_sensitive_data", "can_view_internal_notes", "can_add_internal_notes",
    "can_manage_internal_notes",
)
GLOBAL_PERMISSIONS = ("can_manage_users", "can_manage_site", "can_manage_roles")
ALL_PERMISSIONS = frozenset((*FORM_PERMISSIONS, *GLOBAL_PERMISSIONS))

LEGACY_FLAG_MAP = {
    "can_review": "can_review",
    "can_make_decision": "can_make_decision",
    "can_assign_submissions": "can_assign_submissions",
    "can_view_sensitive_data": "can_view_sensitive_data",
    "can_view_internal_notes": "can_view_internal_notes",
    "can_add_internal_notes": "can_add_internal_notes",
    "can_manage_internal_notes": "can_manage_internal_notes",
}
LEGACY_MANAGE_PERMISSIONS = frozenset(FORM_PERMISSIONS)


class PermissionService:
    """Single source of truth for global and per-form authorization."""

    def has_permission(
        self,
        db,
        user: User | None,
        permission: str,
        *,
        form: Form | int | None = None,
        submission: FormSubmission | None = None,
    ) -> bool:
        if permission not in ALL_PERMISSIONS or not user or not user.is_active or user.is_blocked:
            return False
        if user.role == "super_admin":
            return True
        if permission in GLOBAL_PERMISSIONS:
            return permission in self._global_permission_keys(db, user.id)
        form_id = self.resolve_form_id(db, form=form, submission=submission)
        if form_id is None:
            return False
        role_keys = self._form_permission_keys(db, user.id, form_id)
        if role_keys is not None:
            return permission in role_keys
        return self._legacy_has_permission(db, user, form_id, permission)

    def resolve_form_id(self, db, *, form=None, submission: FormSubmission | None = None) -> int | None:
        if isinstance(form, int):
            return form
        if form is not None:
            return form.id
        if submission is None:
            return None
        if submission.form_version_id:
            from models import FormVersion
            form_id = db.execute(select(FormVersion.form_id).where(FormVersion.id == submission.form_version_id)).scalar_one_or_none()
            if form_id is not None:
                return form_id
        return db.execute(select(Form.id).where(Form.slug == submission.form_slug)).scalar_one_or_none()

    def accessible_form_ids(self, db, user: User) -> list[int]:
        if user.role == "super_admin":
            return list(db.execute(select(Form.id)).scalars())
        ids = set(db.execute(select(FormUserRole.form_id).where(FormUserRole.user_id == user.id)).scalars())
        ids.update(db.execute(select(FormPermission.form_id).where(FormPermission.user_id == user.id)).scalars())
        return sorted(ids)

    def form_ids_with_permission(self, db, user: User, permission: str) -> list[int]:
        """Return only forms where the effective RBAC decision grants ``permission``."""
        return [
            form_id
            for form_id in self.accessible_form_ids(db, user)
            if self.has_permission(db, user, permission, form=form_id)
        ]

    def users_with_permission(self, db, permission: str, form: Form | int) -> list[User]:
        return [
            user for user in db.execute(select(User).where(User.is_active.is_(True), User.is_blocked.is_(False))).scalars()
            if self.has_permission(db, user, permission, form=form)
        ]

    def users_with_form_role(self, db, role_key: str, form_id: int) -> list[User]:
        assigned_ids = select(FormUserRole.user_id).join(
            AccessRole, AccessRole.id == FormUserRole.role_id
        ).where(
            FormUserRole.form_id == form_id,
            AccessRole.key == role_key,
            AccessRole.is_active.is_(True),
        )
        legacy_ids = select(FormPermission.user_id).join(User, User.id == FormPermission.user_id).where(
            FormPermission.form_id == form_id, User.role == role_key
        )
        return list(db.execute(select(User).where(
            User.is_active.is_(True), User.is_blocked.is_(False),
            (User.role == "super_admin") | User.id.in_(assigned_ids) | User.id.in_(legacy_ids),
        )).scalars())

    def replace_form_roles(
        self, db, *, target: User, form: Form, role_ids: Iterable[int], actor: User
    ) -> None:
        wanted = {int(item) for item in role_ids}
        roles = list(db.execute(select(AccessRole).where(
            AccessRole.id.in_(wanted), AccessRole.scope == "form", AccessRole.is_active.is_(True)
        )).scalars()) if wanted else []
        if {role.id for role in roles} != wanted:
            raise ValueError("Wybrano nieprawidłową rolę formularza.")
        existing = {item.role_id: item for item in db.execute(select(FormUserRole).where(
            FormUserRole.user_id == target.id, FormUserRole.form_id == form.id
        )).scalars()}
        for role_id, assignment in existing.items():
            if role_id not in wanted:
                db.delete(assignment)
                self._audit(db, actor, target, role_id, form.id, "revoked")
        for role in roles:
            if role.id not in existing:
                db.add(FormUserRole(user_id=target.id, form_id=form.id, role_id=role.id, granted_by_user_id=actor.id))
                self._audit(db, actor, target, role.id, form.id, "granted")
        # Once this form is managed through RBAC, remove its legacy fallback so
        # revoking the last role cannot silently restore old boolean access.
        db.execute(delete(FormPermission).where(
            FormPermission.user_id == target.id, FormPermission.form_id == form.id
        ))

    def replace_global_roles(self, db, *, target: User, role_ids: Iterable[int], actor: User) -> None:
        wanted = {int(item) for item in role_ids}
        roles = list(db.execute(select(AccessRole).where(
            AccessRole.id.in_(wanted), AccessRole.scope == "global", AccessRole.is_active.is_(True)
        )).scalars()) if wanted else []
        if {role.id for role in roles} != wanted:
            raise ValueError("Wybrano nieprawidłową rolę globalną.")
        if target.id == actor.id and actor.role != "super_admin":
            current = set(self._global_permission_keys(db, actor.id))
            future = self._permission_keys_for_roles(db, wanted)
            if "can_manage_roles" in current and "can_manage_roles" not in future:
                raise ValueError("Nie można odebrać sobie ostatniego uprawnienia do zarządzania rolami.")
        existing = {item.role_id: item for item in db.execute(select(UserGlobalRole).where(UserGlobalRole.user_id == target.id)).scalars()}
        for role_id, assignment in existing.items():
            if role_id not in wanted:
                db.delete(assignment)
                self._audit(db, actor, target, role_id, None, "revoked")
        for role in roles:
            if role.id not in existing:
                db.add(UserGlobalRole(user_id=target.id, role_id=role.id, granted_by_user_id=actor.id))
                self._audit(db, actor, target, role.id, None, "granted")

    def _form_permission_keys(self, db, user_id: int, form_id: int) -> set[str] | None:
        assignments = list(db.execute(select(FormUserRole.role_id).where(
            FormUserRole.user_id == user_id, FormUserRole.form_id == form_id
        )).scalars())
        if not assignments:
            return None
        return self._permission_keys_for_roles(db, assignments)

    def _global_permission_keys(self, db, user_id: int) -> set[str]:
        role_ids = list(db.execute(select(UserGlobalRole.role_id).where(UserGlobalRole.user_id == user_id)).scalars())
        return self._permission_keys_for_roles(db, role_ids)

    @staticmethod
    def _permission_keys_for_roles(db, role_ids: Iterable[int]) -> set[str]:
        ids = list(role_ids)
        if not ids:
            return set()
        return set(db.execute(
            select(Permission.key)
            .join(AccessRolePermission, AccessRolePermission.permission_id == Permission.id)
            .join(AccessRole, AccessRole.id == AccessRolePermission.role_id)
            .where(AccessRolePermission.role_id.in_(ids), AccessRole.is_active.is_(True), Permission.is_active.is_(True))
        ).scalars())

    @staticmethod
    def _legacy_has_permission(db, user: User, form_id: int, permission: str) -> bool:
        legacy = db.execute(select(FormPermission).where(
            FormPermission.user_id == user.id, FormPermission.form_id == form_id
        )).scalar_one_or_none()
        if not legacy:
            return False
        if permission == "can_view_submissions":
            return True
        if user.role == "admin" and legacy.can_manage and permission in LEGACY_MANAGE_PERMISSIONS:
            return True
        flag = LEGACY_FLAG_MAP.get(permission)
        return bool(flag and getattr(legacy, flag, False))

    @staticmethod
    def _audit(db, actor: User, target: User, role_id: int, form_id: int | None, action: str) -> None:
        db.add(RoleAssignmentAudit(
            actor_user_id=actor.id, target_user_id=target.id, role_id=role_id,
            form_id=form_id, action=action, metadata_json={},
        ))
