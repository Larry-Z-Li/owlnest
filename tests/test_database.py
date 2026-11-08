"""Creating the database, the startup order, and upgrades when the tables change."""

import sqlite3
import zipfile
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from property_manager import backup, categories, database
from property_manager.backup import BackupResult, list_backups
from property_manager.database import (
    SCHEMA_VERSION,
    DatabaseTooNewError,
    UpgradeBlockedError,
    get_schema_version,
)
from property_manager.config import DataPaths
from property_manager.models import BankCheck, BankEntry, Lease, Llc, MaintenanceRequest, Property, Settings, Transaction
from property_manager.startup import prepare_data_folder

ALL_TABLES = {
    "settings",
    "properties",
    "leases",
    "walkthroughs",
    "lease_extensions",
    "rent_periods",
    "rent_waivers",
    "notes",
    "bank_entries",
    "llcs",
    "bank_checks",
    "insurances",
    "attachments",
    "reminders",
    "backup_copies",
    "maintenance_requests",
    "transactions",
    "accounts",
    "sign_ins",
    "value_lookups",
}


def version_inside_zip(zip_path, tmp_path) -> int:
    restored = tmp_path / "restored.db"
    with zipfile.ZipFile(zip_path) as zf:
        restored.write_bytes(zf.read("app.db"))
    conn = sqlite3.connect(restored)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_first_start_creates_the_data_folder_tables_and_a_backup(paths):
    engine, result = prepare_data_folder(paths)

    for folder in (paths.documents, paths.backups):
        assert folder.is_dir()
    assert set(inspect(engine).get_table_names()) == ALL_TABLES
    assert get_schema_version(engine) == SCHEMA_VERSION
    assert result.succeeded and result.zip_path.exists()
    with Session(engine) as session:
        assert session.get(Settings, 1) is not None


def test_every_start_makes_a_new_backup(paths):
    prepare_data_folder(paths)
    first = list_backups(paths.backups)[0]
    prepare_data_folder(paths)
    assert list_backups(paths.backups)[0] != first


def test_starting_again_keeps_existing_records(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        session.get(Settings, 1).business_name = "Jane Doe Rentals"
        session.commit()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        assert session.get(Settings, 1).business_name == "Jane Doe Rentals"


def test_links_between_tables_are_enforced(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        session.add(  # there is no property 999
            Lease(property_id=999, tenant_names="Jane Doe", start_date=date(2026, 1, 1), portal_token="x")
        )
        with pytest.raises(IntegrityError):
            session.commit()


NEXT = SCHEMA_VERSION + 1  # a pretend future version, for testing upgrades


def test_upgrade_happens_after_the_startup_backup(paths, tmp_path, monkeypatch):
    prepare_data_folder(paths)  # a database at the current version

    def upgrade_to_next(conn):
        conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN built_in INTEGER")

    monkeypatch.setattr(database, "SCHEMA_VERSION", NEXT)
    monkeypatch.setitem(database.UPGRADE_STEPS, NEXT, upgrade_to_next)
    engine, result = prepare_data_folder(paths)

    assert get_schema_version(engine) == NEXT
    columns = {column["name"] for column in inspect(engine).get_columns("properties")}
    assert "built_in" in columns
    # The backup made during this start has the database as it was before the upgrade.
    assert version_inside_zip(result.zip_path, tmp_path) == SCHEMA_VERSION


def test_a_failed_upgrade_step_changes_nothing(paths, monkeypatch):
    prepare_data_folder(paths)

    def broken_upgrade(conn):
        conn.exec_driver_sql("ALTER TABLE properties ADD COLUMN built_in INTEGER")
        raise RuntimeError("something went wrong halfway")

    monkeypatch.setattr(database, "SCHEMA_VERSION", NEXT)
    monkeypatch.setitem(database.UPGRADE_STEPS, NEXT, broken_upgrade)
    with pytest.raises(RuntimeError):
        prepare_data_folder(paths)

    engine = database.make_engine(paths.database)
    assert get_schema_version(engine) == SCHEMA_VERSION
    columns = {column["name"] for column in inspect(engine).get_columns("properties")}
    assert "built_in" not in columns


def test_upgrade_is_refused_if_the_backup_failed(paths, monkeypatch):
    prepare_data_folder(paths)
    monkeypatch.setattr(database, "SCHEMA_VERSION", NEXT)
    monkeypatch.setitem(database.UPGRADE_STEPS, NEXT, lambda conn: None)

    def failed_backup(*_args, **_kwargs):
        return BackupResult(succeeded=False, finished_at=None, error="disk full")

    monkeypatch.setattr(backup, "make_backup", failed_backup)
    with pytest.raises(UpgradeBlockedError):
        prepare_data_folder(paths)
    assert get_schema_version(database.make_engine(paths.database)) == SCHEMA_VERSION


def test_database_from_a_newer_app_version_is_refused(paths):
    prepare_data_folder(paths)
    conn = sqlite3.connect(paths.database)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(DatabaseTooNewError):
        prepare_data_folder(paths)


def make_first_design_database(db_path) -> None:
    """A database as the app's first design (version 7) left it, with her details and some records."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE settings (id INTEGER PRIMARY KEY, business_name VARCHAR, mailing_address TEXT, phone VARCHAR,
                               invoice_prefix VARCHAR, backup_folder VARCHAR);
        INSERT INTO settings VALUES (1, 'Jane Doe Rentals', '1 Main St', '555-0100', 'INV', '');
        CREATE TABLE properties (id INTEGER PRIMARY KEY, nickname VARCHAR);
        CREATE TABLE units (id INTEGER PRIMARY KEY, property_id INTEGER REFERENCES properties (id), label VARCHAR);
        INSERT INTO properties VALUES (1, 'Elm St');
        INSERT INTO units VALUES (1, 1, 'Main');
        PRAGMA user_version = 7;
        """
    )
    conn.close()


def test_a_database_from_the_first_design_is_backed_up_then_replaced(paths, tmp_path):
    """The redesign started with empty records, keeping only her details from Settings."""
    paths.create_folders()
    make_first_design_database(paths.database)

    engine, result = prepare_data_folder(paths)

    assert get_schema_version(engine) == SCHEMA_VERSION
    assert set(inspect(engine).get_table_names()) == ALL_TABLES  # "units" is gone
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        assert (settings.business_name, settings.mailing_address, settings.phone) == (
            "Jane Doe Rentals",
            "1 Main St",
            "555-0100",
        )
    # The backup made just before has the old records.
    assert version_inside_zip(result.zip_path, tmp_path) == 7


def make_version_8(db_path) -> None:
    """Turn a database back into version 8 (phase 1 of the redesign), removing what versions 9 and 10 added."""
    conn = sqlite3.connect(db_path)
    # (SQLite can't drop a column that links to another table, so the table is made again without them.)
    conn.executescript(
        BEFORE_VERSION_20
        + """
        CREATE TABLE attachments_v8 (
            file_path VARCHAR NOT NULL,
            original_name VARCHAR NOT NULL,
            lease_id INTEGER REFERENCES leases (id),
            walkthrough_id INTEGER REFERENCES walkthroughs (id),
            extension_id INTEGER REFERENCES lease_extensions (id),
            insurance_id INTEGER REFERENCES insurances (id),
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            archived_at DATETIME
        );
        DROP TABLE attachments;
        ALTER TABLE attachments_v8 RENAME TO attachments;
        DROP TABLE transactions;
        DROP TABLE maintenance_requests;
        DROP TABLE sign_ins;
        DROP TABLE accounts;
        DROP TABLE value_lookups;
        ALTER TABLE properties DROP COLUMN look_up_value;
        ALTER TABLE properties DROP COLUMN bedrooms;
        ALTER TABLE properties DROP COLUMN bathrooms;
        ALTER TABLE properties DROP COLUMN square_feet;
        DROP TABLE rent_periods;
        ALTER TABLE leases ADD COLUMN monthly_rent_cents INTEGER NOT NULL DEFAULT 0;
        DROP TABLE rent_waivers;
        DROP TABLE notes;
        ALTER TABLE leases DROP COLUMN rent_due_day;
        ALTER TABLE leases DROP COLUMN rent_checked_from;
        DROP TABLE bank_entries;
        DROP TABLE bank_checks;
        PRAGMA user_version = 8;
        """
    )
    conn.close()


def _columns(engine) -> dict:
    """{table: {column: can be empty}} for every table."""
    found = inspect(engine)
    return {
        table: {column["name"]: column["nullable"] for column in found.get_columns(table)}
        for table in found.get_table_names()
    }


# What version 20 added, to go back to before it. (SQLite can't drop a column that points to
# another table, so the table is made again without it.)
BEFORE_VERSION_20 = """
    CREATE TABLE properties_v19 (
        nickname VARCHAR NOT NULL,
        address TEXT NOT NULL,
        notes TEXT NOT NULL,
        look_up_value BOOLEAN NOT NULL DEFAULT 0,
        bedrooms INTEGER,
        bathrooms FLOAT,
        square_feet INTEGER,
        id INTEGER NOT NULL PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        archived_at DATETIME
    );
    INSERT INTO properties_v19
        SELECT nickname, address, notes, look_up_value, bedrooms, bathrooms, square_feet, id, created_at, updated_at, archived_at
        FROM properties;
    DROP TABLE properties;
    ALTER TABLE properties_v19 RENAME TO properties;
    CREATE TABLE transactions_v19 (
        transaction_date DATE NOT NULL,
        property_id INTEGER,
        tenant_name VARCHAR NOT NULL,
        category VARCHAR NOT NULL,
        amount_cents INTEGER NOT NULL,
        details TEXT NOT NULL,
        request_id INTEGER,
        id INTEGER NOT NULL PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        archived_at DATETIME,
        FOREIGN KEY(property_id) REFERENCES properties (id),
        FOREIGN KEY(request_id) REFERENCES maintenance_requests (id)
    );
    INSERT INTO transactions_v19
        SELECT transaction_date, property_id, tenant_name, category, amount_cents, details, request_id, id, created_at, updated_at, archived_at
        FROM transactions;
    DROP TABLE transactions;
    ALTER TABLE transactions_v19 RENAME TO transactions;
    DROP TABLE llcs;
"""

# What versions 16 and up added, to go back to before them.
BEFORE_VERSION_16 = BEFORE_VERSION_20 + """
    DROP TABLE bank_entries;
    DROP TABLE bank_checks;
    DROP TABLE rent_waivers;
    DROP TABLE notes;
    ALTER TABLE leases DROP COLUMN rent_due_day;
    ALTER TABLE leases DROP COLUMN rent_checked_from;
    ALTER TABLE rent_periods DROP COLUMN late_after_days;
    ALTER TABLE rent_periods DROP COLUMN late_percent;
"""


def test_upgrading_checks_rent_from_this_month_or_the_leases_start(paths):
    engine, _ = prepare_data_folder(paths)
    today = date.today()
    later = (today.replace(day=1) + timedelta(days=70)).replace(day=1)
    with Session(engine) as session:
        elm = Property(nickname="Elm St", address="149 Elm St")
        session.add(elm)
        session.flush()
        session.add(Lease(property_id=elm.id, tenant_names="Old", start_date=date(2020, 1, 1), portal_token="x"))
        session.add(Lease(property_id=elm.id, tenant_names="New", start_date=later, portal_token="y"))
        session.commit()
    conn = sqlite3.connect(paths.database)
    conn.executescript(BEFORE_VERSION_16 + "PRAGMA user_version = 15;")
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        old, new = session.query(Lease).order_by(Lease.id)
        assert old.rent_checked_from == today.replace(day=1) and old.old_rent_due_day == 1
        assert new.rent_checked_from == later


def test_upgrading_adds_llcs_with_homes_in_none(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        session.add(Property(nickname="Elm St", address="149 Elm St"))
        session.commit()
    engine.dispose()
    conn = sqlite3.connect(paths.database)
    conn.executescript(BEFORE_VERSION_20 + "PRAGMA user_version = 19;")
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        assert session.query(Property).one().llc_id is None
        assert session.query(Llc).count() == 0


def test_upgrading_puts_her_homes_and_money_in_their_llcs(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        sparrow = Property(nickname="280 Sparrow Way", address="")
        archived = Property(nickname="1984 White Sand Way", address="", archived_at=datetime(2026, 1, 1))
        abroad = Property(nickname="88 Cedar Boulevard", address="")
        session.add_all([sparrow, archived, abroad, Property(nickname="2716 Palm Isle", address="")])
        session.flush()
        made = dict(transaction_date=date(2026, 6, 11), category="Accounting", amount_cents=-100, tenant_name="")
        session.add_all(
            [
                Transaction(details="Harbor Point Capital LLC: accounting fee", **made),
                Transaction(details="Lakeside: a different fee", **made),
                Transaction(details="Someone else: a fee", **made),
                Transaction(details='Lakeside: accounting fee. CHECK: what is "Lakeside"? Its own file has only this line.', **made),
                Transaction(details="Harbor Point Capital LLC: 3672 rental application fee. CHECK: names 3672 Harbor, but she filed it under other expenses, so it's on no property.", **made),
                # (A transaction for a home goes by its home.)
                Transaction(details="Harbor Point Capital LLC: for Sparrow", property_id=sparrow.id, **made),
            ]
        )
        session.commit()
    engine.dispose()
    conn = sqlite3.connect(paths.database)
    conn.executescript(BEFORE_VERSION_20 + "PRAGMA user_version = 19;")
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        # (Maple Grove has none of its homes here, so it isn't made.)
        assert sorted(llc.name for llc in session.query(Llc)) == ["Harbor Point Capital LLC", "Lakeside Properties LLC"]
        homes = {home.nickname: home.llc.name if home.llc else None for home in session.query(Property)}
        assert homes == {
            "280 Sparrow Way": "Harbor Point Capital LLC",
            "1984 White Sand Way": "Harbor Point Capital LLC",
            "88 Cedar Boulevard": None,
            "2716 Palm Isle": "Lakeside Properties LLC",
        }
        money = {item.details: item.llc.name if item.llc else None for item in session.query(Transaction)}
        assert money == {
            "Harbor Point Capital LLC: accounting fee": "Harbor Point Capital LLC",
            "Lakeside: a different fee": "Lakeside Properties LLC",
            "Someone else: a fee": None,
            # His answers: the note on this one is cleared, and that one moved to Lakeside.
            "Lakeside: accounting fee": "Lakeside Properties LLC",
            "Lakeside Properties LLC: 3672 rental application fee. CHECK: names 3672 Harbor, but she filed it under other expenses, so it's on no property.": "Lakeside Properties LLC",
            "Harbor Point Capital LLC: for Sparrow": None,
        }


def test_upgrading_turns_the_banks_deposits_into_entries(paths):
    engine, _ = prepare_data_folder(paths)
    engine.dispose()
    conn = sqlite3.connect(paths.database)
    conn.executescript(
        BEFORE_VERSION_20
        + """
        ALTER TABLE bank_checks DROP COLUMN item_id;
        ALTER TABLE bank_entries DROP COLUMN item_id;
        ALTER TABLE bank_entries DROP COLUMN linked_from;
        ALTER TABLE bank_entries DROP COLUMN never_link;
        ALTER TABLE bank_entries RENAME TO bank_deposits;
        ALTER TABLE bank_checks RENAME COLUMN new_entries TO new_deposits;
        INSERT INTO bank_deposits (bank_id, account_name, day, amount_cents, description, payee, memo, status, id, created_at, updated_at) VALUES
            ('new', '', '2026-11-01', 100, '', '', '', 'New', 1, '2026-11-01', '2026-11-01'),
            ('added', '', '2026-11-01', 100, '', '', '', 'Added', 2, '2026-11-01', '2026-11-01'),
            ('skipped', '', '2026-11-01', 100, '', '', '', 'Skipped', 3, '2026-11-01', '2026-11-01');
        INSERT INTO bank_checks (asked_at, problem, new_deposits, cursor, id, created_at, updated_at) VALUES
            ('2026-11-01', '', 3, 'bookmark', 1, '2026-11-01', '2026-11-01');
        PRAGMA user_version = 17;
        """
    )
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        assert {entry.bank_id: entry.status for entry in session.query(BankEntry)} == {"new": "New", "added": "Entered", "skipped": "New"}
        check = session.query(BankCheck).one()
        assert check.new_entries == 3 and check.item_id == "" and check.cursor == "bookmark"


def test_upgrading_makes_each_leases_rent_the_first_in_its_rent_list(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        elm = Property(nickname="Elm St", address="149 Elm St")
        session.add(elm)
        session.flush()
        lease = Lease(property_id=elm.id, tenant_names="Jane Doe", start_date=date(2026, 3, 1), portal_token="x")
        session.add(lease)
        session.commit()
    conn = sqlite3.connect(paths.database)
    conn.executescript(
        BEFORE_VERSION_16
        + """
        DROP TABLE rent_periods;
        ALTER TABLE leases ADD COLUMN monthly_rent_cents INTEGER NOT NULL DEFAULT 0;
        UPDATE leases SET monthly_rent_cents = 240000;
        PRAGMA user_version = 13;
        """
    )
    conn.close()

    engine, _ = prepare_data_folder(paths)
    assert "monthly_rent_cents" not in {column["name"] for column in inspect(engine).get_columns("leases")}
    with Session(engine) as session:
        lease = session.query(Lease).one()
        assert [(period.starts_on, period.amount_cents) for period in lease.rent_periods] == [(date(2026, 3, 1), 240000)]
        assert lease.rent_cents == 240000


def test_upgrading_moves_records_to_the_merged_types(paths):
    engine, _ = prepare_data_folder(paths)
    day = date(2026, 1, 1)
    with Session(engine) as session:
        elm = Property(nickname="Elm St", address="149 Elm St")
        session.add(elm)
        session.flush()
        session.add_all(
            [
                Transaction(transaction_date=day, amount_cents=100, category="Rents"),
                Transaction(transaction_date=day, amount_cents=-100, category="Repairs"),
                Transaction(transaction_date=day, amount_cents=-100, category="HOA Dues"),
                Transaction(transaction_date=day, amount_cents=-100, category="Cleaning & Upkeep"),
                Transaction(transaction_date=day, amount_cents=-100, category="Insurance"),
                MaintenanceRequest(property_id=elm.id, title="Sink", category="Repairs", reported_on=day),
            ]
        )
        session.commit()
    conn = sqlite3.connect(paths.database)
    conn.executescript(BEFORE_VERSION_16 + "PRAGMA user_version = 14;")
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        purposes = [item.category for item in session.query(Transaction).order_by(Transaction.id)]
        assert purposes == ["Rental Income", "Repair & Maintenance", "HOA", "Cleaning", "Insurance"]
        assert session.query(MaintenanceRequest).one().category == "Repair & Maintenance"


def test_an_upgraded_database_has_the_same_tables_as_a_new_one(tmp_path):
    """Upgrade steps must build exactly what models.py describes."""
    new_engine, _ = prepare_data_folder(DataPaths(tmp_path / "new"))

    old_paths = DataPaths(tmp_path / "old")
    engine, _ = prepare_data_folder(old_paths)
    engine.dispose()
    make_version_8(old_paths.database)
    upgraded_engine, _ = prepare_data_folder(old_paths)

    assert get_schema_version(upgraded_engine) == SCHEMA_VERSION
    assert _columns(upgraded_engine) == _columns(new_engine)


def test_upgrading_merges_taxes_into_property_tax(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        made = dict(transaction_date=date(2026, 6, 11), amount_cents=-100, tenant_name="")
        session.add_all(
            [
                Transaction(details="a tax", category="Taxes", **made),
                Transaction(details="Harbor Point Capital LLC: franchise tax", category="Taxes", **made),
                Transaction(details="Maple Grove Holdings LLC: government annual fee", category="Taxes", **made),
                Transaction(details="Harbor Point Capital LLC: annual fee to government", category="Taxes", **made),
                # (With the same words under another purpose, it stays as it is.)
                Transaction(details="Harbor Point Capital LLC: franchise tax", category="Accounting", **made),
                Transaction(details="an archived tax", category="Taxes", archived_at=datetime(2026, 7, 1), **made),
                Transaction(details="already", category="Property Tax", **made),
                Transaction(details="a transfer", category="Taxes to Remit", **made),
            ]
        )
        session.commit()
    engine.dispose()
    conn = sqlite3.connect(paths.database)
    conn.execute("PRAGMA user_version = 20")
    conn.commit()
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        purposes = {(item.details, item.category) for item in session.query(Transaction)}
    assert purposes == {
        ("a tax", "Property Tax"),
        ("Harbor Point Capital LLC: franchise tax", "License"),
        ("Maple Grove Holdings LLC: government annual fee", "License"),
        ("Harbor Point Capital LLC: annual fee to government", "License"),
        ("Harbor Point Capital LLC: franchise tax", "Accounting"),
        ("an archived tax", "Property Tax"),
        ("already", "Property Tax"),
        ("a transfer", "Taxes to Remit"),
    }
    assert "Taxes" not in categories.ALL and categories.group_of("Property Tax") == categories.OPERATING


def test_upgrading_splits_flooring_and_remodeling(paths):
    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        made = dict(transaction_date=date(2026, 6, 11), amount_cents=-100, tenant_name="")
        session.add_all(
            [
                Transaction(details="new kitchen", category="Flooring & Remodeling", **made),
                Transaction(details="a roof", category="Structure & Systems", **made),
            ]
        )
        session.commit()
    engine.dispose()
    conn = sqlite3.connect(paths.database)
    conn.execute("PRAGMA user_version = 21")
    conn.commit()
    conn.close()

    engine, _ = prepare_data_folder(paths)
    with Session(engine) as session:
        purposes = {(item.details, item.category) for item in session.query(Transaction)}
    assert purposes == {("new kitchen", "Remodeling"), ("a roof", "Structure & Systems")}
    assert "Flooring & Remodeling" not in categories.ALL
    assert {categories.group_of(name) for name in ("Flooring", "Remodeling", "Structure & Systems")} == {categories.OPERATING}
