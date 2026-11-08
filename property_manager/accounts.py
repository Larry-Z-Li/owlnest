"""Signing in to the app in a web browser, on the server.

Anyone can open the server's address in a browser, on a computer or a phone. To
see her records they sign in with a name and password, which someone already
signed in adds in Settings ("People who can sign in"). The app installed on a
computer doesn't need to sign in: it carries the server's secret key instead
(see security.py and relay.py).

* Passwords are never stored, only a scrambled form (a "hash") that can check a
  password but can't be turned back into it. Werkzeug, which comes with Flask,
  does the scrambling.
* After WRONG_TRIES_ALLOWED wrong passwords in a row, that name is locked: it
  can't sign in at all until someone unlocks it in Settings (or gives it a new
  password). So nobody can try password after password. Devices already signed
  in stay signed in, and the Home page says the name is locked.
* Every wrong login is counted, by day and by name, so Larry can ask whether someone
  is guessing (failed_login_report). The password and any name that doesn't exist are
  never kept, since people sometimes type a password in the name box. The count is in a
  file of its own (one server process, so a lock is enough), because someone guessing
  must not be able to get in the way of her saving her records.
* Signing in gives the browser a long random code in a cookie, which keeps it
  signed in for STAYS_SIGNED_IN_FOR after it was last used. The sign_ins table
  keeps only a scrambled form of each code, one row per device.
* Signing out deletes that device's row. Changing someone's password signs
  them out everywhere else.
"""

import hashlib
import json
import os
import secrets
import threading
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import func, select, update
from werkzeug.security import check_password_hash, generate_password_hash

from .database import db_session
from .models import Account, SignIn

SHORTEST_PASSWORD = 8
LONGEST_PASSWORD = 200
WRONG_TRIES_ALLOWED = 10
# What a wrong login is counted under when the name typed isn't anyone's.
NO_SUCH_NAME = "a name that doesn't exist"
# How many days with wrong logins the report lists, one by one.
REPORT_DAYS = 30
_counting_failed_logins = threading.Lock()
STAYS_SIGNED_IN_FOR = timedelta(days=30)

# The cookie that holds the browser's sign-in code.
COOKIE = "signed_in"

WRONG_NAME_OR_PASSWORD = "That name and password don't match. Please try again."
LOCKED = (
    f"This name is locked, because a wrong password was typed {WRONG_TRIES_ALLOWED} times in a row. "
    "Ask whoever looks after the app to unlock it."
)

# Checked against when nobody has the name typed, so a wrong name takes as long as a
# wrong password and the time it takes doesn't tell anyone which names exist.
_NOBODY = generate_password_hash(secrets.token_urlsafe(16))


def find(name: str) -> Account | None:
    """The account with this name, ignoring capitals and spaces around it."""
    return db_session.scalar(select(Account).where(func.lower(Account.name) == name.strip().lower()))


def everyone() -> list[Account]:
    return list(db_session.scalars(select(Account).order_by(func.lower(Account.name))))


def name_problem(name: str, account: Account | None = None) -> str | None:
    """Why this name can't be used (for a new account, or to rename account), or None."""
    same_name = find(name)
    if same_name is not None and same_name is not account:
        return f"Someone is already called “{same_name.name}”. Please choose another name."
    return None


def password_problem(password: str, again: str) -> str | None:
    """Why this new password can't be used, or None. again is the same password typed a second time."""
    if len(password) < SHORTEST_PASSWORD:
        return (
            f"Please use at least {SHORTEST_PASSWORD} characters. A few words together are "
            "hardest to guess, and easy to remember."
        )
    if len(password) > LONGEST_PASSWORD:
        return f"Please use at most {LONGEST_PASSWORD} characters."
    if password != again:
        return "The two passwords aren't the same. Please type them again."
    return None


def add(name: str, password: str) -> Account:
    """A new account. The caller checks name_problem and password_problem first, then commits."""
    account = Account(name=name.strip(), password_hash=generate_password_hash(password))
    db_session.add(account)
    return account


def change_password(account: Account, password: str, keep: SignIn | None = None) -> None:
    """Give account a new password (which also unlocks it), and sign it out everywhere except keep (this device).

    The caller commits.
    """
    account.password_hash = generate_password_hash(password)
    unlock(account)
    for sign_in in list(account.sign_ins):
        if sign_in is not keep:
            account.sign_ins.remove(sign_in)


def check_password(name: str, password: str, now: datetime) -> tuple[Account | None, str | None]:
    """Check a name and password typed on the sign-in page.

    Returns (the account, None) if they're right, or (None, what to tell them).
    A wrong try is saved here. If they're right, the caller commits, so the wrong tries start over.

    Someone guessing sends many at once, so the read ends before the slow password check, and a
    wrong try only writes. (SQLite refuses a read that turns into a write while another write is going on.)
    """
    account = find(name)
    if account is None:
        db_session.commit()
        check_password_hash(_NOBODY, password)
        _count_failed_login(NO_SUCH_NAME, now)
        return None, WRONG_NAME_OR_PASSWORD
    account_id, who, scrambled, tries, is_locked = (
        account.id, account.name, account.password_hash, account.wrong_tries, account.locked_at is not None,
    )
    db_session.commit()
    if is_locked:
        # (Not even checked: while it's locked, guessing gets nowhere.)
        _count_failed_login(who, now)
        return None, LOCKED
    this_account = update(Account).where(Account.id == account_id).execution_options(synchronize_session=False)
    if check_password_hash(scrambled, password):
        if tries:
            db_session.execute(this_account.values(wrong_tries=0))
        return account, None
    db_session.execute(this_account.values(wrong_tries=Account.wrong_tries + 1))
    db_session.execute(this_account.where(Account.wrong_tries >= WRONG_TRIES_ALLOWED, Account.locked_at.is_(None)).values(locked_at=now))
    tries = db_session.scalar(select(Account.wrong_tries).where(Account.id == account_id))
    db_session.commit()
    _count_failed_login(who, now)
    return None, LOCKED if tries >= WRONG_TRIES_ALLOWED else WRONG_NAME_OR_PASSWORD


def _count_failed_login(who: str, now: datetime) -> None:
    """Add one to today's count. A problem saving it never gets in the way of refusing the login."""
    path = current_app.config["DATA_PATHS"].failed_logins
    with _counting_failed_logins:
        counts = _read_failed_logins(path)
        today = counts["days"].setdefault(now.date().isoformat(), {})
        today[who] = today.get(who, 0) + 1
        counts["latest"] = f"{now:%Y-%m-%d %H:%M}"
        try:
            # (Replaced in one step, so the report never reads half of it.)
            being_written = path.with_suffix(".tmp")
            being_written.write_text(json.dumps(counts))
            os.replace(being_written, path)
        except OSError as problem:
            print(f"Couldn't save the count of wrong logins: {problem}")


def _read_failed_logins(path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"days": {}}


def failed_login_report(now: datetime) -> str:
    """The wrong logins so far, in plain text, for Larry to ask about (see views/settings.py)."""
    counts = _read_failed_logins(current_app.config["DATA_PATHS"].failed_logins)
    days = counts["days"]
    today = now.date()

    def since(how_many_days: int) -> int:
        return sum(
            sum(that_day.values())
            for day, that_day in days.items()
            if date.fromisoformat(day) > today - timedelta(days=how_many_days)
        )

    locked_names = ", ".join(account.name for account in locked()) or "none"
    lines = [
        f"Wrong logins on the sign-in page, as of {now:%Y-%m-%d %H:%M}",
        f"Today: {since(1)}",
        f"Last 7 days: {since(7)}",
        f"Last 30 days: {since(30)}",
        f"All time: {sum(sum(that_day.values()) for that_day in days.values())}",
        f"Latest: {counts.get('latest', 'none')}",
        f"Locked names: {locked_names}",
    ]
    if days:
        lines += ["", "By day:"]
    for day in sorted(days, reverse=True)[:REPORT_DAYS]:
        most_first = sorted(days[day].items(), key=lambda item: (-item[1], item[0]))
        who = ", ".join(f"{name}: {count}" for name, count in most_first)
        lines.append(f"{day}  {sum(days[day].values())}  ({who})")
    return "\n".join(lines)


def unlock(account: Account) -> None:
    """Let a locked name sign in again. The caller commits."""
    account.wrong_tries = 0
    account.locked_at = None


def locked() -> list[Account]:
    """The names locked after too many wrong passwords."""
    return list(db_session.scalars(select(Account).where(Account.locked_at.is_not(None)).order_by(Account.locked_at)))


def start(account: Account, user_agent: str, now: datetime) -> str:
    """Sign account in on this device. Returns the code for the browser's cookie. The caller commits."""
    # Forget this account's devices that haven't been used for a long time.
    for old in list(account.sign_ins):
        if _expired(old, now):
            account.sign_ins.remove(old)
    code = secrets.token_urlsafe(32)
    account.sign_ins.append(SignIn(token_hash=_scramble(code), device=device_name(user_agent), last_used_at=now))
    return code


def sign_in_for(code: str | None, now: datetime) -> SignIn | None:
    """The sign-in that this browser's cookie belongs to, or None if it isn't (or is no longer) signed in."""
    if not code or len(code) > 100:
        return None
    sign_in = db_session.scalar(select(SignIn).where(SignIn.token_hash == _scramble(code)))
    if sign_in is None or _expired(sign_in, now):
        return None
    return sign_in


def device_name(user_agent: str) -> str:
    """A short name for the device a browser is on, such as "iPhone (Safari)", from what the browser says about itself."""
    devices = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"), ("Windows", "Windows computer"),
               ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux computer"))
    # (Order matters: Edge's and Chrome's descriptions also mention Safari.)
    browsers = (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("FxiOS", "Firefox"), ("CriOS", "Chrome"),
                ("Chrome/", "Chrome"), ("Safari/", "Safari"))
    device = next((name for clue, name in devices if clue in user_agent), "Unknown device")
    browser = next((name for clue, name in browsers if clue in user_agent), "")
    return f"{device} ({browser})" if browser else device


def _expired(sign_in: SignIn, now: datetime) -> bool:
    return now - sign_in.last_used_at > STAYS_SIGNED_IN_FOR


def _scramble(code: str) -> str:
    """The form of a sign-in code kept in the database. (The codes are long and random, so a quick scramble is enough.)"""
    return hashlib.sha256(code.encode()).hexdigest()


def put_cookie(response, code: str) -> None:
    """Keep this browser signed in. The cookie is renewed as it's used (see security.py)."""
    response.set_cookie(
        COOKIE,
        code,
        max_age=int(STAYS_SIGNED_IN_FOR.total_seconds()),
        path="/",
        httponly=True,  # page scripts can't read it
        secure=True,  # only sent over HTTPS
        samesite="Lax",  # not sent with forms from other websites
    )


def remove_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="Lax")
