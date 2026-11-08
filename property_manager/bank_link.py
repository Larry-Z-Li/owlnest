"""Her bank's activity, money in and out, through Plaid (plaid.com). It can read, and can't move money.

Connecting:

1. A free Trial account on Plaid's dashboard gives a client ID and a secret. They go
   on Render's Environment page as PLAID_CLIENT_ID and PLAID_SECRET.
2. "Connect the bank" on the "From the bank" page opens Plaid's own page, where she
   signs in at her bank and picks the accounts to share. Her password never comes here.
3. That gives an access token, a password-like key that reads those accounts. It's
   saved in bank-link.json in the data folder. That isn't the database, so it's in no
   backup, and only the server can read the file. It's never printed or shown.

Staying free. Plaid's Trial plan allows 10 connections ("Items"), ever: removing one
doesn't give it back, and more would mean a paid plan. So:

* A new connection only starts when there isn't one, or when she says an account is
  under a different login at the bank ("Add account"). Each login is one connection:
  Plaid won't let a connection change logins.
* A connection that needs her to sign in again is repaired ("update mode"), which
  keeps the same one. Bank of America's consent lasts 12 months, so she'll need to do
  this once a year. Home reminds her 30 days ahead.
* "Add account", for the same login, also uses update mode: the bank's page lets her
  choose which accounts it shares, on the same connection.
* Nothing here applies for a paid plan, and no page removes a connection. Larry
  does that by hand, when he wants to, with bank_by_hand.py on the server. The count of
  connections ever made is kept, since removing one doesn't give it back.

Checking:

1. Only the server checks real accounts, and only once connected. A preview may use
   Plaid's Sandbox, with its made-up bank, by setting PLAID_ENV=sandbox. Tests never
   reach Plaid.
2. It checks once a day, every hour right after connecting while Plaid is still
   gathering its history, and whenever she clicks "Fetch now" in Settings. Never
   more than 24 times in 24 hours, however it's asked. Each check is saved before it's sent.
3. Plaid sends only what's new since the last check, using a bookmark ("cursor"), one
   for each connection. The bookmarks are kept in the database, so restoring a backup
   asks again from that point, and entries already saved are never saved twice.
   A new connection for a login that was connected before gives the same money new ids,
   so money another connection already brought in (the same account, day, amount and
   bank's words) isn't saved again. Its first pull only adds what's older.
4. Only posted activity is kept: the past 13 months on a connection's first pull, then
   whatever came since the last check, however long ago that was. It's listed on the "From
   the bank" page (views/bank.py), which she can't edit. Nothing goes into her books until
   she enters an entry into Transactions. The entry stays listed, marked "In
   Transactions". That transaction can't be archived, only returned to From the bank, and then
   its entry can be entered again (see deleting.py).

Reading an entry: read_words() finds the payment method, who it's from or to, and the
memo in the bank's words. Banks word these differently and may cut them short or leave
them out, so what it can't find stays empty, and the bank's own words are always kept.
It works from those saved words each time, so a fix here also fixes old entries.

The server's hourly checks call check_if_due() (see startup.py).
"""

import json
import os
import re
import ssl
import threading
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import certifi
from sqlalchemy import func, select

from .database import db_session
from .models import BankCheck, BankEntry, Transaction

# The environment variables that hold Plaid's keys, and which of Plaid's systems to use.
CLIENT_ID_VARIABLE = "PLAID_CLIENT_ID"
SECRET_VARIABLE = "PLAID_SECRET"
ENVIRONMENT_VARIABLE = "PLAID_ENV"
PLAID_ADDRESSES = {"production": "https://production.plaid.com", "sandbox": "https://sandbox.plaid.com"}
PLAID_VERSION = "2020-09-14"

CHECK_AGAIN_AFTER = timedelta(days=1)
TRY_AGAIN_WHILE_GATHERING = timedelta(hours=1)
MOST_CHECKS_A_DAY = 24
# What the "Fetch now" button says if it's already been asked that many times.
TOO_MANY_CHECKS = f"Bank: not fetched, because it was checked {MOST_CHECKS_A_DAY} times in the last 24 hours."
# Plaid's word for having everything she asked for when connecting.
HISTORY_READY = "HISTORICAL_UPDATE_COMPLETE"
# The most connections the free plan allows, ever (see the top of this file).
MOST_CONNECTIONS = 10
# How much history Plaid keeps for a connection: its most, 2 years. Plaid sets this when connecting, for good,
# and asking for more costs nothing, so a longer DAYS_OF_HISTORY later needs no reconnecting. (The first 4
# connections asked for only 6 months, so 6 months is all Plaid has for them.)
DAYS_PLAID_KEEPS = 730
# How far back a connection's first pull goes: 13 months (Larry, 2026-11-05: "do 1 year and 1 month").
DAYS_OF_HISTORY = 396
# A deposit dated this long before the fetch that brought it in is history being filled in (such as
# a new connection's first pull), not a new deposit for the box on Home.
NEW_DEPOSIT_DAYS = 14
# How long the page on Plaid's site for connecting stays good.
CONNECT_PAGE_SECONDS = 30 * 60
# Home reminds her this long before the bank's consent runs out.
RENEW_REMINDER = timedelta(days=30)
TIMEOUT_SECONDS = 60
USER_AGENT = "property-manager-app/1.0"
# What Plaid shows her as the app's name, and what it knows her as (no personal details).
NAME_ON_PLAID = "Property Manager"
USER_ON_PLAID = "landlord"
# A transaction this close in date, with the same amount, may be this entry, typed in by hand.
SAME_MONEY_DAYS = 5
LONGEST_PROBLEM = 300
# How many times to start over if her bank's activity changes while reading it (Plaid's advice).
TRIES_WHILE_READING = 3

# The first of these whose words are in an entry is its payment method.
_METHODS = (
    ("Zelle", re.compile(r"\bzelle\b", re.IGNORECASE)),
    ("Venmo", re.compile(r"\bvenmo\b", re.IGNORECASE)),
    ("PayPal", re.compile(r"\bpaypal\b", re.IGNORECASE)),
    ("Wire", re.compile(r"\bwire\b", re.IGNORECASE)),
    # Bank of America's word for a debit card purchase. (It doesn't count as "check".)
    ("Card", re.compile(r"\bcheckcard\b|\bdebit\s+card\b|\bpurchase\b", re.IGNORECASE)),
    # A check deposited at the bank or with its phone app ("MOBILE DEPOSIT", "REMOTE ONLINE DEPOSIT").
    ("Check", re.compile(r"\bcheck\b|\bchk\s+deposit\b|\b(mobile|remote)\b.*\bdeposit\b", re.IGNORECASE)),
    ("Transfer", re.compile(r"\btransfer\b", re.IGNORECASE)),
)
# The ways a person pays a person, whose words say who it's from or to.
_PEOPLE_PAYMENTS = ("Zelle", "Venmo", "PayPal")
# Bank of America's Zelle wording, as far as can be found out so far:
#   Zelle payment from JANE DOE for "Nov rent"; Conf# abc123
#   Zelle payment to JANE DOE for "Nov rent"; Conf# abc123
#   Zelle Transfer Conf# abc123; JANE DOE
_NAMED_PERSON = re.compile(r"\b(?:from|to)\s+(.+?)(?=\s+for\s+[\"“]|\s*;|\s+conf\b|\s+ref\b|\s+on\s+\d|$)", re.IGNORECASE)
_NAME_AFTER_CONF = re.compile(r"\bconf\w*\s*#?\s*\S+\s*;\s*([^;]+?)\s*$", re.IGNORECASE)
_QUOTED_MEMO = re.compile(r"\bfor\s+[\"“](.*?)[\"”]", re.IGNORECASE)

_one_check_at_a_time = threading.Lock()


class BankProblem(Exception):
    """Something went wrong, with a message she can read. It never contains a key or the access token."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code  # Plaid's name for the problem, if it came from Plaid


# ---------- Connecting ----------


def _environment() -> str:
    environment = os.environ.get(ENVIRONMENT_VARIABLE, "").strip().lower()
    return environment if environment in PLAID_ADDRESSES else "production"


def is_set_up(app) -> bool:
    """True if this copy of the app may talk to Plaid: the server, or a preview using the made-up bank."""
    if not (os.environ.get(CLIENT_ID_VARIABLE, "").strip() and os.environ.get(SECRET_VARIABLE, "").strip()):
        return False
    return bool(app.config["ON_SERVER"] or _environment() == "sandbox")


def is_connected(app) -> bool:
    """True if this copy of the app checks a bank."""
    return is_set_up(app) and bool(_connections(app))


def still_gathering(app) -> bool:
    """True if connected, while Plaid hasn't sent all of a new connection's history yet."""
    return is_set_up(app) and any(not connection.get("history_ready") for connection in _connections(app))


def bank_name(app) -> str:
    """The connected bank's name, such as Bank of America."""
    names = {connection.get("bank", ""): True for connection in _connections(app)}  # (in order, without repeats)
    return " and ".join(name for name in names if name)


def renew_by(app) -> date | None:
    """The first day a bank's consent runs out, if that's soon enough to mention."""
    return min((one["renew_by"] for one in trouble(app) if one["renew_by"]), default=None)


def _connections(app) -> list[dict]:
    return _read_link(app.config["DATA_PATHS"])["connections"]


def _key(connection: dict) -> str:
    """How the database tells connections apart: Plaid's id for it. (Empty for the first one ever: see _read_link.)"""
    return connection.get("item_id", "")


def _connection_with(link: dict, key: str) -> dict | None:
    return next((connection for connection in link["connections"] if _key(connection) == key), None)


def start_connecting(app, come_back_to: str, choose_accounts: bool = False, new_login: bool = False, key: str | None = None) -> str:
    """The address of Plaid's page for connecting the bank, or for repairing a connection if there is one.

    choose_accounts: her bank's page also lets her choose which accounts this connection shares ("Add account").
    new_login: a new connection beside the others, for an account under a different login.
    key: which connection, if not the first.
    """
    paths = app.config["DATA_PATHS"]
    link = _read_link(paths)
    fields = {
        "client_name": NAME_ON_PLAID,
        "language": "en",
        "country_codes": ["US"],
        "user": {"client_user_id": USER_ON_PLAID},
        "hosted_link": {"completion_redirect_uri": come_back_to, "url_lifetime_seconds": CONNECT_PAGE_SECONDS},
    }
    if (new_login or not link["connections"]) and _made(link) >= MOST_CONNECTIONS:
        raise BankProblem(f"Plaid's free plan allows {MOST_CONNECTIONS} connections ever, and all of them have been made.")
    connection = None
    if link["connections"] and not new_login:
        connection = link["connections"][0] if key is None else _connection_with(link, key)
        if connection is None:
            raise BankProblem("That bank login isn't connected any more.")
    if connection is not None:
        # Update mode: the same connection, so it doesn't use up another of the 10.
        fields["access_token"] = connection["access_token"]
        if choose_accounts:
            fields["update"] = {"account_selection_enabled": True}
    else:
        fields["products"] = ["transactions"]
        fields["transactions"] = {"days_requested": DAYS_PLAID_KEEPS}
    answer = _post("/link/token/create", fields)
    link["pending_link_token"] = answer["link_token"]
    link.pop("pending_connection", None)
    if connection is not None:
        link["pending_connection"] = _key(connection)
    _write_link(paths, link)
    return answer["hosted_link_url"]


def finish_connecting(app) -> str:
    """See if she finished on Plaid's page. Returns "connected", "repaired", or "" if not (yet)."""
    paths = app.config["DATA_PATHS"]
    link = _read_link(paths)
    token = link.get("pending_link_token")
    if not token:
        return ""
    try:
        answer = _post("/link/token/get", {"link_token": token})
    except BankProblem as problem:
        if problem.code in ("INVALID_LINK_TOKEN", "INVALID_INPUT"):  # (too old to ask about)
            link.pop("pending_link_token")
            link.pop("pending_connection", None)
            _write_link(paths, link)
            return ""
        raise
    sessions = answer.get("link_sessions") or []
    if "pending_connection" in link:
        # (Repairing gives no new key: "An Item's access_token does not change".)
        if not any(session.get("finished_at") and not session.get("exit") for session in sessions):
            return ""
        repaired = _connection_with(link, link.pop("pending_connection"))
        link.pop("pending_link_token")
        if repaired is not None:
            repaired.pop("accounts", None)  # (They may have changed: see connections_on_screen().)
        _write_link(paths, link)
        return "repaired"
    added = [item for session in sessions for item in (session.get("results") or {}).get("item_add_results") or []]
    if not added:
        return ""
    exchanged = _post("/item/public_token/exchange", {"public_token": added[0]["public_token"]})
    link.pop("pending_link_token")
    link["made"] = _made(link) + 1
    link["connections"].append(
        {
            "item_id": exchanged.get("item_id", ""),
            "access_token": exchanged["access_token"],
            "bank": (added[0].get("institution") or {}).get("name", ""),
        }
    )
    _write_link(paths, link)
    return "connected"


def _made(link: dict) -> int:
    """How many connections have ever been made. (Before this was kept, none had been removed, so it's how many there are.)"""
    return link.get("made", len(link["connections"]))


def disconnect(paths, key: str) -> str:
    """End one connection for good, by hand (see bank_by_hand.py). Its entries and checks stay. Returns a line saying so.

    On the free plan this doesn't give the connection back, so the count of ones made stays.
    """
    link = _read_link(paths)
    connection = _connection_with(link, key)
    if connection is None:
        raise BankProblem("There's no connection with that key.")
    link["made"] = _made(link)
    try:
        _post("/item/remove", {"access_token": connection.get("access_token", "")})
    except BankProblem as problem:
        if problem.code != "ITEM_NOT_FOUND":  # (Already gone at Plaid: just forget it here too.)
            raise
    link["connections"].remove(connection)
    _write_link(paths, link)
    left = len(link["connections"])
    return f"Disconnected {connection.get('bank') or 'the bank'} ({key or 'the first connection'}). {left} {'connection' if left == 1 else 'connections'} left; {link['made']} of {MOST_CONNECTIONS} made."


def _read_link(paths) -> dict:
    """The saved connections, as {"connections": [...]}, with what's waiting on Plaid's page if anything is.

    A file from when there could only be one connection has its details at the top level. It's read as
    the first connection, with no item_id, which is also what its checks have in the database, so it
    carries on from its bookmark. The next save writes it the new way.
    """
    try:
        saved = json.loads(paths.bank_link.read_text())
    except (OSError, ValueError):
        return {"connections": []}
    if "connections" in saved:
        return saved
    pending = saved.pop("pending_link_token", None)
    link = {"connections": [saved] if saved.get("access_token") else []}
    if pending:
        link["pending_link_token"] = pending
        if link["connections"]:
            link["pending_connection"] = ""
    return link


def _write_link(paths, link: dict) -> None:
    """Save it so only the server's own user can read it."""
    with os.fdopen(os.open(paths.bank_link, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
        json.dump(link, file)


# ---------- Checking ----------


def check_if_due(app, now: datetime | None = None, ask=None) -> list[str]:
    """Check each connection that's due. Returns lines for the server's log. ask stands in for Plaid in the tests."""
    if not is_connected(app):
        return []
    now = now or datetime.now()
    lines = []
    with _one_check_at_a_time, app.app_context():
        for connection in _connections(app):
            last = _last_check(_key(connection))
            if last is not None:
                gathering = not connection.get("history_ready") and not last.problem
                if now - last.asked_at < (TRY_AGAIN_WHILE_GATHERING if gathering else CHECK_AGAIN_AFTER):
                    continue
            lines.append(_check(app, connection, now, ask or ask_plaid)[1])
    return lines


def check_now(app, now: datetime | None = None, ask=None) -> list[str]:
    """Check every connection right away, within the daily limit: after connecting, or from Settings' "Fetch now" button."""
    if not is_connected(app):
        return []
    now = now or datetime.now()
    with _one_check_at_a_time, app.app_context():
        results = [_check(app, connection, now, ask or ask_plaid) for connection in _connections(app)]
    counts = [new for new, _line in results]
    if len(results) == 1 or None in counts:
        return [line for _new, line in results]
    return [_new_entries_line(sum(counts), still_gathering(app))]


def _check(app, connection: dict, now: datetime, ask) -> tuple[int | None, str]:
    """Ask Plaid about one connection. Returns how many entries were new (None if it couldn't ask), and a line for the log."""
    key = _key(connection)
    asked_lately = db_session.scalar(
        select(func.count(BankCheck.id)).where(BankCheck.item_id == key, BankCheck.asked_at > now - timedelta(days=1))
    )
    if asked_lately >= MOST_CHECKS_A_DAY:
        return None, TOO_MANY_CHECKS
    paths = app.config["DATA_PATHS"]
    bookmark = _last_bookmark(key)
    # Saved before it's sent, so it counts even if something goes wrong while asking.
    check = BankCheck(asked_at=now, cursor=bookmark, item_id=key)
    db_session.add(check)
    db_session.commit()
    try:
        answer = ask(connection.get("access_token", ""), bookmark)
    except BankProblem as problem:
        check.problem = str(problem)
        db_session.commit()
        return None, f"Bank: {problem}"
    link = _read_link(paths)
    saved = _connection_with(link, key) or connection
    skip = _brought_in_already(link, saved, answer.get("accounts") or [])
    # (Only a connection's first pull stops at DAYS_OF_HISTORY. After that, it's whatever came since the last check, however long ago.)
    not_before = date.min if bookmark else now.date() - timedelta(days=DAYS_OF_HISTORY)
    check.new_entries = save_entries(answer, not_before, skip, key)
    check.cursor = answer.get("next_cursor") or bookmark
    db_session.commit()
    before = dict(saved)
    if answer.get("consent_expires"):
        saved["consent_expires"] = answer["consent_expires"]
    if answer.get("accounts"):
        saved["accounts"] = _accounts_to_save(answer["accounts"], skip)
    if answer.get("transactions_update_status") == HISTORY_READY:
        saved["history_ready"] = True
    if saved != before:
        _write_link(paths, link)
    return check.new_entries, _new_entries_line(check.new_entries, not saved.get("history_ready"))


def _new_entries_line(new: int, gathering: bool) -> str:
    line = f"Bank: {new} new {'entry' if new == 1 else 'entries'}."
    return line + " Plaid is still gathering the past 13 months, which can take a few minutes." if gathering else line


def _brought_in_already(link: dict, connection: dict, found: list[dict]) -> set:
    """The ids of accounts an earlier connection already brings in: the same bank, name and last 4 digits.

    Two logins can both see a joint account. Plaid gives its activity different ids under each,
    so without this it would be listed, and each of its entries saved, twice.
    """
    earlier = []
    for other in link["connections"]:
        if other is connection:
            break
        earlier += [(other.get("bank"), account["name"]) for account in other.get("accounts") or []]
    return {account.get("account_id") for account in found if (connection.get("bank"), _account_name(account)) in earlier}


def _last_bookmark(key: str) -> str:
    """Where a connection's last good check got to, so Plaid sends only what's new."""
    query = (
        select(BankCheck.cursor)
        .where(BankCheck.item_id == key, BankCheck.cursor != "")
        .order_by(BankCheck.asked_at.desc(), BankCheck.id.desc())
    )
    return db_session.scalar(query.limit(1)) or ""


def ask_plaid(access_token: str, bookmark: str) -> dict:
    """What's new at the bank since the bookmark, with when the bank's consent runs out."""
    for _try in range(TRIES_WHILE_READING):
        try:
            answer = _read_all_pages(access_token, bookmark)
            break
        except BankProblem as problem:
            if problem.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                raise
    else:
        raise BankProblem("The bank's activity kept changing while the app read it. The app will try again.")
    item = _post("/item/get", {"access_token": access_token}).get("item") or {}
    expires = item.get("consent_expiration_time")
    answer["consent_expires"] = expires[:10] if expires else ""
    return answer


def _read_all_pages(access_token: str, bookmark: str) -> dict:
    """Plaid sends a long list in pages: read them all, or start over if one fails (its advice)."""
    whole = {"added": [], "modified": [], "removed": [], "accounts": [], "next_cursor": bookmark}
    while True:
        fields = {"access_token": access_token, "count": 500, "options": {"include_original_description": True}}
        if whole["next_cursor"]:
            fields["cursor"] = whole["next_cursor"]
        page = _post("/transactions/sync", fields)
        for part in ("added", "modified", "removed"):
            whole[part] += page.get(part) or []
        whole["accounts"] = page.get("accounts") or whole["accounts"]
        whole["next_cursor"] = page.get("next_cursor") or whole["next_cursor"]
        whole["transactions_update_status"] = page.get("transactions_update_status")
        if not page.get("has_more"):
            return whole


def _post(path: str, fields: dict) -> dict:
    """Ask Plaid something. The keys go in headers. Errors are in plain words, and show no keys or tokens."""
    request = urllib.request.Request(
        PLAID_ADDRESSES[_environment()] + path,
        data=json.dumps(fields).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "PLAID-CLIENT-ID": os.environ.get(CLIENT_ID_VARIABLE, "").strip(),
            "PLAID-SECRET": os.environ.get(SECRET_VARIABLE, "").strip(),
            "Plaid-Version": PLAID_VERSION,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        return json.loads(_send(request))
    except urllib.error.HTTPError as error:
        raise _problem_from(error) from None
    except OSError:
        raise BankProblem("Plaid couldn't be reached. The app will try again.") from None
    except ValueError:
        raise BankProblem("Plaid's answer couldn't be read. The app will try again.") from None


def _problem_from(error: urllib.error.HTTPError) -> BankProblem:
    try:
        code = str(json.loads(error.read()).get("error_code") or "")
    except (OSError, ValueError, AttributeError):
        code = ""
    if code in ("ITEM_LOGIN_REQUIRED", "PENDING_DISCONNECT", "PENDING_EXPIRATION"):
        return BankProblem("The bank needs you to sign in again. Click “Reconnect the bank” on the From the bank page.", code)
    if code in ("INVALID_API_KEYS", "UNAUTHORIZED_ENVIRONMENT"):
        return BankProblem("Plaid didn't accept the app's keys. Please check them on Render's Environment page.", code)
    if code == "ADDITION_LIMIT":
        return BankProblem("Plaid's free plan has no connections left, and more would need a paid plan.", code)
    return BankProblem(f"Plaid answered with an error ({code or error.code}). The app will try again.", code)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """The keys go only to the address they're for, so a redirect counts as an error."""

    def redirect_request(self, *_args, **_kwargs):
        return None


_opener = urllib.request.build_opener(
    _NoRedirects, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))
)


def _send(request: urllib.request.Request) -> bytes:
    with _opener.open(request, timeout=TIMEOUT_SECONDS) as answer:
        return answer.read()


def save_entries(answer: dict, not_before: date, skip_accounts: set = frozenset(), connection_key: str = "") -> int:
    """Save the new activity, and return how many entries were new. The caller commits.

    A change Plaid reports updates an entry still waiting. One in Transactions is in her books,
    and stays as it is. skip_accounts: the ids of accounts another connection brings in.
    connection_key: which connection this came from (see _key), so money another one already
    brought in isn't saved twice.
    """
    accounts = {account.get("account_id"): _account_name(account) for account in answer.get("accounts") or []}
    matched = Counter()
    new = 0
    for item in (answer.get("added") or []) + (answer.get("modified") or []):
        cents = _cents(item.get("amount"))
        day = _day(item)
        if item.get("pending") or not cents or day is None or day < not_before or item.get("account_id") in skip_accounts:
            continue
        bank_id = str(item.get("transaction_id"))
        entry = db_session.scalar(select(BankEntry).where(BankEntry.bank_id == bank_id))
        if entry is None:
            words = _clean(item.get("original_description") or item.get("name"))
            same = (accounts.get(item.get("account_id"), ""), day, cents, words)
            if _another_connection_has_it(same, connection_key, matched):
                continue
            entry = BankEntry(bank_id=bank_id, item_id=connection_key)
            db_session.add(entry)
            new += 1
        elif entry.status != "New":
            continue
        entry.account_name = accounts.get(item.get("account_id"), "")
        entry.day = day
        entry.amount_cents = cents
        entry.description = _clean(item.get("original_description") or item.get("name"))
        entry.payee = _clean(item.get("merchant_name"))
        entry.memo = ""
        db_session.flush()
    for item in answer.get("removed") or []:
        entry = db_session.scalar(select(BankEntry).where(BankEntry.bank_id == str(item.get("transaction_id"))))
        if entry is not None and entry.status == "New":
            db_session.delete(entry)
    return new


def _another_connection_has_it(same: tuple, connection_key: str, matched: Counter) -> bool:
    """True if this is money another connection already brought in, such as before a login was connected again.

    same: (account, day, amount, the bank's words). Two payments can match exactly (two $50 to
    the same person on one day), so it counts: this connection only skips as many as the others have.
    """
    account_name, day, cents, words = same
    alike = select(func.count(BankEntry.id)).where(
        BankEntry.account_name == account_name,
        BankEntry.day == day,
        BankEntry.amount_cents == cents,
        BankEntry.description == words,
    )
    others = db_session.scalar(alike.where(BankEntry.item_id != connection_key))
    mine = db_session.scalar(alike.where(BankEntry.item_id == connection_key))
    if others > mine + matched[same]:
        matched[same] += 1
        return True
    return False


def connections_on_screen(app) -> list[dict]:
    """Each connection, for the Accounts page: its name, its accounts (with no keys), and any problem listing them.

    Each check saves a connection's accounts, so this only asks Plaid for ones with none saved.
    """
    paths = app.config["DATA_PATHS"]
    link = _read_link(paths)
    shown = []
    changed = False
    for number, connection in enumerate(link["connections"], 1):
        problem = ""
        if "accounts" not in connection:
            try:
                found = _post("/accounts/get", {"access_token": connection.get("access_token", "")}).get("accounts") or []
                connection["accounts"] = _accounts_to_save(found, _brought_in_already(link, connection, found))
                changed = True
            except BankProblem as error:
                problem = str(error)
        shown.append(
            {
                "key": _key(connection),
                "label": _label(link, number),
                "bank": connection.get("bank", ""),
                "accounts": connection.get("accounts") or [],
                "problem": problem,
            }
        )
    if changed:
        _write_link(paths, link)
    return shown


def _label(link: dict, number: int) -> str:
    """What to call a connection: empty if it's the only one, since there's nothing to tell it from."""
    return f"Login {number}" if len(link["connections"]) > 1 else ""


def can_add_login(app) -> bool:
    return len(_connections(app)) < MOST_CONNECTIONS


def _accounts_to_save(found: list[dict], skip: set = frozenset()) -> list[dict]:
    return [
        {"name": _account_name(account), "kind": str(account.get("subtype") or account.get("type") or "").capitalize()}
        for account in found
        if account.get("account_id") not in skip
    ]


def _account_name(account: dict) -> str:
    name = _clean(account.get("official_name") or account.get("name"))
    return f"{name} ···{account['mask']}" if account.get("mask") else name


def _cents(amount) -> int | None:
    """In cents, positive for money in as in a transaction. Plaid counts money in as negative, so it's turned around."""
    try:
        return -int((Decimal(str(amount)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _day(item: dict) -> date | None:
    """The day it happened, if the bank says, else the day it posted."""
    try:
        return date.fromisoformat(item.get("authorized_date") or item.get("date") or "")
    except ValueError:
        return None


def _clean(words) -> str:
    return " ".join(str(words or "").split())


# ---------- What the screens ask ----------


def _last_check(key: str) -> BankCheck | None:
    query = select(BankCheck).where(BankCheck.item_id == key).order_by(BankCheck.asked_at.desc(), BankCheck.id.desc())
    return db_session.scalar(query.limit(1))


def last_good_check() -> BankCheck | None:
    return db_session.scalar(
        select(BankCheck).where(BankCheck.problem == "").order_by(BankCheck.asked_at.desc(), BankCheck.id.desc()).limit(1)
    )


def trouble(app) -> list[dict]:
    """The connections that need her: what went wrong with the latest check, or a bank's consent running out soon."""
    if not is_set_up(app):
        return []
    found = []
    link = _read_link(app.config["DATA_PATHS"])
    for number, connection in enumerate(link["connections"], 1):
        last = _last_check(_key(connection))
        problem = last.problem if last else ""
        expires = connection.get("consent_expires")
        due = date.fromisoformat(expires) if expires else None
        due = due if due and due - date.today() <= RENEW_REMINDER else None
        if problem or due:
            found.append({"key": _key(connection), "label": _label(link, number), "problem": problem, "renew_by": due})
    return found


def last_problem(app) -> str:
    """What went wrong with the latest check of a connection, if anything did."""
    return next((one["problem"] for one in trouble(app) if one["problem"]), "")


def waiting_count() -> int:
    """How many entries on the "From the bank" page aren't in Transactions yet."""
    return db_session.scalar(select(func.count(BankEntry.id)).where(BankEntry.status == "New"))


def new_deposits_from_latest_fetch() -> int:
    """How many deposits the latest fetch brought in that aren't in her books yet (the box on Home).

    Counted by when they came in, not the date on them, since a bank may post one days after its date.
    Older history filled in by the same fetch (see NEW_DEPOSIT_DAYS) isn't counted.
    """
    latest = db_session.scalar(select(func.max(BankCheck.asked_at)))
    if latest is None:
        return 0
    query = select(func.count(BankEntry.id)).where(
        BankEntry.status == "New",
        BankEntry.amount_cents > 0,
        BankEntry.created_at >= latest,
        BankEntry.day >= latest.date() - timedelta(days=NEW_DEPOSIT_DAYS),
    )
    return db_session.scalar(query)


def has_entries() -> bool:
    return db_session.scalar(select(BankEntry.id).limit(1)) is not None


def waiting_entry(entry_id: int | None) -> BankEntry | None:
    """The entry, if it can still be entered into Transactions: waiting, or checked (Overridden)."""
    entry = db_session.get(BankEntry, entry_id or 0)
    return entry if entry is not None and entry.status in ("New", "Overridden") else None


def entry_of(transaction: Transaction) -> BankEntry | None:
    """The entry a transaction was made from, if it came from the bank. (Not one linked to a transaction she typed in.)"""
    return db_session.scalar(select(BankEntry).where(BankEntry.transaction_id == transaction.id, BankEntry.status == "Entered"))


def link(entry: BankEntry, transaction: Transaction) -> None:
    """Link a waiting or overridden entry to a transaction she typed in (by AI: see ENTRY_STATUSES). The caller commits."""
    if entry.status not in ("New", "Overridden") or entry.never_link:
        raise ValueError(f"Entry {entry.id} can't be linked: it's {entry.status}{', and was unlinked before' if entry.never_link else ''}.")
    entry.linked_from = entry.status
    entry.status = "Linked"
    entry.transaction = transaction


def unlink(entry: BankEntry) -> None:
    """Undo a link, for good: the entry goes back to what it was, and is never linked again. The caller commits."""
    entry.status = entry.linked_from or "New"
    entry.linked_from = ""
    entry.transaction = None
    entry.never_link = True


def unlink_from(transaction: Transaction) -> int:
    """Unlink every entry linked to this transaction (when it's archived). Returns how many. The caller commits."""
    linked = list(db_session.scalars(select(BankEntry).where(BankEntry.transaction_id == transaction.id, BankEntry.status == "Linked")))
    for entry in linked:
        unlink(entry)
    return len(linked)


# ---------- Reading what the bank says ----------


@dataclass
class Reading:
    method: str = ""  # such as Zelle or Check
    who: str = ""  # such as: from JANE DOE
    memo: str = ""

    def after_method(self) -> str:
        """Who and the memo, such as: from JANE DOE, memo: Nov rent."""
        return ", ".join(part for part in (self.who, f"memo: {self.memo}" if self.memo else "") if part)

    def as_details(self) -> str:
        """A transaction's details, such as: Zelle from JANE DOE, memo: Nov rent. Empty if nothing was found."""
        start = " ".join(part for part in (self.method, self.who) if part)
        return ", ".join(part for part in (start, f"memo: {self.memo}" if self.memo else "") if part)


def all_words(entry: BankEntry) -> str:
    """Everything the bank says about an entry, without repeats."""
    words = []
    for text in (entry.description, entry.payee, entry.memo):
        if text and text.lower() not in (word.lower() for word in words):
            words.append(text)
    return " · ".join(words)


def read_words(entry: BankEntry) -> Reading:
    """The payment method, who it's from or to, and the memo, as far as the bank's words say."""
    words = all_words(entry)
    method = next((name for name, pattern in _METHODS if pattern.search(words)), "")
    name = _other_party(entry, method)
    who = f"{'from' if entry.amount_cents > 0 else 'to'} {name}" if name else ""
    return Reading(method, who, _memo(entry, method, name))


def _other_party(entry: BankEntry, method: str) -> str:
    description = entry.description or ""
    # ("from" only counts for a person's payment: "transfer from CHK 1234" isn't anyone's name.)
    if method in _PEOPLE_PAYMENTS:
        for pattern in (_NAMED_PERSON, _NAME_AFTER_CONF):
            found = pattern.search(description)
            if found:
                return found.group(1).strip()
    payee = entry.payee or ""
    return payee if payee.lower() != method.lower() and payee.lower() not in description.lower() else ""


def _memo(entry: BankEntry, method: str, name: str) -> str:
    """A memo the words call out. Otherwise the bank's own words are the memo, unless all they say is who paid."""
    description = entry.description or ""
    quoted = _QUOTED_MEMO.search(description)
    if quoted:
        return quoted.group(1).strip()
    if entry.memo and entry.memo.lower() != description.lower():
        return entry.memo
    return "" if method in _PEOPLE_PAYMENTS and name else description


def details_for(entry: BankEntry) -> str:
    """What a transaction made from this entry says under Details."""
    return read_words(entry).as_details() or all_words(entry)


# ---------- Spotting money she already typed in ----------


def maybe_entered(entries: list[BankEntry]) -> dict[int, Transaction]:
    """For each entry, by id, a transaction that may already be the same money: the same amount, a close date.

    Each transaction counts for one entry only, so two equal deposits don't both claim it. Closest date first.
    """
    if not entries:
        return {}
    close = timedelta(days=SAME_MONEY_DAYS)
    days = [entry.day for entry in entries]
    from_the_bank = select(BankEntry.transaction_id).where(BankEntry.transaction_id.is_not(None))
    query = select(Transaction).where(
        Transaction.archived_at.is_(None),
        Transaction.transaction_date.between(min(days) - close, max(days) + close),
        Transaction.id.not_in(from_the_bank),
    )
    by_amount = defaultdict(list)
    for transaction in db_session.scalars(query):
        by_amount[transaction.amount_cents].append(transaction)
    found = {}
    for entry in entries:
        near = [item for item in by_amount[entry.amount_cents] if abs(item.transaction_date - entry.day) <= close]
        if near:
            closest = min(near, key=lambda item: abs(item.transaction_date - entry.day))
            by_amount[entry.amount_cents].remove(closest)
            found[entry.id] = closest
    return found
