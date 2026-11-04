"""Compatibility revision reserved for the instruction feature.

Revision ID: 20260714_0012
Revises: 20260708_0011
Create Date: 2026-07-14
"""

revision = "20260714_0012"
down_revision = "20260708_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty. Instructions belong to forms, never to submissions.
    pass


def downgrade() -> None:
    pass
