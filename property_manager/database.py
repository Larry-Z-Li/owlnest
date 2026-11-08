"""Connecting to app.db, creating it, and upgrading it when the code changes.

The database remembers a "schema version" number (stored by SQLite itself, as
PRAGMA user_version). When a code update changes the tables in models.py:

1. Raise SCHEMA_VERSION below by one.
2. Add a function to UPGRADE_STEPS that changes an existing database from the
   previous version to the new one (for example, by adding a column).

On startup the app makes a backup first, then runs any upgrade steps that the
database hasn't had yet (see startup.py). Each step runs inside a transaction,
so a step that fails leaves the database exactly as it was.

Versions 1 to 7 were the app's first design (with units, invoices, payments and
so on). Version 8 is the redesign (Oct 2026), which started with empty records:
a database from the first design is backed up as always, then replaced by the
new, empty tables, keeping only her details from Settings. The first design's
code is kept in git, on the branch archive/v1.
"""

from datetime import date, datetime

from sqlalchemy import Connection, Engine, create_engine, event, insert, inspect
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import Base, Settings

# The version of the tables in models.py. See the notes at the top of this file.
SCHEMA_VERSION = 25

# The first version of the redesign. Older databases are replaced (see _replace_first_design).
FIRST_REDESIGN_VERSION = 8

# Her details in Settings, which are kept when a database from the first design is replaced.
_KEPT_SETTINGS = ("business_name", "mailing_address", "phone", "backup_folder")



def _upgrade_to_9(conn: Connection) -> None:
    """Version 9: maintenance requests and transactions, and files attached to them."""
    conn.exec_driver_sql(
        """
        CREATE TABLE maintenance_requests (
            property_id INTEGER NOT NULL REFERENCES properties (id),
            lease_id INTEGER REFERENCES leases (id),
            title VARCHAR NOT NULL,
            description TEXT NOT NULL,
            category VARCHAR NOT NULL,
            company VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            reported_on DATE NOT NULL,
            from_tenant BOOLEAN NOT NULL,
            unread BOOLEAN NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE transactions (
            transaction_date DATE NOT NULL,
            property_id INTEGER REFERENCES properties (id),
            tenant_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            amount_cents INTEGER NOT NULL,
            details TEXT NOT NULL,
            request_id INTEGER REFERENCES maintenance_requests (id),
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql("ALTER TABLE attachments ADD COLUMN request_id INTEGER REFERENCES maintenance_requests (id)")
    conn.exec_driver_sql("ALTER TABLE attachments ADD COLUMN transaction_id INTEGER REFERENCES transactions (id)")


def _upgrade_to_10(conn: Connection) -> None:
    """Version 10: the people who can sign in in a web browser, and the devices they're signed in on."""
    conn.exec_driver_sql(
        """
        CREATE TABLE accounts (
            name VARCHAR NOT NULL,
            password_hash VARCHAR NOT NULL,
            wrong_tries INTEGER NOT NULL,
            locked_at DATETIME,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE sign_ins (
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            token_hash VARCHAR NOT NULL UNIQUE,
            device VARCHAR NOT NULL,
            last_used_at DATETIME NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )


def _upgrade_to_11(conn: Connection) -> None:
    """Version 11: each home's estimated value, looked up from RentCast (see home_values.py)."""
    conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN look_up_value BOOLEAN NOT NULL DEFAULT 0")
    conn.exec_driver_sql(
        """
        CREATE TABLE value_lookups (
            property_id INTEGER REFERENCES properties (id),
            address TEXT NOT NULL,
            asked_at DATETIME NOT NULL,
            value_cents INTEGER,
            low_cents INTEGER,
            high_cents INTEGER,
            problem TEXT NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )


def _upgrade_to_12(conn: Connection) -> None:
    """Version 12: each property's bedrooms, bathrooms and square feet."""
    conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN bedrooms INTEGER")
    conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN bathrooms FLOAT")
    conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN square_feet INTEGER")


def _upgrade_to_13(conn: Connection) -> None:
    """Version 13: photos of each property (attachments linked to the property)."""
    conn.exec_driver_sql("ALTER TABLE attachments ADD COLUMN property_id INTEGER REFERENCES properties (id)")


def _upgrade_to_14(conn: Connection) -> None:
    """Version 14: a lease's rent is a list of amounts by date. Its rent so far becomes the first, from its start."""
    conn.exec_driver_sql(
        """
        CREATE TABLE rent_periods (
            lease_id INTEGER NOT NULL REFERENCES leases (id),
            starts_on DATE NOT NULL,
            amount_cents INTEGER NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql(
        """
        INSERT INTO rent_periods (lease_id, starts_on, amount_cents, created_at, updated_at)
        SELECT id, start_date, monthly_rent_cents, created_at, updated_at FROM leases
        """
    )
    conn.exec_driver_sql("ALTER TABLE leases DROP COLUMN monthly_rent_cents")


def _upgrade_to_15(conn: Connection) -> None:
    """Version 15: types that meant the same thing were merged, keeping the names from her spreadsheets."""
    merged = {
        "Rents": "Rental Income",
        "Repairs": "Repair & Maintenance",
        "HOA Dues": "HOA",
        "Cleaning & Upkeep": "Cleaning",
    }
    for table in ("transactions", "maintenance_requests"):
        for old, new in merged.items():
            conn.exec_driver_sql(f"UPDATE {table} SET category = ? WHERE category = ?", (new, old))


def _upgrade_to_16(conn: Connection) -> None:
    """Version 16: rent reminders (the due day, late fee rules and waivers), and notes.

    Rent is checked from this month on, or from a lease's start if that's later: earlier
    months' payments may never have been entered here.
    """
    this_month = date.today().replace(day=1).isoformat()
    conn.exec_driver_sql("ALTER TABLE leases ADD COLUMN rent_due_day INTEGER NOT NULL DEFAULT 1")
    conn.exec_driver_sql("ALTER TABLE leases ADD COLUMN rent_checked_from DATE")
    conn.exec_driver_sql("UPDATE leases SET rent_checked_from = max(start_date, ?)", (this_month,))
    conn.exec_driver_sql("ALTER TABLE rent_periods ADD COLUMN late_after_days INTEGER NOT NULL DEFAULT 5")
    conn.exec_driver_sql("ALTER TABLE rent_periods ADD COLUMN late_percent FLOAT NOT NULL DEFAULT 5")
    conn.exec_driver_sql(
        """
        CREATE TABLE rent_waivers (
            lease_id INTEGER NOT NULL REFERENCES leases (id),
            month DATE NOT NULL,
            rent_cents INTEGER NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE notes (
            text TEXT NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )


def _upgrade_to_17(conn: Connection) -> None:
    """Version 17: deposits from her bank, waiting for her to check them (see bank_link.py)."""
    conn.exec_driver_sql(
        """
        CREATE TABLE bank_deposits (
            bank_id VARCHAR NOT NULL UNIQUE,
            account_name VARCHAR NOT NULL,
            day DATE NOT NULL,
            amount_cents INTEGER NOT NULL,
            description TEXT NOT NULL,
            payee VARCHAR NOT NULL,
            memo TEXT NOT NULL,
            status VARCHAR NOT NULL,
            transaction_id INTEGER REFERENCES transactions (id),
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE bank_checks (
            asked_at DATETIME NOT NULL,
            problem TEXT NOT NULL,
            new_deposits INTEGER NOT NULL,
            cursor TEXT NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )


def _upgrade_to_18(conn: Connection) -> None:
    """Version 18: the bank brings in money out too, so its deposits became entries, each waiting or entered."""
    conn.exec_driver_sql("ALTER TABLE bank_deposits RENAME TO bank_entries")
    conn.exec_driver_sql("ALTER TABLE bank_checks RENAME COLUMN new_deposits TO new_entries")
    conn.exec_driver_sql("UPDATE bank_entries SET status = 'Entered' WHERE status = 'Added'")
    conn.exec_driver_sql("UPDATE bank_entries SET status = 'New' WHERE status = 'Skipped'")


def _upgrade_to_19(conn: Connection) -> None:
    """Version 19: more than one bank login, so each check says which it was for. Older ones are the first login's."""
    conn.exec_driver_sql("ALTER TABLE bank_checks ADD COLUMN item_id VARCHAR NOT NULL DEFAULT ''")


# Her LLCs, from Larry's folders (Nov 3, 2026): each one's name, how her transactions for the
# LLC itself begin (their details were entered that way), and the homes it owns.
# 88 Cedar Boulevard, is in no LLC.
_HER_LLCS = (
    (
        "Maple Grove Holdings LLC",
        "Maple Grove Holdings LLC:",
        (
            "171 Willow Trail",
            "223 Birch Ave (lower level)",
            "223 Birch Ave (upper level)",
            "410 Aspen Rd",
            "2300 Juniper Cv",
            "2724 Pine Branch",
            "12865 Oakbend",
        ),
    ),
    ("Lakeside Properties LLC", "Lakeside:", ("2716 Palm Isle", "3672 Harbor Ct")),
    (
        "Harbor Point Capital LLC",
        "Harbor Point Capital LLC:",
        (
            "280 Sparrow Way",
            "1000 Elm Grove",
            "1010 Vista Ave",
            "1984 White Sand Way",
            "3540 S Mesa Pl",
            "4149 Bay Isles",
            "11128 Ocean Bay",
            "34132 Clover Ct",
            "36251 Fern Dr",
        ),
    ),
)


# Larry's answers about two of those transactions: what its details say, which LLC it's for, and what they say now.
_HER_TRANSACTION_ANSWERS = (
    (
        'Lakeside: accounting fee. CHECK: what is "Lakeside"? Its own file has only this line.',
        "Lakeside Properties LLC",
        "Lakeside: accounting fee",
    ),
    (
        "Harbor Point Capital LLC: 3672 rental application fee. CHECK: names 3672 Harbor, but she filed it under other expenses, so it's on no property.",
        "Lakeside Properties LLC",
        "Lakeside Properties LLC: 3672 rental application fee. CHECK: names 3672 Harbor, but she filed it under other expenses, so it's on no property.",
    ),
)


def _upgrade_to_20(conn: Connection) -> None:
    """Version 20: LLCs, each just a name, which one owns each home, and which one a transaction is for.

    Her homes and transactions go in their LLCs here, once. A database without her homes gets no LLCs.
    """
    conn.exec_driver_sql(
        """
        CREATE TABLE llcs (
            name VARCHAR NOT NULL,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        )
        """
    )
    conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN llc_id INTEGER REFERENCES llcs (id)")
    conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN llc_id INTEGER REFERENCES llcs (id)")
    now = str(datetime.now())
    for name, details_begin, homes in _HER_LLCS:
        marks = ", ".join("?" * len(homes))
        if not conn.exec_driver_sql(f"SELECT COUNT(*) FROM properties WHERE nickname IN ({marks})", homes).scalar():
            continue
        conn.exec_driver_sql("INSERT INTO llcs (name, created_at, updated_at) VALUES (?, ?, ?)", (name, now, now))
        llc_id = conn.exec_driver_sql("SELECT id FROM llcs WHERE name = ?", (name,)).scalar()
        conn.exec_driver_sql(f"UPDATE properties SET llc_id = ? WHERE nickname IN ({marks})", (llc_id, *homes))
        conn.exec_driver_sql(
            "UPDATE transactions SET llc_id = ? WHERE property_id IS NULL AND details LIKE ?", (llc_id, details_begin + "%")
        )
    for details, llc_name, new_details in _HER_TRANSACTION_ANSWERS:
        conn.exec_driver_sql(
            "UPDATE transactions SET details = ?, llc_id = (SELECT id FROM llcs WHERE name = ?) WHERE property_id IS NULL AND details = ?",
            (new_details, llc_name, details),
        )


# What her LLCs themselves pay the government: not tax on a home, so these go to "License" (his answer, 2026-11-04).
_HER_LLC_FEES = (
    "Maple Grove Holdings LLC: government annual fee",
    "Harbor Point Capital LLC: annual fee to government",
    "Harbor Point Capital LLC: franchise tax",
)


def _upgrade_to_21(conn: Connection) -> None:
    """Version 21: the purpose "Taxes" is merged into "Property Tax" (Larry's word, 2026-11-04), except her LLCs' fees. "Taxes to Remit" stays."""
    marks = ", ".join("?" * len(_HER_LLC_FEES))
    conn.exec_driver_sql(f"UPDATE transactions SET category = 'License' WHERE category = 'Taxes' AND details IN ({marks})", _HER_LLC_FEES)
    for table in ("transactions", "maintenance_requests"):
        conn.exec_driver_sql(f"UPDATE {table} SET category = 'Property Tax' WHERE category = 'Taxes'")


def _upgrade_to_22(conn: Connection) -> None:
    """Version 22: the purpose "Flooring & Remodeling" becomes "Remodeling", beside a new "Flooring" (Larry's word, 2026-11-05).

    (Capital expenses became operating ones at the same time. That needs nothing here: a purpose's group isn't saved.)
    """
    for table in ("transactions", "maintenance_requests"):
        conn.exec_driver_sql(f"UPDATE {table} SET category = 'Remodeling' WHERE category = 'Flooring & Remodeling'")


def _upgrade_to_23(conn: Connection) -> None:
    """Version 23: each bank entry remembers which connection brought it in, so a login connected again isn't listed twice."""
    # (A database made from the models already has it.)
    columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bank_entries)")]
    if "item_id" not in columns:
        conn.exec_driver_sql("ALTER TABLE bank_entries ADD COLUMN item_id VARCHAR NOT NULL DEFAULT ''")


def _upgrade_to_24(conn: Connection) -> None:
    """Version 24: a bank entry can be linked to a transaction she typed in (see ENTRY_STATUSES)."""
    columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bank_entries)")]
    if "linked_from" not in columns:
        conn.exec_driver_sql("ALTER TABLE bank_entries ADD COLUMN linked_from VARCHAR NOT NULL DEFAULT ''")
    if "never_link" not in columns:
        conn.exec_driver_sql("ALTER TABLE bank_entries ADD COLUMN never_link BOOLEAN NOT NULL DEFAULT 0")


def _upgrade_to_25(conn: Connection) -> None:
    """Version 25: a home can have her own estimated value, shown instead of RentCast's."""
    columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(properties)")]
    if "own_value_cents" not in columns:
        conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN own_value_cents INTEGER")
    if "own_value_set_at" not in columns:
        conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN own_value_set_at DATETIME")


# Upgrade steps, by the version they upgrade *to* (9 and up). Each one receives an open database connection.
UPGRADE_STEPS = {
    9: _upgrade_to_9,
    10: _upgrade_to_10,
    11: _upgrade_to_11,
    12: _upgrade_to_12,
    13: _upgrade_to_13,
    14: _upgrade_to_14,
    15: _upgrade_to_15,
    16: _upgrade_to_16,
    17: _upgrade_to_17,
    18: _upgrade_to_18,
    19: _upgrade_to_19,
    20: _upgrade_to_20,
    21: _upgrade_to_21,
    22: _upgrade_to_22,
    23: _upgrade_to_23,
    24: _upgrade_to_24,
    25: _upgrade_to_25,
}

# The screens use this to read and save records: db_session.get(...), db_session.commit(), etc.
# create_app() connects it to the database. Each web request gets its own session,
# which is closed when the request finishes.
db_session = scoped_session(sessionmaker())


class StartupError(Exception):
    """A problem that stops the app from starting. The message is written for her to read."""


class DatabaseTooNewError(StartupError):
    """The database was made by a newer version of the app than the one starting now."""


class UpgradeBlockedError(StartupError):
    """The database needs an upgrade, but no backup could be made first."""


def make_engine(db_path) -> Engine:
    """Return a connection to the SQLite database file at db_path (created if missing)."""
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        # SQLite doesn't check links between tables (such as a unit's property)
        # unless this is turned on for every connection.
        dbapi_connection.execute("PRAGMA foreign_keys = ON")
        # Let SQLAlchemy start transactions itself (see _on_begin), instead of Python's
        # sqlite3 module, which skips them for table changes. This keeps upgrades all-or-nothing.
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def get_schema_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA user_version").scalar()


def _set_schema_version(conn: Connection, version: int) -> None:
    conn.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def is_empty(engine: Engine) -> bool:
    """True if the database has no tables yet (a brand-new data folder)."""
    return not inspect(engine).get_table_names()


def create_tables(engine: Engine) -> None:
    """Create every table in a brand-new database and fill in the starting values."""
    with engine.begin() as conn:
        _create_tables(conn, {})


def _create_tables(conn: Connection, settings: dict) -> None:
    Base.metadata.create_all(conn)
    conn.execute(insert(Settings).values(id=1, **settings))
    _set_schema_version(conn, SCHEMA_VERSION)


def _replace_first_design(conn: Connection) -> None:
    """Replace a database from the app's first design (versions 1-7) with the redesign's empty tables.

    Only her details from Settings are kept. The startup backup, made just before
    this, keeps everything else.
    """
    columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(settings)")}
    kept = [name for name in _KEPT_SETTINGS if name in columns]
    settings = {}
    if kept:
        row = conn.exec_driver_sql(f"SELECT {', '.join(kept)} FROM settings WHERE id = 1").first()
        if row is not None:
            settings = {name: value or "" for name, value in zip(kept, row)}

    # Remove every table. Links between them are only checked at the end, when all are gone.
    conn.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
    tables = [row[0] for row in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in tables:
        conn.exec_driver_sql(f'DROP TABLE "{table}"')
    _create_tables(conn, settings)


def upgrade_if_needed(engine: Engine, *, backup_succeeded: bool) -> None:
    """Bring an existing database up to SCHEMA_VERSION, one step at a time.

    backup_succeeded says whether the backup made just before this worked. If it
    didn't, the upgrade is refused, because changing the tables without a backup is risky.
    """
    version = get_schema_version(engine)
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise DatabaseTooNewError(
            "Your records were saved by a newer version of this app than this one. "
            "Please use the newer version of the app. Nothing has been changed."
        )
    if not backup_succeeded:
        raise UpgradeBlockedError(
            "This update needs to upgrade your records, but a backup could not be made first, "
            "so nothing has been changed. Please check that the computer has free disk space, "
            "then start the app again."
        )
    if version < FIRST_REDESIGN_VERSION:
        with engine.begin() as conn:
            _replace_first_design(conn)
        return
    for step in range(version + 1, SCHEMA_VERSION + 1):
        # One transaction per step: if a step fails, that step's changes are undone.
        with engine.begin() as conn:
            UPGRADE_STEPS[step](conn)
            _set_schema_version(conn, step)


def get_settings() -> Settings:
    """Return her settings (the single row in the settings table), creating it if it's missing."""
    settings = db_session.get(Settings, 1)
    if settings is None:
        settings = Settings(id=1)
        db_session.add(settings)
        db_session.commit()
    return settings
