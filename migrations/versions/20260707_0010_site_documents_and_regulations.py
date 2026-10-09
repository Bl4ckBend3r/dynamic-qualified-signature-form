"""Site documents, contact page and form regulations

Revision ID: 20260707_0010
Revises: 20260610_0009
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260707_0010"
down_revision = "20260610_0009"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _table_columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    tables = _table_names()
    if "form_regulations" not in tables:
        op.create_table(
            "form_regulations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("form_id", sa.Integer(), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
            sa.Column("original_filename", sa.String(512), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("mime_type", sa.String(255), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("form_id", name="uq_form_regulations_form_id"),
        )
        op.create_index("ix_form_regulations_form_id", "form_regulations", ["form_id"])

    if "contact_pages" not in tables:
        op.create_table(
            "contact_pages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(255), nullable=False, server_default="Kontakt"),
            sa.Column("content_html", sa.Text(), nullable=False, server_default=""),
            sa.Column("contact_details", sa.Text(), nullable=False, server_default=""),
            sa.Column("address", sa.Text(), nullable=False, server_default="ul. Podgórna 7\n65-057 Zielona Góra"),
            sa.Column("email", sa.String(255), nullable=False, server_default="wnioski@lubuskie.pl"),
            sa.Column("phone", sa.String(64), nullable=False, server_default=""),
            sa.Column("phones", sa.JSON(), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.execute(
            sa.text(
                "INSERT INTO contact_pages (title, content_html, contact_details, address, email, phone, phones) "
                "VALUES (:title, :content_html, :contact_details, :address, :email, :phone, :phones)"
            ).bindparams(
                sa.bindparam("title", "Kontakt"),
                sa.bindparam("content_html", ""),
                sa.bindparam("contact_details", ""),
                sa.bindparam("address", "ul. Podgórna 7\n65-057 Zielona Góra"),
                sa.bindparam("email", "wnioski@lubuskie.pl"),
                sa.bindparam("phone", "+48 68 45 65 590"),
                sa.bindparam(
                    "phones",
                    [
                        {"label": "tel.", "number": "+48 68 45 65 590"},
                        {"label": "tel.", "number": "+48 68 45 65 591"},
                        {"label": "fax", "number": "+48 68 45 65 468"},
                    ],
                    type_=sa.JSON(),
                ),
            )
        )
    else:
        _add_column_if_missing("contact_pages", sa.Column("address", sa.Text(), nullable=False, server_default=""))
        _add_column_if_missing("contact_pages", sa.Column("phones", sa.JSON(), nullable=True))

    if "service_documents" not in tables:
        op.create_table(
            "service_documents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("document_type", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("content_html", sa.Text(), nullable=False, server_default=""),
            sa.Column("original_filename", sa.String(512), nullable=False, server_default=""),
            sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(255), nullable=False, server_default=""),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("document_type", name="uq_service_documents_document_type"),
        )
        op.create_index("ix_service_documents_document_type", "service_documents", ["document_type"])

    if "mail_footers" in tables:
        with op.batch_alter_table("mail_footers") as batch_op:
            batch_op.alter_column("form_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    tables = _table_names()
    if "mail_footers" in tables:
        with op.batch_alter_table("mail_footers") as batch_op:
            batch_op.alter_column("form_id", existing_type=sa.Integer(), nullable=False)
    if "service_documents" in tables:
        op.drop_table("service_documents")
    if "contact_pages" in tables:
        op.drop_table("contact_pages")
    if "form_regulations" in tables:
        op.drop_table("form_regulations")
