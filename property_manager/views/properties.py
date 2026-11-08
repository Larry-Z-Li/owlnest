"""The Properties screens: each property, its current lease, its insurances, and its previous leases.

The list goes by LLC, with the homes in none at the end. An LLC is just a name.
"""

from datetime import date, datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .. import alerts, attachments, home_values, receipts, records
from ..database import db_session
from ..forms import Form
from ..models import Llc, MaintenanceRequest, Property, Reminder
from ..money import cents_to_text
from ._shared import archive_record, get_active_or_404, read_choice
from .archive import for_property as archived_for_property

bp = Blueprint("properties", __name__, url_prefix="/properties")


# How the Properties list can be sorted. (By name is how the rows arrive.)
SORTS = {
    # Soonest end first, then month-to-month, then homes with no lease.
    "lease": lambda row: (
        (2, date.max) if not row["lease"] else (1, date.max) if not row["lease"].end_date else (0, row["lease"].end_date)
    ),
    # Highest first, then homes without one.
    "value": lambda row: -row["value"].value_cents if row["value"] else 1,
    "rent": lambda row: -row["lease"].rent_cents if row["lease"] else 1,
}


@bp.get("/")
def index():
    search = request.args.get("q", "").strip()
    show = request.args.get("show", "")
    sort = request.args.get("sort", "")
    today = date.today()
    properties = records.active_properties()
    everyone = [
        {
            "property": a_property,
            "lease": records.current_lease(a_property),
            "value": home_values.latest_value(a_property),
            "line": records.home_line(a_property),
            "photo": next(iter(attachments.active(a_property.attachments)), None),
        }
        for a_property in properties
    ]
    rows = []
    for row in everyone:
        lease = row["lease"]
        row["lease_tag"] = _lease_tag(lease, today)
        if show == "leased" and not lease or show == "vacant" and lease:
            continue
        if search and not records.matches_search(
            search, row["property"].nickname, row["property"].address, lease.tenant_names if lease else ""
        ):
            continue
        rows.append(row)
    if sort in SORTS:
        rows.sort(key=SORTS[sort])
    leases = [row["lease"] for row in everyone if row["lease"]]
    return render_template(
        "properties/list.html",
        groups=_groups(rows, properties, bool(search or show)),
        search=search,
        show=show,
        sort=sort,
        home_count=len(everyone),
        leased_count=len(leases),
        monthly_rent=sum(lease.rent_cents for lease in leases),
        total_value=home_values.total_value(properties),
    )


def _groups(rows: list[dict], properties: list[Property], filtering: bool) -> list[dict]:
    """The rows under each LLC. While she's filtering, only the groups with a match show."""
    groups = []
    for llc, homes in records.by_llc(properties):
        ids = {home.id for home in homes}
        found = [row for row in rows if row["property"].id in ids]
        if found or (llc is not None and not filtering):
            groups.append(
                {"llc": llc, "name": llc.name if llc else records.NOT_IN_AN_LLC, "rows": found}
            )
    return groups


def _lease_tag(lease, today: date) -> tuple[str, str]:
    """The colored tag about a home's lease in the Properties list: its words and its color."""
    if lease is None:
        return "Vacant", "tag-vacant"
    if lease.end_date is None:
        return "Month-to-month", "tag-active"
    day = f"{lease.end_date:%b} {lease.end_date.day}, {lease.end_date.year}"
    if lease.end_date < today:
        return f"Ended {day}", "tag-late"
    if (lease.end_date - today).days <= alerts.ENDING_SOON_DAYS:
        return f"Ends {day}", "tag-warning"
    return f"Until {day}", "tag-active"


@bp.get("/new")
def new():
    return _show_form(Form(), None)


@bp.post("/new")
def create():
    form = Form(request.form)
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, None, 400)
    new_property = Property(**fields, own_value_set_at=datetime.now() if fields["own_value_cents"] is not None else None)
    db_session.add(new_property)
    db_session.commit()
    flash(f"{new_property.nickname} was added.", "success")
    return redirect(url_for("properties.detail", property_id=new_property.id))


@bp.get("/<int:property_id>")
def detail(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    reminders = db_session.scalars(
        select(Reminder)
        .where(Reminder.property_id == a_property.id, Reminder.archived_at.is_(None), Reminder.finished.is_(False))
        .order_by(Reminder.due_date)
    )
    return render_template(
        "properties/detail.html",
        a_property=a_property,
        facts=records.home_facts(a_property),
        photos=attachments.active(a_property.attachments),
        # What needs attention here. It can't be dismissed: it stays until it's taken care of.
        warnings=[
            alert
            for alert in alerts.gather(date.today(), current_app.config["DATA_PATHS"], current_app.config["LAST_BACKUP_RESULT"])
            if alert.property_id == a_property.id
        ],
        archives=archived_for_property(a_property),
        value=home_values.latest_value(a_property),
        rentcast_value=home_values.latest_rentcast_value(a_property),
        last_lookup=home_values.latest_lookup(a_property),
        lookups_on=home_values.lookups_are_on(current_app),
        address_has_zip=home_values.has_zip_code(a_property.address),
        lease=records.current_lease(a_property),
        previous_count=len(records.previous_leases(a_property)),
        insurances=[insurance for insurance in a_property.insurances if insurance.archived_at is None],
        reminders=list(reminders),
        today_date=date.today(),
        request_count=db_session.scalar(
            select(func.count(MaintenanceRequest.id)).where(
                MaintenanceRequest.property_id == a_property.id, MaintenanceRequest.archived_at.is_(None)
            )
        ),
        open_requests=list(
            db_session.scalars(
                select(MaintenanceRequest)
                .where(
                    MaintenanceRequest.property_id == a_property.id,
                    MaintenanceRequest.archived_at.is_(None),
                    MaintenanceRequest.status != "Closed",
                )
                .order_by(MaintenanceRequest.reported_on.desc())
            )
        ),
    )


@bp.get("/<int:property_id>/previous-leases")
def previous_leases(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    return render_template(
        "properties/previous_leases.html", a_property=a_property, leases=records.previous_leases(a_property)
    )


@bp.get("/<int:property_id>/edit")
def edit(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    form = Form(
        initial={
            "nickname": a_property.nickname,
            "llc_id": a_property.llc_id or "",
            "address": a_property.address,
            "notes": a_property.notes,
            "look_up_value": "1" if a_property.look_up_value else "",
            "bedrooms": a_property.bedrooms,
            "bathrooms": f"{a_property.bathrooms:g}" if a_property.bathrooms is not None else "",
            "square_feet": a_property.square_feet,
            "own_value": cents_to_text(a_property.own_value_cents),
        }
    )
    return _show_form(form, a_property)


@bp.post("/<int:property_id>/edit")
def update(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    form = Form(request.form)
    fields = _read_fields(form, except_id=a_property.id)
    if not form.is_valid:
        return _show_form(form, a_property, 400)
    own_value = fields.pop("own_value_cents")
    for name, value in fields.items():
        setattr(a_property, name, value)
    if own_value != a_property.own_value_cents:  # (the day it was set changes only when the value does)
        a_property.own_value_cents = own_value
        a_property.own_value_set_at = datetime.now() if own_value is not None else None
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("properties.detail", property_id=a_property.id))


def _show_form(form: Form, a_property: Property | None, status: int = 200):
    llc_options = [(llc.id, llc.name) for llc in records.active_llcs()]
    return render_template("properties/form.html", form=form, a_property=a_property, llc_options=llc_options), status


@bp.get("/llcs/new")
def new_llc():
    return render_template("properties/llc_form.html", form=Form(), llc=None)


@bp.post("/llcs/new")
def create_llc():
    form = Form(request.form)
    name = _read_llc_name(form)
    if not form.is_valid:
        return render_template("properties/llc_form.html", form=form, llc=None), 400
    db_session.add(Llc(name=name))
    db_session.commit()
    flash(f"{name} was added. Choose it when you add or edit a property.", "success")
    return redirect(url_for("properties.index"))


@bp.get("/llcs/<int:llc_id>/edit")
def edit_llc(llc_id: int):
    llc = get_active_or_404(Llc, llc_id)
    return render_template("properties/llc_form.html", form=Form(initial={"name": llc.name}), llc=llc)


@bp.post("/llcs/<int:llc_id>/edit")
def update_llc(llc_id: int):
    llc = get_active_or_404(Llc, llc_id)
    form = Form(request.form)
    name = _read_llc_name(form, llc)
    if not form.is_valid:
        return render_template("properties/llc_form.html", form=form, llc=llc), 400
    llc.name = name
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("properties.index"))


def _read_llc_name(form: Form, llc: Llc | None = None) -> str:
    name = form.text("name", required=True, max_length=200)
    if name:
        problem = records.name_problem(records.name_in_use(Llc, "name", name, llc.id if llc else None), "an LLC", name)
        if problem:
            form.add_error("name", problem)
    return name


@bp.post("/<int:property_id>/archive")
def archive(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    lease = records.current_lease(a_property)
    if lease is not None:
        flash(f"{a_property.nickname} still has a current lease ({lease.tenant_names}). End the lease first.", "error")
        return redirect(url_for("properties.detail", property_id=a_property.id))
    # Its leases, insurances, requests and reminders go along (see records.PROPERTY_BELONGINGS).
    belongings = records.property_belongings(a_property, None)

    def archive_belongings():
        for record in belongings:
            record.archived_at = a_property.archived_at  # the very same time, so they come back together

    name = records.with_belongings(a_property.nickname, belongings)
    archive_record(a_property, "property", name, before_saving=archive_belongings)
    return redirect(url_for("properties.index"))


@bp.get("/<int:property_id>/photos")
def photos(property_id: int):
    """Where she adds and removes the photos in the slideshow on the property's page."""
    a_property = get_active_or_404(Property, property_id)
    return render_template(
        "properties/photos.html",
        a_property=a_property,
        photos=attachments.active(a_property.attachments),
        accept=receipts.PICTURE_ACCEPT,
    )


@bp.post("/<int:property_id>/photos")
def add_photos(property_id: int):
    a_property = get_active_or_404(Property, property_id)
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    back = redirect(url_for("properties.photos", property_id=a_property.id))
    files = request.files.getlist("files")
    if not any(upload.filename for upload in files):
        flash("Please choose a photo first.", "error")
        return back
    try:
        # Photos only (no PDFs). They're redrawn, which also drops hidden details such as where they were taken.
        saved = attachments.save_uploads(
            files, current_app.config["DATA_PATHS"].documents, f"{a_property.nickname} photo", a_property, photos_only=True
        )
    except receipts.ReceiptError as problem:
        flash(f"Nothing was added. {problem}", "error")
        return back
    db_session.commit()
    flash(f"{len(saved)} {'photo was' if len(saved) == 1 else 'photos were'} added.", "success")
    return back


def _read_fields(form: Form, except_id: int | None = None) -> dict:
    nickname = form.text("nickname", required=True, max_length=100)
    if nickname:
        problem = records.name_problem(
            records.name_in_use(Property, "nickname", nickname, except_id), "a property", nickname
        )
        if problem:
            form.add_error("nickname", problem)
    return {
        "nickname": nickname,
        "llc": read_choice(form, "llc_id", Llc),
        "address": form.text("address", max_length=500),
        "notes": form.text("notes", max_length=5000),
        "look_up_value": form.raw("look_up_value") == "1",
        "bedrooms": form.whole_number("bedrooms", maximum=100),
        "bathrooms": form.half_number("bathrooms", maximum=100),
        "square_feet": form.whole_number("square_feet", minimum=1, maximum=1_000_000),
        "own_value_cents": form.money("own_value", required=False),
    }
