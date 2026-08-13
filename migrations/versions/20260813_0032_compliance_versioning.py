"""add versioned regulations, consents and acceptance snapshots

Revision ID: 20260813_0032
Revises: 20260812_0031
Create Date: 2026-08-13
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import unicodedata

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_0032"
down_revision = "20260812_0031"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)} if table in _tables() else set()


def _dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _text(value) -> str:
    value = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def _digest_text(value) -> str:
    return sha256(_text(value).encode("utf-8")).hexdigest()


def _field_text(field: dict) -> str:
    options = field.get("options") or []
    first = options[0] if isinstance(options, list) and options else {}
    label = first.get("label", "") if isinstance(first, dict) else str(first or "")
    return _text(field.get("pdf_text") or label or field.get("label") or "")


def _accepted(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return any(_accepted(item) for item in value)
    return str(value or "").strip().lower() in {"1", "true", "tak", "yes", "on", "checked"}


def _create_tables() -> None:
    if "consent_definitions" not in _tables():
        op.create_table(
            "consent_definitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("consent_key", sa.String(255), nullable=False),
            sa.Column("consent_type", sa.String(64), nullable=False, server_default="other"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("form_id", "consent_key", name="uq_consent_definitions_form_key"),
        )
        op.create_index("ix_consent_definitions_form_id", "consent_definitions", ["form_id"])
    if "consent_versions" not in _tables():
        op.create_table(
            "consent_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("consent_definition_id", sa.Integer(), sa.ForeignKey("consent_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_major", sa.Integer(), nullable=False),
            sa.Column("version_minor", sa.Integer(), nullable=False),
            sa.Column("version_label", sa.String(64), nullable=False),
            sa.Column("consent_type", sa.String(64), nullable=False, server_default="other"),
            sa.Column("title", sa.String(512), nullable=False, server_default=""),
            sa.Column("text_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("consent_definition_id", "version_major", "version_minor", name="uq_consent_versions_number"),
            sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_consent_versions_status"),
        )
        op.create_index("ix_consent_versions_definition_status", "consent_versions", ["consent_definition_id", "status"])
    if "form_regulation_versions" not in _tables():
        op.create_table(
            "form_regulation_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("regulation_id", sa.Integer(), sa.ForeignKey("form_regulations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_major", sa.Integer(), nullable=False),
            sa.Column("version_minor", sa.Integer(), nullable=False),
            sa.Column("version_label", sa.String(64), nullable=False),
            sa.Column("title", sa.String(512), nullable=False, server_default="Regulamin"),
            sa.Column("original_filename", sa.String(512), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("mime_type", sa.String(255), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("regulation_id", "version_major", "version_minor", name="uq_regulation_versions_number"),
            sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_regulation_versions_status"),
        )
        op.create_index("ix_regulation_versions_form_status", "form_regulation_versions", ["form_id", "status"])
    if "regulation_version_id" not in _columns("form_versions"):
        with op.batch_alter_table("form_versions") as batch:
            batch.add_column(sa.Column("regulation_version_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_form_versions_regulation_version_id",
                "form_regulation_versions",
                ["regulation_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "form_version_consents" not in _tables():
        op.create_table(
            "form_version_consents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("form_version_id", sa.Integer(), sa.ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("consent_version_id", sa.Integer(), sa.ForeignKey("consent_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("form_version_id", "consent_version_id", name="uq_form_version_consents_link"),
        )
    if "submission_consents" not in _tables():
        op.create_table(
            "submission_consents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("consent_version_id", sa.Integer(), sa.ForeignKey("consent_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("regulation_version_id", sa.Integer(), sa.ForeignKey("form_regulation_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("consent_key", sa.String(255), nullable=False),
            sa.Column("consent_version", sa.String(64), nullable=False),
            sa.Column("consent_title_snapshot", sa.String(512), nullable=False, server_default=""),
            sa.Column("consent_text_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("consent_sha256", sa.String(64), nullable=False),
            sa.Column("regulation_version", sa.String(64), nullable=False, server_default=""),
            sa.Column("regulation_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("submission_id", "consent_key", name="uq_submission_consents_submission_key"),
        )
        op.create_index("ix_submission_consents_submission_id", "submission_consents", ["submission_id"])


def _backfill() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    forms = sa.Table("forms", meta, autoload_with=bind)
    form_versions = sa.Table("form_versions", meta, autoload_with=bind)
    definitions = sa.Table("consent_definitions", meta, autoload_with=bind)
    versions = sa.Table("consent_versions", meta, autoload_with=bind)
    links = sa.Table("form_version_consents", meta, autoload_with=bind)
    regulations = sa.Table("form_regulations", meta, autoload_with=bind)
    regulation_versions = sa.Table("form_regulation_versions", meta, autoload_with=bind)
    submissions = sa.Table("form_submissions", meta, autoload_with=bind)
    snapshots = sa.Table(
        "submission_consents",
        meta,
        sa.Column("metadata_json", sa.JSON()),
        autoload_with=bind,
        extend_existing=True,
    )
    now = datetime.now(timezone.utc)
    if (
        bind.execute(sa.select(sa.func.count(links.c.id))).scalar_one()
        or bind.execute(sa.select(sa.func.count(definitions.c.id))).scalar_one()
        or bind.execute(sa.select(sa.func.count(regulation_versions.c.id))).scalar_one()
        or bind.execute(sa.select(sa.func.count(snapshots.c.id))).scalar_one()
    ):
        return

    definition_ids: dict[tuple[int, str], int] = {}
    current_by_definition: dict[int, dict] = {}
    version_links: dict[int, list[dict]] = {}
    rows = bind.execute(sa.select(form_versions).order_by(form_versions.c.form_id, form_versions.c.version_major, form_versions.c.version_minor)).mappings().all()
    for form_version in rows:
        config = _dict(form_version["definition_json"])
        for order, field in enumerate(config.get("fields") or []):
            if not isinstance(field, dict) or field.get("type") != "checkbox" or not field.get("name"):
                continue
            key = str(field["name"])
            map_key = (form_version["form_id"], key)
            definition_id = definition_ids.get(map_key)
            if definition_id is None:
                existing = bind.execute(sa.select(definitions.c.id).where(definitions.c.form_id == map_key[0], definitions.c.consent_key == key)).scalar_one_or_none()
                if existing is None:
                    result = bind.execute(definitions.insert().values(form_id=map_key[0], consent_key=key, consent_type=str(field.get("consent_type") or "other"), created_at=now))
                    existing = result.inserted_primary_key[0]
                definition_id = int(existing)
                definition_ids[map_key] = definition_id
            text = _field_text(field)
            digest = _digest_text(text)
            title = str(field.get("label") or key)
            required = bool(field.get("required"))
            current = current_by_definition.get(definition_id)
            consent_type = str(field.get("consent_type") or "other")
            if current and (current["sha256"], current["title"], current["required"], current["type"]) == (digest, title, required, consent_type):
                consent_version = current
            else:
                if current:
                    bind.execute(versions.update().where(versions.c.id == current["id"]).values(status="archived", archived_at=now))
                minor = current["version_minor"] + 1 if current else 0
                result = bind.execute(versions.insert().values(consent_definition_id=definition_id, version_major=1, version_minor=minor, version_label=f"1.{minor}", consent_type=consent_type, title=title, text_snapshot=text, sha256=digest, required=required, status="published", created_at=now, published_at=now))
                consent_version = {"id": result.inserted_primary_key[0], "version_minor": minor, "version_label": f"1.{minor}", "sha256": digest, "title": title, "text": text, "required": required, "key": key, "type": consent_type}
                current_by_definition[definition_id] = consent_version
            bind.execute(links.insert().values(form_version_id=form_version["id"], consent_version_id=consent_version["id"], sort_order=order))
            version_links.setdefault(form_version["id"], []).append(consent_version)

    form_names = {row.id: row.name for row in bind.execute(sa.select(forms.c.id, forms.c.name))}
    published_forms = {row.form_id: row.id for row in bind.execute(sa.select(form_versions.c.id, form_versions.c.form_id).where(form_versions.c.status == "published"))}
    regulation_by_form: dict[int, dict] = {}
    for regulation in bind.execute(sa.select(regulations)).mappings():
        path = Path(regulation["storage_path"])
        if not path.is_file():
            continue
        digest = sha256(path.read_bytes()).hexdigest()
        result = bind.execute(regulation_versions.insert().values(regulation_id=regulation["id"], form_id=regulation["form_id"], version_major=1, version_minor=0, version_label="1.0", title=f"Regulamin — {form_names.get(regulation['form_id'], '')}", original_filename=regulation["original_filename"], storage_path=regulation["storage_path"], mime_type=regulation["mime_type"], size_bytes=regulation["size_bytes"], sha256=digest, status="published", created_at=now, created_by_id=regulation.get("uploaded_by_user_id"), published_at=now))
        item = {"id": result.inserted_primary_key[0], "version_label": "1.0", "sha256": digest}
        regulation_by_form[regulation["form_id"]] = item
        published_version_id = published_forms.get(regulation["form_id"])
        if published_version_id:
            bind.execute(form_versions.update().where(form_versions.c.id == published_version_id).values(regulation_version_id=item["id"]))

    for submission in bind.execute(sa.select(submissions)).mappings():
        form_version_id = submission.get("form_version_id")
        if not form_version_id:
            continue
        data = dict(submission)
        data.update(_dict(submission.get("data_json")))
        regulation = regulation_by_form.get(next((row.form_id for row in rows if row.id == form_version_id), -1))
        for item in version_links.get(form_version_id, []):
            accepted = _accepted(data.get(item["key"]))
            is_regulation = item["type"] == "regulation" or "regulamin" in item["key"].lower()
            bind.execute(snapshots.insert().values(submission_id=submission["id"], consent_version_id=item["id"], regulation_version_id=regulation["id"] if regulation and is_regulation else None, consent_key=item["key"], consent_version=item["version_label"], consent_title_snapshot=item["title"], consent_text_snapshot=item["text"], consent_sha256=item["sha256"], regulation_version=regulation["version_label"] if regulation and is_regulation else "", regulation_sha256=regulation["sha256"] if regulation and is_regulation else "", accepted=accepted, accepted_at=None, metadata_json={"migration_snapshot": True, "consent_type": item["type"]}, created_at=now))


def upgrade() -> None:
    _create_tables()
    _backfill()


def downgrade() -> None:
    for table in ("submission_consents", "form_version_consents"):
        if table in _tables():
            op.drop_table(table)
    if "regulation_version_id" in _columns("form_versions"):
        with op.batch_alter_table("form_versions") as batch:
            batch.drop_constraint("fk_form_versions_regulation_version_id", type_="foreignkey")
            batch.drop_column("regulation_version_id")
    for table in ("form_regulation_versions", "consent_versions", "consent_definitions"):
        if table in _tables():
            op.drop_table(table)
