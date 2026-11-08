"""The Lease screens: adding a property's lease, its walkthroughs, extending it, ending it, and reviving it.

The rules are in leasing.py; files attached to a lease are handled by attachments.py.
"""

from datetime import date, timedelta
from urllib.parse import quote

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from .. import attachments, leasing, rent_ledger
from ..database import db_session, get_settings
from ..formatting import nice_date
from ..forms import Form, email_problem
from ..models import (
    DEFAULT_LATE_AFTER_DAYS,
    DEFAULT_LATE_PERCENT,
    WALKTHROUGH_KINDS,
    Lease,
    Property,
    RentPeriod,
    Walkthrough,
)
from ..money import format_money
from ._shared import archive_record, as_id, get_active_or_404
from .files import attach_chosen_files

bp = Blueprint("leases", __name__, url_prefix="/leases")

# The walkthroughs' names in addresses, such as /leases/3/walkthroughs/move-in.
WALKTHROUGH_SLUGS = {kind.lower(): kind for kind in WALKTHROUGH_KINDS}


# ---------- Adding and editing ----------


@bp.get("/new")
def new():
    a_property = get_active_or_404(Property, as_id(request.args.get("property_id")) or 0)
    problem = leasing.start_problem(a_property)
    if problem:
        flash(problem, "error")
        return redirect(url_for("properties.detail", property_id=a_property.id))
    initial = {
        "start_date": date.today().isoformat(),
        "late_percent": f"{DEFAULT_LATE_PERCENT:g}",
        "late_after_days": DEFAULT_LATE_AFTER_DAYS,
    }
    return _show_form(Form(initial=initial), None, a_property)


@bp.post("/new")
def create():
    a_property = get_active_or_404(Property, as_id(request.form.get("property_id")) or 0)
    problem = leasing.start_problem(a_property)
    if problem:
        flash(problem, "error")
        return redirect(url_for("properties.detail", property_id=a_property.id))
    form = Form(request.form)
    fields = _read_fields(form)
    amount = form.money("monthly_rent")
    late_rule = _read_late_rule(form)
    if not form.is_valid:
        return _show_form(form, None, a_property, status=400)
    first_rent = RentPeriod(starts_on=fields["start_date"], amount_cents=amount, **late_rule)
    lease = Lease(
        property=a_property,
        portal_token=leasing.make_token(),
        rent_periods=[first_rent],
        rent_checked_from=leasing.first_rent_check(fields["start_date"], date.today()),
        **fields,
    )
    db_session.add(lease)
    db_session.commit()
    flash("The lease was added. Its tenant portal link is below: send it to the tenants.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="portal"))


@bp.get("/<int:lease_id>")
def detail(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    public_address = current_app.config["PUBLIC_ADDRESS"]
    link = leasing.portal_link(public_address, lease) if public_address else ""
    today = date.today()
    rent_months, _paid_ahead = rent_ledger.work_out(lease, today)
    return render_template(
        "leases/detail.html",
        lease=lease,
        today=today,
        owing=[month for month in rent_months if month.owed_cents],
        walkthroughs={walkthrough.kind: walkthrough for walkthrough in lease.walkthroughs},
        walkthrough_kinds=WALKTHROUGH_KINDS,
        documents=attachments.active(lease.attachments),
        portal_link=link,
        portal_email=_portal_email(lease, link) if link and lease.tenant_email else "",
        revive_problem=leasing.revive_problem(lease) if lease.status == "Ended" else None,
        accept=attachments.ACCEPT,
    )


@bp.get("/<int:lease_id>/edit")
def edit(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    initial = {
        "tenant_names": lease.tenant_names,
        "tenant_phone": lease.tenant_phone,
        "tenant_email": lease.tenant_email,
        "start_date": lease.start_date.isoformat(),
        "end_date": lease.end_date.isoformat() if lease.end_date else "",
        "deposit": f"{lease.deposit_cents / 100:,.2f}" if lease.deposit_cents else "",
        "notes": lease.notes,
    }
    return _show_form(Form(initial=initial), lease, lease.property)


@bp.post("/<int:lease_id>/edit")
def update(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    form = Form(request.form)
    fields = _read_fields(form)
    if fields["start_date"] and leasing.start_date_problem(lease, fields["start_date"]):
        form.add_error("start_date", leasing.start_date_problem(lease, fields["start_date"]))
    if not form.is_valid:
        return _show_form(form, lease, lease.property, status=400)
    for name, value in fields.items():
        setattr(lease, name, value)
    lease.rent_periods[0].starts_on = lease.start_date  # (the first rent starts with the lease)
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id))


@bp.post("/<int:lease_id>/archive")
def archive(lease_id: int):
    """Move a lease to the Archive (from there it can be restored, or deleted forever)."""
    lease = get_active_or_404(Lease, lease_id)
    archive_record(lease, "lease", f"The lease for {lease.tenant_names}")
    return redirect(url_for("properties.detail", property_id=lease.property_id))


# ---------- The rent list ----------


@bp.get("/<int:lease_id>/rent/new")
def rent_new(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    # A new rent starts out with the late fee rule of the one in effect now.
    rule = lease.current_rent_period
    initial = {"late_percent": f"{rule.late_percent:g}", "late_after_days": rule.late_after_days}
    return _show_rent(lease, None, Form(initial=initial))


@bp.post("/<int:lease_id>/rent/new")
def rent_create(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    form = Form(request.form)
    starts_on = form.date("starts_on")
    amount = form.money("amount")
    late_rule = _read_late_rule(form)
    if starts_on and leasing.rent_date_problem(lease, starts_on):
        form.add_error("starts_on", leasing.rent_date_problem(lease, starts_on))
    if not form.is_valid:
        return _show_rent(lease, None, form, status=400)
    lease.rent_periods.append(RentPeriod(starts_on=starts_on, amount_cents=amount, **late_rule))
    db_session.commit()
    flash(f"The rent is {format_money(amount)} a month from {nice_date(starts_on)}.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="rent"))


@bp.get("/<int:lease_id>/rent/<int:period_id>")
def rent_edit(lease_id: int, period_id: int):
    lease = get_active_or_404(Lease, lease_id)
    period = _rent_period_or_404(lease, period_id)
    initial = {
        "starts_on": period.starts_on.isoformat(),
        "amount": f"{period.amount_cents / 100:,.2f}",
        "late_percent": f"{period.late_percent:g}",
        "late_after_days": period.late_after_days,
    }
    return _show_rent(lease, period, Form(initial=initial))


@bp.post("/<int:lease_id>/rent/<int:period_id>")
def rent_update(lease_id: int, period_id: int):
    lease = get_active_or_404(Lease, lease_id)
    period = _rent_period_or_404(lease, period_id)
    form = Form(request.form)
    amount = form.money("amount")
    late_rule = _read_late_rule(form)
    # (The first rent's date is the lease's start date, so it isn't on the form.)
    starts_on = period.starts_on if period is lease.rent_periods[0] else form.date("starts_on")
    if starts_on and period is not lease.rent_periods[0] and leasing.rent_date_problem(lease, starts_on, period):
        form.add_error("starts_on", leasing.rent_date_problem(lease, starts_on, period))
    if not form.is_valid:
        return _show_rent(lease, period, form, status=400)
    period.starts_on = starts_on
    period.amount_cents = amount
    period.late_after_days = late_rule["late_after_days"]
    period.late_percent = late_rule["late_percent"]
    db_session.commit()
    flash(f"The rent is {format_money(amount)} a month from {nice_date(starts_on)}.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="rent"))


@bp.post("/<int:lease_id>/rent/<int:period_id>/remove")
def rent_remove(lease_id: int, period_id: int):
    lease = get_active_or_404(Lease, lease_id)
    period = _rent_period_or_404(lease, period_id)
    if period is lease.rent_periods[0]:
        abort(404)  # (a lease always has its first rent; the page has no button for this)
    db_session.delete(period)
    db_session.commit()
    flash(f"The rent from {nice_date(period.starts_on)} was removed.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="rent"))


# ---------- Waiving ----------


@bp.post("/<int:lease_id>/rent/waive")
def rent_waive(lease_id: int):
    """The buttons on the lease's rent reminder: let go of a month's late fee, or of all that's left."""
    lease = get_active_or_404(Lease, lease_id)
    wanted = request.form.get("month", "")
    month = next((one for one in rent_ledger.owing(lease, date.today()) if f"{one.month:%Y-%m}" == wanted), None)
    if month is None:
        abort(404)
    if request.form.get("what") == "rest":
        rent_ledger.waive_rest(lease, month)
        flash(f"The rest of the {month.name} rent was waived, with its late fee.", "success")
    else:
        rent_ledger.waive_fee(lease, month)
        flash(f"The {month.name} late fee was waived.", "success")
    db_session.commit()
    return redirect(url_for("leases.detail", lease_id=lease.id))


@bp.post("/<int:lease_id>/rent/waive/undo")
def rent_waive_undo(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    try:
        month = date.fromisoformat(request.form.get("month", "") + "-01")
    except ValueError:
        abort(404)
    rent_ledger.undo_waiver(lease, month)
    db_session.commit()
    flash(f"The {month:%B %Y} rent is back to what's owed without the waiver.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id))


# ---------- Extending, ending and reviving ----------


@bp.get("/<int:lease_id>/extend")
def extend_form(lease_id: int):
    lease = _active_lease(lease_id)
    # A new rent usually starts when the new term does: the day after the current end date.
    rent_start = (lease.end_date + timedelta(days=1)).isoformat() if lease.end_date else ""
    return _show_extend(lease, Form(initial={"rent_starts_on": rent_start}))


@bp.post("/<int:lease_id>/extend")
def extend(lease_id: int):
    lease = _active_lease(lease_id)
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    form = Form(request.form)
    new_end_date = form.date("new_end_date")
    if new_end_date and new_end_date <= (lease.end_date or lease.start_date):
        form.add_error("new_end_date", "The new end date must be after the lease's current end date.")
    new_rent = form.money("new_rent", required=False)
    rent_starts_on = None
    if form.raw("new_rent").strip():
        rent_starts_on = form.date("rent_starts_on")
    if rent_starts_on and leasing.rent_date_problem(lease, rent_starts_on, last_day=new_end_date):
        form.add_error("rent_starts_on", leasing.rent_date_problem(lease, rent_starts_on, last_day=new_end_date))
    signed_on = form.date("signed_on", required=False)
    notes = form.text("notes", max_length=5000)
    if not form.is_valid:
        return _show_extend(lease, form, status=400)

    extension = leasing.extend(lease, new_end_date, signed_on, notes)
    rent_changes = new_rent is not None and leasing.add_rent(lease, rent_starts_on, new_rent)
    if not attach_chosen_files(extension, form):
        return _show_extend(lease, form, status=400)
    db_session.commit()
    message = f"The lease now ends on {new_end_date:%b} {new_end_date.day}, {new_end_date.year}."
    if rent_changes:
        message += f" The rent is {format_money(new_rent)} a month from {nice_date(rent_starts_on)}."
    flash(message, "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="extensions"))


@bp.get("/<int:lease_id>/end")
def end_form(lease_id: int):
    lease = _active_lease(lease_id)
    return _show_end(lease, Form(initial={"ended_on": date.today().isoformat()}))


@bp.post("/<int:lease_id>/end")
def end(lease_id: int):
    lease = _active_lease(lease_id)
    form = Form(request.form)
    ended_on = form.date("ended_on")
    if ended_on and ended_on < lease.start_date:
        form.add_error("ended_on", "The lease can't end before it started.")
    if form.is_valid and leasing.ends_early(lease, ended_on) and request.form.get("confirm_early") != "1":
        form.add_error("confirm_early", "This ends the lease before its end date. Please tick the box to confirm.")
    if not form.is_valid:
        return _show_end(lease, form, status=400)
    leasing.end(lease, ended_on)
    db_session.commit()
    flash(
        f"The lease for {lease.tenant_names} has ended. It's now under previous leases, "
        "and the tenants' portal link no longer works. (If this was a mistake, click “Revive lease”.)",
        "success",
    )
    return redirect(url_for("properties.detail", property_id=lease.property_id))


@bp.post("/<int:lease_id>/revive")
def revive(lease_id: int):
    lease = get_active_or_404(Lease, lease_id)
    if lease.status != "Ended":
        return redirect(url_for("leases.detail", lease_id=lease.id))
    problem = leasing.revive_problem(lease)
    if problem:
        flash(problem, "error")
        return redirect(url_for("leases.detail", lease_id=lease.id))
    leasing.revive(lease)
    db_session.commit()
    flash("The lease is current again, and the tenants' portal link works again.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id))


# ---------- The tenant portal link ----------


@bp.post("/<int:lease_id>/portal-link")
def new_portal_link(lease_id: int):
    """Replace the portal link (if it reached someone it shouldn't). The old link stops working."""
    lease = _active_lease(lease_id)
    lease.portal_token = leasing.make_token()
    db_session.commit()
    flash("The tenants have a new portal link. The old link no longer works, so send them this one.", "success")
    return redirect(url_for("leases.detail", lease_id=lease.id, _anchor="portal"))


def _portal_email(lease: Lease, link: str) -> str:
    """A "mailto:" link that opens her own email program with a message to the tenants, ready to send."""
    sign_off = get_settings().business_name.strip() or "Your landlord"
    address_lines = [line.strip() for line in lease.property.address.splitlines() if line.strip()]
    home = address_lines[0] if address_lines else lease.property.nickname
    body = (
        f"Hello {lease.tenant_names},\n\n"
        f"You can send repair requests for {home}, with photos, and see how they're going, here:\n\n"
        f"{link}\n\n"
        "Please keep this link to yourselves: anyone who has it can see and send your requests.\n\n"
        f"{sign_off}"
    )
    subject = "Your link for repair requests"
    return f"mailto:{quote(lease.tenant_email)}?subject={quote(subject)}&body={quote(body)}"


# ---------- Walkthroughs ----------


@bp.get("/<int:lease_id>/walkthroughs/<kind>")
def walkthrough(lease_id: int, kind: str):
    lease, kind = _lease_and_kind(lease_id, kind)
    existing = _walkthrough(lease, kind)
    initial = (
        {"walk_date": existing.walk_date.isoformat(), "notes": existing.notes}
        if existing
        else {"walk_date": (lease.start_date if kind == "Move-in" else (lease.ended_on or date.today())).isoformat()}
    )
    return _show_walkthrough(lease, kind, existing, Form(initial=initial))


@bp.post("/<int:lease_id>/walkthroughs/<kind>")
def save_walkthrough(lease_id: int, kind: str):
    lease, kind = _lease_and_kind(lease_id, kind)
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    existing = _walkthrough(lease, kind)
    form = Form(request.form)
    walk_date = form.date("walk_date")
    notes = form.text("notes", max_length=10000)
    if not form.is_valid:
        return _show_walkthrough(lease, kind, existing, form, status=400)

    record = existing or Walkthrough(lease=lease, kind=kind)
    record.walk_date = walk_date
    record.notes = notes
    db_session.add(record)
    if not attach_chosen_files(record, form):
        return _show_walkthrough(lease, kind, existing, form, status=400)
    db_session.commit()
    flash(f"The {kind.lower()} walkthrough was saved.", "success")
    return redirect(url_for("leases.walkthrough", lease_id=lease.id, kind=kind.lower()))


# ---------- Helpers ----------


def _active_lease(lease_id: int) -> Lease:
    """A current lease; ended ones can't be extended or ended again."""
    lease = get_active_or_404(Lease, lease_id)
    if lease.status != "Active":
        abort(404)
    return lease


def _lease_and_kind(lease_id: int, slug: str) -> tuple[Lease, str]:
    if slug not in WALKTHROUGH_SLUGS:
        abort(404)
    return get_active_or_404(Lease, lease_id), WALKTHROUGH_SLUGS[slug]


def _walkthrough(lease: Lease, kind: str) -> Walkthrough | None:
    return next((one for one in lease.walkthroughs if one.kind == kind), None)


def _read_fields(form: Form) -> dict:
    start_date = form.date("start_date")
    end_date = form.date("end_date", required=False)
    if start_date and end_date and end_date <= start_date:
        form.add_error("end_date", "The end date must be after the start date.")
    email = form.text("tenant_email", max_length=200)
    if email and email_problem(email):
        form.add_error("tenant_email", email_problem(email))
    return {
        "tenant_names": form.text("tenant_names", required=True, max_length=300),
        "tenant_phone": form.text("tenant_phone", max_length=100),
        "tenant_email": email,
        "start_date": start_date,
        "end_date": end_date,
        "deposit_cents": form.money("deposit", required=False, allow_zero=True) or 0,
        "notes": form.text("notes", max_length=5000),
    }


def _show_form(form: Form, lease: Lease | None, a_property: Property, status: int = 200):
    return render_template("leases/form.html", form=form, lease=lease, a_property=a_property), status


def _read_late_rule(form: Form) -> dict:
    """The late fee fields of a rent. Left empty, they're the defaults. (A percent of 0 means no late fee.)"""
    percent = form.half_number("late_percent", maximum=100)
    days = form.whole_number("late_after_days", minimum=1, maximum=365)
    return {
        "late_percent": DEFAULT_LATE_PERCENT if percent is None else percent,
        "late_after_days": days or DEFAULT_LATE_AFTER_DAYS,
    }


def _rent_period_or_404(lease: Lease, period_id: int) -> RentPeriod:
    for period in lease.rent_periods:
        if period.id == period_id:
            return period
    abort(404)


def _show_rent(lease: Lease, period: RentPeriod | None, form: Form, status: int = 200):
    return render_template("leases/rent.html", lease=lease, period=period, form=form), status


def _show_extend(lease: Lease, form: Form, status: int = 200):
    return render_template("leases/extend.html", lease=lease, form=form, accept=attachments.ACCEPT), status


def _show_end(lease: Lease, form: Form, status: int = 200):
    return render_template("leases/end.html", lease=lease, form=form, today=date.today()), status


def _show_walkthrough(lease: Lease, kind: str, existing: Walkthrough | None, form: Form, status: int = 200):
    return (
        render_template(
            "leases/walkthrough.html",
            lease=lease,
            kind=kind,
            walkthrough=existing,
            files=attachments.active(existing.attachments) if existing else [],
            form=form,
            accept=attachments.ACCEPT,
        ),
        status,
    )
