"""The Insurance screens: each property's policies (property, renters, and so on), with their papers.

An insurance is only a record and a reminder: 60 days before it ends, it shows
on the Home page (see alerts.py). It isn't linked to any money.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import attachments
from ..database import db_session
from ..forms import Form
from ..models import INSURANCE_KINDS, Insurance, Property
from ._shared import archive_record, as_id, get_active_or_404
from .files import attach_chosen_files

bp = Blueprint("insurances", __name__, url_prefix="/insurances")


@bp.get("/new")
def new():
    a_property = get_active_or_404(Property, as_id(request.args.get("property_id")) or 0)
    return _show_form(Form(initial={"kind": "Property"}), None, a_property)


@bp.post("/new")
def create():
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    a_property = get_active_or_404(Property, as_id(request.form.get("property_id")) or 0)
    form = Form(request.form)
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, None, a_property, status=400)
    insurance = Insurance(property=a_property, **fields)
    db_session.add(insurance)
    if not attach_chosen_files(insurance, form):
        return _show_form(form, None, a_property, status=400)
    db_session.commit()
    flash(f"The {insurance.kind.lower()} insurance was added.", "success")
    return redirect(url_for("properties.detail", property_id=a_property.id, _anchor="insurance"))


@bp.get("/<int:insurance_id>/edit")
def edit(insurance_id: int):
    insurance = get_active_or_404(Insurance, insurance_id)
    initial = {
        "kind": insurance.kind,
        "company": insurance.company,
        "policy_number": insurance.policy_number,
        "start_date": insurance.start_date.isoformat(),
        "end_date": insurance.end_date.isoformat(),
        "notes": insurance.notes,
    }
    return _show_form(Form(initial=initial), insurance, insurance.property)


@bp.post("/<int:insurance_id>/edit")
def update(insurance_id: int):
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    insurance = get_active_or_404(Insurance, insurance_id)
    form = Form(request.form)
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, insurance, insurance.property, status=400)
    for name, value in fields.items():
        setattr(insurance, name, value)
    if not attach_chosen_files(insurance, form):
        return _show_form(form, insurance, insurance.property, status=400)
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("properties.detail", property_id=insurance.property_id, _anchor="insurance"))


@bp.post("/<int:insurance_id>/archive")
def archive(insurance_id: int):
    insurance = get_active_or_404(Insurance, insurance_id)
    archive_record(insurance, "insurance", f"The {insurance.kind.lower()} insurance for {insurance.property.nickname}")
    return redirect(url_for("properties.detail", property_id=insurance.property_id, _anchor="insurance"))


def _read_fields(form: Form) -> dict:
    start_date = form.date("start_date")
    end_date = form.date("end_date")
    if start_date and end_date and end_date <= start_date:
        form.add_error("end_date", "The end date must be after the start date.")
    return {
        "kind": form.text("kind", required=True, max_length=100),
        "company": form.text("company", max_length=200),
        "policy_number": form.text("policy_number", max_length=100),
        "start_date": start_date,
        "end_date": end_date,
        "notes": form.text("notes", max_length=5000),
    }


def _show_form(form: Form, insurance: Insurance | None, a_property: Property, status: int = 200):
    return (
        render_template(
            "insurances/form.html",
            form=form,
            insurance=insurance,
            a_property=a_property,
            kinds=INSURANCE_KINDS,
            files=attachments.active(insurance.attachments) if insurance else [],
            accept=attachments.ACCEPT,
        ),
        status,
    )
