from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select

from models import (
    Form,
    FormAssignmentState,
    FormPermission,
    FormSubmission,
    SubmissionAssignmentHistory,
    SubmissionStepDeadline,
    User,
)


PRIORITIES = ("low", "normal", "high", "urgent")
PRIORITY_LABELS = {"low": "Niski", "normal": "Normalny", "high": "Wysoki", "urgent": "Pilny"}
ASSIGNMENT_SOURCES = ("manual", "round_robin", "workflow_rule", "system")
REVIEW_STATUSES = ("FORM_SUBMITTED", "WAITING_FOR_OFFICER_DECISION", "WAITING_FOR_REVIEW")
OFFICE_SIGNATURE_STATUSES = ("AGREEMENT_WAITING_FOR_OFFICE_SIGNATURE",)
FINAL_STATUSES = (
    "COMPLETED", "CANCELLED", "OFFICER_REJECTED", "AUTO_REJECTED",
    "AGREEMENT_SIGNED_BY_OFFICE", "AGREEMENT_REJECTED_BY_OFFICE",
)
ADMIN_ROLES = ("super_admin", "admin", "form_manager")


class SubmissionAssignmentError(ValueError):
    pass


class SubmissionAssignmentPermissionError(SubmissionAssignmentError):
    pass


class SubmissionAssignmentService:
    """Owns assignment mutations, audit rows, queues and automatic routing."""

    def _permission(self, db, user_id: int, form_id: int) -> FormPermission | None:
        return db.execute(
            select(FormPermission).where(
                FormPermission.user_id == user_id,
                FormPermission.form_id == form_id,
            )
        ).scalar_one_or_none()

    def can_assign(self, db, actor: User, form: Form) -> bool:
        if actor.role == "super_admin":
            return True
        permission = self._permission(db, actor.id, form.id)
        return bool(permission and permission.can_assign_submissions)

    def can_review(self, db, user: User, form: Form) -> bool:
        if not user.is_active or user.is_blocked or user.role not in ADMIN_ROLES:
            return False
        if user.role == "super_admin":
            return True
        permission = self._permission(db, user.id, form.id)
        return bool(permission and permission.can_review)

    def eligible_users(
        self,
        db,
        form: Form,
        *,
        configured_user_ids: Iterable[int] | None = None,
    ) -> list[User]:
        configured = {int(item) for item in (configured_user_ids or ()) if str(item).isdigit()}
        permission_user_ids = select(FormPermission.user_id).where(
            FormPermission.form_id == form.id,
            FormPermission.can_review.is_(True),
        )
        query = select(User).where(
            User.is_active.is_(True),
            User.is_blocked.is_(False),
            User.role.in_(ADMIN_ROLES),
            or_(User.role == "super_admin", User.id.in_(permission_user_ids)),
        )
        if configured:
            query = query.where(User.id.in_(configured))
        elif configured_user_ids is not None:
            return []
        return db.execute(query.order_by(User.id)).scalars().all()

    def assign(
        self,
        db,
        submission: FormSubmission,
        form: Form,
        *,
        assignee_id: int | None,
        actor: User | None,
        reason: str = "",
        source: str = "manual",
        authorize: bool = True,
    ) -> SubmissionAssignmentHistory | None:
        if source not in ASSIGNMENT_SOURCES:
            raise SubmissionAssignmentError("Nieprawidłowe źródło przydziału.")
        if authorize and (actor is None or not self.can_assign(db, actor, form)):
            raise SubmissionAssignmentPermissionError("Brak uprawnienia do przydzielania spraw.")
        if submission.form_slug != form.slug:
            raise SubmissionAssignmentError("Zgłoszenie nie należy do wskazanego formularza.")
        previous_user_id = submission.assigned_to_user_id
        if previous_user_id == assignee_id:
            return None
        assignee = db.get(User, assignee_id) if assignee_id is not None else None
        if assignee_id is not None and (assignee is None or not self.can_review(db, assignee, form)):
            raise SubmissionAssignmentError("Wybrany użytkownik nie może prowadzić spraw tego formularza.")
        now = datetime.now(timezone.utc)
        history = SubmissionAssignmentHistory(
            submission_id=submission.id,
            assigned_to_user_id=assignee_id,
            assigned_by_user_id=actor.id if actor else None,
            previous_user_id=previous_user_id,
            assigned_at=now,
            unassigned_at=now if previous_user_id is not None else None,
            reason=str(reason or "").strip(),
            source=source,
        )
        submission.assigned_to_user_id = assignee_id
        submission.assigned_at = now if assignee_id is not None else None
        submission.assigned_by_user_id = actor.id if actor else None
        db.add(history)
        db.flush()
        return history

    def update_case_metadata(
        self,
        submission: FormSubmission,
        *,
        priority: str,
        due_at: datetime | None,
    ) -> None:
        if priority not in PRIORITIES:
            raise SubmissionAssignmentError("Nieprawidłowy priorytet sprawy.")
        submission.priority = priority
        submission.due_at = due_at

    def claim(self, db, submission: FormSubmission, form: Form, *, actor: User) -> SubmissionAssignmentHistory | None:
        if not self.can_review(db, actor, form):
            raise SubmissionAssignmentPermissionError("Brak uprawnienia do przejęcia sprawy.")
        if submission.assigned_to_user_id not in (None, actor.id) and not self.can_assign(db, actor, form):
            raise SubmissionAssignmentPermissionError("Sprawa ma już prowadzącego.")
        return self.assign(
            db, submission, form, assignee_id=actor.id, actor=actor,
            reason="Samodzielne przejęcie sprawy", source="manual", authorize=False,
        )

    def apply_filters(self, query, filters: Mapping[str, str], *, current_user_id: int):
        queue = str(filters.get("queue") or "").strip()
        assignee = str(filters.get("assignee") or "").strip()
        priority = str(filters.get("priority") or "").strip()
        now = datetime.now(timezone.utc)
        if queue == "mine" or assignee == "me":
            query = query.where(FormSubmission.assigned_to_user_id == current_user_id)
        elif queue == "unassigned" or assignee == "unassigned":
            query = query.where(FormSubmission.assigned_to_user_id.is_(None))
        elif assignee.isdigit():
            query = query.where(FormSubmission.assigned_to_user_id == int(assignee))
        if queue == "overdue" or str(filters.get("overdue") or "") in {"1", "true", "on"}:
            query = query.where(FormSubmission.due_at.is_not(None), FormSubmission.due_at < now)
        if queue in {"sla_overdue", "sla_today", "sla_24h", "sla_48h"}:
            deadline_query = select(SubmissionStepDeadline.submission_id).where(SubmissionStepDeadline.status == "active")
            if queue == "sla_overdue":
                deadline_query = deadline_query.where(SubmissionStepDeadline.due_at < now)
            elif queue == "sla_today":
                local_now = now.astimezone(ZoneInfo("Europe/Warsaw"))
                tomorrow = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
                deadline_query = deadline_query.where(SubmissionStepDeadline.due_at >= now, SubmissionStepDeadline.due_at < tomorrow)
            else:
                hours = 24 if queue == "sla_24h" else 48
                deadline_query = deadline_query.where(
                    SubmissionStepDeadline.due_at >= now,
                    SubmissionStepDeadline.due_at <= now + timedelta(hours=hours),
                )
            query = query.where(FormSubmission.id.in_(deadline_query))
        if queue == "requiring_decision":
            query = query.where(FormSubmission.process_status.in_(REVIEW_STATUSES))
        if queue == "waiting_office_signature":
            query = query.where(FormSubmission.process_status.in_(OFFICE_SIGNATURE_STATUSES))
        if priority in PRIORITIES:
            query = query.where(FormSubmission.priority == priority)
        workflow_stage = str(filters.get("workflow_stage") or "").strip()
        if workflow_stage:
            query = query.where(or_(FormSubmission.workflow_stage == workflow_stage, FormSubmission.workflow_step == workflow_stage))
        decision = str(filters.get("officer_decision") or "").strip()
        if decision:
            query = query.where(FormSubmission.officer_decision == decision)
        return query

    def queue_counts(self, db, *, user_id: int, form_slugs: Iterable[str] | None = None) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        active = ~FormSubmission.process_status.in_(FINAL_STATUSES)
        query = select(
            func.sum(case((and_(active, FormSubmission.assigned_to_user_id == user_id), 1), else_=0)),
            func.sum(case((and_(active, FormSubmission.assigned_to_user_id.is_(None)), 1), else_=0)),
            func.sum(case((and_(active, FormSubmission.due_at.is_not(None), FormSubmission.due_at < now), 1), else_=0)),
            func.sum(case((FormSubmission.process_status.in_(REVIEW_STATUSES), 1), else_=0)),
            func.sum(case((FormSubmission.process_status.in_(OFFICE_SIGNATURE_STATUSES), 1), else_=0)),
        )
        if form_slugs is not None:
            query = query.where(FormSubmission.form_slug.in_(list(form_slugs) or [""]))
        values = db.execute(query).one()
        keys = ("mine", "unassigned", "overdue", "requiring_decision", "waiting_office_signature")
        return {key: int(value or 0) for key, value in zip(keys, values)}

    def auto_assign_created(self, session_factory, *, submission_id: str, form_config: Mapping) -> int | None:
        assignment = form_config.get("assignment") if isinstance(form_config, Mapping) else None
        if not isinstance(assignment, Mapping) or assignment.get("mode") != "round_robin":
            return None
        configured_ids = assignment.get("eligible_users")
        if not isinstance(configured_ids, list):
            return None
        with session_factory() as db:
            submission = db.execute(
                select(FormSubmission).where(FormSubmission.submission_id == submission_id).with_for_update()
            ).scalar_one_or_none()
            if submission is None or submission.assigned_to_user_id is not None:
                return None
            form = db.execute(select(Form).where(Form.slug == submission.form_slug).with_for_update()).scalar_one_or_none()
            if form is None:
                return None
            users = self.eligible_users(db, form, configured_user_ids=configured_ids)
            if not users:
                return None
            state = db.get(FormAssignmentState, form.id)
            if state is None:
                if db.bind.dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                    db.execute(insert(FormAssignmentState).values(form_id=form.id).on_conflict_do_nothing())
                elif db.bind.dialect.name in {"mysql", "mariadb"}:
                    from sqlalchemy.dialects.mysql import insert
                    db.execute(insert(FormAssignmentState).values(form_id=form.id).prefix_with("IGNORE"))
                else:
                    from sqlalchemy.dialects.sqlite import insert
                    db.execute(insert(FormAssignmentState).values(form_id=form.id).on_conflict_do_nothing())
                state = db.execute(
                    select(FormAssignmentState).where(FormAssignmentState.form_id == form.id).with_for_update()
                ).scalar_one()
            user_ids = [user.id for user in users]
            try:
                index = (user_ids.index(state.last_assigned_user_id) + 1) % len(user_ids)
            except ValueError:
                index = 0
            assignee_id = user_ids[index]
            self.assign(
                db, submission, form, assignee_id=assignee_id, actor=None,
                reason="Automatyczny przydział round-robin", source="round_robin", authorize=False,
            )
            state.last_assigned_user_id = assignee_id
            db.commit()
            return assignee_id
