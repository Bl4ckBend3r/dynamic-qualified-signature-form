from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from models import (
    BusinessCalendar,
    FormSubmission,
    MailTemplate,
    SubmissionDeadlineNotification,
    SubmissionStepDeadline,
    User,
    WorkflowStepSlaDefinition,
)
from services.permission_service import PermissionService


SUPPORTED_UNITS = {"hours", "calendar_days", "business_days"}
SLA_MAIL_EVENTS = {"sla_before_deadline", "sla_deadline", "sla_overdue", "sla_escalation"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_duration(
    start: datetime,
    value: int,
    unit: str,
    *,
    weekend_days: Iterable[int] = (5, 6),
    holidays: Iterable[date] = (),
) -> datetime:
    """Add a portable SLA duration while preserving the local wall-clock time."""
    unit = str(unit or "").strip()
    if unit not in SUPPORTED_UNITS:
        raise ValueError(f"Nieobsługiwana jednostka SLA: {unit}")
    value = int(value)
    if unit == "hours":
        return start + timedelta(hours=value)
    if unit == "calendar_days":
        return start + timedelta(days=value)
    if value == 0:
        return start

    weekend = {int(item) for item in weekend_days}
    holiday_dates = set(holidays)
    direction = 1 if value > 0 else -1
    remaining = abs(value)
    result = start
    while remaining:
        result += timedelta(days=direction)
        if result.weekday() not in weekend and result.date() not in holiday_dates:
            remaining -= 1
    return result


@dataclass(frozen=True)
class DeadlineView:
    state: str
    overdue_seconds: int


class WorkflowSlaService:
    def __init__(self, mail_dispatch_service=None) -> None:
        self.mail_dispatch_service = mail_dispatch_service

    @staticmethod
    def state(deadline: SubmissionStepDeadline, *, now: datetime | None = None) -> DeadlineView:
        now = _utc(now or datetime.now(timezone.utc))
        if deadline.status == "completed" or deadline.completed_at:
            return DeadlineView("completed", 0)
        if deadline.status == "cancelled" or deadline.cancelled_at:
            return DeadlineView("cancelled", 0)
        due_at = _utc(deadline.due_at)
        delta = int((now - due_at).total_seconds())
        if delta > 0:
            return DeadlineView("overdue", delta)
        if (due_at - now) <= timedelta(hours=48):
            return DeadlineView("due_soon", 0)
        return DeadlineView("active", 0)

    def initialize_submission(self, session_factory, *, public_submission_id: str, form_config: dict | None = None) -> None:
        with session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == public_submission_id)
            ).scalar_one_or_none()
            if submission is None:
                return
            self.enter_step(
                db,
                submission,
                str(submission.workflow_stage or submission.workflow_step or "submission"),
                form_config=form_config,
            )
            db.commit()

    def transition(
        self,
        db,
        submission: FormSubmission,
        *,
        previous_step: str | None,
        new_step: str | None,
        form_config: dict | None = None,
        now: datetime | None = None,
    ) -> SubmissionStepDeadline | None:
        if str(previous_step or "") == str(new_step or ""):
            return None
        now = _utc(now or datetime.now(timezone.utc))
        for row in db.execute(
            select(SubmissionStepDeadline).where(
                SubmissionStepDeadline.submission_id == submission.id,
                SubmissionStepDeadline.status == "active",
            )
        ).scalars():
            row.status = "completed"
            row.completed_at = now
        return self.enter_step(db, submission, str(new_step or ""), form_config=form_config, now=now)

    def enter_step(
        self,
        db,
        submission: FormSubmission,
        step_key: str,
        *,
        form_config: dict | None = None,
        now: datetime | None = None,
    ) -> SubmissionStepDeadline | None:
        step_key = str(step_key or "").strip()
        if not step_key or submission.form_version_id is None:
            return None
        existing = db.execute(
            select(SubmissionStepDeadline).where(
                SubmissionStepDeadline.submission_id == submission.id,
                SubmissionStepDeadline.workflow_step == step_key,
                SubmissionStepDeadline.status == "active",
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        config = form_config or (submission.form_version.definition_json if submission.form_version else {}) or {}
        sla = self._sla_for_step(config, step_key)
        if sla is None:
            return None
        definition = self._definition(db, submission.form_version_id, step_key, sla)
        entered_at = _utc(now or datetime.now(timezone.utc))
        weekend, holidays = self._calendar_parts(definition.business_calendar)
        calendar_timezone = self._calendar_timezone(definition.business_calendar)
        calculation_start = entered_at if definition.deadline_unit == "hours" else entered_at.astimezone(calendar_timezone)
        due_at = add_duration(
            calculation_start,
            definition.deadline_value,
            definition.deadline_unit,
            weekend_days=weekend,
            holidays=holidays,
        ).astimezone(timezone.utc)
        snapshot = {
            "deadline": {"value": definition.deadline_value, "unit": definition.deadline_unit},
            "business_calendar_id": definition.business_calendar_id,
            "weekend_days": list(weekend),
            "holidays": sorted(item.isoformat() for item in holidays),
            "timezone_name": str(calendar_timezone.key),
            "actor_type": definition.actor_type,
            "reminders": list(definition.reminders_json or []),
            "escalation": dict(definition.escalation_json or {}),
        }
        row = SubmissionStepDeadline(
            submission=submission,
            sla_definition=definition,
            workflow_step=step_key,
            actor_type=definition.actor_type,
            entered_at=entered_at,
            due_at=due_at,
            status="active",
            sla_definition_snapshot=snapshot,
        )
        db.add(row)
        db.flush()
        return row

    def process_due(self, db, *, now: datetime | None = None) -> dict[str, int]:
        now = _utc(now or datetime.now(timezone.utc))
        rows = db.execute(
            select(SubmissionStepDeadline).where(
                SubmissionStepDeadline.status == "active",
                SubmissionStepDeadline.due_at <= now + timedelta(days=366),
            ).order_by(SubmissionStepDeadline.due_at, SubmissionStepDeadline.id)
        ).scalars().all()
        result = {"examined": len(rows), "claimed": 0, "sent": 0, "skipped": 0, "failed": 0}
        for deadline in rows:
            for schedule in self._schedules(deadline):
                if _utc(schedule["scheduled_for"]) > now:
                    continue
                recipients = self._recipients(db, deadline, schedule["recipients"])
                for recipient_email, recipient_user_id in recipients:
                    notification = SubmissionDeadlineNotification(
                        submission_step_deadline_id=deadline.id,
                        reminder_key=schedule["key"],
                        recipient_email=recipient_email,
                        recipient_user_id=recipient_user_id,
                        notification_type=schedule["event_type"],
                        scheduled_for=schedule["scheduled_for"],
                        claimed_at=now,
                        status="claimed",
                    )
                    db.add(notification)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        continue
                    result["claimed"] += 1
                    outcome = self._dispatch(db, deadline.id, notification.id, now=now)
                    result[outcome] += 1
        return result

    def _dispatch(self, db, deadline_id: int, notification_id: int, *, now: datetime) -> str:
        deadline = db.get(SubmissionStepDeadline, deadline_id)
        notification = db.get(SubmissionDeadlineNotification, notification_id)
        if deadline is None or notification is None or deadline.status != "active":
            if notification is not None:
                notification.status = "skipped"
                notification.error_message = "Etap został zakończony lub anulowany."
                db.commit()
            return "skipped"
        if self.mail_dispatch_service is None:
            notification.status = "skipped"
            notification.error_message = "Brak skonfigurowanego dispatchera poczty."
            db.commit()
            return "skipped"
        submission = deadline.submission
        form = submission.form_version.form if submission.form_version else None
        if form is None:
            notification.status = "failed"
            notification.error_message = "Nie znaleziono formularza dla wersji zgłoszenia."
            db.commit()
            return "failed"
        templates = db.execute(
            select(MailTemplate).where(
                MailTemplate.form_id == form.id,
                MailTemplate.is_active.is_(True),
                or_(
                    MailTemplate.template_type == notification.notification_type,
                    MailTemplate.trigger_event == notification.notification_type,
                ),
            )
        ).scalars().all()
        template = self.mail_dispatch_service.select_template(
            templates, submission=submission, event_type=notification.notification_type
        )
        context = self._mail_context(deadline, now)
        dispatch = self.mail_dispatch_service.dispatch_to_submission(
            db=db,
            form=form,
            submission=submission,
            template=template,
            to_email=notification.recipient_email,
            event_type=notification.notification_type,
            extra_context=context,
        )
        notification.email_log_id = getattr(getattr(dispatch, "log", None), "id", None)
        notification.status = "sent" if dispatch.status == "sent" else ("failed" if dispatch.status == "failed" else "skipped")
        notification.sent_at = now if notification.status == "sent" else None
        notification.error_message = str(getattr(dispatch, "error_message", "") or "")
        db.commit()
        return notification.status

    @staticmethod
    def _sla_for_step(form_config: Mapping[str, Any], step_key: str) -> dict | None:
        workflow = form_config.get("workflow") if isinstance(form_config.get("workflow"), Mapping) else {}
        for step in workflow.get("steps") or []:
            if not isinstance(step, Mapping) or str(step.get("id") or "") != step_key:
                continue
            raw = step.get("sla") if isinstance(step.get("sla"), Mapping) else {}
            deadline = raw.get("deadline") if isinstance(raw.get("deadline"), Mapping) else step.get("deadline")
            if not isinstance(deadline, Mapping):
                return None
            unit = str(deadline.get("unit") or "").strip()
            try:
                value = int(deadline.get("value"))
            except (TypeError, ValueError):
                return None
            if value < 0 or unit not in SUPPORTED_UNITS or raw.get("active", True) is False:
                return None
            return {
                "deadline_value": value,
                "deadline_unit": unit,
                "business_calendar_id": raw.get("business_calendar_id") or step.get("business_calendar_id"),
                "actor_type": str(raw.get("actor_type") or ("participant" if step.get("requires_user_action") else "office")),
                "reminders": list(raw.get("reminders") or step.get("reminders") or []),
                "escalation": dict(raw.get("escalation") or step.get("escalation") or {}),
            }
        return None

    @staticmethod
    def _definition(db, form_version_id: int, step_key: str, sla: dict) -> WorkflowStepSlaDefinition:
        row = db.execute(
            select(WorkflowStepSlaDefinition).where(
                WorkflowStepSlaDefinition.form_version_id == form_version_id,
                WorkflowStepSlaDefinition.step_key == step_key,
            )
        ).scalar_one_or_none()
        if row is None:
            row = WorkflowStepSlaDefinition(form_version_id=form_version_id, step_key=step_key)
            db.add(row)
        row.deadline_value = sla["deadline_value"]
        row.deadline_unit = sla["deadline_unit"]
        row.business_calendar_id = sla.get("business_calendar_id")
        row.actor_type = sla["actor_type"] if sla["actor_type"] in {"participant", "office"} else "office"
        row.reminders_json = sla["reminders"]
        row.escalation_json = sla["escalation"]
        row.active = True
        db.flush()
        return row

    @staticmethod
    def _calendar_parts(calendar: BusinessCalendar | None) -> tuple[tuple[int, ...], set[date]]:
        if calendar is None:
            return (5, 6), set()
        weekend = tuple(int(item) for item in (calendar.weekend_days or [5, 6]))
        return weekend, {item.holiday_date for item in calendar.holidays}

    @staticmethod
    def _calendar_timezone(calendar: BusinessCalendar | None) -> ZoneInfo:
        name = str(getattr(calendar, "timezone_name", "") or "Europe/Warsaw")
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("Europe/Warsaw")

    def _schedules(self, deadline: SubmissionStepDeadline) -> list[dict]:
        snapshot = deadline.sla_definition_snapshot or {}
        weekend = snapshot.get("weekend_days") or [5, 6]
        holidays = {date.fromisoformat(item) for item in snapshot.get("holidays") or []}
        try:
            calendar_timezone = ZoneInfo(str(snapshot.get("timezone_name") or "Europe/Warsaw"))
        except ZoneInfoNotFoundError:
            calendar_timezone = ZoneInfo("Europe/Warsaw")
        schedules = []
        for index, item in enumerate(snapshot.get("reminders") or []):
            if not isinstance(item, Mapping):
                continue
            try:
                offset = int(item.get("offset", 0))
            except (TypeError, ValueError):
                continue
            unit = str(item.get("unit") or "calendar_days")
            if unit == "days":
                unit = "calendar_days"
            if unit not in SUPPORTED_UNITS:
                continue
            event_type = "sla_before_deadline" if offset < 0 else ("sla_deadline" if offset == 0 else "sla_overdue")
            schedule_start = deadline.due_at if unit == "hours" else _utc(deadline.due_at).astimezone(calendar_timezone)
            scheduled_for = add_duration(schedule_start, offset, unit, weekend_days=weekend, holidays=holidays).astimezone(timezone.utc)
            schedules.append({
                "key": str(item.get("key") or f"reminder-{index}"),
                "event_type": event_type,
                "scheduled_for": scheduled_for,
                "recipients": item.get("recipients") or item.get("recipient") or [snapshot.get("actor_type", "office")],
            })
        escalation = snapshot.get("escalation") or {}
        if isinstance(escalation, Mapping) and escalation.get("recipients"):
            try:
                offset = int(escalation.get("offset", escalation.get("value", 0)))
                unit = str(escalation.get("unit") or "calendar_days")
                if unit == "days":
                    unit = "calendar_days"
                if unit in SUPPORTED_UNITS and offset >= 0:
                    schedule_start = deadline.due_at if unit == "hours" else _utc(deadline.due_at).astimezone(calendar_timezone)
                    schedules.append({
                        "key": str(escalation.get("key") or "escalation"),
                        "event_type": "sla_escalation",
                        "scheduled_for": add_duration(schedule_start, offset, unit, weekend_days=weekend, holidays=holidays).astimezone(timezone.utc),
                        "recipients": escalation["recipients"],
                    })
            except (TypeError, ValueError):
                pass
        return schedules

    @staticmethod
    def _recipient_specs(value: Any) -> list[Any]:
        if isinstance(value, (str, Mapping)):
            return [value]
        return list(value or [])

    def _recipients(self, db, deadline: SubmissionStepDeadline, specs: Any) -> list[tuple[str, int | None]]:
        submission = deadline.submission
        form_id = submission.form_version.form_id if submission.form_version else None
        permission_service = PermissionService()
        result: dict[str, int | None] = {}
        for spec in self._recipient_specs(specs):
            kind = str(spec.get("type") if isinstance(spec, Mapping) else spec or "").strip()
            role = str(spec.get("role") or "") if isinstance(spec, Mapping) else ""
            if kind.startswith("role:"):
                role, kind = kind.split(":", 1)[1], "role"
            if kind == "office":
                assigned = submission.assigned_to
                kind = "assigned_officer" if (
                    assigned
                    and form_id is not None
                    and permission_service.has_permission(
                        db, assigned, "can_view_submissions", form=form_id
                    )
                ) else "form_managers"
            if kind == "participant" and submission.email:
                result[submission.email.strip().casefold()] = None
            elif kind == "assigned_officer" and submission.assigned_to and submission.assigned_to.email:
                if form_id is not None and permission_service.has_permission(
                    db, submission.assigned_to, "can_view_submissions", form=form_id
                ):
                    result[submission.assigned_to.email.strip().casefold()] = submission.assigned_to.id
            elif kind in {"form_managers", "role"} and form_id is not None:
                if kind == "form_managers":
                    users = [
                        user for user in permission_service.users_with_permission(
                            db, "can_edit_workflow", form_id
                        )
                        if permission_service.has_permission(
                            db, user, "can_view_submissions", form=form_id
                        )
                    ]
                else:
                    users = [
                        user for user in permission_service.users_with_form_role(db, role, form_id)
                        if permission_service.has_permission(
                            db, user, "can_view_submissions", form=form_id
                        )
                    ]
                for user in users:
                    result[user.email.strip().casefold()] = user.id
        return [(email, user_id) for email, user_id in result.items() if email]

    @staticmethod
    def _mail_context(deadline: SubmissionStepDeadline, now: datetime) -> dict[str, Any]:
        submission = deadline.submission
        definition = (submission.form_version.definition_json if submission.form_version else {}) or {}
        workflow = definition.get("workflow") or {}
        step = next((item for item in workflow.get("steps") or [] if item.get("id") == deadline.workflow_step), {})
        overdue_seconds = max(0, int((_utc(now) - _utc(deadline.due_at)).total_seconds()))
        return {
            "participant_name": " ".join(filter(None, [submission.imiona, submission.nazwisko])).strip(),
            "submission_id": submission.submission_id,
            "step_label": step.get("admin_label") or step.get("label") or deadline.workflow_step,
            "due_at": deadline.due_at,
            "overdue_by": timedelta(seconds=overdue_seconds),
            "overdue_hours": overdue_seconds // 3600,
            "assigned_officer": submission.assigned_to.email if submission.assigned_to else "",
            "form_name": submission.form_name,
            "sla_actor_type": deadline.actor_type,
        }
