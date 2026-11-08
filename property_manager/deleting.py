"""Deleting records forever, from the Archive page.

Everything first goes to the Archive (see views/archive.py); only archived records
can be deleted forever. The exception is a transaction from the bank: it can't be
archived, and is deleted from its own page (see views/transactions.py), which lets
its entry be entered again. Deleting a record also deletes what belongs to it, such as
a lease's walkthroughs and extensions and the files attached to them. Records that
only point to it are kept and just lose the link: a request keeps its property but
forgets the lease, and a transaction keeps its amount but forgets the request.
Deleting a property also deletes its own leases, insurances, reminders and photos
(listed only on its page, which an archived property doesn't show); it can't be
deleted while it has maintenance requests or transactions. Its value lookups are
kept, unlinked, because they still count toward RentCast's monthly limit (see
home_values.py).

The attached files themselves stay in the data folder (the app never deletes files
there, except old backups), and so does every backup made before.
"""

from sqlalchemy import func, select

from .database import db_session
from .records import join_with_and
from .models import BankEntry, Insurance, Lease, MaintenanceRequest, Property, Reminder, Transaction, ValueLookup


def delete_forever(record) -> str | None:
    """Delete a record, and what belongs to it, forever. The caller commits.

    Returns a reason it can't be deleted (nothing is changed then), or None.
    """
    if isinstance(record, Property):
        problem = _property_still_used(record)
        if problem:
            return problem
        for lease in list(record.leases):
            delete_forever(lease)
        for insurance in list(record.insurances):
            _delete_with_files(insurance)
        for reminder in db_session.scalars(select(Reminder).where(Reminder.property_id == record.id)):
            db_session.delete(reminder)
        # Its value lookups are kept (just unlinked): they still count toward RentCast's
        # monthly limit (see home_values.py).
        for lookup in db_session.scalars(select(ValueLookup).where(ValueLookup.property_id == record.id)):
            lookup.property = None
    elif isinstance(record, Lease):
        for maintenance_request in db_session.scalars(
            select(MaintenanceRequest).where(MaintenanceRequest.lease_id == record.id)
        ):
            maintenance_request.lease = None
        for walkthrough in record.walkthroughs:
            _delete_with_files(walkthrough)
        for rent_period in record.rent_periods:
            db_session.delete(rent_period)
        for waiver in record.rent_waivers:
            db_session.delete(waiver)
        for extension in record.extensions:
            _delete_with_files(extension)
    elif isinstance(record, MaintenanceRequest):
        for transaction in record.transactions:
            transaction.request = None
    elif isinstance(record, Transaction):
        # The money did move, so its entry from the bank can be entered again. (One linked to it goes back to what it was.)
        for entry in db_session.scalars(select(BankEntry).where(BankEntry.transaction_id == record.id)):
            entry.transaction = None
            entry.status = (entry.linked_from or "New") if entry.status == "Linked" else "New"
            entry.never_link = entry.never_link or bool(entry.linked_from)
            entry.linked_from = ""
    _delete_with_files(record)
    return None


def _delete_with_files(record) -> None:
    """Delete a record and the files attached to it (an attached file itself has none)."""
    for attachment in getattr(record, "attachments", []):
        db_session.delete(attachment)
    db_session.delete(record)


def _property_still_used(a_property: Property) -> str | None:
    """A property can't be deleted forever while it has maintenance requests or transactions
    (they're on the Archive page, where she can delete them forever first)."""
    counts = []
    for model, singular, plural in (
        (MaintenanceRequest, "request", "requests"),
        (Transaction, "transaction", "transactions"),
    ):
        number = db_session.scalar(select(func.count()).select_from(model).where(model.property_id == a_property.id))
        if number:
            counts.append(f"{number} {singular if number == 1 else plural}")
    if not counts:
        return None
    return (
        f"{a_property.nickname} still has {join_with_and(counts)}. To delete it forever, delete those forever first; "
        "or keep it in the Archive, where it does no harm."
    )
