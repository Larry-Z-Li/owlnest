"""The "From the bank" screen: her bank's activity, money in and out (see bank_link.py).

An entry here is like a transaction she can't edit. "Enter into Transactions" opens the
transaction form with the date, money in or out, amount and details filled in, and the
property, purpose and request left for her (views/transactions.py). Once that's saved, the
entry stays listed here, marked "In Transactions", and its transaction shows a BOFA tag.

The Accounts page lists the accounts the bank shares. "Add account" asks which login an account
is under: the same one reopens the bank's page to choose accounts on that connection, and a
different one starts a new connection beside it. No page disconnects: Larry does that by hand.
"""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import bank_link
from ..database import db_session
from ..models import BankEntry
from ._shared import money_in, money_out, months_of, own_page

bp = Blueprint("bank", __name__, url_prefix="/bank")

# The "Money in or out" menu's choices (empty: both).
DIRECTIONS = {"in": "Money in", "out": "Money out"}
# The status menu's choices, in Larry's order (empty: every entry), and the status each one means.
STATUSES = {"checked": ("Checked", "Overridden"), "linked": ("Linked with AI", "Linked"), "entered": ("In Transactions", "Entered"), "waiting": ("Not entered", "New")}


@bp.get("/")
def index():
    # (In case she connected on Plaid's page but didn't come back here from it.)
    if bank_link.is_set_up(current_app):
        _finish_connecting()
    every_entry = list(db_session.scalars(select(BankEntry).order_by(BankEntry.day.desc(), BankEntry.id.desc())))
    # Her choices in the menus above the list (anything else, such as an old account name, shows everything).
    accounts = sorted({entry.account_name for entry in every_entry if entry.account_name})
    account = request.args.get("account", "")
    account = account if account in accounts else ""
    direction = request.args.get("direction", "")
    direction = direction if direction in DIRECTIONS else ""
    status = request.args.get("status", "")
    status = status if status in STATUSES else ""
    entries = [
        entry
        for entry in every_entry
        if (not account or entry.account_name == account)
        and (not direction or (entry.amount_cents > 0) == (direction == "in"))
        and (not status or entry.status == STATUSES[status][1])
    ]
    return render_template(
        "bank/list.html",
        set_up=bank_link.is_set_up(current_app),
        connected=bank_link.is_connected(current_app),
        gathering=bank_link.still_gathering(current_app),
        bank_name=bank_link.bank_name(current_app),
        trouble=bank_link.trouble(current_app),
        last_check=bank_link.last_good_check(),
        months=months_of(entries, lambda entry: entry.day),
        count=len(entries),
        any_entries=bool(every_entry),
        accounts=accounts,
        account=account,
        directions=DIRECTIONS,
        direction=direction,
        statuses={value: label for value, (label, _status) in STATUSES.items()},
        status=status,
        filtering=bool(account or direction or status),
        money_in=money_in(entries),
        money_out=money_out(entries),
        read_words=bank_link.read_words,
        maybe_entered=bank_link.maybe_entered([entry for entry in entries if entry.status in ("New", "Overridden")]),
    )


@bp.post("/<int:entry_id>/unlink")
def unlink(entry_id: int):
    """Undo a link made by AI, for good (it asked her first)."""
    entry = db_session.get(BankEntry, entry_id)
    if entry is None:
        abort(404)
    back = own_page(request.form.get("back", "")) if request.form.get("back") else url_for("bank.index")
    if entry.status == "Linked":
        bank_link.unlink(entry)
        db_session.commit()
        flash("Unlinked." + (" It's back to Overridden." if entry.status == "Overridden" else " It's waiting to be entered again."), "success")
    return redirect(back + f"#entry-{entry.id}")


@bp.get("/accounts")
def accounts():
    if bank_link.is_set_up(current_app):
        _finish_connecting()
    if not bank_link.is_connected(current_app):
        return redirect(url_for("bank.index"))
    connections = bank_link.connections_on_screen(current_app)
    for connection in connections:
        if connection["problem"]:
            flash(connection["problem"], "error")
    return render_template(
        "bank/accounts.html",
        connections=connections,
        count=sum(len(connection["accounts"]) for connection in connections),
        bank_name=bank_link.bank_name(current_app),
        last_check=bank_link.last_good_check(),
    )


@bp.get("/accounts/add")
def add_account():
    """Which login is the account under? It decides whether this uses up one of the free connections."""
    if not bank_link.is_connected(current_app):
        return redirect(url_for("bank.index"))
    return render_template(
        "bank/add_account.html",
        connections=bank_link.connections_on_screen(current_app),
        bank_name=bank_link.bank_name(current_app),
        can_add_login=bank_link.can_add_login(current_app),
        most_connections=bank_link.MOST_CONNECTIONS,
    )


@bp.post("/connect")
def connect():
    """Go to Plaid's page, to connect the bank, repair the connection, or add an account. Only Plaid's page ever sees her password."""
    if not bank_link.is_set_up(current_app):
        abort(404)
    new_login = request.form.get("new_login") == "1"
    adding = request.form.get("add_account") == "1"
    # (The address Plaid sends her back to. On the server, it's the address everyone uses.)
    values = {"adding": "1"} if adding or new_login else {}
    base = current_app.config["PUBLIC_ADDRESS"]
    come_back_to = f"{base}{url_for('bank.connected', **values)}" if base else url_for("bank.connected", _external=True, **values)
    try:
        return redirect(bank_link.start_connecting(current_app, come_back_to, adding, new_login, request.form.get("connection")))
    except bank_link.BankProblem as problem:
        flash(str(problem), "error")
        return redirect(url_for("bank.index"))


@bp.get("/connected")
def connected():
    """Where Plaid's page sends her back to."""
    if not bank_link.is_set_up(current_app):
        abort(404)
    adding = request.args.get("adding") == "1"
    _finish_connecting(adding)
    return redirect(url_for("bank.accounts" if adding else "bank.index"))


def _finish_connecting(adding: bool = False) -> None:
    try:
        finished = bank_link.finish_connecting(current_app)
    except bank_link.BankProblem as problem:
        flash(str(problem), "error")
        return
    if finished:
        bank_link.check_now(current_app._get_current_object())
        name = bank_link.bank_name(current_app) or "The bank"
        if finished == "connected" and adding:
            flash("The other login is connected. Its past 13 months will show up on From the bank.", "success")
        elif finished == "connected":
            flash(f"{name} is connected. The past 13 months will show up here.", "success")
        elif adding:
            flash("Your accounts were updated. A new account's activity can take a few minutes to reach From the bank.", "success")
        else:
            flash(f"{name} is reconnected.", "success")
