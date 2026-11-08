"""Each home's estimated value, from RentCast (rentcast.io), looked up about once a month.

RentCast's free plan includes 50 lookups a month and charges for every one after
that, and it can't stop at 50 by itself. So the app keeps count, and never asks
more often than these rules allow:

1. Only the server looks values up, and only when RENTCAST_API_KEY is set (on
   Render's Environment page). The app on a computer, and the tests, never do.
2. Only homes with "Look up its estimated value each month" ticked (on the
   property's Edit page) are looked up, and never archived ones.
3. A home is looked up again 32 days after its last lookup, or sooner if its
   address changes. RentCast's month starts on the day the plan began (not on
   the 1st) and is never longer than 31 days, so 32 days apart means a home is
   never looked up twice in one of RentCast's months. A lookup that failed is
   tried again a week later.
4. However many homes there are, the app makes at most 40 lookups in any 31
   days. That leaves 10 of the 50 for trying RentCast's website, which counts too.
5. Each lookup is saved in the database before it's sent, and every one counts
   toward the 40, even one that failed: one that timed out may still have been
   counted by RentCast.
6. A home whose address has no ZIP code is skipped, because RentCast needs the full address.
7. If RentCast doesn't accept the key, lookups stop until the server restarts
   (which it does when the key is changed on Render).

RentCast's answer also says how many bedrooms and bathrooms the home has, and its
square feet. Those fill in the property's own fields when they're still empty
(she can always change them), at no extra cost.

She can also type in her own value on the property's Edit page. While it's set, it's the
home's value everywhere, instead of RentCast's, even if lookups go on (Larry, 2026-11-05).

The server's hourly checks call look_up_due_values() (see startup.py).
"""

import json
import os
import re
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import urlencode

import certifi
from sqlalchemy import func, select

from . import records
from .database import db_session
from .models import Property, ValueLookup

# The environment variable that holds the RentCast API key (rule 1).
API_KEY_VARIABLE = "RENTCAST_API_KEY"
# RentCast's "value estimate" (see developers.rentcast.io).
VALUE_ESTIMATE_URL = "https://api.rentcast.io/v1/avm/value"

LOOK_UP_AGAIN_AFTER = timedelta(days=32)  # rule 3
TRY_AGAIN_AFTER_A_PROBLEM = timedelta(days=7)  # rule 3
MOST_LOOKUPS = 40  # rule 4: at most this many...
LIMIT_PERIOD = timedelta(days=31)  # ...in any 31 days
# How long to wait for RentCast's answer.
TIMEOUT_SECONDS = 30

# A US ZIP code, such as 92660 or 92660-1234 (rule 6).
_ZIP_CODE = re.compile(r"\b\d{5}(-\d{4})?\b")

# Only one round of lookups at a time. (The server runs one copy of the app, so this covers it.)
_one_round_at_a_time = threading.Lock()


class LookupProblem(Exception):
    """A lookup that didn't work, with a message she can read.

    stop_all: no more lookups until the server restarts (RentCast didn't accept the key).
    stop_round: no more lookups this hour (RentCast couldn't be reached); the rest wait an hour.
    """

    def __init__(self, message: str, *, stop_round: bool = False, stop_all: bool = False):
        super().__init__(message)
        self.stop_round = stop_round or stop_all
        self.stop_all = stop_all


def api_key() -> str:
    return os.environ.get(API_KEY_VARIABLE, "").strip()


def lookups_are_on(app) -> bool:
    """True if this copy of the app looks values up: only the server, and only with a key (rule 1)."""
    return bool(app.config["ON_SERVER"] and api_key())


def look_up_due_values(app, now: datetime | None = None, ask=None) -> list[str]:
    """Look up the value of each home that's due, within the rules above.

    Returns a line about each lookup, for the server's log. ask is the function
    that asks RentCast (the tests pass a stand-in, so they never reach RentCast).
    """
    if not lookups_are_on(app):
        return []
    now = now or datetime.now()
    ask = ask or ask_rentcast
    log = []
    with _one_round_at_a_time, app.app_context():
        for a_property in records.active_properties():
            if app.config.get("VALUE_LOOKUPS_STOPPED"):
                break
            if not a_property.look_up_value:
                continue
            address = one_line_address(a_property.address)
            if not has_zip_code(address) or not is_due(a_property, address, now):
                continue
            if lookups_since(now - LIMIT_PERIOD) >= MOST_LOOKUPS:
                log.append(f"Home values: stopped, because {MOST_LOOKUPS} lookups were made in the last 31 days.")
                break

            # Saved before it's sent, so it counts even if something goes wrong while asking (rule 5).
            lookup = ValueLookup(property=a_property, address=address, asked_at=now)
            db_session.add(lookup)
            db_session.commit()
            try:
                answer = ask(address, api_key())
            except LookupProblem as problem:
                lookup.problem = str(problem)
                db_session.commit()
                log.append(f"Home values: {a_property.nickname}: {problem}")
                if problem.stop_all:
                    app.config["VALUE_LOOKUPS_STOPPED"] = str(problem)
                if problem.stop_round:
                    break
                continue
            lookup.value_cents, lookup.low_cents, lookup.high_cents = answer.value_cents, answer.low_cents, answer.high_cents
            fill_in_the_home(a_property, answer)
            db_session.commit()
            log.append(f"Home values: {a_property.nickname}: ${lookup.value_cents // 100:,}")
    return log


def is_due(a_property: Property, address: str, now: datetime) -> bool:
    """True if the home should be looked up now (rule 3)."""
    last = latest_lookup(a_property)
    if last is None or last.address != address:
        return True
    wait = LOOK_UP_AGAIN_AFTER if last.value_cents is not None else TRY_AGAIN_AFTER_A_PROBLEM
    return now - last.asked_at >= wait


def lookups_since(start: datetime) -> int:
    """How many lookups were made after start, for every home (archived and deleted ones too)."""
    return db_session.scalar(select(func.count()).select_from(ValueLookup).where(ValueLookup.asked_at > start))


def one_line_address(address: str) -> str:
    """ "149 Elm St\\nSpringfield, IL 62701" becomes "149 Elm St, Springfield, IL 62701". """
    return ", ".join(line.strip() for line in address.splitlines() if line.strip())


def has_zip_code(address: str) -> bool:
    return bool(_ZIP_CODE.search(address))


# ---------- Asking RentCast ----------


@dataclass
class Answer:
    """What RentCast says about a home: its value, and the range it's 85% sure of, in cents."""

    value_cents: int
    low_cents: int | None
    high_cents: int | None
    # The home itself, when RentCast knows.
    bedrooms: int | None = None
    bathrooms: float | None = None
    square_feet: int | None = None


def fill_in_the_home(a_property: Property, answer: Answer) -> None:
    """Fill in the home's bedrooms, bathrooms and square feet from RentCast, where they're still empty."""
    if a_property.bedrooms is None:
        a_property.bedrooms = answer.bedrooms
    if a_property.bathrooms is None:
        a_property.bathrooms = answer.bathrooms
    if a_property.square_feet is None:
        a_property.square_feet = answer.square_feet


def ask_rentcast(address: str, key: str) -> Answer:
    """Ask RentCast what the home at address is worth. Raises LookupProblem if there's no answer."""
    request = urllib.request.Request(
        f"{VALUE_ESTIMATE_URL}?{urlencode({'address': address})}",
        headers={"Accept": "application/json", "X-Api-Key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=_secure_context()) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise _problem_from(error) from None
    except OSError as error:
        # No internet, RentCast is down, or it didn't answer in time.
        reason = getattr(error, "reason", None) or error
        raise LookupProblem(f"RentCast couldn't be reached ({reason}).", stop_round=True) from None
    return read_answer(body)


def read_answer(body: bytes) -> Answer:
    """Read RentCast's answer, such as {"price": 468000, "priceRangeLow": 440000, "priceRangeHigh": 495000,
    "subjectProperty": {"bedrooms": 3, "bathrooms": 2.5, "squareFootage": 1916, ...}, ...}."""
    try:
        answer = json.loads(body)
        value = answer["price"]
        low, high = answer.get("priceRangeLow"), answer.get("priceRangeHigh")
        home = answer.get("subjectProperty") or {}
        bedrooms, bathrooms, square_feet = home.get("bedrooms"), home.get("bathrooms"), home.get("squareFootage")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise LookupProblem("RentCast's answer couldn't be read.") from None
    if not _is_dollars(value):
        raise LookupProblem("RentCast didn't give a value for this address.")
    return Answer(
        value_cents=_cents(value),
        low_cents=_cents(low) if _is_dollars(low) else None,
        high_cents=_cents(high) if _is_dollars(high) else None,
        bedrooms=int(bedrooms) if _is_count(bedrooms) else None,
        bathrooms=float(bathrooms) if _is_count(bathrooms) else None,
        square_feet=int(square_feet) if _is_count(square_feet) and square_feet > 0 else None,
    )


def _is_count(number) -> bool:
    """A believable number of rooms or square feet."""
    return isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number < 1_000_000


def _is_dollars(amount) -> bool:
    return isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0


def _cents(dollars: float) -> int:
    return round(dollars * 100)


def _problem_from(error: urllib.error.HTTPError) -> LookupProblem:
    """A plain-words problem for an error answer from RentCast (it doesn't charge for these)."""
    if error.code in (401, 403):
        return LookupProblem(
            "RentCast didn't accept the API key, so no more values are looked up until the key is fixed. "
            "(Change RENTCAST_API_KEY on Render's Environment page; the server then restarts.)",
            stop_all=True,
        )
    if error.code == 404:
        return LookupProblem("RentCast couldn't find this address. Check that the address is complete and spelled right.")
    if error.code == 429:
        return LookupProblem("RentCast asked the app to slow down. The other homes wait an hour.", stop_round=True)
    if error.code >= 500:
        return LookupProblem(f"RentCast had a problem (error {error.code}). The other homes wait an hour.", stop_round=True)
    message = _message_in(error)
    return LookupProblem(f"RentCast couldn't look this address up (error {error.code}{': ' + message if message else ''}).")


def _message_in(error: urllib.error.HTTPError) -> str:
    """RentCast's own explanation of an error, if it gave one."""
    try:
        message = json.loads(error.read()).get("message", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return str(message)[:200]


@lru_cache(maxsize=1)
def _secure_context() -> ssl.SSLContext:
    """Checks that RentCast really is RentCast, using certifi's list of trusted certificates (as relay.py does)."""
    return ssl.create_default_context(cafile=certifi.where())


# ---------- For the screens ----------


def latest_lookup(a_property: Property) -> ValueLookup | None:
    """The home's most recent lookup, whether it worked or not."""
    return db_session.scalar(
        select(ValueLookup)
        .where(ValueLookup.property_id == a_property.id)
        .order_by(ValueLookup.asked_at.desc(), ValueLookup.id.desc())
        .limit(1)
    )


@dataclass
class OwnValue:
    """Her own value for a home, which is shown instead of RentCast's. It answers like a lookup does."""

    value_cents: int
    asked_at: datetime  # when she set it
    low_cents: None = None
    high_cents: None = None
    own: bool = True


def latest_values(a_property: Property, how_many: int = 1) -> list:
    """The home's value: her own if she set one (then only that, so there's no change to work out),
    or else its most recent lookups that gave a value, newest first."""
    if a_property.own_value_cents is not None:
        return [OwnValue(a_property.own_value_cents, a_property.own_value_set_at or datetime.now())]
    return latest_rentcast_values(a_property, how_many)


def latest_rentcast_values(a_property: Property, how_many: int = 1) -> list[ValueLookup]:
    """The home's most recent lookups that gave a value, newest first."""
    return list(
        db_session.scalars(
            select(ValueLookup)
            .where(ValueLookup.property_id == a_property.id, ValueLookup.value_cents.is_not(None))
            .order_by(ValueLookup.asked_at.desc(), ValueLookup.id.desc())
            .limit(how_many)
        )
    )


def latest_value(a_property: Property):
    """The value shown for the home (her own, or RentCast's latest), or None."""
    found = latest_values(a_property)
    return found[0] if found else None


def latest_rentcast_value(a_property: Property) -> ValueLookup | None:
    found = latest_rentcast_values(a_property)
    return found[0] if found else None


@dataclass
class Total:
    """What her homes are worth together, for the Home page."""

    cents: int  # the newest value of each home that has one, added up
    names: list[str]  # the homes that have a value
    newest_asked_at: datetime  # when the newest of those values was looked up
    # How much that changed since each home's lookup before (None until every home has had two).
    change_cents: int | None
    previous_asked_at: datetime | None  # the oldest of those earlier lookups
    own_count: int = 0  # how many of the values are her own


def total_value(properties: list[Property]) -> Total | None:
    """The total of the homes' newest values, or None if none has a value yet."""
    found = [(a_property.nickname, latest_values(a_property, how_many=2)) for a_property in properties]
    names = [name for name, pair in found if pair]
    pairs = [pair for _name, pair in found if pair]
    if not pairs:
        return None
    total = sum(pair[0].value_cents for pair in pairs)
    if all(len(pair) == 2 for pair in pairs):
        change = total - sum(pair[1].value_cents for pair in pairs)
        previous = min(pair[1].asked_at for pair in pairs)
    else:
        change, previous = None, None
    newest = max(pair[0].asked_at for pair in pairs)
    own = sum(1 for pair in pairs if getattr(pair[0], "own", False))
    return Total(cents=total, names=names, newest_asked_at=newest, change_cents=change, previous_asked_at=previous, own_count=own)
