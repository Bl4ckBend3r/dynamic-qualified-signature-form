"""Add safe logo placement defaults to the JSON mail layout.

Revision ID: 20260715_0016
Revises: 20260715_0015
Create Date: 2026-07-15
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "20260715_0016"
down_revision = "20260715_0015"
branch_labels = None
depends_on = None

LOGO_DEFAULTS = {
    "logo_position": "footer",
    "logo_alignment": "center",
    "logo_height_px": 64,
}


def _layout_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "system_mail_settings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("system_mail_settings")}
    if "layout_config" not in columns:
        return

    settings = sa.table(
        "system_mail_settings",
        sa.column("id", sa.Integer()),
        sa.column("layout_config", sa.JSON()),
    )
    rows = bind.execute(sa.select(settings.c.id, settings.c.layout_config)).mappings().all()
    for row in rows:
        layout = _layout_dict(row["layout_config"])
        for key, default in LOGO_DEFAULTS.items():
            layout.setdefault(key, default)
        bind.execute(
            settings.update().where(settings.c.id == row["id"]).values(layout_config=layout)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "system_mail_settings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("system_mail_settings")}
    if "layout_config" not in columns:
        return

    settings = sa.table(
        "system_mail_settings",
        sa.column("id", sa.Integer()),
        sa.column("layout_config", sa.JSON()),
    )
    rows = bind.execute(sa.select(settings.c.id, settings.c.layout_config)).mappings().all()
    for row in rows:
        layout = _layout_dict(row["layout_config"])
        for key in LOGO_DEFAULTS:
            layout.pop(key, None)
        bind.execute(
            settings.update().where(settings.c.id == row["id"]).values(layout_config=layout)
        )
