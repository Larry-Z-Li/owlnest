"""The rules for a property's leases: one current lease at a time, extending, ending, and reviving.

* A property has at most one Active lease, its current lease. Ended leases are its
  previous leases.
* Each lease gets a tenant portal link when it's made (see views/portal.py). The
  link works only while the lease is Active, so ending a lease turns it off, and
  reviving a lease that was ended by mistake turns it back on.
* Extending a lease moves its end date later and keeps a record of the extension,
  where the signed extension agreement can be attached.
* A lease's rent is a list of amounts by date (RentPeriod). The first starts with the
  lease; each later one starts after that, by the end of the lease.
"""

import hmac
import secrets
from datetime import date

from sqlalchemy import select

from . import records
from .database import db_session
from .formatting import nice_date
from .models import Lease, LeaseExtension, Property, RentPeriod


def make_token() -> str:
    """A new random code for a portal link: 43 letters, numbers, - and _, impossible to guess."""
    return secrets.token_urlsafe(32)


def portal_link(public_address: str, lease: Lease) -> str:
    return f"{public_address}/portal/link/{lease.portal_token}"


def lease_for_token(token: str | None) -> Lease | None:
    """The lease whose portal link has this code (Active or not), or None."""
    if not token or len(token) > 100:
        return None
    for lease in db_session.scalars(select(Lease).where(Lease.archived_at.is_(None))):
        # compare_digest takes the same time however much of the code is right, so timing gives nothing away.
        if hmac.compare_digest(lease.portal_token.encode(), token.encode()):
            return lease
    return None


def start_problem(a_property: Property) -> str | None:
    """Why a new lease can't be added to this property yet, or None if it can."""
    current = records.current_lease(a_property)
    if current is not None:
        return (
            f"{a_property.nickname} already has a current lease ({current.tenant_names}). "
            "End that lease first; it then moves to the previous leases."
        )
    return None


def extend(lease: Lease, new_end_date: date, signed_on: date | None, notes: str) -> LeaseExtension:
    """Move the lease's end date to new_end_date, keeping a record of it. The caller commits."""
    extension = LeaseExtension(
        lease=lease, previous_end_date=lease.end_date, new_end_date=new_end_date, signed_on=signed_on, notes=notes
    )
    db_session.add(extension)
    lease.end_date = new_end_date
    return extension


def rent_date_problem(lease: Lease, starts_on: date, period: RentPeriod | None = None, last_day: date | None = None) -> str | None:
    """Why a later rent can't start on this date, or None if it can.

    period is the one being edited, if any. last_day is the lease's last day, when it's about to change.
    """
    if starts_on <= lease.start_date:
        return f"A rent change must start after the lease's start date, {nice_date(lease.start_date)}."
    last_day = last_day or lease.ended_on or lease.end_date
    if last_day and starts_on > last_day:
        return f"The lease ends on {nice_date(last_day)}. To change the rent after that, extend the lease."
    if any(other is not period and other.starts_on == starts_on for other in lease.rent_periods):
        return "There's already a rent starting on that date. Edit that one instead."
    return None


def start_date_problem(lease: Lease, start_date: date) -> str | None:
    """Why the lease's start date can't move here, or None if it can: it has to stay before its later rents."""
    for period in lease.rent_periods[1:]:
        if period.starts_on <= start_date:
            return (
                f"The rent changes on {nice_date(period.starts_on)}, so the lease has to start before that. "
                "Change that rent first."
            )
    return None


def add_rent(lease: Lease, starts_on: date, amount_cents: int) -> bool:
    """Add a rent from starts_on, unless that's already the rent by then. True if it was added. The caller commits.

    It keeps the late fee rule of the rent before it.
    """
    before = lease.rent_period_on(starts_on)
    if before.amount_cents == amount_cents:
        return False
    lease.rent_periods.append(
        RentPeriod(
            starts_on=starts_on,
            amount_cents=amount_cents,
            late_after_days=before.late_after_days,
            late_percent=before.late_percent,
        )
    )
    return True


def first_rent_check(start_date: date, today: date) -> date:
    """Where a new lease's rent starts being checked: from its start, but no earlier than this month.

    (A lease that began long ago may have months of payments that were never entered here.)
    """
    return max(start_date, today.replace(day=1))


def ends_early(lease: Lease, ended_on: date) -> bool:
    """True if ending the lease on ended_on is before its end date."""
    return lease.end_date is not None and ended_on < lease.end_date


def end(lease: Lease, ended_on: date) -> None:
    """End the lease: it moves to the property's previous leases, and its portal link stops working."""
    lease.status = "Ended"
    lease.ended_on = ended_on


def revive_problem(lease: Lease) -> str | None:
    """Why an ended lease can't be made current again, or None if it can."""
    if lease.property.archived_at is not None:
        return f"{lease.property.nickname} is in the Archive. Restore it first."
    current = records.current_lease(lease.property)
    if current is not None and current.id != lease.id:
        return (
            f"{lease.property.nickname} already has a current lease ({current.tenant_names}). "
            "A property can only have one current lease."
        )
    return None


def revive(lease: Lease) -> None:
    """Make an ended lease current again, as if it had never ended. Its portal link works again."""
    lease.status = "Active"
    lease.ended_on = None
