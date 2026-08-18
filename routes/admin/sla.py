from __future__ import annotations

from copy import deepcopy
from datetime import date
import json

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from models import BusinessCalendar, BusinessCalendarHoliday, Form
from services.workflow_config_service import WorkflowConfigNormalizer, WorkflowConfigValidator

from . import bp, db_session_factory, ensure_form_access, login_required, permission_required


@bp.route("/business-calendars", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_site")
def business_calendars():
    with db_session_factory()() as db:
        selected_id = request.args.get("id", type=int) or request.form.get("calendar_id", type=int)
        calendar = db.get(BusinessCalendar, selected_id) if selected_id else None
        if request.method == "POST":
            if request.form.get("action") == "new":
                calendar = BusinessCalendar(name="Nowy kalendarz", weekend_days=[5, 6])
                db.add(calendar)
                db.commit()
                return redirect(url_for("admin.business_calendars", id=calendar.id))
            calendar = calendar or abort(404)
            name = str(request.form.get("name") or "").strip()
            if not name:
                flash("Nazwa kalendarza jest wymagana.", "error")
            else:
                weekend_days = sorted({int(value) for value in request.form.getlist("weekend_days") if value.isdigit() and 0 <= int(value) <= 6})
                holidays = []
                try:
                    for line in str(request.form.get("holidays") or "").splitlines():
                        raw = line.strip()
                        if not raw:
                            continue
                        raw_date, _, holiday_name = raw.partition("|")
                        holidays.append(BusinessCalendarHoliday(holiday_date=date.fromisoformat(raw_date.strip()), name=holiday_name.strip()))
                except ValueError:
                    flash("Dni wolne podaj jako RRRR-MM-DD|Nazwa, po jednym w wierszu.", "error")
                else:
                    calendar.name = name
                    calendar.timezone_name = str(request.form.get("timezone_name") or "Europe/Warsaw").strip()
                    calendar.weekend_days = weekend_days
                    calendar.active = request.form.get("active") == "on"
                    calendar.holidays = holidays
                    db.commit()
                    flash("Kalendarz dni roboczych został zapisany.", "success")
                    return redirect(url_for("admin.business_calendars", id=calendar.id))
        calendars = db.execute(select(BusinessCalendar).order_by(BusinessCalendar.name, BusinessCalendar.id)).scalars().all()
        if calendar is None and calendars:
            calendar = calendars[0]
        return render_template("admin/sla/calendars.html", calendars=calendars, calendar=calendar)


@bp.route("/forms/<int:form_id>/sla", methods=["GET", "POST"])
@login_required
def form_sla(form_id: int):
    with db_session_factory()() as db:
        form = ensure_form_access(
            db,
            form_id,
            permission="can_edit_workflow" if request.method == "POST" else "can_view_submissions",
        )
        definition = deepcopy(form.definition_json or {})
        workflow = WorkflowConfigNormalizer().normalize(definition.get("workflow") or {})
        can_edit_workflow = current_app.extensions["services"].permission_service.has_permission(
            db, g.admin_user, "can_edit_workflow", form=form
        )
        calendars = db.execute(select(BusinessCalendar).where(BusinessCalendar.active.is_(True)).order_by(BusinessCalendar.name)).scalars().all()
        if request.method == "POST":
            try:
                for step in workflow.get("steps") or []:
                    step_id = step["id"]
                    raw_value = str(request.form.get(f"deadline_value__{step_id}") or "").strip()
                    if not raw_value:
                        step.pop("sla", None)
                        continue
                    reminders = json.loads(request.form.get(f"reminders__{step_id}") or "[]")
                    escalation = json.loads(request.form.get(f"escalation__{step_id}") or "{}")
                    if not isinstance(reminders, list) or not isinstance(escalation, dict):
                        raise ValueError("Przypomnienia muszą być listą, a eskalacja obiektem JSON.")
                    calendar_id = request.form.get(f"calendar__{step_id}", type=int)
                    step["sla"] = {
                        "active": True,
                        "deadline": {
                            "value": int(raw_value),
                            "unit": str(request.form.get(f"deadline_unit__{step_id}") or "calendar_days"),
                        },
                        "business_calendar_id": calendar_id,
                        "actor_type": str(request.form.get(f"actor_type__{step_id}") or "office"),
                        "reminders": reminders,
                        "escalation": escalation,
                    }
                errors = WorkflowConfigValidator().validate(workflow, definition)
                if errors:
                    raise ValueError(" ".join(errors))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                flash(f"Nie zapisano SLA: {exc}", "error")
            else:
                definition["workflow"] = workflow
                form.definition_json = definition
                db.commit()
                flash("Konfiguracja SLA została zapisana w roboczej definicji formularza.", "success")
                return redirect(url_for("admin.form_sla", form_id=form.id))
        return render_template(
            "admin/sla/form.html", form=form, workflow=workflow, calendars=calendars,
            can_edit_workflow=can_edit_workflow,
        )
