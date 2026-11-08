"""What happens every time the app starts, in this order:

1. Make sure the data folder and its sub-folders exist.
2. If there is no database yet, create a new, empty one.
3. Make a backup zip (app.db, receipts, documents), remove the older ones not
   worth keeping, and copy it to the second backup folder too. (The server has
   no second folder. See backup.py.)
4. If a code update changed the tables, upgrade the database. This always
   happens after step 3, so there is a backup from just before the upgrade.

While the app runs, start_hourly_checks() also makes a backup once a day, and
(on the server) looks up the homes' values when they're due (see home_values.py)
and checks the bank for new deposits (see bank_link.py).
"""

import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from . import backup, bank_link, database, home_values
from .config import DataPaths

# Only one backup at a time. Two made at the same moment (say, both computers asking for
# the newest backup just as the daily one starts) would pick the same file name and
# spoil each other's zip.
_one_backup_at_a_time = threading.Lock()
# backup_if_due holds this while it checks and backs up, so two callers can't both decide a backup is due.
_waiting_for_backups = threading.Lock()


def prepare_data_folder(paths: DataPaths, on_server: bool = False) -> tuple[Engine, backup.BackupResult]:
    """Run the startup steps above. Returns the database connection and the backup's result.

    Raises database.StartupError (with a message she can read) if the app must not start.
    """
    paths.create_folders()

    engine = database.make_engine(paths.database)
    if database.is_empty(engine):
        database.create_tables(engine)

    second_folder = _second_folder(on_server, _read_backup_folder_setting(engine))
    result = backup.make_backup(paths, extra_folder=second_folder)

    database.upgrade_if_needed(engine, backup_succeeded=result.succeeded)
    return engine, result


def backup_now(app, now: datetime | None = None) -> backup.BackupResult:
    """Make a backup now, with the second copy going to the folder chosen in Settings.

    Used by "Back up now", the import, and the daily backup. (The backup made at
    startup reads the setting differently, because it runs before any upgrade.)
    """
    with app.app_context():
        second_folder = _second_folder(app.config["ON_SERVER"], database.get_settings().backup_folder)
    with _one_backup_at_a_time:
        result = backup.make_backup(app.config["DATA_PATHS"], extra_folder=second_folder, now=now)
    app.config["LAST_BACKUP_RESULT"] = result
    return result


def _second_folder(on_server: bool, backup_folder_setting: str) -> str:
    """Where the second copy of each backup goes ("" for nowhere).

    The server has no second folder: a second folder on the same disk wouldn't
    help, and each computer keeps its own copies instead.
    """
    return "" if on_server else str(backup.second_folder(backup_folder_setting))


def backup_if_due(app, now: datetime | None = None) -> backup.BackupResult | None:
    """Make a backup if the newest one is a day old or more; otherwise do nothing and return None.

    The hourly checks call this, because the app may stay open for weeks and
    backups are otherwise only made when it starts.
    """
    paths = app.config["DATA_PATHS"]
    now = now or datetime.now()
    with _waiting_for_backups:
        # (Checked while no other backup can start, so two callers can't both decide one is due.)
        last = backup.last_successful_backup(paths.backups)
        if last is not None and now - last < timedelta(days=1):
            return None
        return backup_now(app, now)


def start_hourly_checks(app) -> None:
    """Check every hour whether a backup is due, and whether any home's value is due.

    Backups are made every time the app starts, but the app may stay open for
    weeks (and the server runs all the time), so this makes sure there is at
    least one backup a day. Values are only looked up on the server (see home_values.py).
    """

    def check_every_hour():
        while True:
            time.sleep(60 * 60)
            try:
                result = backup_if_due(app)
                if result:
                    print(f"{datetime.now():%Y-%m-%d %H:%M} Daily backup: {result.summary()}")
            except Exception:
                app.logger.exception("The daily backup check failed")
            try:
                for line in home_values.look_up_due_values(app):
                    print(f"{datetime.now():%Y-%m-%d %H:%M} {line}")
            except Exception:
                app.logger.exception("Looking up the homes' values failed")
            try:
                for line in bank_link.check_if_due(app):
                    print(f"{datetime.now():%Y-%m-%d %H:%M} {line}")
            except Exception:
                app.logger.exception("Checking the bank failed")

    threading.Thread(target=check_every_hour, daemon=True).start()


def _read_backup_folder_setting(engine: Engine) -> str:
    """Read the backup folder from Settings.

    This uses plain SQL instead of the Settings model, so it still works on an
    older database that hasn't been upgraded yet.
    """
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT backup_folder FROM settings WHERE id = 1")).scalar()
        return value or ""
    except SQLAlchemyError:
        return ""
