"""The Transactions screens: every bit of money in or out (rent, expenses, insurance, loans, anything).

One list, newest first and grouped by month, with the property, the tenant, what it
was for, and the amount (negative for money out). Adding and editing use the same form. A
transaction can be linked to a maintenance request; it then takes on the
request's type and property (see records.follow_request).
"""

from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import attachments, bank_link, categories, deleting, records
from ..database import db_session
from ..forms import Form
from ..models import BankEntry, Llc, MaintenanceRequest, Property, Transaction
from ..money import cents_to_text, format_money
from ._shared import archive_record, as_id, get_active_or_404, money_in, money_out, months_of, read_choice
from .archive import removed_files
from .files import attach_chosen_files

bp = Blueprint("transactions", __name__, url_prefix="/transactions")

# How far back closed requests are offered for linking (open ones always are).
CLOSED_REQUESTS_OFFERED = timedelta(days=365)

# The Period menu above the list.
PERIODS = [("", "All dates"), ("this-year", "This year"), ("last-year", "Last year"), ("custom", "Pick dates…")]


@bp.get("/")
def index():
    property_id = request.args.get("property_id", "")
    category = request.args.get("category", "")
    today = date.today()
    # (from and to alone still work, as in a link from elsewhere.)
    period = request.args.get("period") or ("custom" if request.args.get("from") or request.args.get("to") else "")
    start, end = _period_dates(period, today)
    search = request.args.get("q", "").strip()

    query = select(Transaction).where(Transaction.archived_at.is_(None))
    if property_id.startswith("llc-") and as_id(property_id[4:]) is not None:
        query = query.where(records.in_llc(as_id(property_id[4:])))
    elif property_id == "none":
        query = query.where(Transaction.property_id.is_(None))
    elif as_id(property_id) is not None:
        query = query.where(Transaction.property_id == as_id(property_id))
    if category in categories.ALL:
        query = query.where(Transaction.category == category)
    if start:
        query = query.where(Transaction.transaction_date >= start)
    if end:
        query = query.where(Transaction.transaction_date <= end)
    if search:
        query = query.where(records.search_filter(search, Transaction.tenant_name, Transaction.details))
    query = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
    items = list(db_session.scalars(query))
    filtering = bool(property_id or category or period or search)
    return render_template(
        "transactions/list.html",
        months=months_of(items, lambda item: item.transaction_date, filtering),
        count=len(items),
        money_in=money_in(items),
        money_out=money_out(items),
        property_id=property_id,
        property_groups=records.property_choices(),
        llcs=records.active_llcs(),
        category=category,
        category_groups=categories.choices(),
        period=period,
        periods=PERIODS,
        start=start,
        end=end,
        search=search,
        filtering=filtering,
        look_of=categories.look_of,
        bank_on=bank_link.is_set_up(current_app) or bank_link.has_entries(),
        bank_waiting=bank_link.waiting_count(),
        from_the_bank=set(db_session.scalars(select(BankEntry.transaction_id).where(BankEntry.status == "Entered"))),
        # Ones she typed in that a bank entry is "Linked with AI" to.
        ai_linked=set(db_session.scalars(select(BankEntry.transaction_id).where(BankEntry.status == "Linked"))),
    )


def _period_dates(period: str, today: date) -> tuple[date | None, date | None]:
    """The first and last day of a choice in the Period menu (None: no limit)."""
    if period == "this-year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if period == "last-year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if period == "custom":
        return _date_arg("from"), _date_arg("to")
    return None, None


@bp.get("/new")
def new():
    # "Enter into Transactions" on the "From the bank" page: what the bank says is filled in, and the rest is hers.
    if request.args.get("bank_entry"):
        entry = bank_link.waiting_entry(as_id(request.args["bank_entry"]))
        if entry is None:
            return _already_entered()
        initial = {
            "transaction_date": entry.day.isoformat(),
            "direction": "in" if entry.amount_cents > 0 else "out",
            "amount": cents_to_text(abs(entry.amount_cents)),
            "details": bank_link.details_for(entry),
        }
        return _show_form(Form(initial=initial), None, entry=entry)
    initial = {"transaction_date": date.today().isoformat(), "direction": "out"}
    # The Maintenance screen links here with its request already chosen.
    linked = db_session.get(MaintenanceRequest, as_id(request.args.get("request_id")) or 0)
    if linked is not None and linked.archived_at is None:
        initial.update(request_id=linked.id, property_id=linked.property_id, category=linked.category)
    elif as_id(request.args.get("property_id")) is not None:
        initial["property_id"] = as_id(request.args["property_id"])
    return _show_form(Form(initial=initial), None)


@bp.post("/new")
def create():
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    form = Form(request.form)
    entry = None
    if form.raw("bank_entry"):
        entry = bank_link.waiting_entry(as_id(form.raw("bank_entry")))
        if entry is None:  # (A second click on the button then adds nothing.)
            return _already_entered()
    fields = _read_fields(form)
    if not form.is_valid:
        return _show_form(form, None, status=400, entry=entry)
    transaction = Transaction(**fields)
    db_session.add(transaction)
    if not attach_chosen_files(transaction, form):
        return _show_form(form, None, status=400, entry=entry)
    if entry is not None:
        entry.transaction = transaction
        entry.status = "Entered"
    db_session.commit()
    if entry is not None:
        flash("It's in Transactions now, marked BOFA.", "success")
        return redirect(url_for("bank.index"))
    flash("The transaction was added.", "success")
    if request.form.get("then") == "another":
        return redirect(url_for("transactions.new"))
    return redirect(url_for("transactions.index"))


@bp.get("/<int:transaction_id>")
def edit(transaction_id: int):
    transaction = get_active_or_404(Transaction, transaction_id)
    initial = {
        "transaction_date": transaction.transaction_date.isoformat(),
        "direction": "in" if transaction.amount_cents > 0 else "out",
        "amount": cents_to_text(abs(transaction.amount_cents)),
        "property_id": transaction.property_id or "",
        "llc_id": transaction.llc_id or "",
        "tenant_name": transaction.tenant_name,
        "category": transaction.category,
        "request_id": transaction.request_id or "",
        "details": transaction.details,
    }
    return _show_form(Form(initial=initial), transaction)


@bp.post("/<int:transaction_id>")
def update(transaction_id: int):
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read
    transaction = get_active_or_404(Transaction, transaction_id)
    form = Form(request.form)
    fields = _read_fields(form, transaction)
    if not form.is_valid:
        return _show_form(form, transaction, status=400)
    for name, value in fields.items():
        setattr(transaction, name, value)
    if not attach_chosen_files(transaction, form):
        return _show_form(form, transaction, status=400)
    db_session.commit()
    flash("Your changes were saved.", "success")
    return redirect(url_for("transactions.index"))


@bp.post("/<int:transaction_id>/archive")
def archive(transaction_id: int):
    transaction = get_active_or_404(Transaction, transaction_id)
    if bank_link.entry_of(transaction) is not None:
        flash("This one came from the bank, so it can't be archived. You can return it to From the bank instead.", "warning")
        return redirect(url_for("transactions.edit", transaction_id=transaction.id))
    day = transaction.transaction_date
    name = f"The {transaction.category} transaction of {format_money(abs(transaction.amount_cents))} on {day:%b} {day.day}, {day.year}"
    # Bank entries linked to it with AI are unlinked for good (restoring it doesn't link them again).
    bank_link.unlink_from(transaction)
    archive_record(transaction, "transaction", name, undo_question=records.transaction_restore_warning(transaction))
    return redirect(url_for("transactions.index"))


@bp.post("/<int:transaction_id>/delete")
def delete(transaction_id: int):
    """Return a transaction from the bank to From the bank: it's deleted, and its entry can be entered again (see deleting.py)."""
    transaction = get_active_or_404(Transaction, transaction_id)
    if bank_link.entry_of(transaction) is None:
        abort(404)  # (Any other transaction goes to the Archive first.)
    deleting.delete_forever(transaction)
    db_session.commit()
    flash("The transaction was returned to From the bank. You can enter it again there.", "success")
    return redirect(url_for("transactions.index"))


def _read_fields(form: Form, transaction: Transaction | None = None) -> dict:
    day = form.date("transaction_date")
    direction = form.raw("direction")
    if direction not in ("in", "out"):
        form.add_error("direction", "Please choose money in or money out.")
    amount = form.money("amount")

    linked = read_choice(form, "request_id", MaintenanceRequest, keep=[transaction.request] if transaction else [])
    if linked is not None:
        # A transaction for a request takes on the request's type and property.
        a_property, category = linked.property, linked.category
    else:
        keep = [transaction.property] if transaction and transaction.property else []
        a_property = read_choice(form, "property_id", Property, keep=keep)
        category = form.raw("category")
        if category not in categories.ALL:
            form.add_error("category", "Please choose what it was for.")
    # For the LLC itself, only without a property: with one, it's that property's LLC.
    llc = None if a_property is not None else read_choice(form, "llc_id", Llc)

    tenant_name = form.text("tenant_name", max_length=300)
    if not tenant_name and a_property is not None and day is not None:
        # Filled in from the lease that covered that day, if she left it empty.
        lease = records.lease_on(a_property, day)
        tenant_name = lease.tenant_names if lease else ""
    return {
        "transaction_date": day,
        "amount_cents": (amount or 0) * (1 if direction == "in" else -1),
        "property": a_property,
        "llc": llc,
        "tenant_name": tenant_name,
        "category": category,
        "request": linked,
        "details": form.text("details", max_length=5000),
    }


def _request_choices(transaction: Transaction | None) -> list[MaintenanceRequest]:
    """The requests a transaction can be linked to: open ones, recently closed ones, and its own."""
    since = date.today() - CLOSED_REQUESTS_OFFERED
    query = (
        select(MaintenanceRequest)
        .where(MaintenanceRequest.archived_at.is_(None))
        .order_by(MaintenanceRequest.reported_on.desc(), MaintenanceRequest.id.desc())
    )
    offered = [
        one
        for one in db_session.scalars(query)
        if one.status != "Closed" or one.reported_on >= since or (transaction and one.id == transaction.request_id)
    ]
    if transaction and transaction.request and transaction.request not in offered:
        offered.append(transaction.request)  # an archived request it's already linked to
    return offered


def _already_entered():
    flash("That one from the bank was already entered.", "warning")
    return redirect(url_for("bank.index"))


def _show_form(form: Form, transaction: Transaction | None, status: int = 200, entry: BankEntry | None = None):
    properties = records.active_properties()
    if transaction and transaction.property and transaction.property not in properties:
        properties.append(transaction.property)
    offered = _request_choices(transaction)
    return (
        render_template(
            "transactions/form.html",
            form=form,
            transaction=transaction,
            entry=entry,
            from_the_bank=transaction is not None and bank_link.entry_of(transaction) is not None,
            property_groups=records.property_choices(properties),
            llc_options=[(llc.id, llc.name) for llc in records.active_llcs()],
            # Each property's LLC, by its id: the LLC menu follows the property (see transaction_form.js).
            llc_by_property={str(one.id): one.llc_id or "" for one in properties},
            category_groups=categories.choices(),
            request_options=[(one.id, f"{records.request_name(one)} ({one.status})") for one in offered],
            # Each request's property and type, by its id: a transaction linked to it takes them on.
            requests_by_id={str(one.id): {"property_id": one.property_id, "type": one.category} for one in offered},
            files=attachments.active(transaction.attachments) if transaction else [],
            removed_files=removed_files(transaction) if transaction else [],
            accept=attachments.ACCEPT,
        ),
        status,
    )


def _date_arg(name: str) -> date | None:
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return None
