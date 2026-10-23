"""add scalable global and form-scoped RBAC

Revision ID: 20260818_0041
Revises: 20260818_0040
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260818_0041"
down_revision = "20260818_0040"
branch_labels = None
depends_on = None


FORM_PERMISSIONS = {
    "can_view_submissions": "Podgląd zgłoszeń",
    "can_review": "Weryfikacja zgłoszeń",
    "can_make_decision": "Podejmowanie decyzji",
    "can_return_for_correction": "Zwrot do poprawy",
    "can_assign_submissions": "Przydzielanie spraw",
    "can_manage_documents": "Zarządzanie dokumentami",
    "can_sign_office_agreement": "Podpis umowy przez urząd",
    "can_send_email": "Wysyłanie wiadomości",
    "can_export_data": "Eksport danych",
    "can_edit_form": "Edycja formularza",
    "can_edit_workflow": "Edycja workflow",
    "can_view_sensitive_data": "Podgląd danych wrażliwych",
    "can_view_internal_notes": "Podgląd notatek wewnętrznych",
    "can_add_internal_notes": "Dodawanie notatek wewnętrznych",
    "can_manage_internal_notes": "Zarządzanie notatkami wewnętrznymi",
}
GLOBAL_PERMISSIONS = {
    "can_manage_users": "Zarządzanie użytkownikami",
    "can_manage_site": "Zarządzanie ustawieniami globalnymi",
    "can_manage_roles": "Zarządzanie rolami i uprawnieniami",
}
ROLE_PERMISSIONS = {
    "form_administrator": set(FORM_PERMISSIONS),
    "case_officer": {"can_view_submissions", "can_review", "can_return_for_correction", "can_view_internal_notes", "can_add_internal_notes"},
    "reviewer": {"can_view_submissions", "can_review"},
    "decision_maker": {"can_view_submissions", "can_make_decision"},
    "assignment_manager": {"can_view_submissions", "can_assign_submissions"},
    "document_operator": {"can_view_submissions", "can_manage_documents"},
    "office_signer": {"can_view_submissions", "can_sign_office_agreement"},
    "mail_operator": {"can_view_submissions", "can_send_email"},
    "data_exporter": {"can_view_submissions", "can_export_data"},
    "sensitive_viewer": {"can_view_submissions", "can_view_sensitive_data"},
    "internal_note_viewer": {"can_view_submissions", "can_view_internal_notes"},
    "internal_note_author": {"can_view_submissions", "can_view_internal_notes", "can_add_internal_notes"},
    "internal_note_manager": {"can_view_submissions", "can_view_internal_notes", "can_manage_internal_notes"},
    "read_only": {"can_view_submissions"},
    "system_administrator": set(GLOBAL_PERMISSIONS),
}
ROLE_NAMES = {
    "form_administrator": "Administrator formularza", "case_officer": "Pracownik prowadzący",
    "reviewer": "Weryfikator", "decision_maker": "Osoba decyzyjna",
    "assignment_manager": "Koordynator przydziałów", "document_operator": "Operator dokumentów",
    "office_signer": "Podpisujący po stronie urzędu", "mail_operator": "Operator korespondencji",
    "data_exporter": "Eksporter danych", "sensitive_viewer": "Dostęp do danych wrażliwych",
    "internal_note_viewer": "Podgląd notatek", "internal_note_author": "Dodawanie notatek",
    "internal_note_manager": "Zarządzanie notatkami", "read_only": "Tylko odczyt",
    "system_administrator": "Administrator systemu",
}
PERMISSION_CATEGORIES = {
    "can_manage_documents": "documents", "can_sign_office_agreement": "documents",
    "can_send_email": "communication",
    "can_export_data": "data", "can_view_sensitive_data": "data",
    "can_edit_form": "configuration", "can_edit_workflow": "configuration",
    "can_view_internal_notes": "notes", "can_add_internal_notes": "notes", "can_manage_internal_notes": "notes",
}


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False), sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("key", name="uq_permissions_key"),
    )
    op.create_index("ix_permissions_key", "permissions", ["key"])
    op.create_table(
        "access_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_access_roles_key"),
    )
    op.create_index("ix_access_roles_key", "access_roles", ["key"])
    op.create_table(
        "access_role_permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("access_roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_access_role_permissions_role_permission"),
    )
    op.create_index("ix_access_role_permissions_role_id", "access_role_permissions", ["role_id"])
    op.create_index("ix_access_role_permissions_permission_id", "access_role_permissions", ["permission_id"])
    op.create_table(
        "form_user_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("access_roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "form_id", "role_id", name="uq_form_user_roles_assignment"),
    )
    for column in ("user_id", "form_id", "role_id"):
        op.create_index(f"ix_form_user_roles_{column}", "form_user_roles", [column])
    op.create_table(
        "user_global_roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("access_roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_global_roles_assignment"),
    )
    op.create_index("ix_user_global_roles_user_id", "user_global_roles", ["user_id"])
    op.create_index("ix_user_global_roles_role_id", "user_global_roles", ["role_id"])
    op.create_table(
        "role_assignment_audits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("access_roles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", _json_type(), nullable=False),
    )
    op.create_index("ix_role_assignment_audits_target_user_id", "role_assignment_audits", ["target_user_id"])
    op.create_index("ix_role_assignment_audits_form_id", "role_assignment_audits", ["form_id"])
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "form_fields" in table_names and "data_classification" not in {
        column["name"] for column in inspector.get_columns("form_fields")
    }:
        op.add_column("form_fields", sa.Column("data_classification", sa.String(32), nullable=False, server_default="normal"))
    if "submission_files" in table_names and "data_classification" not in {
        column["name"] for column in inspector.get_columns("submission_files")
    }:
        op.add_column("submission_files", sa.Column("data_classification", sa.String(32), nullable=False, server_default="normal"))

    permissions = sa.table("permissions", sa.column("id"), sa.column("key"), sa.column("name"), sa.column("category"), sa.column("scope"), sa.column("is_active"))
    roles = sa.table("access_roles", sa.column("id"), sa.column("key"), sa.column("name"), sa.column("scope"), sa.column("is_system"), sa.column("is_active"))
    links = sa.table("access_role_permissions", sa.column("role_id"), sa.column("permission_id"))
    form_roles = sa.table("form_user_roles", sa.column("user_id"), sa.column("form_id"), sa.column("role_id"), sa.column("granted_by_user_id"))
    op.bulk_insert(permissions, [
        {"key": key, "name": name, "category": "global" if key in GLOBAL_PERMISSIONS else PERMISSION_CATEGORIES.get(key, "submissions"), "scope": "global" if key in GLOBAL_PERMISSIONS else "form", "is_active": True}
        for key, name in {**FORM_PERMISSIONS, **GLOBAL_PERMISSIONS}.items()
    ])
    op.bulk_insert(roles, [
        {"key": key, "name": ROLE_NAMES[key], "scope": "global" if key == "system_administrator" else "form", "is_system": True, "is_active": True}
        for key in ROLE_PERMISSIONS
    ])
    permission_ids = dict(bind.execute(sa.select(permissions.c.key, permissions.c.id)).all())
    role_ids = dict(bind.execute(sa.select(roles.c.key, roles.c.id)).all())
    op.bulk_insert(links, [
        {"role_id": role_ids[role_key], "permission_id": permission_ids[permission_key]}
        for role_key, permission_keys in ROLE_PERMISSIONS.items() for permission_key in sorted(permission_keys)
    ])

    required_legacy_columns = {
        "user_id", "form_id", "can_manage", "can_review", "can_make_decision",
        "can_view_sensitive_data", "can_assign_submissions", "can_view_internal_notes",
        "can_add_internal_notes", "can_manage_internal_notes",
    }
    form_permission_columns = {
        column["name"] for column in inspector.get_columns("form_permissions")
    } if "form_permissions" in table_names else set()
    user_columns = {
        column["name"] for column in inspector.get_columns("users")
    } if "users" in table_names else set()
    rows = []
    if required_legacy_columns <= form_permission_columns and {"id", "role"} <= user_columns:
        legacy = sa.table(
            "form_permissions", sa.column("user_id"), sa.column("form_id"), sa.column("can_manage"),
            sa.column("can_review"), sa.column("can_make_decision"), sa.column("can_view_sensitive_data"),
            sa.column("can_assign_submissions"), sa.column("can_view_internal_notes"),
            sa.column("can_add_internal_notes"), sa.column("can_manage_internal_notes"),
        )
        users = sa.table("users", sa.column("id"), sa.column("role"))
        rows = bind.execute(
            sa.select(legacy, users.c.role.label("user_role")).join(users, users.c.id == legacy.c.user_id)
        ).mappings().all()
    assignments = []
    for row in rows:
        role_keys = {"read_only"}
        if row["can_manage"] and row["user_role"] == "admin":
            role_keys = {"form_administrator"}
        else:
            if row["can_review"]: role_keys.add("reviewer")
            if row["can_make_decision"]: role_keys.add("decision_maker")
            if row["can_assign_submissions"]: role_keys.add("assignment_manager")
            if row["can_view_sensitive_data"]: role_keys.add("sensitive_viewer")
            if row["can_view_internal_notes"]: role_keys.add("internal_note_viewer")
            if row["can_add_internal_notes"]: role_keys.add("internal_note_author")
            if row["can_manage_internal_notes"]: role_keys.add("internal_note_manager")
        assignments.extend({"user_id": row["user_id"], "form_id": row["form_id"], "role_id": role_ids[key], "granted_by_user_id": None} for key in sorted(role_keys))
    if assignments:
        op.bulk_insert(form_roles, assignments)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "submission_files" in table_names and "data_classification" in {
        column["name"] for column in inspector.get_columns("submission_files")
    }:
        op.drop_column("submission_files", "data_classification")
    if "form_fields" in table_names and "data_classification" in {
        column["name"] for column in inspector.get_columns("form_fields")
    }:
        op.drop_column("form_fields", "data_classification")
    op.drop_table("role_assignment_audits")
    op.drop_table("user_global_roles")
    op.drop_table("form_user_roles")
    op.drop_table("access_role_permissions")
    op.drop_table("access_roles")
    op.drop_table("permissions")
