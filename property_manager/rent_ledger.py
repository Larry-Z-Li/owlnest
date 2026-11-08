"""Whether each month's rent has been paid, and the late fee if it wasn't paid on time.

None of this is saved. It's worked out from the lease's rent list and the property's
transactions each time it's needed, so a payment typed in late (or with its date
fixed afterwards) gives the right answer by itself: what counts is the transaction's
date, not the day it was typed in.

* Each month's rent is due on the day of the month the lease started (in a shorter month,
  its last day). The amount and the late fee rule are the ones in the rent list on that date.
* Payments are the property's Rental Income and Late Fees transactions. They go to
  the oldest rent still unpaid, so one payment can cover two months, or pay ahead.
* All rent comes before any late fee, so an unpaid late fee can't make a later month late.
* The late fee is late_percent of the month's whole rent, added once if the rent
  wasn't paid in full by late_after_days days after its due date. With 5% after
  5 days on $3,000: 4 days late is $0, and 5 days or more is $150.
* A month keeps its reminder until its rent and late fee are paid, or she waives them.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select

from . import categories
from .database import db_session
from .models import Lease, RentPeriod, RentWaiver, Transaction


@dataclass
class RentMonth:
    month: date  # its first day
    due_on: date
    rent_cents: int  # the month's whole rent
    rule: RentPeriod  # the rent list's row it comes from, with the late fee rule
    forgiven_cents: int = 0  # rent she waived
    fee_waived: bool = False
    waived: bool = False  # she waived something this month, so it can be undone
    rent_paid_cents: int = 0
    paid_on: date | None = None  # the day the rent was paid in full
    days_late: int = 0  # until the rent was paid in full, or until today
    fee_cents: int = 0
    fee_paid_cents: int = 0

    @property
    def name(self) -> str:
        return f"{self.month:%B %Y}"

    @property
    def past_grace(self) -> bool:
        """True from the day its late fee is added. Until then, a reminder about it is yellow, and from then on, red."""
        return self.days_late >= self.rule.late_after_days

    @property
    def rent_owed_cents(self) -> int:
        return self.rent_cents - self.forgiven_cents - self.rent_paid_cents

    @property
    def fee_owed_cents(self) -> int:
        return self.fee_cents - self.fee_paid_cents

    @property
    def owed_cents(self) -> int:
        return self.rent_owed_cents + self.fee_owed_cents

    @property
    def possible_fee_cents(self) -> int:
        """The late fee this month has if it's paid late enough."""
        return round(self.rent_cents * self.rule.late_percent / 100)

    @property
    def fee_comes_on(self) -> date | None:
        """The day the late fee is added, if that's still to come."""
        if self.fee_cents or self.fee_waived or not self.possible_fee_cents or not self.rent_owed_cents:
            return None
        return self.due_on + timedelta(days=self.rule.late_after_days)

    @property
    def status(self) -> tuple[str, str]:
        """Its words and color, for the lease's list of payments."""
        if self.rent_owed_cents:
            return ("Partly paid", "tag-warning") if self.rent_paid_cents else ("Not paid", "tag-late")
        if self.fee_owed_cents:
            return "Late fee owed", "tag-warning"
        if self.forgiven_cents:
            return "Waived", "tag-scheduled"
        return "Paid", "tag-active"


def due_date(month: date, due_day: int) -> date:
    """The rent's due date in a month: its due day, or the month's last day if it's shorter."""
    return month.replace(day=min(due_day, calendar.monthrange(month.year, month.month)[1]))


def work_out(lease: Lease, today: date) -> tuple[list[RentMonth], int]:
    """Every month's rent so far, oldest first, and the money left over that pays ahead."""
    # An ended lease stops: no more rent comes due, and no more late fees start.
    last_day = min(today, lease.ended_on) if lease.ended_on else today
    waivers = {waiver.month: waiver for waiver in lease.rent_waivers}
    months = [_month(lease, month, due_on, waivers.get(month)) for month, due_on in _due_dates(lease, last_day)]

    # First all the rent, oldest first. Each payment is [its date, what's left of it].
    payments = [[payment.transaction_date, payment.amount_cents] for payment in _payments(lease, today)]
    for month in months:
        while month.rent_owed_cents and payments:
            payment = payments[0]
            used = min(payment[1], month.rent_owed_cents)
            payment[1] -= used
            month.rent_paid_cents += used
            if not month.rent_owed_cents:
                month.paid_on = payment[0]
            if not payment[1]:
                payments.pop(0)
        if month.paid_on or month.rent_owed_cents:
            until = month.paid_on or last_day
            month.days_late = max((until - month.due_on).days, 0)
        if not month.fee_waived and month.days_late >= month.rule.late_after_days:
            month.fee_cents = month.possible_fee_cents

    # What's left pays the late fees, and whatever's left after that pays ahead.
    left_over = sum(amount for _day, amount in payments)
    for month in months:
        month.fee_paid_cents = min(month.fee_cents, left_over)
        left_over -= month.fee_paid_cents
    return months, left_over


def owing(lease: Lease, today: date) -> list[RentMonth]:
    """The months with rent or a late fee still owed."""
    return [month for month in work_out(lease, today)[0] if month.owed_cents]


def waive_fee(lease: Lease, month: RentMonth) -> None:
    """Let go of this month's late fee. The caller commits."""
    _waiver_for(lease, month)


def waive_rest(lease: Lease, month: RentMonth) -> None:
    """Let go of this month's late fee and the rest of its rent. The caller commits."""
    _waiver_for(lease, month).rent_cents += month.rent_owed_cents


def undo_waiver(lease: Lease, month: date) -> None:
    """Take back whatever she waived for this month. The caller commits."""
    for waiver in list(lease.rent_waivers):
        if waiver.month == month:
            lease.rent_waivers.remove(waiver)
            db_session.delete(waiver)


def _waiver_for(lease: Lease, month: RentMonth) -> RentWaiver:
    for waiver in lease.rent_waivers:
        if waiver.month == month.month:
            return waiver
    waiver = RentWaiver(month=month.month, rent_cents=0)
    lease.rent_waivers.append(waiver)
    return waiver


def _month(lease: Lease, month: date, due_on: date, waiver: RentWaiver | None) -> RentMonth:
    rule = lease.rent_period_on(due_on)
    return RentMonth(
        month=month,
        due_on=due_on,
        rent_cents=rule.amount_cents,
        rule=rule,
        forgiven_cents=waiver.rent_cents if waiver else 0,
        fee_waived=waiver is not None,
        waived=waiver is not None,
    )


def _checked_from(lease: Lease) -> date:
    return lease.rent_checked_from or lease.start_date


def _due_dates(lease: Lease, last_day: date):
    """Each month with rent due, and its due date, from where checking starts through last_day."""
    first_day = max(lease.start_date, _checked_from(lease))
    month = first_day.replace(day=1)
    while due_date(month, lease.rent_due_day) <= last_day:
        if due_date(month, lease.rent_due_day) >= first_day:
            yield month, due_date(month, lease.rent_due_day)
        month = (month + timedelta(days=32)).replace(day=1)


def _payments(lease: Lease, today: date) -> list[Transaction]:
    """The lease's payments so far, oldest first."""
    query = select(Transaction).where(
        Transaction.archived_at.is_(None),
        Transaction.property_id == lease.property_id,
        Transaction.category.in_((categories.RENT, categories.LATE_FEES)),
        Transaction.amount_cents > 0,
        Transaction.transaction_date <= today,
    )
    query = query.order_by(Transaction.transaction_date, Transaction.id)
    # Money from the property's next lease is that lease's.
    later_starts = [
        other.start_date
        for other in lease.property.leases
        if other.archived_at is None and other.start_date > lease.start_date
    ]
    next_lease_starts = min(later_starts, default=None)
    checked_from = _checked_from(lease)

    def counts(payment: Transaction) -> bool:
        day = payment.transaction_date
        if next_lease_starts and day >= next_lease_starts:
            return False
        if day >= checked_from:
            return True
        # (Rent paid before the lease began, such as when signing it, counts if it names these tenants.)
        return checked_from <= lease.start_date and payment.tenant_name == lease.tenant_names

    return [payment for payment in db_session.scalars(query) if counts(payment)]
