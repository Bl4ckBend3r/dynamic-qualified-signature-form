"""add immutable form versions and bind submissions to snapshots

Revision ID: 20260812_0031
Revises: 20260804_0030
Create Date: 2026-08-12
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260812_0031"
down_revision = "20260804_0030"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _as_options(value):
    if isinstance(value, (list, dict)):
        return deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, (list, dict)) else []
        except (TypeError, ValueError):
            return []
    return []


def _json_storage_value(value: dict):
    """Serialize JSON explicitly when MariaDB reflects JSON as LONGTEXT."""
    if op.get_bind().dialect.name in {"mysql", "mariadb"}:
        return json.dumps(value, ensure_ascii=False)
    return value


def _technical_snapshot(form: dict, fields: list[dict]) -> dict:
    definition = _as_dict(form.get("definition_json"))
    configured_fields = {
        str(field.get("name")): deepcopy(field)
        for field in definition.get("fields") or []
        if isinstance(field, dict) and field.get("name")
    }
    rendered_fields: list[dict] = []
    current_section = None
    for field in sorted(fields, key=lambda item: (item.get("sort_order") or 0, item.get("id") or 0)):
        if "active" in field and not field.get("active"):
            continue
        section = str(field.get("section") or "")
        if section and section != current_section:
            rendered_fields.append({"type": "section", "label": section})
            current_section = section
        config = configured_fields.get(str(field.get("name") or ""), {})
        config.update(
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                "options": _as_options(field.get("options")),
                "default": field.get("default_value"),
                "stage": str(field.get("stage") or "initial_submission"),
            }
        )
        rendered_fields.append(config)

    definition["title"] = str(form.get("title") or form.get("name") or "")
    definition["description"] = str(form.get("description") or "")
    definition["fields"] = rendered_fields
    definition["user_instruction"] = str(form.get("user_instruction") or "")
    definition["user_instruction_config"] = _as_dict(form.get("user_instruction_config"))
    definition["label_text"] = str(form.get("label_text") or "")
    definition["label_variant"] = str(form.get("label_variant") or "project")
    definition["label_color"] = str(form.get("label_color") or "#b38d45")
    definition["label_background"] = str(form.get("label_background") or "#f7f3ec")
    definition["logo_alignment"] = str(form.get("logo_alignment") or "left")
    definition["_form_metadata"] = {
        "name": str(form.get("name") or ""),
        "title": str(form.get("title") or ""),
        "description": str(form.get("description") or ""),
        "user_instruction": str(form.get("user_instruction") or ""),
        "user_instruction_config": _as_dict(form.get("user_instruction_config")),
        "label_text": str(form.get("label_text") or ""),
        "label_variant": str(form.get("label_variant") or "project"),
        "label_color": str(form.get("label_color") or "#b38d45"),
        "label_background": str(form.get("label_background") or "#f7f3ec"),
        "logo_id": form.get("logo_id"),
        "logo_alignment": str(form.get("logo_alignment") or "left"),
    }
    return definition


def _create_version_table() -> None:
    if "form_versions" in _tables():
        return
    op.create_table(
        "form_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_major", sa.Integer(), nullable=False),
        sa.Column("version_minor", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("definition_json", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source_version_id",
            sa.Integer(),
            sa.ForeignKey("form_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("form_id", "version_major", "version_minor", name="uq_form_versions_number"),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_form_versions_status"),
    )
    op.create_index("ix_form_versions_form_status", "form_versions", ["form_id", "status"])
    if op.get_bind().dialect.name in {"postgresql", "sqlite"}:
        op.create_index(
            "uq_form_versions_one_published",
            "form_versions",
            ["form_id"],
            unique=True,
            postgresql_where=sa.text("status = 'published'"),
            sqlite_where=sa.text("status = 'published'"),
        )


def _add_submission_version_column() -> None:
    if "form_version_id" not in _columns("form_submissions"):
        with op.batch_alter_table("form_submissions") as batch:
            batch.add_column(sa.Column("form_version_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_form_submissions_form_version_id",
                "form_versions",
                ["form_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "ix_form_submissions_form_version_id" not in _indexes("form_submissions"):
        op.create_index("ix_form_submissions_form_version_id", "form_submissions", ["form_version_id"])


def _backfill_versions() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    forms = sa.Table("forms", metadata, autoload_with=bind)
    form_fields = sa.Table("form_fields", metadata, autoload_with=bind)
    form_versions = sa.Table("form_versions", metadata, autoload_with=bind)
    submissions = sa.Table("form_submissions", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc)

    for form_row in bind.execute(sa.select(forms)).mappings():
        form = dict(form_row)
        existing_id = bind.execute(
            sa.select(form_versions.c.id).where(
                form_versions.c.form_id == form["id"],
                form_versions.c.version_major == 1,
                form_versions.c.version_minor == 0,
            )
        ).scalar_one_or_none()
        if existing_id is None:
            field_rows = [
                dict(row)
                for row in bind.execute(
                    sa.select(form_fields).where(form_fields.c.form_id == form["id"])
                ).mappings()
            ]
            result = bind.execute(
                form_versions.insert().values(
                    form_id=form["id"],
                    version_major=1,
                    version_minor=0,
                    version_label="1.0",
                    status="published",
                    definition_json=_json_storage_value(_technical_snapshot(form, field_rows)),
                    created_at=now,
                    created_by_id=form.get("created_by_id"),
                    updated_at=now,
                    published_at=now,
                    published_by_id=form.get("created_by_id"),
                    change_summary=(
                        "Migracja techniczna aktualnej konfiguracji formularza; "
                        "wcześniejsza historia nie była dostępna."
                    ),
                )
            )
            existing_id = result.inserted_primary_key[0]
        bind.execute(
            submissions.update()
            .where(
                submissions.c.form_slug == form["slug"],
                submissions.c.form_version_id.is_(None),
            )
            .values(form_version_id=existing_id)
        )


def upgrade() -> None:
    _create_version_table()
    _add_submission_version_column()
    _backfill_versions()


def downgrade() -> None:
    if "form_version_id" in _columns("form_submissions"):
        if "ix_form_submissions_form_version_id" in _indexes("form_submissions"):
            op.drop_index("ix_form_submissions_form_version_id", table_name="form_submissions")
        with op.batch_alter_table("form_submissions") as batch:
            batch.drop_constraint("fk_form_submissions_form_version_id", type_="foreignkey")
            batch.drop_column("form_version_id")
    if "form_versions" in _tables():
        op.drop_table("form_versions")
