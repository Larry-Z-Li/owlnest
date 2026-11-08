"""Archived ("deleted") records, with Restore and Delete forever buttons.

Archiving a record just fills in its archived_at date, which hides it everywhere
else; restoring clears it. A property takes its leases, insurances, requests and
reminders along, and brings them back (see records.property_belongings).

Where they're listed: the Archive page has the archived properties, maintenance
requests and transactions. A property's own archived leases, insurances, photos and
files are at the bottom of its page, under Archives (for_property), and the removed
files of a request or transaction are on its own page (removed_files). Only from here can a record be deleted forever (see
deleting.py).
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from .. import alerts, attachments, deleting, records
from ..database import db_session
from ..models import (
    Attachment,
    Insurance,
    Lease,
    LeaseExtension,
    MaintenanceRequest,
    Note,
    Property,
    Reminder,
    Transaction,
    Walkthrough,
)
from ..money import format_money
from ._shared import own_page
from .files import owner_page

bp = Blueprint("archive", __name__, url_prefix="/archive")


def _property_archived(record) -> str | None:
    """A record of an archived property can't come back until the property does."""
    if record.property is not None and record.property.archived_at is not None:
        return f"{record.property.nickname} is in the Archive. Restore it first."
    return None


def _lease_restore_problem(lease: Lease) -> str | None:
    problem = _property_archived(lease)
    current = records.current_lease(lease.property)
    if problem is None and lease.status == "Active" and current is not None:
        problem = f"{lease.property.nickname} already has a current lease ({current.tenant_names})."
    return problem


def _attachment_restore_problem(attachment: Attachment) -> str | None:
    owner = attachments.owner_of(attachment)
    if getattr(owner, "archived_at", None) is not None:
        return "What it was attached to is in the Archive. Restore that first."
    return None


def _no_problem(_record) -> None:
    return None


# Each kind of record the Archive page handles:
#   heading, model, how to name a record, how to check it can be restored, and where to go afterwards.
KINDS = {
    "property": (
        "Properties",
        Property,
        lambda record: record.nickname,
        _no_problem,
        lambda record: url_for("properties.detail", property_id=record.id),
    ),
    "lease": (
        "Leases",
        Lease,
        records.lease_name,
        _lease_restore_problem,
        lambda record: url_for("leases.detail", lease_id=record.id),
    ),
    "insurance": (
        "Insurances",
        Insurance,
        lambda record: f"{record.kind} insurance, {record.property.nickname}",
        _property_archived,
        lambda record: url_for("properties.detail", property_id=record.property_id, _anchor="insurance"),
    ),
    "request": (
        "Maintenance requests",
        MaintenanceRequest,
        records.request_name,
        _property_archived,
        lambda record: url_for("maintenance.edit", request_id=record.id),
    ),
    "transaction": (
        "Transactions",
        Transaction,
        lambda record: f"{record.transaction_date:%b} {record.transaction_date.day}, {record.transaction_date.year}: "
        f"{record.category}, {format_money(record.amount_cents)}"
        + (f" ({record.property.nickname})" if record.property else ""),
        lambda record: _property_archived(record) if record.property else None,
        lambda record: url_for("transactions.edit", transaction_id=record.id),
    ),
    "attachment": (
        "Files",
        Attachment,
        lambda record: record.original_name or record.file_path,
        _attachment_restore_problem,
        lambda record: owner_page(attachments.owner_of(record)),
    ),
    "reminder": (
        "Reminders",
        Reminder,
        alerts.reminder_label,
        lambda record: _property_archived(record) if record.property else None,
        lambda record: url_for("reminders.edit", reminder_id=record.id),
    ),
    "note": (
        "Notes",
        Note,
        lambda record: record.text if len(record.text) <= 80 else record.text[:80] + "…",
        _no_problem,
        lambda record: url_for("notes.index"),
    ),
}


@bp.get("/")
def index():
    search = request.args.get("q", "").strip()
    sections = [
        ("Properties", _items("property", _archived(Property))),
        ("Maintenance requests", _items("request", _archived(MaintenanceRequest))),
        ("Transactions", _items("transaction", _archived(Transaction))),
        ("Notes", _items("note", _archived(Note))),
        # Only reminders archived before reminders stopped being archivable, and not for one property.
        ("Reminders", _items("reminder", _archived(Reminder, Reminder.property_id.is_(None)))),
    ]
    if search:
        sections = [
            (heading, [item for item in items if records.matches_search(search, item["name"])])
            for heading, items in sections
        ]
    return render_template("archive.html", sections=sections, search=search)


def for_property(a_property: Property) -> list[tuple[str, list[dict]]]:
    """What's archived from a property's own records, for the Archives box at the bottom of its page.

    Only the groups that have something. (Reminders are there only if they were
    archived before reminders stopped being archivable.)
    """
    files = [
        attachment
        for attachment in _archived(
            Attachment,
            or_(
                Attachment.lease_id.is_not(None),
                Attachment.walkthrough_id.is_not(None),
                Attachment.extension_id.is_not(None),
                Attachment.insurance_id.is_not(None),
            ),
        )
        if _property_of(attachments.owner_of(attachment)) == a_property
    ]
    groups = [
        ("Leases", _items("lease", _archived(Lease, Lease.property_id == a_property.id))),
        ("Insurance", _items("insurance", _archived(Insurance, Insurance.property_id == a_property.id))),
        ("Reminders", _items("reminder", _archived(Reminder, Reminder.property_id == a_property.id))),
        ("Photos", _items("attachment", _archived(Attachment, Attachment.property_id == a_property.id))),
        ("Files", _items("attachment", files)),
    ]
    return [(heading, items) for heading, items in groups if items]


def removed_files(owner) -> list[dict]:
    """The removed (archived) files of a maintenance request or transaction, for its own page."""
    return _items("attachment", [attachment for attachment in owner.attachments if attachment.archived_at is not None])


def _archived(model, *conditions) -> list:
    """The archived records of a table (meeting the conditions, if any), newest first."""
    query = select(model).where(model.archived_at.is_not(None), *conditions).order_by(model.archived_at.desc())
    return list(db_session.scalars(query))


def _items(kind: str, found: list) -> list[dict]:
    """The rows of an archived list (see templates/_archived.html)."""
    _heading, _model, name_of, _problem, _next_page = KINDS[kind]
    return [
        {
            "kind": kind,
            "id": record.id,
            "name": name_of(record),
            "archived_at": record.archived_at,
            # A question the Restore button asks first (OK or Cancel), if any.
            "restore_question": records.transaction_restore_warning(record) if kind == "transaction" else "",
            "delete_question": _delete_question(kind, record, name_of),
        }
        for record in found
    ]


def _delete_question(kind: str, record, name_of) -> str:
    question = f"Delete {name_of(record)} forever?"
    if kind == "property":
        question = f"Delete {record.nickname} forever, with its leases, insurance, reminders and photos?"
    return question + " It can't be undone (earlier backups still have it)."


def _property_of(owner) -> Property | None:
    """The property a lease, walkthrough, extension or insurance belongs to."""
    if isinstance(owner, (Lease, Insurance)):
        return owner.property
    if isinstance(owner, (Walkthrough, LeaseExtension)):
        return owner.lease.property
    return None


def _back(default: str) -> str:
    """The page to go back to: the one the button was on (its "back" field), if it's one of the app's own."""
    back = request.form.get("back", "")
    return own_page(back) if back else default


@bp.post("/restore/<kind>/<int:record_id>")
def restore(kind: str, record_id: int):
    if kind not in KINDS:
        abort(404)
    _heading, model, name_of, restore_problem, next_page = KINDS[kind]
    record = db_session.get(model, record_id)
    if record is None:
        abort(404)
    if record.archived_at is None:
        # Already restored (for example, Undo was clicked twice).
        return redirect(next_page(record))

    problem = restore_problem(record)
    if problem:
        flash(f"{name_of(record)} can't be restored yet. {problem}", "error")
        return redirect(_back(url_for("archive.index")))

    # A transaction whose request is in the Archive comes back without that link
    # (the Restore button asked her first: see records.transaction_restore_warning).
    unlinked = ""
    if kind == "transaction" and record.request is not None and record.request.archived_at is not None:
        unlinked = f" It's no longer linked to the request “{records.request_name(record.request)}”, which is in the Archive."
        record.request = None

    # A property brings back what was archived along with it.
    belongings = records.property_belongings(record, record.archived_at) if kind == "property" else []
    for one in [record, *belongings]:
        one.archived_at = None
    db_session.commit()
    flash(f"{records.with_belongings(name_of(record), belongings)} was restored.{unlinked}", "success")
    return redirect(next_page(record))


@bp.post("/delete/<kind>/<int:record_id>")
def delete_forever(kind: str, record_id: int):
    if kind not in KINDS:
        abort(404)
    _heading, model, name_of, _restore_problem, _next_page = KINDS[kind]
    record = db_session.get(model, record_id)
    if record is None or record.archived_at is None:
        abort(404)  # (only archived records can be deleted forever)
    name = name_of(record)
    problem = deleting.delete_forever(record)
    if problem:
        db_session.rollback()
        flash(problem, "error")
    else:
        db_session.commit()
        flash(f"{name} was deleted forever.", "success")
    return redirect(_back(url_for("archive.index")))
