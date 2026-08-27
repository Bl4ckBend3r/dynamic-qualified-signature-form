from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, event, func, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


JsonDict = JSON().with_variant(JSONB, "postgresql")


class FormSubmission(Base):
    __tablename__ = "form_submissions"
    __table_args__ = (
        CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name="ck_form_submissions_priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    form_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    form_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("form_versions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
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
    assigned_to_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    priority: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal", index=True, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)

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
    # Canonical layered workflow state. The columns above remain compatibility
    # aliases until all external integrations have migrated.
    workflow_stage: Mapped[str | None] = mapped_column(String(100), default=None, nullable=True)
    final_outcome: Mapped[str | None] = mapped_column(String(100), default=None, nullable=True)
    document_states: Mapped[dict | None] = mapped_column(JsonDict, default=None, nullable=True)
    legacy_process_status: Mapped[str | None] = mapped_column(String(100), default=None, nullable=True)
    officer_decision: Mapped[str] = mapped_column(String(128), default="", nullable=False)
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
    additional_fields_completed: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    correction_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list["SubmissionFile"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )
    form_version: Mapped["FormVersion | None"] = relationship(back_populates="submissions")
    consents: Mapped[list["SubmissionConsent"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionConsent.id",
    )
    checklist_results: Mapped[list["SubmissionChecklistItemResult"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    internal_notes: Mapped[list["SubmissionInternalNote"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    step_deadlines: Mapped[list["SubmissionStepDeadline"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionStepDeadline.entered_at.desc(), SubmissionStepDeadline.id.desc()",
    )
    assigned_to: Mapped["User | None"] = relationship(foreign_keys=[assigned_to_user_id])
    assigned_by: Mapped["User | None"] = relationship(foreign_keys=[assigned_by_user_id])
    assignment_history: Mapped[list["SubmissionAssignmentHistory"]] = relationship(
        back_populates="submission",
        order_by="SubmissionAssignmentHistory.id",
        passive_deletes=True,
    )


class SubmissionTraining(Base):
    __tablename__ = "submission_trainings"
    __table_args__ = (
        UniqueConstraint("submission_id", "training_id", name="uq_submission_trainings_submission_training"),
        Index("ix_submission_trainings_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), index=True, nullable=False)
    training_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    training_name_snapshot: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    training_price_snapshot: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    training_snapshot: Mapped[dict | None] = mapped_column(
        JsonDict,
        default=dict,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(64), default="selected", nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by_event: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    agreement_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    agreement_file_id: Mapped[int | None] = mapped_column(ForeignKey("submission_files.id", ondelete="SET NULL"), nullable=True)
    agreement_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agreement_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_agreement_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    unselected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class TrainingParticipantActionHistory(Base):
    __tablename__ = "training_participant_action_history"
    __table_args__ = (
        Index("ix_training_participant_history_training", "submission_training_id", "created_at"),
        Index("ix_training_action_history_scope", "form_id", "training_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    training_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_training_id: Mapped[int | None] = mapped_column(ForeignKey("submission_trainings.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    new_value: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class TrainingAttendanceSession(Base):
    __tablename__ = "training_attendance_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_training_attendance_sessions_status"),
        Index("ix_training_attendance_sessions_training", "form_id", "training_id", "session_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    training_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    end_time: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrainingAttendanceRecord(Base):
    __tablename__ = "training_attendance_records"
    __table_args__ = (
        UniqueConstraint("session_id", "submission_training_id", name="uq_training_attendance_session_participant"),
        UniqueConstraint("token_hash", name="uq_training_attendance_records_token_hash"),
        CheckConstraint("status IN ('pending', 'present', 'absent', 'excused')", name="ck_training_attendance_records_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("training_attendance_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    submission_training_id: Mapped[int] = mapped_column(ForeignKey("submission_trainings.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    confirmation_method: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class TrainingSurvey(Base):
    __tablename__ = "training_surveys"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'closed')", name="ck_training_surveys_status"),
        Index("ix_training_surveys_training", "form_id", "training_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    training_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrainingSurveyQuestion(Base):
    __tablename__ = "training_survey_questions"
    __table_args__ = (
        CheckConstraint("question_type IN ('scale', 'single_choice', 'multiple_choice', 'text')", name="ck_training_survey_questions_type"),
        Index("ix_training_survey_questions_order", "survey_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("training_surveys.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    options_json: Mapped[list] = mapped_column(JsonDict, default=list, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TrainingSurveyInvitation(Base):
    __tablename__ = "training_survey_invitations"
    __table_args__ = (
        UniqueConstraint("survey_id", "submission_training_id", name="uq_training_survey_invitation_participant"),
        UniqueConstraint("token_hash", name="uq_training_survey_invitations_token_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("training_surveys.id", ondelete="CASCADE"), index=True, nullable=False)
    submission_training_id: Mapped[int] = mapped_column(ForeignKey("submission_trainings.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class TrainingSurveyResponse(Base):
    __tablename__ = "training_survey_responses"
    __table_args__ = (UniqueConstraint("invitation_id", name="uq_training_survey_response_invitation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invitation_id: Mapped[int] = mapped_column(ForeignKey("training_survey_invitations.id", ondelete="CASCADE"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class TrainingSurveyAnswer(Base):
    __tablename__ = "training_survey_answers"
    __table_args__ = (UniqueConstraint("response_id", "question_id", name="uq_training_survey_answer_question"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("training_survey_responses.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("training_survey_questions.id", ondelete="CASCADE"), nullable=False)
    value_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)


class SubmissionFile(Base):
    __tablename__ = "submission_files"
    __table_args__ = (
        Index("ix_submission_files_document_type", "document_type"),
        Index("ix_submission_files_status", "status"),
        Index("ix_submission_files_created_at", "created_at"),
        Index("ix_submission_files_attachment_field", "submission_id", "field_key", "attachment_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    document_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    file_role: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="application/pdf", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="uploaded", nullable=False)
    signature_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    signature_validation_result: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    agreement_number: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    training_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    field_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    attachment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    data_classification: Mapped[str] = mapped_column(String(32), default="normal", server_default="normal", nullable=False)
    uploaded_by_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    workflow_step_at_upload: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    antivirus_status: Mapped[str] = mapped_column(String(64), default="not_configured", nullable=False)
    rejection_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    submission: Mapped[FormSubmission] = relationship(back_populates="files")


class SubmissionWorkflowEvent(Base):
    __tablename__ = "submission_workflow_events"
    __table_args__ = (
        Index("ix_submission_workflow_events_created_at", "created_at"),
        Index("ix_submission_workflow_events_new_status", "new_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("form_submissions.id", ondelete="SET NULL"), index=True, nullable=True)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    previous_status: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    new_status: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    previous_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    new_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decision_code: Mapped[str | None] = mapped_column(String(128), default=None, nullable=True)
    user_message: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    side_effects: Mapped[dict | None] = mapped_column(JsonDict, default=None, nullable=True)
    source: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SubmissionDecision(Base):
    __tablename__ = "submission_decisions"
    __table_args__ = (
        Index("ix_submission_decisions_decision", "decision"),
        Index("ix_submission_decisions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("form_submissions.id", ondelete="SET NULL"), index=True, nullable=True)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    form_slug: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    decision: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    decision_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    semantic_category: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    workflow_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    target_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    justification: Mapped[str] = mapped_column(Text, default="", nullable=False)
    officer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    officer_email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    previous_status: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    target_status: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    email_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_log_id: Mapped[int | None] = mapped_column(ForeignKey("email_logs.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RepeatableGroupItemDecision(Base):
    __tablename__ = "repeatable_group_item_decisions"
    __table_args__ = (
        Index("ix_repeatable_item_decision_lookup", "submission_id", "group_key", "item_id", "decided_at"),
        Index("ix_repeatable_item_decision_code", "decision_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    group_key: Mapped[str] = mapped_column(String(255), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    decision_type_id: Mapped[int | None] = mapped_column(ForeignKey("decision_type_definitions.id", ondelete="SET NULL"), nullable=True)
    decision_code: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_label: Mapped[str] = mapped_column(String(255), nullable=False)
    semantic_category: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    target_step: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    decided_by: Mapped["User | None"] = relationship(foreign_keys=[decided_by_user_id])


class SubmissionAssignmentHistory(Base):
    __tablename__ = "submission_assignment_history"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual', 'round_robin', 'workflow_rule', 'system')",
            name="ck_submission_assignment_history_source",
        ),
        Index("ix_submission_assignment_history_submission_id", "submission_id"),
        Index("ix_submission_assignment_history_assigned_at", "assigned_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("form_submissions.id", ondelete="SET NULL"), nullable=True)
    assigned_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    previous_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)

    submission: Mapped[FormSubmission | None] = relationship(back_populates="assignment_history")
    assigned_to: Mapped["User | None"] = relationship(foreign_keys=[assigned_to_user_id])
    assigned_by: Mapped["User | None"] = relationship(foreign_keys=[assigned_by_user_id])
    previous_user: Mapped["User | None"] = relationship(foreign_keys=[previous_user_id])


class FormAssignmentState(Base):
    __tablename__ = "form_assignment_states"

    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), primary_key=True)
    last_assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )


def _reject_assignment_history_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("Submission assignment history is immutable.")


event.listen(SubmissionAssignmentHistory, "before_update", _reject_assignment_history_mutation)
event.listen(SubmissionAssignmentHistory, "before_delete", _reject_assignment_history_mutation)


def _reject_internal_note_revision_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("Submission internal note revision is immutable.")


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

    permissions: Mapped[list["FormPermission"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    forms_created: Mapped[list["Form"]] = relationship(
        back_populates="creator"
    )


class AdminLoginAttempt(Base):
    __tablename__ = "admin_login_attempts"
    __table_args__ = (
        Index(
            "ix_admin_login_attempts_key_attempted",
            "key_hash",
            "attempted_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SubmissionInternalNote(Base):
    __tablename__ = "submission_internal_notes"
    __table_args__ = (
        Index("ix_submission_internal_notes_submission_created", "submission_id", "created_at"),
        Index("ix_submission_internal_notes_submission_important", "submission_id", "is_important"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parent_note_id: Mapped[int | None] = mapped_column(ForeignKey("submission_internal_notes.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    submission: Mapped[FormSubmission] = relationship(back_populates="internal_notes")
    author: Mapped[User] = relationship(foreign_keys=[author_user_id])
    archived_by: Mapped[User | None] = relationship(foreign_keys=[archived_by_user_id])
    parent_note: Mapped["SubmissionInternalNote | None"] = relationship(remote_side=[id], foreign_keys=[parent_note_id])
    revisions: Mapped[list["SubmissionInternalNoteRevision"]] = relationship(
        back_populates="note", cascade="all, delete-orphan", order_by="SubmissionInternalNoteRevision.edited_at"
    )
    mentions: Mapped[list["SubmissionInternalNoteMention"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )


class SubmissionInternalNoteRevision(Base):
    __tablename__ = "submission_internal_note_revisions"
    __table_args__ = (Index("ix_submission_internal_note_revisions_note_edited", "note_id", "edited_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("submission_internal_notes.id", ondelete="CASCADE"), nullable=False)
    previous_content: Mapped[str] = mapped_column(Text, nullable=False)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    previous_is_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    new_is_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    note: Mapped[SubmissionInternalNote] = relationship(back_populates="revisions")
    edited_by: Mapped[User] = relationship()


class SubmissionInternalNoteMention(Base):
    __tablename__ = "submission_internal_note_mentions"
    __table_args__ = (UniqueConstraint("note_id", "mentioned_user_id", name="uq_submission_internal_note_mention"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("submission_internal_notes.id", ondelete="CASCADE"), nullable=False)
    mentioned_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    note: Mapped[SubmissionInternalNote] = relationship(back_populates="mentions")
    mentioned_user: Mapped[User] = relationship()


event.listen(SubmissionInternalNoteRevision, "before_update", _reject_internal_note_revision_mutation)


@event.listens_for(Session, "before_flush")
def _require_internal_note_revision(session, _flush_context, _instances) -> None:
    for note in (item for item in session.dirty if isinstance(item, SubmissionInternalNote)):
        state = inspect(note)
        content_history = state.attrs.content.history
        important_history = state.attrs.is_important.history
        if not content_history.has_changes() and not important_history.has_changes():
            continue
        previous_content = content_history.deleted[0] if content_history.deleted else note.content
        new_content = content_history.added[0] if content_history.added else note.content
        previous_important = important_history.deleted[0] if important_history.deleted else note.is_important
        new_important = important_history.added[0] if important_history.added else note.is_important
        has_revision = any(
            isinstance(item, SubmissionInternalNoteRevision)
            and item.note_id == note.id
            and item.previous_content == previous_content
            and item.new_content == new_content
            and item.previous_is_important == previous_important
            and item.new_is_important == new_important
            for item in session.new
        )
        if not has_revision:
            raise ValueError("Submission internal note content cannot change without a revision.")


class DecisionTypeDefinition(Base):
    __tablename__ = "decision_type_definitions"
    __table_args__ = (
        CheckConstraint("semantic_category IN ('positive', 'negative', 'correction', 'neutral')", name="ck_decision_type_semantic_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    semantic_category: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


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

    forms: Mapped[list["Form"]] = relationship(back_populates="logo", foreign_keys="Form.logo_id")
    project_forms: Mapped[list["Form"]] = relationship(back_populates="project_logo", foreign_keys="Form.project_logo_id")
    mail_footers: Mapped[list["MailFooter"]] = relationship(back_populates="logo")
    site_footers: Mapped[list["SiteFooter"]] = relationship(back_populates="logo")


class Form(Base):
    __tablename__ = "forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    user_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_instruction_config: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    definition_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_listed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    share_token_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    training_selection_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    label_text: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    label_variant: Mapped[str] = mapped_column(String(64), default="project", nullable=False)
    label_color: Mapped[str] = mapped_column(String(64), default="#b38d45", nullable=False)
    label_background: Mapped[str] = mapped_column(String(64), default="#f7f3ec", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), nullable=True)
    project_logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), nullable=True)
    logo_alignment: Mapped[str] = mapped_column(String(16), default="left", nullable=False)
    mail_mode: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    smtp_config: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    smtp_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    logo: Mapped[Logo | None] = relationship(back_populates="forms", foreign_keys=[logo_id])
    project_logo: Mapped[Logo | None] = relationship(back_populates="project_forms", foreign_keys=[project_logo_id])
    fields: Mapped[list["FormField"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    versions: Mapped[list["FormVersion"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormVersion.version_major, FormVersion.version_minor",
    )
    consent_definitions: Mapped[list["ConsentDefinition"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
    )
    regulation_versions: Mapped[list["FormRegulationVersion"]] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
    )
    permissions: Mapped[list["FormPermission"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    mail_templates: Mapped[list["MailTemplate"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    mail_footers: Mapped[list["MailFooter"]] = relationship(back_populates="form", cascade="all, delete-orphan")
    regulation: Mapped["FormRegulation | None"] = relationship(
        back_populates="form",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def active(self) -> bool:
        return self.is_active

    @active.setter
    def active(self, value: bool) -> None:
        self.is_active = bool(value)


class FormVersion(Base):
    __tablename__ = "form_versions"
    __table_args__ = (
        UniqueConstraint("form_id", "version_major", "version_minor", name="uq_form_versions_number"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_form_versions_status"),
        Index("ix_form_versions_form_status", "form_id", "status"),
        Index(
            "uq_form_versions_one_published",
            "form_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
            sqlite_where=text("status = 'published'"),
        ).ddl_if(dialect=("postgresql", "sqlite")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    definition_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_version_id: Mapped[int | None] = mapped_column(ForeignKey("form_versions.id", ondelete="SET NULL"), nullable=True)
    regulation_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("form_regulation_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    form: Mapped[Form] = relationship(back_populates="versions")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    published_by: Mapped[User | None] = relationship(foreign_keys=[published_by_id])
    source_version: Mapped["FormVersion | None"] = relationship(remote_side=[id], foreign_keys=[source_version_id])
    submissions: Mapped[list[FormSubmission]] = relationship(back_populates="form_version")
    regulation_version: Mapped["FormRegulationVersion | None"] = relationship(
        foreign_keys=[regulation_version_id],
    )
    consent_links: Mapped[list["FormVersionConsent"]] = relationship(
        back_populates="form_version",
        cascade="all, delete-orphan",
        order_by="FormVersionConsent.sort_order, FormVersionConsent.id",
    )
    verification_checklists: Mapped[list["VerificationChecklistDefinition"]] = relationship(
        back_populates="form_version",
        cascade="all, delete-orphan",
        order_by="VerificationChecklistDefinition.position, VerificationChecklistDefinition.id",
    )
    sla_definitions: Mapped[list["WorkflowStepSlaDefinition"]] = relationship(
        back_populates="form_version",
        cascade="all, delete-orphan",
        order_by="WorkflowStepSlaDefinition.step_key, WorkflowStepSlaDefinition.id",
    )


class BusinessCalendar(Base):
    __tablename__ = "business_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw", nullable=False)
    weekend_days: Mapped[list] = mapped_column(JsonDict, default=lambda: [5, 6], nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    holidays: Mapped[list["BusinessCalendarHoliday"]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan", order_by="BusinessCalendarHoliday.holiday_date"
    )


class BusinessCalendarHoliday(Base):
    __tablename__ = "business_calendar_holidays"
    __table_args__ = (UniqueConstraint("business_calendar_id", "holiday_date", name="uq_business_calendar_holiday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_calendar_id: Mapped[int] = mapped_column(ForeignKey("business_calendars.id", ondelete="CASCADE"), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    calendar: Mapped[BusinessCalendar] = relationship(back_populates="holidays")


class WorkflowStepSlaDefinition(Base):
    __tablename__ = "workflow_step_sla_definitions"
    __table_args__ = (
        UniqueConstraint("form_version_id", "step_key", name="uq_workflow_step_sla_version_step"),
        CheckConstraint("deadline_unit IN ('hours', 'calendar_days', 'business_days')", name="ck_workflow_step_sla_unit"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_version_id: Mapped[int] = mapped_column(ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False)
    step_key: Mapped[str] = mapped_column(String(128), nullable=False)
    deadline_value: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    business_calendar_id: Mapped[int | None] = mapped_column(ForeignKey("business_calendars.id", ondelete="SET NULL"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="office", nullable=False)
    reminders_json: Mapped[list] = mapped_column(JsonDict, default=list, nullable=False)
    escalation_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    form_version: Mapped[FormVersion] = relationship(back_populates="sla_definitions")
    business_calendar: Mapped[BusinessCalendar | None] = relationship()
    deadlines: Mapped[list["SubmissionStepDeadline"]] = relationship(back_populates="sla_definition")


class SubmissionStepDeadline(Base):
    __tablename__ = "submission_step_deadlines"
    __table_args__ = (
        Index("ix_submission_step_deadlines_submission_status", "submission_id", "status"),
        Index("ix_submission_step_deadlines_due_status", "due_at", "status"),
        CheckConstraint("status IN ('active', 'completed', 'cancelled')", name="ck_submission_step_deadline_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    sla_definition_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_step_sla_definitions.id", ondelete="SET NULL"), nullable=True)
    workflow_step: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), default="office", nullable=False)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    sla_definition_snapshot: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)

    submission: Mapped[FormSubmission] = relationship(back_populates="step_deadlines")
    sla_definition: Mapped[WorkflowStepSlaDefinition | None] = relationship(back_populates="deadlines")
    notifications: Mapped[list["SubmissionDeadlineNotification"]] = relationship(
        back_populates="deadline", cascade="all, delete-orphan"
    )


class SubmissionDeadlineNotification(Base):
    __tablename__ = "submission_deadline_notifications"
    __table_args__ = (
        UniqueConstraint(
            "submission_step_deadline_id", "reminder_key", "recipient_email", "notification_type",
            name="uq_submission_deadline_notification_delivery",
        ),
        Index("ix_submission_deadline_notifications_status_scheduled", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_step_deadline_id: Mapped[int] = mapped_column(ForeignKey("submission_step_deadlines.id", ondelete="CASCADE"), nullable=False)
    reminder_key: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="claimed", nullable=False)
    email_log_id: Mapped[int | None] = mapped_column(ForeignKey("email_logs.id", ondelete="SET NULL"), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)

    deadline: Mapped[SubmissionStepDeadline] = relationship(back_populates="notifications")
    recipient_user: Mapped[User | None] = relationship()


class VerificationChecklistDefinition(Base):
    __tablename__ = "verification_checklist_definitions"
    __table_args__ = (
        Index("ix_verification_checklists_version_step", "form_version_id", "workflow_step"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_version_id: Mapped[int] = mapped_column(ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_step: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    form_version: Mapped[FormVersion] = relationship(back_populates="verification_checklists")
    items: Mapped[list["VerificationChecklistItemDefinition"]] = relationship(
        back_populates="checklist", cascade="all, delete-orphan",
        order_by="VerificationChecklistItemDefinition.position, VerificationChecklistItemDefinition.id",
    )


class VerificationChecklistItemDefinition(Base):
    __tablename__ = "verification_checklist_item_definitions"
    __table_args__ = (UniqueConstraint("checklist_id", "key", name="uq_verification_checklist_item_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("verification_checklist_definitions.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    document_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_not_applicable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rules_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)

    checklist: Mapped[VerificationChecklistDefinition] = relationship(back_populates="items")
    results: Mapped[list["SubmissionChecklistItemResult"]] = relationship(back_populates="item_definition")


class SubmissionChecklistItemResult(Base):
    __tablename__ = "submission_checklist_item_results"
    __table_args__ = (
        UniqueConstraint("submission_id", "checklist_item_definition_id", name="uq_submission_checklist_item_result"),
        CheckConstraint("result IN ('yes', 'no', 'not_applicable', 'pending')", name="ck_submission_checklist_item_result"),
        Index("ix_submission_checklist_results_submission_result", "submission_id", "result"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False)
    checklist_item_definition_id: Mapped[int] = mapped_column(ForeignKey("verification_checklist_item_definitions.id", ondelete="RESTRICT"), nullable=False)
    result: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    officer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    submission: Mapped[FormSubmission] = relationship(back_populates="checklist_results")
    item_definition: Mapped[VerificationChecklistItemDefinition] = relationship(back_populates="results")
    officer: Mapped[User | None] = relationship()
    evidence_links: Mapped[list["SubmissionChecklistEvidence"]] = relationship(back_populates="result_record", cascade="all, delete-orphan")
    history: Mapped[list["SubmissionChecklistResultHistory"]] = relationship(back_populates="result_record", cascade="all, delete-orphan")


class SubmissionChecklistEvidence(Base):
    __tablename__ = "submission_checklist_evidence"
    __table_args__ = (UniqueConstraint("result_id", "submission_file_id", name="uq_submission_checklist_evidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("submission_checklist_item_results.id", ondelete="CASCADE"), nullable=False)
    submission_file_id: Mapped[int] = mapped_column(ForeignKey("submission_files.id", ondelete="RESTRICT"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    result_record: Mapped[SubmissionChecklistItemResult] = relationship(back_populates="evidence_links")
    submission_file: Mapped[SubmissionFile] = relationship()


class SubmissionChecklistResultHistory(Base):
    __tablename__ = "submission_checklist_result_history"
    __table_args__ = (Index("ix_submission_checklist_history_result_changed", "result_id", "changed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("submission_checklist_item_results.id", ondelete="CASCADE"), nullable=False)
    previous_result: Mapped[str] = mapped_column(String(32), nullable=False)
    new_result: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    new_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    officer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    result_record: Mapped[SubmissionChecklistItemResult] = relationship(back_populates="history")
    officer: Mapped[User | None] = relationship()


def _reject_published_checklist_mutation(_mapper, _connection, target) -> None:
    checklist = target if isinstance(target, VerificationChecklistDefinition) else target.checklist
    if checklist.form_version.status in {"published", "archived"}:
        raise ValueError("Checklisty opublikowanej lub archiwalnej wersji formularza są immutable.")


for _model in (VerificationChecklistDefinition, VerificationChecklistItemDefinition):
    event.listen(_model, "before_update", _reject_published_checklist_mutation)
    event.listen(_model, "before_delete", _reject_published_checklist_mutation)


def _reject_checklist_history_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("Historia wyniku checklisty jest immutable.")


event.listen(SubmissionChecklistResultHistory, "before_update", _reject_checklist_history_mutation)
event.listen(SubmissionChecklistResultHistory, "before_delete", _reject_checklist_history_mutation)


class FormDraft(Base):
    __tablename__ = "form_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'submitted', 'expired', 'abandoned')", name="ck_form_drafts_status"),
        Index("ix_form_drafts_form_email_status", "form_id", "email", "status"),
        Index("ix_form_drafts_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    form_version_id: Mapped[int | None] = mapped_column(ForeignKey("form_versions.id", ondelete="RESTRICT"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    data_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_autosave_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("form_submissions.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    submission_public_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )

    form: Mapped[Form] = relationship()
    form_version: Mapped[FormVersion | None] = relationship()
    submission: Mapped[FormSubmission | None] = relationship()


class SystemMailSettings(Base):
    __tablename__ = "system_mail_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    smtp_config: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    smtp_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout_config: Mapped[dict | None] = mapped_column(JsonDict, nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_by: Mapped[User | None] = relationship()


class PlatformMailTemplate(Base):
    __tablename__ = "platform_mail_templates"
    __table_args__ = (UniqueConstraint("template_type", name="uq_platform_mail_templates_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_type: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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

    updated_by: Mapped[User | None] = relationship()


class FormField(Base):
    __tablename__ = "form_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(Text, default="", nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="text", nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    options: Mapped[dict | list] = mapped_column(JsonDict, default=list, nullable=False)
    default_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    section: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    stage: Mapped[str] = mapped_column(String(64), default="initial_submission", nullable=False)
    availability_json: Mapped[list] = mapped_column(JsonDict, default=list, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_classification: Mapped[str] = mapped_column(String(32), default="normal", server_default="normal", nullable=False)

    form: Mapped[Form] = relationship(back_populates="fields")


class FormRegulation(Base):
    __tablename__ = "form_regulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    form: Mapped[Form] = relationship(back_populates="regulation")
    uploaded_by: Mapped[User | None] = relationship()
    versions: Mapped[list["FormRegulationVersion"]] = relationship(
        back_populates="regulation",
        cascade="all, delete-orphan",
        order_by="FormRegulationVersion.version_major, FormRegulationVersion.version_minor",
    )


class FormRegulationVersion(Base):
    __tablename__ = "form_regulation_versions"
    __table_args__ = (
        UniqueConstraint("regulation_id", "version_major", "version_minor", name="uq_regulation_versions_number"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_regulation_versions_status"),
        Index("ix_regulation_versions_form_status", "form_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("form_regulations.id", ondelete="CASCADE"), nullable=False)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="Regulamin", nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    regulation: Mapped[FormRegulation] = relationship(back_populates="versions")
    form: Mapped[Form] = relationship(back_populates="regulation_versions")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])


class ConsentDefinition(Base):
    __tablename__ = "consent_definitions"
    __table_args__ = (UniqueConstraint("form_id", "consent_key", name="uq_consent_definitions_form_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    consent_key: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(64), default="other", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    form: Mapped[Form] = relationship(back_populates="consent_definitions")
    versions: Mapped[list["ConsentVersion"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
        order_by="ConsentVersion.version_major, ConsentVersion.version_minor",
    )


class ConsentVersion(Base):
    __tablename__ = "consent_versions"
    __table_args__ = (
        UniqueConstraint("consent_definition_id", "version_major", "version_minor", name="uq_consent_versions_number"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_consent_versions_status"),
        Index("ix_consent_versions_definition_status", "consent_definition_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consent_definition_id: Mapped[int] = mapped_column(ForeignKey("consent_definitions.id", ondelete="CASCADE"), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(64), default="other", nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    text_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    definition: Mapped[ConsentDefinition] = relationship(back_populates="versions")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])


class FormVersionConsent(Base):
    __tablename__ = "form_version_consents"
    __table_args__ = (UniqueConstraint("form_version_id", "consent_version_id", name="uq_form_version_consents_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_version_id: Mapped[int] = mapped_column(ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=False)
    consent_version_id: Mapped[int] = mapped_column(ForeignKey("consent_versions.id", ondelete="RESTRICT"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    form_version: Mapped[FormVersion] = relationship(back_populates="consent_links")
    consent_version: Mapped[ConsentVersion] = relationship()


class SubmissionConsent(Base):
    __tablename__ = "submission_consents"
    __table_args__ = (UniqueConstraint("submission_id", "consent_key", name="uq_submission_consents_submission_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("form_submissions.id", ondelete="CASCADE"), index=True, nullable=False)
    consent_version_id: Mapped[int | None] = mapped_column(ForeignKey("consent_versions.id", ondelete="SET NULL"), nullable=True)
    regulation_version_id: Mapped[int | None] = mapped_column(ForeignKey("form_regulation_versions.id", ondelete="SET NULL"), nullable=True)
    consent_key: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_title_snapshot: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    consent_text_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    regulation_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    regulation_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    submission: Mapped[FormSubmission] = relationship(back_populates="consents")
    consent_version_record: Mapped[ConsentVersion | None] = relationship(foreign_keys=[consent_version_id])
    regulation_version_record: Mapped[FormRegulationVersion | None] = relationship(foreign_keys=[regulation_version_id])


def _immutable_published_content(mapper, connection, target) -> None:
    state = inspect(target)
    status_history = state.attrs.status.history
    original_status = status_history.deleted[0] if status_history.deleted else target.status
    if original_status not in {"published", "archived"}:
        return
    allowed = {"status", "archived_at"}
    changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
    if changed - allowed:
        raise ValueError("Opublikowanej lub archiwalnej treści compliance nie można edytować.")


event.listen(ConsentVersion, "before_update", _immutable_published_content)
event.listen(FormRegulationVersion, "before_update", _immutable_published_content)


@event.listens_for(SubmissionConsent, "before_update")
def _immutable_submission_consent(mapper, connection, target) -> None:
    raise ValueError("Snapshotu zaakceptowanej zgody nie można edytować.")


class ContactPage(Base):
    __tablename__ = "contact_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), default="Kontakt", nullable=False)
    content_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    contact_details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    phones: Mapped[list] = mapped_column(JsonDict, default=list, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_by: Mapped[User | None] = relationship()


class ServiceDocument(Base):
    __tablename__ = "service_documents"
    __table_args__ = (UniqueConstraint("document_type", name="uq_service_documents_document_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_by: Mapped[User | None] = relationship()


class FormPermission(Base):
    __tablename__ = "form_permissions"
    __table_args__ = (UniqueConstraint("user_id", "form_id", name="uq_form_permissions_user_form"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_make_decision: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_view_sensitive_data: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_assign_submissions: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_view_internal_notes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_add_internal_notes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_internal_notes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="permissions")
    form: Mapped[Form] = relationship(back_populates="permissions")


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("key", name="uq_permissions_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="submissions", nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="form", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AccessRole(Base):
    __tablename__ = "access_roles"
    __table_args__ = (UniqueConstraint("key", name="uq_access_roles_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="form", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    permission_links: Mapped[list["AccessRolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class AccessRolePermission(Base):
    __tablename__ = "access_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_access_role_permissions_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("access_roles.id", ondelete="CASCADE"), index=True, nullable=False)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), index=True, nullable=False)

    role: Mapped[AccessRole] = relationship(back_populates="permission_links")
    permission: Mapped[Permission] = relationship()


class FormUserRole(Base):
    __tablename__ = "form_user_roles"
    __table_args__ = (UniqueConstraint("user_id", "form_id", "role_id", name="uq_form_user_roles_assignment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    form_id: Mapped[int] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("access_roles.id", ondelete="CASCADE"), index=True, nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    role: Mapped[AccessRole] = relationship()


class UserGlobalRole(Base):
    __tablename__ = "user_global_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_global_roles_assignment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("access_roles.id", ondelete="CASCADE"), index=True, nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)

    role: Mapped[AccessRole] = relationship()


class RoleAssignmentAudit(Base):
    __tablename__ = "role_assignment_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("access_roles.id", ondelete="SET NULL"), nullable=True)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id", ondelete="SET NULL"), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JsonDict, default=dict, nullable=False)


def _reject_role_assignment_audit_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("Role assignment audit is immutable.")


event.listen(RoleAssignmentAudit, "before_update", _reject_role_assignment_audit_mutation)
event.listen(RoleAssignmentAudit, "before_delete", _reject_role_assignment_audit_mutation)


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
    show_process_status: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id", ondelete="CASCADE"), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    logo_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), index=True, nullable=True)
    logo_alignment: Mapped[str] = mapped_column(String(20), default="left", nullable=False)
    logo_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_position: Mapped[str] = mapped_column(String(30), default="top", nullable=False)
    contact_html: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    links: Mapped[list | None] = mapped_column(JsonDict, default=list, nullable=True)
    legal_text: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    use_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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


class SiteFooter(Base):
    """Public-site footer configuration, independent from mail and form branding."""

    __tablename__ = "site_footers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default="Stopka strony", nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    left_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    right_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout: Mapped[str] = mapped_column(String(30), default="two_columns", nullable=False)
    social_links: Mapped[list | None] = mapped_column(JsonDict, default=list, nullable=True)
    social_position: Mapped[str] = mapped_column(String(30), default="left", nullable=False)
    social_icon_style: Mapped[str] = mapped_column(String(30), default="gold", nullable=False)
    social_show_labels: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    logo_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    logo_id: Mapped[int | None] = mapped_column(ForeignKey("logos.id", ondelete="SET NULL"), index=True, nullable=True)
    logo_alignment: Mapped[str] = mapped_column(String(20), default="left", nullable=False)
    logo_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_position: Mapped[str] = mapped_column(String(30), default="top", nullable=False)
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

    logo: Mapped[Logo | None] = relationship(back_populates="site_footers")


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int | None] = mapped_column(ForeignKey("forms.id", ondelete="SET NULL"), index=True, nullable=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("form_submissions.id", ondelete="SET NULL"), index=True, nullable=True)
    public_submission_id: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    html_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("mail_templates.id", ondelete="SET NULL"), nullable=True)
    footer_id: Mapped[int | None] = mapped_column(ForeignKey("mail_footers.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="sent", nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(100), default="email_delivery", nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    administrator_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    repeatable_group_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    repeatable_item_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    item_decision_id: Mapped[int | None] = mapped_column(ForeignKey("repeatable_group_item_decisions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
