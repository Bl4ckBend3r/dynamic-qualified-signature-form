"""add independent public site footer configuration

Revision ID: 20260720_0019
Revises: 20260720_0018
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_0019"
down_revision = "20260720_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "site_footers" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "site_footers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False, server_default="Stopka strony"),
        sa.Column("html_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("logo_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("logo_id", sa.Integer(), sa.ForeignKey("logos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("logo_alignment", sa.String(length=20), nullable=False, server_default="left"),
        sa.Column("logo_width", sa.Integer(), nullable=True),
        sa.Column("logo_height", sa.Integer(), nullable=True),
        sa.Column("logo_position", sa.String(length=30), nullable=False, server_default="top"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_site_footers_logo_id", "site_footers", ["logo_id"])


def downgrade() -> None:
    if "site_footers" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("site_footers")
