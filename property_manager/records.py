"""Rules about records that several screens share."""

from datetime import date, datetime

from sqlalchemy import func, or_, select

from .database import db_session
from .models import Insurance, Lease, Llc, MaintenanceRequest, Property, Reminder, Transaction


def name_key(text: str) -> str:
    """A version of a name for comparing: "  Elm   ST " and "elm st" are the same name."""
    return " ".join((text or "").split()).casefold()


def search_filter(search: str, *columns):
    """A database condition that matches records where any of the columns contains the search text."""
    return or_(*(column.icontains(search, autoescape=True) for column in columns))


def matches_search(search: str, *texts: str) -> bool:
    """True if the search text appears in any of the texts (ignoring upper/lower case)."""
    wanted = name_key(search)
    return any(wanted in name_key(text) for text in texts if text)


def name_in_use(model, attribute: str, name: str, except_id: int | None = None) -> str | None:
    """Check whether another record already uses this name (ignoring upper/lower case).

    Returns None if the name is free, "active" if a normal record uses it, or
    "archived" if only an archived record uses it. Names must be unique because
    the drop-down lists and the Excel import find records by name.
    """
    key = name_key(name)
    found = None
    for record in db_session.scalars(select(model)):
        if record.id != except_id and name_key(getattr(record, attribute)) == key:
            if record.archived_at is None:
                return "active"
            found = "archived"
    return found


def name_problem(in_use: str | None, kind: str, name: str) -> str | None:
    """A plain-words message for a name that's already used, or None if it's free."""
    if in_use == "active":
        return f'There is already {kind} named "{name}". Please use a different name.'
    if in_use == "archived":
        return (
            f'{kind[0].upper()}{kind[1:]} named "{name}" is in the Archive. '
            "Restore it from the Archive page, or use a different name."
        )
    return None


# ---------- Properties and leases ----------


def active_properties() -> list[Property]:
    """All properties that aren't archived, sorted by nickname."""
    query = select(Property).where(Property.archived_at.is_(None)).order_by(func.lower(Property.nickname))
    return list(db_session.scalars(query))


# What the homes that no LLC owns are listed under.
NOT_IN_AN_LLC = "Not in an LLC"


def active_llcs() -> list[Llc]:
    """All LLCs, sorted by name."""
    query = select(Llc).where(Llc.archived_at.is_(None)).order_by(func.lower(Llc.name))
    return list(db_session.scalars(query))


def in_llc(llc_id: int):
    """For a query of transactions: an LLC's money, both for its homes and for the LLC itself."""
    return or_(Transaction.llc_id == llc_id, Transaction.property_id.in_(select(Property.id).where(Property.llc_id == llc_id)))


def by_llc(properties: list[Property]) -> list[tuple[Llc | None, list[Property]]]:
    """Each LLC with its homes (some may have none), then None with the homes in no LLC."""
    llcs = active_llcs()
    known = {llc.id for llc in llcs}
    groups = [(llc, [one for one in properties if one.llc_id == llc.id]) for llc in llcs]
    return groups + [(None, [one for one in properties if one.llc_id not in known])]


def property_choices(properties: list[Property] | None = None) -> list[tuple[str, list[tuple[int, str]]]]:
    """A menu of properties, grouped by LLC: [(the group's heading, [(id, name), ...]), ...].

    With no LLCs in use, it's one group with no heading, so the menu is just the list.
    """
    groups = by_llc(active_properties() if properties is None else properties)
    choices = [
        (llc.name if llc else NOT_IN_AN_LLC, [(one.id, f"{one.nickname} (archived)" if one.archived_at else one.nickname) for one in homes])
        for llc, homes in groups
        if homes
    ]
    return [("", choices[0][1])] if len(choices) == 1 and choices[0][0] == NOT_IN_AN_LLC else choices


def current_lease(a_property: Property) -> Lease | None:
    """The property's Active lease, if it has one."""
    for lease in a_property.leases:
        if lease.status == "Active" and lease.archived_at is None:
            return lease
    return None


def previous_leases(a_property: Property) -> list[Lease]:
    """The property's ended leases, the most recent first."""
    ended = [lease for lease in a_property.leases if lease.status == "Ended" and lease.archived_at is None]
    return sorted(ended, key=lambda lease: (lease.ended_on or lease.start_date, lease.id), reverse=True)


def is_past_end_date(lease: Lease, today: date | None = None) -> bool:
    """True for an Active lease whose end date has passed (she may want to end or extend it)."""
    today = today or date.today()
    return lease.status == "Active" and lease.end_date is not None and lease.end_date < today


def lease_name(lease: Lease) -> str:
    """ "Elm St (Jane Doe)" """
    return f"{lease.property.nickname} ({lease.tenant_names})"


def lease_on(a_property: Property, day: date) -> Lease | None:
    """The property's lease that covered this day (its start to its end, or to today if it's current)."""
    for lease in a_property.leases:
        if lease.archived_at is not None or lease.start_date > day:
            continue
        last_day = lease.ended_on if lease.status == "Ended" else None
        if last_day is None or day <= last_day:
            return lease
    return None


# What goes to the Archive along with a property, and comes back with it: everything
# that belongs to it but its transactions, since the money still came in or went out,
# even after the house is sold.
PROPERTY_BELONGINGS = (
    (Lease, "lease", "leases"),
    (Insurance, "insurance", "insurances"),
    (MaintenanceRequest, "request", "requests"),
    (Reminder, "reminder", "reminders"),
)


def property_belongings(a_property: Property, archived_at: datetime | None) -> list:
    """The property's leases, insurances, requests and reminders with this archived_at.

    None gives the ones not in the Archive. The property's own archived_at gives the
    ones archived along with it, since they get the very same date and time.
    """
    found = []
    for model, _singular, _plural in PROPERTY_BELONGINGS:
        # (Comparing with None finds the empty ones: SQL's "IS NULL".)
        query = select(model).where(model.property_id == a_property.id, model.archived_at == archived_at)
        found += db_session.scalars(query)
    return found


def with_belongings(name: str, belongings: list) -> str:
    """ "Elm St (with its 1 lease and 2 requests)", for the Archive's messages. """
    counts = []
    for model, singular, plural in PROPERTY_BELONGINGS:
        number = sum(isinstance(record, model) for record in belongings)
        if number:
            counts.append(f"{number} {singular if number == 1 else plural}")
    return f"{name} (with its {join_with_and(counts)})" if counts else name


def join_with_and(parts: list[str]) -> str:
    """ "a, b and c" """
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def home_facts(a_property: Property) -> str:
    """ "3 beds · 2.5 baths · 1,916 sq ft", leaving out what isn't known ("" if none is)."""
    facts = []
    if a_property.bedrooms is not None:
        facts.append(f"{a_property.bedrooms} {'bed' if a_property.bedrooms == 1 else 'beds'}")
    if a_property.bathrooms is not None:
        facts.append(f"{a_property.bathrooms:g} {'bath' if a_property.bathrooms == 1 else 'baths'}")
    if a_property.square_feet is not None:
        facts.append(f"{a_property.square_feet:,} sq ft")
    return " · ".join(facts)


def home_line(a_property: Property) -> str:
    """The line under a home's name in the Properties list, without repeating its street:
    "Springfield, IL 62701 · 3 bd · 2.5 ba"."""
    address = ", ".join(line.strip() for line in a_property.address.splitlines() if line.strip())
    place = address.split(",", 1)[1].strip() if "," in address else address
    parts = [place] if place and place != a_property.nickname else []
    if a_property.bedrooms is not None:
        parts.append(f"{a_property.bedrooms} bd")
    if a_property.bathrooms is not None:
        parts.append(f"{a_property.bathrooms:g} ba")
    return " · ".join(parts)


# ---------- Maintenance requests ----------


def request_name(maintenance_request: MaintenanceRequest) -> str:
    """ "Elm St: Kitchen sink is leaking" """
    return f"{maintenance_request.property.nickname}: {maintenance_request.title}"


def transaction_restore_warning(transaction) -> str:
    """What to ask before restoring a transaction whose request is in the Archive ("" if it isn't)."""
    linked = transaction.request
    if linked is None or linked.archived_at is None:
        return ""
    return (
        f"This transaction is linked to the request “{request_name(linked)}”, which is in the Archive. "
        "If you restore the transaction now, it won't be linked to that request any more. "
        "To keep the link, click Cancel and restore the request first."
    )


def follow_request(maintenance_request: MaintenanceRequest) -> None:
    """Give the request's transactions its type and property, as the client asked."""
    for transaction in maintenance_request.transactions:
        transaction.category = maintenance_request.category
        transaction.property = maintenance_request.property


def request_cost(maintenance_request: MaintenanceRequest) -> int:
    """What a request has cost so far: its transactions' money out, as a positive number of cents."""
    return -sum(
        transaction.amount_cents
        for transaction in maintenance_request.transactions
        if transaction.archived_at is None and transaction.amount_cents < 0
    )
