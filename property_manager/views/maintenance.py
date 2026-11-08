"""The Maintenance screens: requests for things to fix or do at her properties.

She adds requests here; tenants send them from the tenant portal (views/portal.py).
Each has a type (from the client's list in categories.py), the company fixing it,
a status (Open, In progress or Closed), files such as pictures and receipts, and
any number of transactions, which take on its type.
"""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import attachments, categories, records
from ..database import db_session
from ..forms import Form
from ..models import REQUEST_STATUSES, MaintenanceRequest, Property
from ._shared import archive_record, as_id, get_active_or_404, own_page, read_choice
from .archive import removed_files
from .files import attach_chosen_files

bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")

@bp.get("/")
def index():
    show = request.args.get("show", "not-closed")
    property_id = as_id(request.args.get("property_id"))
    category = request.args.get("category", "")
    query = select(MaintenanceRequest).where(MaintenanceRequest.archived_at.is_(None))
    if property_id is not None:
        query = query.where(MaintenanceRequest.property_id == property_id)
    if category in categories.ALL:
        query = query.where(MaintenanceRequest.category == category)
    # The tabs' counts follow the property and type chosen.
    statuses = list(db_session.scalars(query.with_only_columns(MaintenanceRequest.status)))
    if show == "not-closed":
        query = query.where(MaintenanceRequest.status != "Closed")
    elif show in REQUEST_STATUSES:
        query = query.where(MaintenanceRequest.status == show)
    query = query.order_by(MaintenanceRequest.reported_on.desc(), MaintenanceRequest.id.desc())
    return render_template(
        "maintenance/list.html",
        maintenance_requests=list(db_session.scalars(query)),
        statuses_shown=statuses_shown(show),
        show=show,
        open_count=sum(1 for status in statuses if status != "Closed"),
        closed_count=sum(1 for status in statuses if status == "Closed"),
        property_id=property_id,
        property_groups=records.property_choices(),
        category=category,
        category_groups=categories.choices(),
        request_cost=records.request_cost,
    )


@bp.get("/new")
def new():
    initial = {
        "reported_on": date.today().isoformat(),
        "category": categories.DEFAULT_REQUEST_TYPE,
        "status": "Open",
        "property_id": request.args.get("property_id", ""),
    }
    return _show_form(Form(initial=initial), None)


@bp.post("/new")
def create():
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    form = Form(request.form)
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, None, status=400)
    maintenance_request = MaintenanceRequest(**fields)
    db_session.add(maintenance_request)
    if not attach_chosen_files(maintenance_request, form):
        return _show_form(form, None, status=400)
    db_session.commit()
    flash("The request was added.", "success")
    return redirect(url_for("maintenance.edit", request_id=maintenance_request.id))


@bp.get("/<int:request_id>")
def edit(request_id: int):
    """A request's page: the same form as for a new one, plus its files and transactions."""
    maintenance_request = get_active_or_404(MaintenanceRequest, request_id)
    if maintenance_request.unread:
        maintenance_request.unread = False  # she has seen it now
        db_session.commit()
    initial = {
        "property_id": maintenance_request.property_id,
        "title": maintenance_request.title,
        "description": maintenance_request.description,
        "category": maintenance_request.category,
        "company": maintenance_request.company,
        "status": maintenance_request.status,
        "reported_on": maintenance_request.reported_on.isoformat(),
    }
    return _show_form(Form(initial=initial), maintenance_request)


@bp.post("/<int:request_id>")
def update(request_id: int):
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    maintenance_request = get_active_or_404(MaintenanceRequest, request_id)
    form = Form(request.form)
    fields = _read_fields(form, maintenance_request)
    if not form.is_valid:
        return _show_form(form, maintenance_request, status=400)
    for name, value in fields.items():
        setattr(maintenance_request, name, value)
    records.follow_request(maintenance_request)  # its transactions take on its type
    if not attach_chosen_files(maintenance_request, form):
        return _show_form(form, maintenance_request, status=400)
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("maintenance.edit", request_id=maintenance_request.id))


@bp.post("/<int:request_id>/status")
def change_status(request_id: int):
    """Her pick in the status menu on a list (the Maintenance list, or Home).

    static/status.js sends it without leaving the page, and takes the request off the
    list if the list doesn't show requests with the new status.
    """
    maintenance_request = get_active_or_404(MaintenanceRequest, request_id)
    status = request.form.get("status", "")
    if status not in REQUEST_STATUSES:
        abort(400)
    maintenance_request.status = status
    db_session.commit()
    if request.form.get("stay_on_page"):
        return "", 204  # (status.js changes the page itself)
    flash(f"“{maintenance_request.title}” is now {status.lower()}.", "success")
    return redirect(own_page(request.form.get("next", "")), 303)


@bp.post("/<int:request_id>/archive")
def archive(request_id: int):
    maintenance_request = get_active_or_404(MaintenanceRequest, request_id)
    archive_record(maintenance_request, "request", f"The request “{maintenance_request.title}”")
    return redirect(url_for("maintenance.index"))


def statuses_shown(show: str) -> list[str]:
    """The statuses of the requests the list shows, for its "Show" choice."""
    if show == "not-closed":
        return [status for status in REQUEST_STATUSES if status != "Closed"]
    if show in REQUEST_STATUSES:
        return [show]
    return list(REQUEST_STATUSES)


def _read_fields(form: Form, maintenance_request: MaintenanceRequest | None = None) -> dict:
    keep = [maintenance_request.property] if maintenance_request else []
    a_property = read_choice(form, "property_id", Property, "Please choose the property.", keep=keep)
    category = form.raw("category")
    if category not in categories.ALL:
        form.add_error("category", "Please choose the type of request.")
    status = form.raw("status")
    if status not in REQUEST_STATUSES:
        form.add_error("status", "Please choose the status.")
    return {
        "property": a_property,
        "title": form.text("title", required=True, max_length=150),
        "description": form.text("description", max_length=10000),
        "category": category,
        "company": form.text("company", max_length=200),
        "status": status,
        "reported_on": form.date("reported_on"),
    }


def _show_form(form: Form, maintenance_request: MaintenanceRequest | None, status: int = 200):
    current = maintenance_request.property if maintenance_request else None
    properties = records.active_properties()
    if current is not None and current not in properties:
        properties.append(current)  # an archived property this request already belongs to
    return (
        render_template(
            "maintenance/form.html",
            form=form,
            maintenance_request=maintenance_request,
            property_groups=records.property_choices(properties),
            category_groups=categories.choices(),
            status_options=[(status, status) for status in REQUEST_STATUSES],
            files=attachments.active(maintenance_request.attachments) if maintenance_request else [],
            removed_files=removed_files(maintenance_request) if maintenance_request else [],
            transactions=[
                transaction for transaction in maintenance_request.transactions if transaction.archived_at is None
            ]
            if maintenance_request
            else [],
            cost=records.request_cost(maintenance_request) if maintenance_request else 0,
            accept=attachments.ACCEPT,
        ),
        status,
    )
