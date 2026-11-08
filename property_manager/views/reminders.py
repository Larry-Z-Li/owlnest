"""The Reminders screens: everything that needs her, and the reminders she adds herself.

The list has what the top bar's "needs attention" button counts (see alerts.py): rent
that's due, leases and insurances ending, new requests and so on, each linking to its
page. Those aren't saved anywhere: they go away by themselves once they're taken care of.

Below that are her own reminders that aren't close yet (property tax, a license, an
inspection). One of those needs attention from "remind me N days before" until she clicks
Done. A repeating reminder then moves to its next date (next month or next year); a
one-time reminder stops being listed.
"""

import calendar
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import alerts, records
from ..database import db_session
from ..forms import Form
from ..models import REMINDER_KINDS, REMINDER_REPEATS, Property, Reminder
from ._shared import as_id, get_active_or_404, read_choice

bp = Blueprint("reminders", __name__, url_prefix="/reminders")


@bp.get("/")
def index():
    property_id = as_id(request.args.get("property_id"))
    today = date.today()
    needs_attention = alerts.gather(today, current_app.config["DATA_PATHS"], current_app.config["LAST_BACKUP_RESULT"])
    later = alerts.not_close_yet(today)
    if property_id is not None:
        needs_attention = [alert for alert in needs_attention if alert.property_id == property_id]
        later = [alert for alert in later if alert.property_id == property_id]
    return render_template(
        "reminders/list.html",
        needs_attention=needs_attention,
        later=later,
        property_id=property_id,
        property_groups=records.property_choices(),
    )


@bp.get("/new")
def new():
    initial = {
        "property_id": request.args.get("property_id", ""),
        "kind": request.args.get("kind", ""),
        "remind_days_before": 30,
        "repeat": "Every year",
    }
    return _show_form(Form(initial=initial), reminder=None)


@bp.post("/new")
def create():
    form = Form(request.form)
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, reminder=None, status=400)
    reminder = Reminder(**fields)
    db_session.add(reminder)
    db_session.commit()
    flash(f"The reminder “{alerts.reminder_label(reminder)}” was added.", "success")
    return redirect(url_for("reminders.index"))


@bp.get("/<int:reminder_id>/edit")
def edit(reminder_id: int):
    reminder = get_active_or_404(Reminder, reminder_id)
    form = Form(
        initial={
            "property_id": reminder.property_id or "",
            "kind": reminder.kind,
            "title": reminder.title,
            "due_date": reminder.due_date.isoformat(),
            "remind_days_before": reminder.remind_days_before,
            "repeat": reminder.repeat,
            "notes": reminder.notes,
        }
    )
    return _show_form(form, reminder=reminder)


@bp.post("/<int:reminder_id>/edit")
def update(reminder_id: int):
    reminder = get_active_or_404(Reminder, reminder_id)
    form = Form(request.form)
    fields = _read_fields(form, reminder)
    if not form.is_valid:
        return _show_form(form, reminder=reminder, status=400)
    for name, value in fields.items():
        setattr(reminder, name, value)
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("reminders.index"))


@bp.post("/<int:reminder_id>/done")
def done(reminder_id: int):
    """Mark a reminder done: a repeating one moves to its next date; a one-time one is finished."""
    reminder = get_active_or_404(Reminder, reminder_id)
    today = date.today()
    reminder.last_done_on = today
    if reminder.repeat == "Every month":
        reminder.due_date = next_date(reminder.due_date, months=1)
    elif reminder.repeat == "Every year":
        reminder.due_date = next_date(reminder.due_date, months=12)
    else:
        reminder.finished = True
    db_session.commit()
    if reminder.finished:
        flash(f"“{alerts.reminder_label(reminder)}” is done.", "success")
    else:
        flash(
            f"“{alerts.reminder_label(reminder)}” is done. The next one is due on "
            f"{reminder.due_date:%b} {reminder.due_date.day}, {reminder.due_date.year}.",
            "success",
        )
    return redirect(request.referrer or url_for("home.index"))


def next_date(day: date, months: int) -> date:
    """The same day, `months` later. If that month is shorter (Jan 31 + 1 month), its last day."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _show_form(form: Form, reminder: Reminder | None, status: int = 200):
    used_kinds = set(db_session.scalars(select(Reminder.kind).distinct()))
    properties = records.active_properties()
    if reminder and reminder.property and reminder.property not in properties:
        properties.append(reminder.property)  # an archived property it's already about
    return render_template(
        "reminders/form.html",
        form=form,
        reminder=reminder,
        property_groups=records.property_choices(properties),
        kind_suggestions=sorted(set(REMINDER_KINDS) | used_kinds, key=str.casefold),
        repeat_options=[(repeat, repeat) for repeat in REMINDER_REPEATS],
    ), status


def _read_fields(form: Form, reminder: Reminder | None = None) -> dict:
    a_property = read_choice(form, "property_id", Property, keep=[reminder.property] if reminder else [])
    repeat = form.raw("repeat")
    if repeat not in REMINDER_REPEATS:
        form.add_error("repeat", "Please choose how often it repeats.")
    return {
        "property": a_property,
        "kind": form.text("kind", required=True, max_length=100),
        "title": form.text("title", max_length=200),
        "due_date": form.date("due_date"),
        "remind_days_before": form.whole_number("remind_days_before", required=True, minimum=0, maximum=365),
        "repeat": repeat,
        "notes": form.text("notes", max_length=2000),
    }
