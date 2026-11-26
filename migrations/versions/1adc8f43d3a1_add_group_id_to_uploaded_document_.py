"""Add group ID to uploaded document renames."""

from alembic import op
import sqlalchemy as sa

revision = "1adc8f43d3a1"
down_revision = "20261126_0052"
branch_labels = None
depends_on = None

TABLE = "uploaded_document_renames"
INDEX = "ix_uploaded_document_renames_group"


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    count = connection.execute(
        sa.text("SELECT COUNT(*) FROM uploaded_document_renames")
    ).scalar_one()

    if count:
        raise RuntimeError(
            "Existing journal entries require a separate backfill."
        )

    columns = {
        c["name"] for c in inspector.get_columns(TABLE)
    }

    if "group_id" not in columns:
        op.add_column(
            TABLE,
            sa.Column("group_id", sa.Integer(), nullable=False),
        )

    indexes = {
        i["name"] for i in sa.inspect(connection).get_indexes(TABLE)
    }

    if INDEX not in indexes:
        op.create_index(
            INDEX,
            TABLE,
            ["migration_id", "group_id"],
            unique=False,
        )


def downgrade():
    connection = op.get_bind()

    count = connection.execute(
        sa.text("SELECT COUNT(*) FROM uploaded_document_renames")
    ).scalar_one()

    if count:
        raise RuntimeError("Rename journal is not empty.")

    op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, "group_id")
