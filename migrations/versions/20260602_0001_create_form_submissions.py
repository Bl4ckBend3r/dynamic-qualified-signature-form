"""create form submissions tables

Revision ID: 20260602_0001
Revises:
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_0001"
down_revision = None
branch_labels = None
depends_on = None


def _text_column(name: str, length: int | None = None, default: str = ""):
    column_type = sa.String(length) if length else sa.Text()
    return sa.Column(name, column_type, nullable=False, server_default=default)


def _bool_column(name: str):
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def upgrade() -> None:
    op.create_table(
        "form_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.String(64), nullable=False),
        sa.Column("form_slug", sa.String(255), nullable=False),
        _text_column("form_name", 255),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        _text_column("access_token", 255),
        _text_column("imiona", 255),
        _text_column("nazwisko", 255),
        _text_column("obywatelstwo", 255),
        sa.Column("data_urodzenia", sa.Date(), nullable=True),
        _text_column("miejsce_urodzenia", 255),
        _text_column("pesel", 32),
        _text_column("plec", 64),
        sa.Column("wiek", sa.Integer(), nullable=True),
        _text_column("wyksztalcenie", 255),
        _text_column("wojewodztwo", 255),
        _text_column("powiat", 255),
        _text_column("gmina", 255),
        _text_column("miejscowosc", 255),
        _text_column("kod_pocztowy", 32),
        _text_column("ulica", 255),
        _text_column("nr_budynku", 64),
        _text_column("nr_lokalu", 64),
        _text_column("telefon", 64),
        _text_column("email", 255),
        _text_column("zamieszkuje_lubuskie", 64),
        _text_column("pracuje_lubuskie", 64),
        _text_column("osoba_niepelnosprawna", 128),
        _text_column("specjalne_potrzeby", 64),
        _text_column("specjalne_potrzeby_opis"),
        _text_column("mniejszosc_narodowa", 128),
        _text_column("osoba_bezdomna", 64),
        _text_column("niekorzystna_sytuacja", 128),
        _text_column("dzial_wsparcia", 64),
        _bool_column("osw_regulamin"),
        _bool_column("osw_kryteria"),
        _bool_column("osw_finansowanie"),
        _bool_column("osw_brak_gwarancji"),
        _bool_column("osw_rodo"),
        _bool_column("osw_ewaluacja"),
        _bool_column("osw_zatrudnienie"),
        _bool_column("osw_monitoring"),
        _bool_column("osw_prawdziwosc"),
        _text_column("deklaracja_18_lat", 64),
        _text_column("deklaracja_lubuskie", 64),
        _text_column("deklaracja_wlasna_inicjatywa", 64),
        _text_column("deklaracja_brak_dzialalnosci", 64),
        _text_column("deklaracja_brak_ksztalcenia", 64),
        _text_column("deklaracja_obszar_wiejski", 64),
        _text_column("deklaracja_niepelnosprawnosc", 64),
        _text_column("deklaracja_umiejetnosci_podstawowe", 64),
        _text_column("deklaracja_grupa_niekorzystna", 64),
        _bool_column("deklaracja_zgoda_wizerunek"),
        _bool_column("deklaracja_prawdziwosc_danych"),
        _text_column("selected_trainings"),
        _text_column("training_agreements"),
        _text_column("pdf_filename", 512),
        _text_column("signed_pdf_filename", 512),
        _text_column("signature_status", 64, "manual"),
        _text_column("signature_request_id", 255, "mobywatel-manual"),
        _text_column("signature_method", 64),
        _text_column("process_status", 128, "FORM_SUBMITTED"),
        _text_column("workflow_step", 128),
        _text_column("officer_decision", 32),
        _text_column("officer_decision_reason"),
        _text_column("officer_decision_email_requested", 16),
        _text_column("officer_decision_email_sent", 16),
        _text_column("acceptance_required", 16),
        _text_column("acceptance_email_sent", 16),
        _text_column("decision_email_sent", 16),
        _text_column("decision_email_sent_for", 64),
        _text_column("akceptacja", 16),
        _text_column("declaration_required", 16, "Nie"),
        _text_column("declaration_generated", 16),
        _text_column("declaration_filename", 512),
        _text_column("declaration_signed", 16),
        _text_column("declaration_signature_type", 64),
        _text_column("declaration_signature_valid", 16),
        _text_column("declaration_signature_error"),
        _text_column("declaration_signed_filename", 512),
        _text_column("agreement_required", 16, "Nie"),
        _text_column("agreement_blocked", 16),
        _text_column("agreement_block_reason"),
        _text_column("agreement_generated", 16),
        _text_column("agreement_filename", 512),
        sa.Column("agreement_generated_at", sa.Date(), nullable=True),
        _text_column("agreement_signed", 16),
        _text_column("agreement_signature_type", 64),
        _text_column("agreement_signature_valid", 16),
        _text_column("agreement_signature_error"),
        _text_column("agreement_signed_filename", 512),
        _text_column("office_agreement_signed_email_sent", 16),
        _text_column("office_agreement_signed_email_sent_for", 64),
        _text_column("agreement_success_email_sent", 16),
        _text_column("agreement_success_email_sent_for", 64),
        _text_column("requirements_rejection_email_sent", 16),
        _text_column("correction_required", 16),
        _text_column("correction_message"),
        _text_column("correction_fields"),
        sa.Column("correction_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_form_submissions_submission_id", "form_submissions", ["submission_id"], unique=True)
    op.create_index("ix_form_submissions_form_slug", "form_submissions", ["form_slug"])

    op.create_table(
        "submission_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("form_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_submission_id", sa.String(64), nullable=False),
        sa.Column("form_slug", sa.String(255), nullable=False),
        _text_column("document_id", 128),
        _text_column("document_type", 128),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        _text_column("mime_type", 255, "application/pdf"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        _bool_column("signed"),
        _text_column("checksum_sha256", 64),
        _text_column("status", 64, "uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_submission_files_public_submission_id", "submission_files", ["public_submission_id"])
    op.create_index("ix_submission_files_form_slug", "submission_files", ["form_slug"])


def downgrade() -> None:
    op.drop_index("ix_submission_files_form_slug", table_name="submission_files")
    op.drop_index("ix_submission_files_public_submission_id", table_name="submission_files")
    op.drop_table("submission_files")
    op.drop_index("ix_form_submissions_form_slug", table_name="form_submissions")
    op.drop_index("ix_form_submissions_submission_id", table_name="form_submissions")
    op.drop_table("form_submissions")
