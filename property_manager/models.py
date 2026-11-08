"""The database tables.

Each class below is one table in app.db. A few rules apply to all of them:

* Money is stored as whole cents, in columns whose names end in "_cents"
  ($1,250.00 is stored as 125000). See money.py for why.
* Dates are stored as YYYY-MM-DD.
* Nothing is ever deleted from the screens. "Deleting" a record fills in its
  archived_at date, which hides it; the Archive page can clear it again.

If you change these tables, also raise SCHEMA_VERSION in database.py and add an
upgrade step there, so existing databases are upgraded when the app starts.
"""

from datetime import date, datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# A lease is Active (the property's current lease) or Ended (one of its previous leases).
LEASE_STATUSES = ("Active", "Ended")
# The two walkthrough reports of a lease: before the tenants moved in, and after they moved out.
WALKTHROUGH_KINDS = ("Move-in", "Move-out")
# Where a maintenance request stands. The tenant sees it in the tenant portal too.
REQUEST_STATUSES = ("Open", "In progress", "Closed")
# Suggestions for the kind of insurance (she can type her own).
INSURANCE_KINDS = ("Property", "Renters", "Liability", "Flood", "Earthquake", "Umbrella")
# How often a reminder comes back after she marks it done.
REMINDER_REPEATS = ("Once", "Every month", "Every year")
# Suggestions for the kind of reminder (she can type her own).
REMINDER_KINDS = (
    "Property tax due",
    "HOA dues",
    "License or permit renewal",
    "Inspection",
    "Smoke detector check",
    "Furnace or AC service",
    "Other",
)


class Base(DeclarativeBase):
    """The parent class of every table."""


class RecordMixin:
    """Columns that every kind of record has."""

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)
    # Empty for normal records. Set to the date and time a record was "deleted" (archived).
    archived_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Settings(Base):
    """Her own details and app options. This table always has exactly one row, with id 1."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Shown to tenants in the tenant portal.
    business_name: Mapped[str] = mapped_column(default="")
    mailing_address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(default="")
    # Where every backup is also copied when the app runs on one computer (run.py --local).
    # Empty means the standard folder in Documents. The server doesn't use it.
    backup_folder: Mapped[str] = mapped_column(default="")


class Llc(RecordMixin, Base):
    """A company that owns some of her homes. It's just a name; each home says which one, if any."""

    __tablename__ = "llcs"

    name: Mapped[str]


class Property(RecordMixin, Base):
    """A house she rents out. It has at most one Active lease at a time."""

    __tablename__ = "properties"

    nickname: Mapped[str]  # what she calls it, such as "Elm St"
    address: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Look up its estimated value from RentCast each month (see home_values.py). Off unless she ticks it.
    look_up_value: Mapped[bool] = mapped_column(default=False)
    # Her own estimated value, and when she set it (Larry, 2026-11-05: "the custom value would override").
    # While it's set, it's shown instead of RentCast's, even if lookups go on. Emptying it shows RentCast's again.
    own_value_cents: Mapped[int | None] = mapped_column(default=None)
    own_value_set_at: Mapped[datetime | None] = mapped_column(default=None)
    # The home itself. Empty until she fills them in, or a RentCast lookup does (see home_values.py).
    bedrooms: Mapped[int | None] = mapped_column(default=None)
    bathrooms: Mapped[float | None] = mapped_column(default=None)  # such as 2.5
    square_feet: Mapped[int | None] = mapped_column(default=None)
    # The LLC that owns it, if one does.
    llc_id: Mapped[int | None] = mapped_column(ForeignKey("llcs.id"), default=None)
    llc: Mapped[Llc | None] = relationship()

    # Photos of the home, in the order they were added (the slideshow on its page).
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="property", order_by="Attachment.id")
    leases: Mapped[list["Lease"]] = relationship(back_populates="property", order_by="Lease.start_date.desc()")
    insurances: Mapped[list["Insurance"]] = relationship(back_populates="property", order_by="Insurance.end_date")


# Where an entry from the bank is (see BankEntry): not in her books yet, entered into Transactions, or
# overridden: checked, such as the rows Larry marked TRUE in her bank statements (2026-11-05). It shows
# a check mark, and can still be entered into Transactions if she wants (it isn't counted as waiting).
# Linked: matched by AI (Claude, at Larry's asking) to a transaction she had typed in herself.
# She can unlink it, and so can archiving that transaction; then it's never linked again.
ENTRY_STATUSES = ("New", "Entered", "Overridden", "Linked")

# A new rent's late fee, unless she changes it: 5% of the rent, once it's 5 days late.
DEFAULT_LATE_AFTER_DAYS = 5
DEFAULT_LATE_PERCENT = 5.0


class LeaseRent:
    """What a lease's rent is on any day.

    (On its own, because inside the Lease class "property" means the lease's home, not Python's @property.)
    """

    @property
    def rent_due_day(self) -> int:
        """The day of the month the rent is due: the day the lease started (in a shorter month, its last day)."""
        return self.start_date.day

    def rent_period_on(self, day: date) -> "RentPeriod":
        """The rent period in effect on a day: the latest that has started by then (before any has, the first)."""
        started = [period for period in self.rent_periods if period.starts_on <= day]
        if started:
            return max(started, key=lambda period: period.starts_on)
        return min(self.rent_periods, key=lambda period: period.starts_on)

    @property
    def current_rent_period(self) -> "RentPeriod":
        """The rent period in effect today. A lease that has ended stays at its last one."""
        today = date.today()
        return self.rent_period_on(min(today, self.ended_on) if self.ended_on else today)

    @property
    def rent_cents(self) -> int:
        """The monthly rent in effect today."""
        return self.current_rent_period.amount_cents

    @property
    def next_rent_period(self) -> "RentPeriod | None":
        """The next rent change still to come, if there is one."""
        if self.ended_on:
            return None
        today = date.today()
        coming = [period for period in self.rent_periods if period.starts_on > today]
        return min(coming, key=lambda period: period.starts_on) if coming else None


class Lease(LeaseRent, RecordMixin, Base):
    """An agreement to rent a property to its tenants.

    A property has at most one Active lease: its current lease. Ended leases are its
    previous leases. Ending one by mistake can be undone ("Revive").
    """

    __tablename__ = "leases"

    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    # The tenants' names, such as "Jane Doe and John Doe", and how to reach them.
    tenant_names: Mapped[str]
    tenant_phone: Mapped[str] = mapped_column(default="")
    tenant_email: Mapped[str] = mapped_column(default="")
    start_date: Mapped[date]
    # Empty for a month-to-month lease. "Extend lease" moves it later (see LeaseExtension).
    end_date: Mapped[date | None] = mapped_column(default=None)
    # No longer used: rent is due on the day of the month the lease started (see rent_due_day above), since Nov 2026.
    # Kept so older databases don't need changing.
    old_rent_due_day: Mapped[int] = mapped_column("rent_due_day", default=1)
    # Rent is checked from this date on (see rent_ledger.py), so a lease that began before its
    # payments were kept here doesn't show months of "unpaid" rent. It's set when the lease is added
    # (see leasing.first_rent_check), and no page changes it. Empty: from the lease's start.
    rent_checked_from: Mapped[date | None] = mapped_column(default=None)
    deposit_cents: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="Active")  # one of LEASE_STATUSES
    # The day the tenants left, filled in when she ends the lease.
    ended_on: Mapped[date | None] = mapped_column(default=None)
    # The secret part of the tenants' link to the tenant portal, made with the lease.
    # The link works only while the lease is Active.
    portal_token: Mapped[str]
    notes: Mapped[str] = mapped_column(Text, default="")
    # The end date whose "lease ending soon" reminder she dismissed. If the end date
    # changes (for example, the lease is extended), the reminder comes back.
    # No longer used: reminders can't be dismissed any more, only taken care of (Nov 2026).
    # Kept so older databases don't need changing.
    end_alert_dismissed_for: Mapped[date | None] = mapped_column(default=None)

    property: Mapped[Property] = relationship(back_populates="leases")
    walkthroughs: Mapped[list["Walkthrough"]] = relationship(back_populates="lease", order_by="Walkthrough.id")
    extensions: Mapped[list["LeaseExtension"]] = relationship(back_populates="lease", order_by="LeaseExtension.id")
    # What the rent is from each date on (at least one: the rent the lease started with).
    rent_periods: Mapped[list["RentPeriod"]] = relationship(back_populates="lease", order_by="RentPeriod.starts_on")
    rent_waivers: Mapped[list["RentWaiver"]] = relationship(back_populates="lease", order_by="RentWaiver.month")
    # Papers about the lease itself, such as the signed lease.
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="lease", order_by="Attachment.id")


class RentPeriod(RecordMixin, Base):
    """One stretch of a lease's rent: amount_cents a month, from starts_on until the next one starts.

    A lease's first one starts on the lease's start date, and the later ones after it. Nothing
    has to happen on the day the rent changes: which one applies is worked out from today's date
    each time it's needed (see Lease.rent_cents).
    """

    __tablename__ = "rent_periods"

    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    starts_on: Mapped[date]
    amount_cents: Mapped[int]
    # The late fee: late_percent of the month's rent, added once when it's late_after_days days late.
    late_after_days: Mapped[int] = mapped_column(default=DEFAULT_LATE_AFTER_DAYS)
    late_percent: Mapped[float] = mapped_column(default=DEFAULT_LATE_PERCENT)

    lease: Mapped[Lease] = relationship(back_populates="rent_periods")


class RentWaiver(RecordMixin, Base):
    """She waived a month's late fee on the lease's page, and maybe the rest of its rent too."""

    __tablename__ = "rent_waivers"

    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    month: Mapped[date]  # the first day of the month
    rent_cents: Mapped[int] = mapped_column(default=0)  # how much of the rent she let go

    lease: Mapped[Lease] = relationship(back_populates="rent_waivers")


class Note(RecordMixin, Base):
    """Some text she wants to keep (the Notes page)."""

    __tablename__ = "notes"

    text: Mapped[str] = mapped_column(Text)


class Walkthrough(RecordMixin, Base):
    """A walkthrough report: the home's condition when the tenants moved in, or after they moved out."""

    __tablename__ = "walkthroughs"

    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    kind: Mapped[str]  # one of WALKTHROUGH_KINDS
    walk_date: Mapped[date]
    notes: Mapped[str] = mapped_column(Text, default="")

    lease: Mapped[Lease] = relationship(back_populates="walkthroughs")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="walkthrough", order_by="Attachment.id")


class LeaseExtension(RecordMixin, Base):
    """The lease was extended: its end date moved from previous_end_date to new_end_date."""

    __tablename__ = "lease_extensions"

    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    previous_end_date: Mapped[date | None] = mapped_column(default=None)  # empty: it was month-to-month
    new_end_date: Mapped[date]
    signed_on: Mapped[date | None] = mapped_column(default=None)
    notes: Mapped[str] = mapped_column(Text, default="")

    lease: Mapped[Lease] = relationship(back_populates="extensions")
    # The signed extension agreement, and anything else about it.
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="extension", order_by="Attachment.id")


class Insurance(RecordMixin, Base):
    """An insurance policy on a property. It's only a record and a reminder: it isn't linked to any money."""

    __tablename__ = "insurances"

    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    kind: Mapped[str]  # such as "Property" or "Renters" (see INSURANCE_KINDS)
    company: Mapped[str] = mapped_column(default="")
    policy_number: Mapped[str] = mapped_column(default="")
    start_date: Mapped[date]
    end_date: Mapped[date]
    notes: Mapped[str] = mapped_column(Text, default="")
    # The end date whose "insurance ending soon" reminder she dismissed (see Lease).
    # No longer used: reminders can't be dismissed any more, only taken care of (Nov 2026).
    # Kept so older databases don't need changing.
    end_alert_dismissed_for: Mapped[date | None] = mapped_column(default=None)

    property: Mapped[Property] = relationship(back_populates="insurances")
    # The policy, its declarations page, and anything else.
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="insurance", order_by="Attachment.id")


class MaintenanceRequest(RecordMixin, Base):
    """Something at a property that needs fixing or doing. (Added in schema version 9.)

    She adds them on the Maintenance screen, and tenants send them from the tenant
    portal. Transactions can be linked to a request (several to one request), and
    then take on its type.
    """

    __tablename__ = "maintenance_requests"

    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    # The lease whose tenants sent it from the tenant portal (empty if she added it herself).
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), default=None)
    title: Mapped[str]  # such as "Kitchen sink is leaking"
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str]  # its type: one of the names in categories.py
    company: Mapped[str] = mapped_column(default="")  # the company fixing it
    status: Mapped[str] = mapped_column(default="Open")  # one of REQUEST_STATUSES
    reported_on: Mapped[date]
    from_tenant: Mapped[bool] = mapped_column(default=False)
    # True for a request from the tenant portal she hasn't opened yet.
    unread: Mapped[bool] = mapped_column(default=False)

    property: Mapped[Property] = relationship()
    lease: Mapped[Lease | None] = relationship()
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="request", order_by="Transaction.transaction_date"
    )
    # Pictures, receipts, quotes and anything else.
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="request", order_by="Attachment.id")


class Transaction(RecordMixin, Base):
    """Money in or out: rent, an expense, insurance, a loan payment, anything. (Added in schema version 9.)"""

    __tablename__ = "transactions"

    transaction_date: Mapped[date]
    # Empty for money that isn't about one property (such as office supplies).
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), default=None)
    tenant_name: Mapped[str] = mapped_column(default="")
    category: Mapped[str]  # what it's for: one of the names in categories.py
    # Positive for money in, negative for money out.
    amount_cents: Mapped[int]
    details: Mapped[str] = mapped_column(Text, default="")
    # The maintenance request it's for, if any. It then has the request's type and property.
    request_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_requests.id"), default=None)

    property: Mapped[Property | None] = relationship()
    # The LLC it's for, when it's about the LLC itself and not one property. (With a property,
    # it's that property's LLC, so moving the home moves its money: see cash_flow.llc_id_of.)
    llc_id: Mapped[int | None] = mapped_column(ForeignKey("llcs.id"), default=None)
    llc: Mapped[Llc | None] = relationship()
    request: Mapped[MaintenanceRequest | None] = relationship(back_populates="transactions")
    # Receipts and anything else.
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="transaction", order_by="Attachment.id")


class BankEntry(RecordMixin, Base):
    """Money in or out of her bank account, from Plaid (see bank_link.py). Like a transaction she can't edit.

    It stays out of her books until she enters it into Transactions from the "From the bank" page,
    where it's still listed afterwards.
    """

    __tablename__ = "bank_entries"

    # Which one it is at the bank: the bank's id for it. Never saved twice.
    bank_id: Mapped[str] = mapped_column(unique=True)
    # Which connection brought it in: Plaid's id for it (see bank_link._key). Empty for entries saved
    # before this was kept (2026-11-05), which all came from the first 4 connections.
    item_id: Mapped[str] = mapped_column(default="")
    account_name: Mapped[str] = mapped_column(default="")
    day: Mapped[date]
    # Positive for money in, negative for money out, as in a transaction.
    amount_cents: Mapped[int]
    # The bank's own words, kept as they came. bank_link.read_words() finds the method, payer and memo in them.
    description: Mapped[str] = mapped_column(Text, default="")
    payee: Mapped[str] = mapped_column(default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(default="New")  # one of ENTRY_STATUSES
    # The transaction it became (Entered), or was linked to (Linked). Returning that one to From the
    # bank empties this, and the entry can be entered again. Several entries can link to one
    # transaction, such as rent paid in two halves.
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), default=None)
    # What it was before it was linked (New or Overridden), which unlinking brings back.
    linked_from: Mapped[str] = mapped_column(default="")
    # Unlinked once (by her, or by archiving its transaction), so it's never linked again.
    never_link: Mapped[bool] = mapped_column(default=False)

    transaction: Mapped[Transaction | None] = relationship()


class BankCheck(RecordMixin, Base):
    """One time the app asked Plaid for new activity. Saved just before asking, and counted to stay within its limits."""

    __tablename__ = "bank_checks"

    asked_at: Mapped[datetime]
    # Which connection it was for: Plaid's id for it, or empty for the first one ever (see bank_link._read_link).
    item_id: Mapped[str] = mapped_column(default="")
    # What went wrong, in plain words. Empty if it worked.
    problem: Mapped[str] = mapped_column(Text, default="")
    new_entries: Mapped[int] = mapped_column(default=0)
    # Plaid's bookmark for what's been read so far. Empty until its first pull is ready.
    cursor: Mapped[str] = mapped_column(Text, default="")


class Attachment(RecordMixin, Base):
    """A file (a PDF or a photo) attached to a record: a lease, walkthrough, extension, insurance,
    maintenance request or transaction, or a photo of a property (shown on its page).

    Exactly one of the links below is filled in. The file is in the data folder's
    documents/ folder, so every backup includes it. See attachments.py.
    """

    __tablename__ = "attachments"

    # The file's name in documents/, such as 2026-11-02_elm-st_a1b2c3.pdf.
    file_path: Mapped[str]
    # What the file was called on her computer, for showing and downloading it.
    original_name: Mapped[str] = mapped_column(default="")

    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), default=None)
    walkthrough_id: Mapped[int | None] = mapped_column(ForeignKey("walkthroughs.id"), default=None)
    extension_id: Mapped[int | None] = mapped_column(ForeignKey("lease_extensions.id"), default=None)
    insurance_id: Mapped[int | None] = mapped_column(ForeignKey("insurances.id"), default=None)
    # (Added in schema version 9.)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_requests.id"), default=None)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), default=None)
    # (Added in schema version 13.)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), default=None)

    lease: Mapped[Lease | None] = relationship(back_populates="attachments")
    walkthrough: Mapped[Walkthrough | None] = relationship(back_populates="attachments")
    extension: Mapped[LeaseExtension | None] = relationship(back_populates="attachments")
    insurance: Mapped[Insurance | None] = relationship(back_populates="attachments")
    request: Mapped[MaintenanceRequest | None] = relationship(back_populates="attachments")
    transaction: Mapped[Transaction | None] = relationship(back_populates="attachments")
    property: Mapped[Property | None] = relationship(back_populates="attachments")


class Reminder(RecordMixin, Base):
    """Something to remember on a date, such as property tax. Leases and insurances remind her by themselves."""

    __tablename__ = "reminders"

    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), default=None)
    kind: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    due_date: Mapped[date]
    remind_days_before: Mapped[int] = mapped_column(default=30)
    repeat: Mapped[str] = mapped_column(default="Once")  # one of REMINDER_REPEATS
    notes: Mapped[str] = mapped_column(Text, default="")
    last_done_on: Mapped[date | None] = mapped_column(default=None)
    finished: Mapped[bool] = mapped_column(default=False)

    property: Mapped[Property | None] = relationship()


class ValueLookup(RecordMixin, Base):
    """One time the app asked RentCast what a home is worth (see home_values.py).

    Each lookup is saved just before it's sent, and kept for good: the app counts
    them to stay within RentCast's 50 free lookups a month.
    """

    __tablename__ = "value_lookups"

    # Empty only if the property was later deleted forever (its lookups still count).
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), default=None)
    address: Mapped[str] = mapped_column(Text)  # the address sent to RentCast, on one line
    asked_at: Mapped[datetime]
    # RentCast's estimate, and the range it's 85% sure the value is in. Empty if the lookup failed.
    value_cents: Mapped[int | None] = mapped_column(default=None)
    low_cents: Mapped[int | None] = mapped_column(default=None)
    high_cents: Mapped[int | None] = mapped_column(default=None)
    # Why it failed, in plain words. Empty if it worked (or if the app stopped while asking).
    problem: Mapped[str] = mapped_column(Text, default="")

    property: Mapped[Property | None] = relationship()


class BackupCopy(RecordMixin, Base):
    """When a computer with the app last saved a copy of the server's newest backup.

    The app on each computer tells the server after saving one (see relay.py), so the
    server can warn when copies stop: they're the only copies of her records off the server.
    """

    __tablename__ = "backup_copies"

    computer: Mapped[str]  # the computer's name, such as "Cindys-iMac"
    last_copied_at: Mapped[datetime]


class Account(RecordMixin, Base):
    """Someone who can sign in to the app in a web browser (on the server only), such as "Anna".

    The password itself is never stored: only a scrambled form of it (a "hash"),
    from which it can be checked but not read back. See accounts.py.
    """

    __tablename__ = "accounts"

    name: Mapped[str]  # what they type to sign in; no two accounts have the same name
    password_hash: Mapped[str]
    # Wrong passwords typed in a row. After too many, the name is locked (locked_at is set)
    # until someone unlocks it in Settings or gives it a new password.
    wrong_tries: Mapped[int] = mapped_column(default=0)
    locked_at: Mapped[datetime | None] = mapped_column(default=None)

    sign_ins: Mapped[list["SignIn"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", order_by="SignIn.last_used_at.desc()"
    )


class SignIn(RecordMixin, Base):
    """One device (a browser on a computer or phone) that someone is signed in on.

    The browser keeps a long random code in a cookie; this table keeps only a
    scrambled form of it (token_hash), so even someone who saw app.db couldn't
    sign in with it. Signing out deletes the row, and so does changing the password.
    """

    __tablename__ = "sign_ins"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    token_hash: Mapped[str] = mapped_column(unique=True)
    device: Mapped[str] = mapped_column(default="")  # such as "iPhone (Safari)"
    last_used_at: Mapped[datetime]

    account: Mapped[Account] = relationship(back_populates="sign_ins")
