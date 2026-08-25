from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import secrets
from typing import Iterable, Mapping

from sqlalchemy import func, select

from models import (
    FormSubmission,
    SubmissionFile,
    SubmissionTraining,
    TrainingAttendanceRecord,
    TrainingAttendanceSession,
    TrainingParticipantActionHistory,
    TrainingSurvey,
    TrainingSurveyAnswer,
    TrainingSurveyInvitation,
    TrainingSurveyQuestion,
    TrainingSurveyResponse,
)
from services.submission_training_service import ACTIVE_STATUSES, PARTICIPANT_STATUS_LABELS
from services.training_catalog_service import TrainingCatalogService


ATTENDANCE_STATUSES = {"pending", "present", "absent", "excused"}
SESSION_STATUSES = {"draft", "active", "closed"}
SURVEY_STATUSES = {"draft", "active", "closed"}
QUESTION_TYPES = {"scale", "single_choice", "multiple_choice", "text"}


class TrainingManagementError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _not_expired(value: datetime | None) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value >= _utcnow()


class TrainingManagementService:
    """Operational training state. This service never mutates FormVersion or form definitions."""

    def list_trainings(self, db, form) -> list[dict]:
        training_field = TrainingCatalogService.get_training_field(form)
        catalog = (
            TrainingCatalogService.get_trainings_for_field(training_field, active_only=False)
            if training_field else []
        )
        rows = db.execute(
            select(SubmissionTraining, FormSubmission)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(FormSubmission.form_slug == form.slug)
        ).all()
        grouped: dict[str, list[SubmissionTraining]] = defaultdict(list)
        snapshots: dict[str, dict] = {}
        for participant, _submission in rows:
            grouped[participant.training_id].append(participant)
            snapshots.setdefault(participant.training_id, participant.training_snapshot or {})
        catalog_by_id = {str(item.get("id")): item for item in catalog}
        training_ids = list(catalog_by_id)
        training_ids.extend(key for key in grouped if key not in catalog_by_id)
        result = []
        for training_id in training_ids:
            item = dict(catalog_by_id.get(training_id) or snapshots.get(training_id) or {})
            participants = grouped.get(training_id, [])
            active = [row for row in participants if row.status in ACTIVE_STATUSES]
            cancelled = [row for row in participants if row.status in {"cancelled", "removed", "unselected", "cancelled_before_signed_agreement"}]
            capacity = item.get("capacity")
            occupied = sum(1 for row in participants if row.is_locked)
            available = None if capacity in (None, "") else max(0, int(capacity) - occupied)
            signed = sum(1 for row in participants if row.status == "agreement_signed_by_office")
            attendance = db.execute(
                select(func.count(func.distinct(TrainingAttendanceRecord.submission_training_id)))
                .join(SubmissionTraining, SubmissionTraining.id == TrainingAttendanceRecord.submission_training_id)
                .where(SubmissionTraining.id.in_([row.id for row in participants] or [-1]), TrainingAttendanceRecord.status == "present")
            ).scalar() or 0
            dates = item.get("dates") or []
            result.append({
                **item,
                "id": training_id,
                "in_catalog": training_id in catalog_by_id,
                "name": item.get("name") or next((row.training_name_snapshot for row in participants), training_id),
                "status": (
                    "archived"
                    if item.get("archived") or training_id not in catalog_by_id
                    else ("active" if item.get("active", True) else "inactive")
                ),
                "date": (dates[0].get("start_date") if dates and isinstance(dates[0], Mapping) else ""),
                "participant_count": len(participants),
                "active_count": len(active),
                "cancelled_count": len(cancelled),
                "signed_agreement_count": signed,
                "present_count": int(attendance),
                "capacity": capacity,
                "available_seats": available,
            })
        return result

    def require_training(self, db, form, training_id: str) -> dict:
        training_id = str(training_id or "").strip()
        match = next((item for item in self.list_trainings(db, form) if item["id"] == training_id), None)
        if match is None:
            raise TrainingManagementError("Nie znaleziono szkolenia w tym formularzu.")
        return match

    def participants(self, db, form, training_id: str, *, include_pii: bool, filters: Mapping | None = None) -> list[dict]:
        self.require_training(db, form, training_id)
        rows = db.execute(
            select(SubmissionTraining, FormSubmission)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(FormSubmission.form_slug == form.slug, SubmissionTraining.training_id == training_id)
            .order_by(SubmissionTraining.selected_at.desc(), SubmissionTraining.id.desc())
        ).all()
        filters = filters or {}
        attendance_by_training = {}
        for training_pk, status in db.execute(
                select(TrainingAttendanceRecord.submission_training_id, TrainingAttendanceRecord.status)
                .where(TrainingAttendanceRecord.submission_training_id.in_([row.id for row, _ in rows] or [-1]))
                .order_by(TrainingAttendanceRecord.updated_at.desc())
            ).all():
            attendance_by_training.setdefault(training_pk, status)
        invitation_rows = db.execute(
            select(TrainingSurveyInvitation.submission_training_id, TrainingSurveyInvitation.completed_at)
            .where(TrainingSurveyInvitation.submission_training_id.in_([row.id for row, _ in rows] or [-1]))
        ).all()
        invited = {participant_id for participant_id, _ in invitation_rows}
        survey_completed = {participant_id for participant_id, completed_at in invitation_rows if completed_at is not None}
        result = []
        for participant, submission in rows:
            agreement = "signed" if participant.status == "agreement_signed_by_office" else ("cancelled" if participant.status in {"cancelled", "removed"} else "pending")
            attendance = attendance_by_training.get(participant.id, "pending")
            survey_sent = participant.id in invited
            if filters.get("status") and participant.status != filters["status"]:
                continue
            if filters.get("agreement") and agreement != filters["agreement"]:
                continue
            if filters.get("attendance") and attendance != filters["attendance"]:
                continue
            if filters.get("survey_sent") in {"yes", "no"} and survey_sent != (filters["survey_sent"] == "yes"):
                continue
            searchable = f"{submission.imiona} {submission.nazwisko} {submission.email}".casefold()
            if include_pii and filters.get("query") and str(filters["query"]).casefold() not in searchable:
                continue
            result.append({
                "id": participant.id,
                "submission_id": submission.submission_id,
                "name": f"{submission.imiona} {submission.nazwisko}".strip() if include_pii else "Dane zastrzeżone",
                "email": submission.email if include_pii else "",
                "phone": submission.telefon if include_pii else "",
                "status": participant.status,
                "status_label": PARTICIPANT_STATUS_LABELS.get(participant.status, participant.status),
                "agreement_status": agreement,
                "selected_at": participant.selected_at,
                "attendance": attendance,
                "survey_sent": survey_sent,
                "survey_status": "answered" if participant.id in survey_completed else ("sent" if survey_sent else "not_sent"),
                "is_active": participant.status in ACTIVE_STATUSES,
            })
        return result

    def participant(self, db, form, training_id: str, participant_id: int):
        row = db.execute(
            select(SubmissionTraining, FormSubmission)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(
                SubmissionTraining.id == participant_id,
                SubmissionTraining.training_id == training_id,
                FormSubmission.form_slug == form.slug,
            )
        ).one_or_none()
        if row is None:
            raise TrainingManagementError("Nie znaleziono uczestnictwa w tym szkoleniu.")
        return row

    def cancel_participation(self, db, form, training_id: str, participant_id: int, *, actor_id: int, reason: str):
        reason = str(reason or "").strip()
        if not reason:
            raise TrainingManagementError("Podaj powód unieważnienia.")
        participant, submission = self.participant(db, form, training_id, participant_id)
        previous = participant.status
        participant.status = "cancelled"
        participant.is_locked = False
        participant.unselected_at = _utcnow()
        participant.updated_at = _utcnow()
        files = db.execute(
            select(SubmissionFile).where(
                SubmissionFile.submission_id == submission.id,
                SubmissionFile.training_key == training_id,
                SubmissionFile.document_type.like("%agreement%"),
            )
        ).scalars().all()
        previous_files = {str(item.id): item.status for item in files}
        for item in files:
            item.status = "cancelled"
        db.add(TrainingParticipantActionHistory(
            form_id=form.id,
            training_id=training_id,
            submission_training_id=participant.id,
            action="participation_and_agreement_cancelled",
            previous_value=previous,
            new_value="cancelled",
            reason=reason,
            metadata_json={"agreement_file_previous_statuses": previous_files},
            actor_user_id=actor_id,
        ))
        return participant

    def create_attendance_session(self, db, form, training_id: str, *, name: str, session_date: date, start_time: str = "", end_time: str = "", actor_id: int):
        self.require_training(db, form, training_id)
        if not str(name or "").strip():
            raise TrainingManagementError("Nazwa listy obecności jest wymagana.")
        session = TrainingAttendanceSession(form_id=form.id, training_id=training_id, name=str(name).strip(), session_date=session_date, start_time=start_time, end_time=end_time, created_by_user_id=actor_id)
        db.add(session)
        db.flush()
        for participant, _ in self._active_participant_rows(db, form, training_id):
            db.add(TrainingAttendanceRecord(session_id=session.id, submission_training_id=participant.id))
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=training_id, action="attendance_session_created", new_value=str(session.id), actor_user_id=actor_id, metadata_json={"name": session.name}))
        return session

    def set_attendance_session_status(self, db, session: TrainingAttendanceSession, status: str, *, actor_id: int):
        if status not in SESSION_STATUSES:
            raise TrainingManagementError("Nieprawidłowy status sesji.")
        previous = session.status
        session.status = status
        session.closed_at = _utcnow() if status == "closed" else None
        db.add(TrainingParticipantActionHistory(form_id=session.form_id, training_id=session.training_id, action="attendance_session_status", previous_value=previous, new_value=status, actor_user_id=actor_id, metadata_json={"session_id": session.id}))

    def attendance_links(self, db, session: TrainingAttendanceSession, *, ttl_days: int = 14) -> list[tuple[TrainingAttendanceRecord, FormSubmission, str]]:
        rows = db.execute(
            select(TrainingAttendanceRecord, FormSubmission)
            .join(SubmissionTraining, SubmissionTraining.id == TrainingAttendanceRecord.submission_training_id)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(TrainingAttendanceRecord.session_id == session.id, SubmissionTraining.status.in_(ACTIVE_STATUSES))
        ).all()
        result = []
        for record, submission in rows:
            token = secrets.token_urlsafe(32)
            record.token_hash = _token_hash(token)
            record.token_expires_at = _utcnow() + timedelta(days=ttl_days)
            result.append((record, submission, token))
        return result

    def resolve_attendance_token(self, db, token: str) -> tuple[TrainingAttendanceRecord, TrainingAttendanceSession] | None:
        record = db.execute(select(TrainingAttendanceRecord).where(TrainingAttendanceRecord.token_hash == _token_hash(str(token or "")))).scalar_one_or_none()
        if record is None or not _not_expired(record.token_expires_at):
            return None
        session = db.get(TrainingAttendanceSession, record.session_id)
        if session is None or session.status != "active":
            return None
        return record, session

    def confirm_attendance(self, db, token: str) -> tuple[TrainingAttendanceRecord, TrainingAttendanceSession] | None:
        resolved = self.resolve_attendance_token(db, token)
        if resolved is None:
            return None
        record, session = resolved
        if record.status != "present":
            record.status = "present"
            record.confirmation_method = "email_link"
            record.confirmed_at = _utcnow()
            record.token_used_at = _utcnow()
        return record, session

    def update_attendance(self, db, form, session_id: int, participant_id: int, status: str, *, actor_id: int):
        if status not in ATTENDANCE_STATUSES - {"pending"}:
            raise TrainingManagementError("Nieprawidłowy status obecności.")
        record = db.execute(
            select(TrainingAttendanceRecord)
            .join(TrainingAttendanceSession, TrainingAttendanceSession.id == TrainingAttendanceRecord.session_id)
            .join(SubmissionTraining, SubmissionTraining.id == TrainingAttendanceRecord.submission_training_id)
            .where(TrainingAttendanceRecord.session_id == session_id, TrainingAttendanceRecord.submission_training_id == participant_id, TrainingAttendanceSession.form_id == form.id, TrainingAttendanceSession.training_id == SubmissionTraining.training_id)
        ).scalar_one_or_none()
        if record is None:
            raise TrainingManagementError("Nie znaleziono rekordu obecności.")
        attendance_session = db.get(TrainingAttendanceSession, session_id)
        previous = record.status
        record.status = status
        record.confirmation_method = "manual"
        record.confirmed_at = _utcnow()
        record.updated_by_user_id = actor_id
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=attendance_session.training_id, submission_training_id=participant_id, action="attendance_manual_update", previous_value=previous, new_value=status, actor_user_id=actor_id, metadata_json={"session_id": session_id}))
        return record

    def create_survey(self, db, form, training_id: str, *, name: str, description: str, anonymous: bool, questions: Iterable[Mapping], actor_id: int):
        self.require_training(db, form, training_id)
        if not str(name or "").strip():
            raise TrainingManagementError("Nazwa ankiety jest wymagana.")
        survey = TrainingSurvey(form_id=form.id, training_id=training_id, name=str(name).strip(), description=str(description or "").strip(), anonymous=anonymous, created_by_user_id=actor_id)
        db.add(survey)
        db.flush()
        question_count = 0
        for index, raw in enumerate(questions):
            question_type = str(raw.get("type") or "").strip()
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if question_type not in QUESTION_TYPES:
                raise TrainingManagementError("Nieprawidłowy typ pytania ankiety.")
            db.add(TrainingSurveyQuestion(survey_id=survey.id, text=text, question_type=question_type, options_json=list(raw.get("options") or []), required=bool(raw.get("required", True)), sort_order=index))
            question_count += 1
        if not question_count:
            raise TrainingManagementError("Dodaj co najmniej jedno pytanie ankiety.")
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=training_id, action="survey_created", new_value=str(survey.id), actor_user_id=actor_id, metadata_json={"name": survey.name, "anonymous": survey.anonymous}))
        return survey

    def set_survey_status(self, db, form, survey: TrainingSurvey, status: str, *, actor_id: int):
        if status not in SURVEY_STATUSES:
            raise TrainingManagementError("Nieprawidłowy status ankiety.")
        previous = survey.status
        survey.status = status
        survey.closed_at = _utcnow() if status == "closed" else None
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=survey.training_id, action="survey_status", previous_value=previous, new_value=status, actor_user_id=actor_id, metadata_json={"survey_id": survey.id}))

    def survey_links(self, db, form, survey: TrainingSurvey, *, ttl_days: int = 30) -> list[tuple[TrainingSurveyInvitation, FormSubmission, str]]:
        result = []
        for participant, submission in self._active_participant_rows(db, form, survey.training_id):
            invitation = db.execute(select(TrainingSurveyInvitation).where(TrainingSurveyInvitation.survey_id == survey.id, TrainingSurveyInvitation.submission_training_id == participant.id)).scalar_one_or_none()
            token = secrets.token_urlsafe(32)
            if invitation is None:
                invitation = TrainingSurveyInvitation(survey_id=survey.id, submission_training_id=participant.id, token_hash=_token_hash(token))
                db.add(invitation)
            else:
                invitation.token_hash = _token_hash(token)
            invitation.token_expires_at = _utcnow() + timedelta(days=ttl_days)
            invitation.sent_at = _utcnow()
            result.append((invitation, submission, token))
        return result

    def resolve_survey_invitation(self, db, token: str):
        invitation = db.execute(select(TrainingSurveyInvitation).where(TrainingSurveyInvitation.token_hash == _token_hash(str(token or "")))).scalar_one_or_none()
        if invitation is None or not _not_expired(invitation.token_expires_at):
            return None
        survey = db.get(TrainingSurvey, invitation.survey_id)
        if survey is None or survey.status != "active":
            return None
        invitation.opened_at = invitation.opened_at or _utcnow()
        questions = db.execute(select(TrainingSurveyQuestion).where(TrainingSurveyQuestion.survey_id == survey.id).order_by(TrainingSurveyQuestion.sort_order, TrainingSurveyQuestion.id)).scalars().all()
        return invitation, survey, questions

    def submit_survey(self, db, token: str, answers: Mapping[str, object]):
        resolved = self.resolve_survey_invitation(db, token)
        if resolved is None:
            return None
        invitation, survey, questions = resolved
        response = db.execute(select(TrainingSurveyResponse).where(TrainingSurveyResponse.invitation_id == invitation.id)).scalar_one_or_none()
        if response is None:
            response = TrainingSurveyResponse(invitation_id=invitation.id)
            db.add(response)
            db.flush()
        for question in questions:
            raw = answers.get(str(question.id))
            values = list(raw) if isinstance(raw, (list, tuple)) else ([str(raw)] if raw not in (None, "") else [])
            if question.required and not values:
                raise TrainingManagementError(f"Odpowiedz na pytanie: {question.text}")
            if question.question_type == "scale" and values and values[0] not in {"1", "2", "3", "4", "5"}:
                raise TrainingManagementError("Ocena musi mieścić się w skali 1–5.")
            if question.question_type == "single_choice" and len(values) > 1:
                raise TrainingManagementError("Wybierz jedną odpowiedź.")
            if question.question_type in {"single_choice", "multiple_choice"}:
                allowed = {str(item) for item in (question.options_json or [])}
                if any(value not in allowed for value in values):
                    raise TrainingManagementError("Wybrano niedozwoloną odpowiedź.")
            if question.question_type == "text" and any(len(value) > 10000 for value in values):
                raise TrainingManagementError("Odpowiedź tekstowa jest zbyt długa.")
            answer = db.execute(select(TrainingSurveyAnswer).where(TrainingSurveyAnswer.response_id == response.id, TrainingSurveyAnswer.question_id == question.id)).scalar_one_or_none()
            if answer is None:
                answer = TrainingSurveyAnswer(response_id=response.id, question_id=question.id)
                db.add(answer)
            answer.value_json = {"values": values}
        invitation.completed_at = _utcnow()
        response.updated_at = _utcnow()
        return response

    def survey_results(self, db, survey: TrainingSurvey) -> dict:
        invited = db.execute(select(func.count(TrainingSurveyInvitation.id)).where(TrainingSurveyInvitation.survey_id == survey.id)).scalar() or 0
        completed = db.execute(select(func.count(TrainingSurveyInvitation.id)).where(TrainingSurveyInvitation.survey_id == survey.id, TrainingSurveyInvitation.completed_at.is_not(None))).scalar() or 0
        questions = db.execute(select(TrainingSurveyQuestion).where(TrainingSurveyQuestion.survey_id == survey.id).order_by(TrainingSurveyQuestion.sort_order, TrainingSurveyQuestion.id)).scalars().all()
        result = []
        for question in questions:
            values = []
            for payload in db.execute(select(TrainingSurveyAnswer.value_json).join(TrainingSurveyResponse, TrainingSurveyResponse.id == TrainingSurveyAnswer.response_id).join(TrainingSurveyInvitation, TrainingSurveyInvitation.id == TrainingSurveyResponse.invitation_id).where(TrainingSurveyInvitation.survey_id == survey.id, TrainingSurveyAnswer.question_id == question.id)).scalars():
                values.extend((payload or {}).get("values") or [])
            item = {"question": question, "count": len(values), "distribution": dict(Counter(values)), "texts": values if question.question_type == "text" else []}
            if question.question_type == "scale" and values:
                item["average"] = sum(int(value) for value in values) / len(values)
            result.append(item)
        return {"invited": invited, "responses": completed, "response_rate": (completed * 100 / invited) if invited else 0, "questions": result}

    @staticmethod
    def _active_participant_rows(db, form, training_id: str):
        return db.execute(select(SubmissionTraining, FormSubmission).join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id).where(FormSubmission.form_slug == form.slug, SubmissionTraining.training_id == training_id, SubmissionTraining.status.in_(ACTIVE_STATUSES))).all()
