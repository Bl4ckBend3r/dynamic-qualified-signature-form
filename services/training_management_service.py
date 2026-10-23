from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import secrets
from statistics import mean, median
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
SURVEY_TYPES = {"survey", "pre_test", "post_test"}
ATTEMPT_POLICIES = {"single_attempt", "multiple_attempts"}
QUESTION_TYPES = {"scale", "single_choice", "multiple_choice", "true_false", "text"}


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

    def create_survey(
        self, db, form, training_id: str, *, name: str, description: str,
        anonymous: bool, questions: Iterable[Mapping], actor_id: int,
        survey_type: str = "survey", attempt_policy: str = "single_attempt",
        post_requires_attendance: bool = False,
    ):
        self.require_training(db, form, training_id)
        if not str(name or "").strip():
            raise TrainingManagementError("Nazwa ankiety jest wymagana.")
        if survey_type not in SURVEY_TYPES:
            raise TrainingManagementError("Nieprawidłowy typ ankiety/testu.")
        if attempt_policy not in ATTEMPT_POLICIES:
            raise TrainingManagementError("Nieprawidłowa polityka podejść.")
        survey = TrainingSurvey(
            form_id=form.id, training_id=training_id, name=str(name).strip(),
            description=str(description or "").strip(), anonymous=anonymous,
            survey_type=survey_type, attempt_policy=attempt_policy,
            post_requires_attendance=bool(post_requires_attendance and survey_type == "post_test"),
            created_by_user_id=actor_id,
        )
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
            options = [str(value) for value in (raw.get("options") or [])]
            if question_type == "true_false":
                options = ["true", "false"]
            correct_answers = [str(value) for value in (raw.get("correct_answers") or [])]
            is_scored = bool(raw.get("is_scored", survey_type in {"pre_test", "post_test"} and question_type in {"single_choice", "multiple_choice", "true_false"}))
            try:
                points = Decimal(str(raw.get("points", "1") if is_scored else "0"))
            except (InvalidOperation, TypeError):
                raise TrainingManagementError("Punktacja pytania musi być liczbą.")
            if points < 0:
                raise TrainingManagementError("Punktacja pytania nie może być ujemna.")
            if is_scored and (question_type not in {"single_choice", "multiple_choice", "true_false"} or not correct_answers):
                raise TrainingManagementError("Pytanie punktowane wymaga poprawnej odpowiedzi.")
            allowed = set(options)
            if any(value not in allowed for value in correct_answers):
                raise TrainingManagementError("Poprawna odpowiedź musi należeć do listy opcji.")
            db.add(TrainingSurveyQuestion(
                survey_id=survey.id, text=text, question_type=question_type,
                options_json=options, required=bool(raw.get("required", True)),
                sort_order=index, is_scored=is_scored, points=points,
                correct_answers_json=correct_answers,
                comparison_key=str(raw.get("comparison_key") or "").strip()[:128],
            ))
            question_count += 1
        if not question_count:
            raise TrainingManagementError("Dodaj co najmniej jedno pytanie ankiety.")
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=training_id, action="survey_created", new_value=str(survey.id), actor_user_id=actor_id, metadata_json={"name": survey.name, "anonymous": survey.anonymous, "survey_type": survey.survey_type}))
        return survey

    def set_survey_status(self, db, form, survey: TrainingSurvey, status: str, *, actor_id: int):
        if status not in SURVEY_STATUSES:
            raise TrainingManagementError("Nieprawidłowy status ankiety.")
        if status == "active" and survey.survey_type in {"pre_test", "post_test"}:
            other = db.execute(select(TrainingSurvey.id).where(
                TrainingSurvey.form_id == form.id,
                TrainingSurvey.training_id == survey.training_id,
                TrainingSurvey.survey_type == survey.survey_type,
                TrainingSurvey.status == "active",
                TrainingSurvey.id != survey.id,
            )).first()
            if other:
                raise TrainingManagementError("Dla szkolenia może być aktywny tylko jeden test danego typu.")
        previous = survey.status
        survey.status = status
        survey.closed_at = _utcnow() if status == "closed" else None
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=survey.training_id, action="survey_status", previous_value=previous, new_value=status, actor_user_id=actor_id, metadata_json={"survey_id": survey.id}))

    def update_test(self, db, form, survey: TrainingSurvey, *, name: str, description: str, attempt_policy: str, post_requires_attendance: bool, questions: Iterable[Mapping], actor_id: int):
        if survey.survey_type not in {"pre_test", "post_test"}:
            raise TrainingManagementError("Ta operacja dotyczy wyłącznie testów PRE/POST.")
        if survey.status != "draft":
            raise TrainingManagementError("Edytować można wyłącznie test w statusie szkicu.")
        used = db.execute(select(TrainingSurveyResponse.id).join(TrainingSurveyInvitation).where(TrainingSurveyInvitation.survey_id == survey.id).limit(1)).first()
        if used:
            raise TrainingManagementError("Test ma już podejścia. Utwórz nowy szkic, aby zachować historię wyników.")
        if not str(name or "").strip() or attempt_policy not in ATTEMPT_POLICIES:
            raise TrainingManagementError("Uzupełnij nazwę i prawidłową politykę podejść.")
        existing = db.execute(select(TrainingSurveyQuestion).where(TrainingSurveyQuestion.survey_id == survey.id)).scalars().all()
        for question in existing:
            db.delete(question)
        db.flush()
        survey.name = str(name).strip()
        survey.description = str(description or "").strip()
        survey.attempt_policy = attempt_policy
        survey.post_requires_attendance = bool(post_requires_attendance and survey.survey_type == "post_test")
        count = 0
        for index, raw in enumerate(questions):
            question_type = str(raw.get("type") or "").strip()
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if question_type not in QUESTION_TYPES:
                raise TrainingManagementError("Nieprawidłowy typ pytania testowego.")
            options = [str(value) for value in (raw.get("options") or [])]
            if question_type == "true_false":
                options = ["true", "false"]
            correct = [str(value) for value in (raw.get("correct_answers") or [])]
            is_scored = question_type in {"single_choice", "multiple_choice", "true_false"}
            try:
                points = Decimal(str(raw.get("points", "1") if is_scored else "0"))
            except (InvalidOperation, TypeError):
                raise TrainingManagementError("Punktacja pytania musi być liczbą.")
            if points < 0 or (is_scored and (not correct or any(value not in set(options) for value in correct))):
                raise TrainingManagementError("Sprawdź punktację i poprawne odpowiedzi pytania.")
            db.add(TrainingSurveyQuestion(survey_id=survey.id, text=text, question_type=question_type, options_json=options, required=True, sort_order=index, is_scored=is_scored, points=points, correct_answers_json=correct, comparison_key=str(raw.get("comparison_key") or "")[:128]))
            count += 1
        if not count:
            raise TrainingManagementError("Dodaj co najmniej jedno pytanie testowe.")
        db.add(TrainingParticipantActionHistory(form_id=form.id, training_id=survey.training_id, action="survey_updated", new_value=str(survey.id), actor_user_id=actor_id, metadata_json={"survey_type": survey.survey_type, "name": survey.name}))

    def survey_links(self, db, form, survey: TrainingSurvey, *, ttl_days: int = 30) -> list[tuple[TrainingSurveyInvitation, FormSubmission, str]]:
        result = []
        for participant, submission in self._active_participant_rows(db, form, survey.training_id):
            if survey.survey_type == "post_test" and survey.post_requires_attendance and not self._participant_is_present(db, participant.id):
                continue
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
        participant = db.get(SubmissionTraining, invitation.submission_training_id)
        if participant is None or participant.training_id != survey.training_id or participant.status not in ACTIVE_STATUSES:
            return None
        if survey.survey_type == "post_test" and survey.post_requires_attendance and not self._participant_is_present(db, participant.id):
            return None
        invitation.opened_at = invitation.opened_at or _utcnow()
        questions = db.execute(select(TrainingSurveyQuestion).where(TrainingSurveyQuestion.survey_id == survey.id).order_by(TrainingSurveyQuestion.sort_order, TrainingSurveyQuestion.id)).scalars().all()
        return invitation, survey, questions

    def submit_survey(self, db, token: str, answers: Mapping[str, object]):
        resolved = self.resolve_survey_invitation(db, token)
        if resolved is None:
            return None
        invitation, survey, questions = resolved
        responses = db.execute(select(TrainingSurveyResponse).where(TrainingSurveyResponse.invitation_id == invitation.id).order_by(TrainingSurveyResponse.attempt_number)).scalars().all()
        completed = [item for item in responses if item.status == "completed"]
        if survey.survey_type in {"pre_test", "post_test"} and survey.attempt_policy == "single_attempt" and completed:
            raise TrainingManagementError("Ten test został już ukończony.")
        if survey.survey_type == "survey":
            response = responses[0] if responses else None
            attempt_number = 1
        else:
            response = None
            attempt_number = max((item.attempt_number for item in responses), default=0) + 1
        if response is None:
            response = TrainingSurveyResponse(invitation_id=invitation.id, attempt_number=attempt_number, status="in_progress")
            db.add(response)
            db.flush()
        score_points = Decimal("0")
        max_points = Decimal("0")
        snapshot_questions = []
        for question in questions:
            raw = answers.get(str(question.id))
            values = list(raw) if isinstance(raw, (list, tuple)) else ([str(raw)] if raw not in (None, "") else [])
            if question.required and not values:
                raise TrainingManagementError(f"Odpowiedz na pytanie: {question.text}")
            if question.question_type == "scale" and values and values[0] not in {"1", "2", "3", "4", "5"}:
                raise TrainingManagementError("Ocena musi mieścić się w skali 1–5.")
            if question.question_type == "single_choice" and len(values) > 1:
                raise TrainingManagementError("Wybierz jedną odpowiedź.")
            if question.question_type in {"single_choice", "multiple_choice", "true_false"}:
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
            correct = None
            awarded = None
            points_max = None
            if question.is_scored:
                expected = {str(value) for value in (question.correct_answers_json or [])}
                correct = set(values) == expected
                points_max = Decimal(str(question.points or 0))
                awarded = points_max if correct else Decimal("0")
                max_points += points_max
                score_points += awarded
            question_snapshot = {
                "question_id": question.id, "question_key": question.comparison_key,
                "text": question.text, "question_type": question.question_type,
                "options": list(question.options_json or []),
                "correct_answers": list(question.correct_answers_json or []),
                "is_scored": bool(question.is_scored), "points": str(question.points or 0),
            }
            answer.question_snapshot_json = question_snapshot
            answer.is_correct = correct
            answer.points_awarded = awarded
            answer.points_max = points_max
            snapshot_questions.append(question_snapshot)
        completed_at = _utcnow()
        invitation.completed_at = completed_at
        response.status = "completed"
        response.completed_at = completed_at
        response.submitted_at = completed_at
        response.score_points = score_points if survey.survey_type != "survey" else None
        response.max_points = max_points if survey.survey_type != "survey" else None
        response.score_percent = (score_points * Decimal("100") / max_points).quantize(Decimal("0.01")) if max_points else (Decimal("0") if survey.survey_type != "survey" else None)
        response.test_snapshot_json = {
            "survey_id": survey.id, "survey_type": survey.survey_type,
            "name": survey.name, "attempt_policy": survey.attempt_policy,
            "questions": snapshot_questions,
        }
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
        attempts = self.test_attempt_rows(db, survey) if survey.survey_type in {"pre_test", "post_test"} else []
        scores = [row["score_percent"] for row in attempts if row["score_percent"] is not None]
        summary = {
            "participants": invited,
            "started": len({row["submission_training_id"] for row in attempts}),
            "completed": len({row["submission_training_id"] for row in attempts if row["status"] == "completed"}),
            "mean": mean(scores) if scores else None,
            "median": median(scores) if scores else None,
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        }
        return {"invited": invited, "responses": completed, "response_rate": (completed * 100 / invited) if invited else 0, "questions": result, "attempts": attempts, "summary": summary}

    def test_attempt_rows(self, db, survey: TrainingSurvey, *, include_pii: bool = False) -> list[dict]:
        rows = db.execute(
            select(TrainingSurveyResponse, TrainingSurveyInvitation, SubmissionTraining, FormSubmission)
            .join(TrainingSurveyInvitation, TrainingSurveyInvitation.id == TrainingSurveyResponse.invitation_id)
            .join(SubmissionTraining, SubmissionTraining.id == TrainingSurveyInvitation.submission_training_id)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(TrainingSurveyInvitation.survey_id == survey.id)
            .order_by(SubmissionTraining.id, TrainingSurveyResponse.attempt_number)
        ).all()
        return [self._attempt_row(survey, response, invitation, participant, submission, include_pii=include_pii) for response, invitation, participant, submission in rows]

    @staticmethod
    def _attempt_row(survey, response, invitation, participant, submission, *, include_pii: bool) -> dict:
        row = {
            "test_id": survey.id, "test_type": survey.survey_type, "test_title": survey.name,
            "participant_id": participant.id, "submission_training_id": participant.id,
            "submission_id": submission.submission_id, "attempt_number": response.attempt_number,
            "status": response.status, "started_at": response.started_at,
            "completed_at": response.completed_at, "score_points": float(response.score_points) if response.score_points is not None else None,
            "max_points": float(response.max_points) if response.max_points is not None else None,
            "score_percent": float(response.score_percent) if response.score_percent is not None else None,
        }
        if include_pii:
            row.update(participant_name=f"{submission.imiona} {submission.nazwisko}".strip(), participant_email=submission.email)
        return row

    def comparison(self, db, form, training_id: str, *, include_pii: bool = False) -> dict:
        surveys = db.execute(select(TrainingSurvey).where(
            TrainingSurvey.form_id == form.id, TrainingSurvey.training_id == training_id,
            TrainingSurvey.survey_type.in_({"pre_test", "post_test"}),
        ).order_by(TrainingSurvey.id.desc())).scalars().all()
        selected = {}
        for survey in surveys:
            selected.setdefault(survey.survey_type, survey)
        attempts_by_type = {}
        for survey_type, survey in selected.items():
            latest = {}
            for row in self.test_attempt_rows(db, survey, include_pii=include_pii):
                if row["status"] == "completed":
                    latest[row["submission_training_id"]] = row
            attempts_by_type[survey_type] = latest
        participants = self._active_participant_rows(db, form, training_id)
        rows = []
        changes = []
        for participant, submission in participants:
            pre = attempts_by_type.get("pre_test", {}).get(participant.id)
            post = attempts_by_type.get("post_test", {}).get(participant.id)
            change = None
            if pre and post and pre["score_percent"] is not None and post["score_percent"] is not None:
                change = post["score_percent"] - pre["score_percent"]
                changes.append(change)
            row = {
                "participant_id": participant.id, "submission_training_id": participant.id,
                "submission_id": submission.submission_id, "pre": pre, "post": post,
                "change_percentage_points": change,
                "status": "both_completed" if pre and post else ("missing_post" if pre else ("missing_pre" if post else "none")),
            }
            if include_pii:
                row.update(participant_name=f"{submission.imiona} {submission.nazwisko}".strip(), participant_email=submission.email)
            rows.append(row)
        pre_scores = [row["pre"]["score_percent"] for row in rows if row["pre"] and row["pre"]["score_percent"] is not None]
        post_scores = [row["post"]["score_percent"] for row in rows if row["post"] and row["post"]["score_percent"] is not None]
        return {"rows": rows, "pre_survey": selected.get("pre_test"), "post_survey": selected.get("post_test"), "summary": {"mean_pre": mean(pre_scores) if pre_scores else None, "mean_post": mean(post_scores) if post_scores else None, "mean_change": mean(changes) if changes else None}}

    def detailed_answer_rows(self, db, survey: TrainingSurvey, *, include_pii: bool = False) -> list[dict]:
        rows = db.execute(
            select(TrainingSurveyAnswer, TrainingSurveyResponse, TrainingSurveyInvitation, SubmissionTraining, FormSubmission)
            .join(TrainingSurveyResponse, TrainingSurveyResponse.id == TrainingSurveyAnswer.response_id)
            .join(TrainingSurveyInvitation, TrainingSurveyInvitation.id == TrainingSurveyResponse.invitation_id)
            .join(SubmissionTraining, SubmissionTraining.id == TrainingSurveyInvitation.submission_training_id)
            .join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id)
            .where(TrainingSurveyInvitation.survey_id == survey.id)
            .order_by(SubmissionTraining.id, TrainingSurveyResponse.attempt_number, TrainingSurveyAnswer.id)
        ).all()
        result = []
        for answer, response, _invitation, participant, submission in rows:
            snapshot = answer.question_snapshot_json or {}
            row = {
                "test_type": survey.survey_type, "test_id": survey.id,
                "participant_id": participant.id, "submission_training_id": participant.id,
                "submission_id": submission.submission_id, "attempt_number": response.attempt_number,
                "question_id": snapshot.get("question_id", answer.question_id),
                "question_key": snapshot.get("question_key", ""), "question_text": snapshot.get("text", ""),
                "question_type": snapshot.get("question_type", ""), "answer": list((answer.value_json or {}).get("values") or []),
                "is_correct": answer.is_correct, "points_awarded": float(answer.points_awarded) if answer.points_awarded is not None else None,
                "points_max": float(answer.points_max) if answer.points_max is not None else None,
                "completed_at": response.completed_at,
            }
            if include_pii:
                row.update(participant_name=f"{submission.imiona} {submission.nazwisko}".strip(), participant_email=submission.email)
            result.append(row)
        return result

    @staticmethod
    def _participant_is_present(db, participant_id: int) -> bool:
        return bool(db.execute(select(TrainingAttendanceRecord.id).where(
            TrainingAttendanceRecord.submission_training_id == participant_id,
            TrainingAttendanceRecord.status == "present",
        ).limit(1)).first())

    @staticmethod
    def _active_participant_rows(db, form, training_id: str):
        return db.execute(select(SubmissionTraining, FormSubmission).join(FormSubmission, FormSubmission.id == SubmissionTraining.submission_id).where(FormSubmission.form_slug == form.slug, SubmissionTraining.training_id == training_id, SubmissionTraining.status.in_(ACTIVE_STATUSES))).all()
