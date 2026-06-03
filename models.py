from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


JsonDict = JSON().with_variant(JSONB, "postgresql")


class FormSubmission(Base):
    __tablename__ = "form_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    form_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    data_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    access_token: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    imiona: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    nazwisko: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    obywatelstwo: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    data_urodzenia: Mapped[date | None] = mapped_column(Date, nullable=True)
    miejsce_urodzenia: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    pesel: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    plec: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    wiek: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wyksztalcenie: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    wojewodztwo: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    powiat: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    gmina: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    miejscowosc: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    kod_pocztowy: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    ulica: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    nr_budynku: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nr_lokalu: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    telefon: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    zamieszkuje_lubuskie: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    pracuje_lubuskie: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    osoba_niepelnosprawna: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    specjalne_potrzeby: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    specjalne_potrzeby_opis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mniejszosc_narodowa: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    osoba_bezdomna: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    niekorzystna_sytuacja: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    dzial_wsparcia: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    osw_regulamin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_kryteria: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_finansowanie: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_brak_gwarancji: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_rodo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_ewaluacja: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_zatrudnienie: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_monitoring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    osw_prawdziwosc: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    deklaracja_18_lat: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_lubuskie: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_wlasna_inicjatywa: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_brak_dzialalnosci: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_brak_ksztalcenia: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_obszar_wiejski: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_niepelnosprawnosc: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_umiejetnosci_podstawowe: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_grupa_niekorzystna: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deklaracja_zgoda_wizerunek: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deklaracja_prawdziwosc_danych: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    selected_trainings: Mapped[str] = mapped_column(Text, default="", nullable=False)
    training_agreements: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pdf_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    signed_pdf_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    signature_status: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    signature_request_id: Mapped[str] = mapped_column(String(255), default="mobywatel-manual", nullable=False)
    signature_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    process_status: Mapped[str] = mapped_column(String(128), default="FORM_SUBMITTED", nullable=False)
    workflow_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    officer_decision: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    officer_decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    officer_decision_email_requested: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    officer_decision_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    acceptance_required: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    acceptance_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    decision_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    decision_email_sent_for: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    akceptacja: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    declaration_required: Mapped[str] = mapped_column(String(16), default="Nie", nullable=False)
    declaration_generated: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    declaration_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    declaration_signed: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    declaration_signature_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    declaration_signature_valid: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    declaration_signature_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    declaration_signed_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    agreement_required: Mapped[str] = mapped_column(String(16), default="Nie", nullable=False)
    agreement_blocked: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    agreement_block_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agreement_generated: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    agreement_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    agreement_generated_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    agreement_signed: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    agreement_signature_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    agreement_signature_valid: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    agreement_signature_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agreement_signed_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    office_agreement_signed_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    office_agreement_signed_email_sent_for: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    agreement_success_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    agreement_success_email_sent_for: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    requirements_rejection_email_sent: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    correction_required: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    correction_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    correction_fields: Mapped[str] = mapped_column(Text, default="", nullable=False)
    correction_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list["SubmissionFile"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class SubmissionFile(Base):
    __tablename__ = "submission_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    document_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="application/pdf", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="uploaded", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    submission: Mapped[FormSubmission] = relationship(back_populates="files")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), default="form_manager", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    permissions: Mapped[list["FormPermission"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    forms_created: Mapped[list["Form"]] = relationship(back_populates="creator")


class Logo(Base):
    __tablename__ = "logos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    forms: Mapped[list["Form"]] = relationship(back_populates="logo")
    mail_footers: Mapped[list["MailFooter"]] = relationship(back_populates="logo")


class Form(Base):
    __tablename__ = "forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    definition_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    label_text: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    label_variant: Mapped[str] = mapped_column(String(64), default="project", nullable=False)
    label_color: Mapped[str] = mapped_column(String(64), default="#b38d45", nullable=False)
    label_background: Mapped[str] = mapped_column(String(64), default="#f7f3ec", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    creator: Mapped[User | None] = relationship(back_populates="forms_created")
    logo: Mapped[Logo | None] = relationship(back_populates="forms")
    fields: Mapped[list["FormField"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    permissions: Mapped[list["FormPermission"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    mail_templates: Mapped[list["MailTemplate"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    mail_footers: Mapped[list["MailFooter"]] = relationship(back_populates="form", cascade="all, delete-orphan")

    @property
    def active(self) -> bool:
        return self.is_active

    @active.setter
    def active(self, value: bool) -> None:
        self.is_active = bool(value)


class FormField(Base):
    __tablename__ = "form_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(Text, default="", nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="text", nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    options: Mapped[dict | list] = mapped_column(JsonDict, default=list, nullable=False)
    default_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    section: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    form: Mapped[Form] = relationship(back_populates="fields")


class FormPermission(Base):
    __tablename__ = "form_permissions"
    __table_args__ = (UniqueConstraint("user_id", "form_id", name="uq_form_permissions_user_form"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="permissions")
    form: Mapped[Form] = relationship(back_populates="permissions")


class MailTemplate(Base):
    __tablename__ = "mail_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_type: Mapped[str] = mapped_column(String(128), default="manual", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mail_type: Mapped[str] = mapped_column(String(64), default="html_text", nullable=False)
    content_title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    content_intro: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instruction_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instruction_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    footer_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    trigger_status: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    trigger_decision: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_default_for_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_platform_layout: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    form: Mapped[Form] = relationship(back_populates="mail_templates")
    assets: Mapped[list["MailTemplateAsset"]] = relationship(back_populates="template", cascade="all, delete-orphan")

    @property
    def subject_template(self) -> str:
        return self.subject

    @subject_template.setter
    def subject_template(self, value: str) -> None:
        self.subject = value or ""

    @property
    def body_html(self) -> str:
        return self.html_body

    @body_html.setter
    def body_html(self, value: str) -> None:
        self.html_body = value or ""

    @property
    def body_text(self) -> str:
        return self.text_body

    @body_text.setter
    def body_text(self, value: str) -> None:
        self.text_body = value or ""

    @property
    def active(self) -> bool:
        return self.is_active

    @active.setter
    def active(self, value: bool) -> None:
        self.is_active = bool(value)


class MailTemplateAsset(Base):
    __tablename__ = "mail_template_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("mail_templates.id", ondelete="CASCADE"), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, default=b"", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    template: Mapped[MailTemplate] = relationship(back_populates="assets")


class MailFooter(Base):
    __tablename__ = "mail_footers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    logo_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), index=True, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    form: Mapped[Form] = relationship(back_populates="mail_footers")
    logo: Mapped[Logo | None] = relationship(back_populates="mail_footers")


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id", ondelete="SET NULL"), index=True, nullable=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("form_submissions.id", ondelete="SET NULL"), index=True, nullable=True)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("mail_templates.id", ondelete="SET NULL"), nullable=True)
    footer_id: Mapped[int | None] = mapped_column(ForeignKey("mail_footers.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="sent", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
