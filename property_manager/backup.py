"""Automatic backups.

Every time the app starts, and whenever she clicks "Back up now" in Settings, the app:

1. Copies app.db, receipts/, invoices/ and documents/ into one dated zip file in the data
   folder's backups/ folder, for example backup_2026-09-24_15-10-02.zip.
2. Removes older backup zips, keeping one from every other day for the last 10 days
   and one from each of the 5 months before this one (see backups_to_keep).
3. Copies the zip to a second folder as well, and keeps the same ones there.
   The second folder is "Property Manager Backups" in her Documents folder,
   unless she chose another one in Settings (such as an iCloud Drive or Dropbox
   folder, so a copy exists off this computer).

On the server, there is no second folder. Instead, the app on each computer downloads the newest
backup once a day into that computer's own "Property Manager Backups" folder
(see relay.py).

The database is copied with SQLite's own backup function rather than a plain file
copy, so the copy is complete and correct even if the app is saving something at
that moment.

remove_old_backups() is the only code in the whole app that deletes files, and it
only deletes backup zips (files named like backup_2026-09-24_15-10-02.zip).
"""

import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DataPaths, default_second_backup_folder

# Which backups are kept (see backups_to_keep): the 5 newest pairs of days, which is about
# 10 days and 5 backups, and the 5 months before this one.
DAY_PAIRS_KEPT = 5
MONTHS_KEPT = 5
# The same thing in words, for the Settings page.
WHAT_IS_KEPT = "one from every other day for the last 10 days, and one from each of the 5 months before this one"

# The home page shows a warning if the newest backup is older than this.
OVERDUE_AFTER = timedelta(days=7)

# Backup zip names look like backup_2026-09-24_15-10-02.zip, or backup_2026-09-24_15-10-02_2.zip
# if two backups are made in the same second. Only files named like this are ever removed.
_NAME_PATTERN = re.compile(r"^backup_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_(\d+))?\.zip$")
_TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Suggested name for the folder that holds backup copies inside Dropbox, iCloud Drive, etc.
SUGGESTED_FOLDER_NAME = "Property Manager Backups"


@dataclass
class BackupResult:
    """What happened during one backup, in words she can read."""

    succeeded: bool
    finished_at: datetime
    zip_path: Path | None = None
    error: str = ""
    # Only filled in when a backup folder is set in Settings.
    extra_folder: str = ""
    extra_copy_error: str = ""

    def summary(self) -> str:
        """A short report for the launcher window."""
        if not self.succeeded:
            return f"WARNING: {self.error}"
        lines = [f"Backup saved: {self.zip_path.name}"]
        if self.extra_folder and self.extra_copy_error:
            lines.append(f"WARNING: {self.extra_copy_error}")
        elif self.extra_folder:
            lines.append(f"A copy was also saved in {self.extra_folder}")
        return "\n".join(lines)


def make_backup(paths: DataPaths, extra_folder: str = "", now: datetime | None = None) -> BackupResult:
    """Make one backup zip, remove the older ones not worth keeping, and copy the new one to extra_folder if set.

    Never raises: any problem is reported in the returned BackupResult instead,
    so a failed backup never stops her from using the app.
    """
    now = now or datetime.now()
    try:
        paths.create_folders()
        zip_path = _new_zip_path(paths.backups, now)
        _write_zip(paths, zip_path, now)
    except Exception as error:
        return BackupResult(
            succeeded=False, finished_at=now, error=f"The backup could not be made: {error}"
        )

    result = BackupResult(succeeded=True, finished_at=now, zip_path=zip_path)

    # Old backups are only removed after a new one has been made successfully.
    try:
        remove_old_backups(paths.backups)
    except OSError:
        pass  # Not serious: the new backup is safe, and the next backup will try again.

    if extra_folder.strip():
        result.extra_folder = extra_folder.strip()
        result.extra_copy_error = copy_to_extra_folder(zip_path, result.extra_folder)
    return result


def copy_database(db_path: Path) -> bytes:
    """Return a complete, consistent copy of the database, made with SQLite's backup function."""
    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(":memory:")
    try:
        source.backup(copy)
        check = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"the database looks damaged ({check})")
        return copy.serialize()
    finally:
        copy.close()
        source.close()


def _write_zip(paths: DataPaths, zip_path: Path, now: datetime) -> None:
    # Write to a ".partial" name first and rename it at the very end, so a
    # half-written zip is never mistaken for a good backup.
    partial = zip_path.with_name(zip_path.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BACKUP-INFO.txt", _info_text(now))
        if paths.database.exists():
            zf.writestr("app.db", copy_database(paths.database))
        _add_folder(zf, paths.receipts, "receipts")
        _add_folder(zf, paths.invoices, "invoices")
        _add_folder(zf, paths.documents, "documents")
    partial.replace(zip_path)


def _add_folder(zf: zipfile.ZipFile, folder: Path, name_in_zip: str) -> None:
    """Add every file in folder (and its sub-folders) to the zip, under name_in_zip/."""
    zf.mkdir(name_in_zip)  # so the folder appears in the zip even when it's empty
    if not folder.is_dir():
        return
    for file in sorted(folder.rglob("*")):
        if file.is_file() and file.name != ".DS_Store":
            zf.write(file, arcname=f"{name_in_zip}/{file.relative_to(folder).as_posix()}")


def _info_text(now: datetime) -> str:
    return (
        "Property Manager backup\n"
        f"Made on {now:%Y-%m-%d} at {now:%H:%M:%S}.\n\n"
        "It contains app.db (all records), receipts/, invoices/ and documents/.\n"
        'To restore it, follow "Restoring from a backup" in the app\'s README.\n'
    )


def _new_zip_path(backups_folder: Path, now: datetime) -> Path:
    """Pick a name for a new backup zip that doesn't overwrite an existing one."""
    stamp = now.strftime(_TIME_FORMAT)
    path = backups_folder / f"backup_{stamp}.zip"
    counter = 2
    while path.exists() or path.with_name(path.name + ".partial").exists():
        path = backups_folder / f"backup_{stamp}_{counter}.zip"
        counter += 1
    return path


def is_backup_name(name: str) -> bool:
    """True if a file name looks like a backup zip, such as backup_2026-09-24_15-10-02.zip."""
    return _NAME_PATTERN.match(name) is not None


def list_backups(backups_folder: Path) -> list[Path]:
    """Return the backup zips in backups_folder, newest first. Files with other names are ignored."""
    found = []
    for path in backups_folder.glob("backup_*.zip"):
        match = _NAME_PATTERN.match(path.name)
        if match and path.is_file():
            stamp, counter = match.group(1), int(match.group(2) or 1)
            try:
                datetime.strptime(stamp, _TIME_FORMAT)
            except ValueError:
                continue  # (Not a real date, so it isn't one of ours. It's left alone.)
            found.append((stamp, counter, path))
    found.sort(reverse=True)
    return [path for _stamp, _counter, path in found]


def _day_of(backup_path: Path) -> date:
    return datetime.strptime(_NAME_PATTERN.match(backup_path.name).group(1), _TIME_FORMAT).date()


def backups_to_keep(backups: list[Path]) -> set[Path]:
    """Which of these backups (newest first) to keep: the last one of each of

    * the 5 newest pairs of days, which is one from every other day for about 10 days, and
    * the 5 months before the newest backup's month.

    The pairs of days are fixed on the calendar, not counted back from today. Counted from
    today, the pairs would shift each day, so yesterday's backup might not be the one wanted
    today, and gaps would grow. This way, a backup that's kept stays kept until it's too old.

    It goes by the newest backup's date, so a folder that sat unused isn't emptied by the
    calendar moving on, and the newest backup is always kept.
    """
    if not backups:
        return set()
    newest_day = _day_of(backups[0])
    last_of_pair = {}
    last_of_month = {}
    for path in backups:  # (Newest first, so the first one found for each is the last one made.)
        day = _day_of(path)
        last_of_pair.setdefault(day.toordinal() // 2, path)
        last_of_month.setdefault(day.year * 12 + day.month - 1, path)
    newest_pair = newest_day.toordinal() // 2
    this_month = newest_day.year * 12 + newest_day.month - 1
    keep = {path for pair, path in last_of_pair.items() if pair > newest_pair - DAY_PAIRS_KEPT}
    keep |= {path for month, path in last_of_month.items() if this_month - MONTHS_KEPT <= month < this_month}
    return keep


def remove_old_backups(backups_folder: Path) -> list[Path]:
    """Delete the backup zips in a folder that aren't worth keeping, and return the ones deleted.

    This is the only code in the app that deletes files. Files that aren't named
    like a backup zip are never touched.
    """
    backups = list_backups(backups_folder)
    keep = backups_to_keep(backups)
    old_backups = [path for path in backups if path not in keep]
    for path in old_backups:
        path.unlink()
    return old_backups


def last_successful_backup(backups_folder: Path) -> datetime | None:
    """Return when the newest backup zip was made, or None if there are no backups yet."""
    backups = list_backups(backups_folder)
    if not backups:
        return None
    stamp = _NAME_PATTERN.match(backups[0].name).group(1)
    return datetime.strptime(stamp, _TIME_FORMAT)


def is_overdue(last_backup: datetime | None, now: datetime | None = None) -> bool:
    """True if no backup has succeeded in the last 7 days."""
    now = now or datetime.now()
    return last_backup is None or now - last_backup > OVERDUE_AFTER


def second_folder(setting: str) -> Path:
    """Where backups are also copied: the folder chosen in Settings, or the standard one if none was chosen."""
    if setting.strip():
        return Path(setting.strip()).expanduser()
    return default_second_backup_folder()


def copy_to_extra_folder(zip_path: Path, folder_text: str) -> str:
    """Copy one backup zip into the second backup folder, then remove the older ones there not worth keeping.

    Returns "" if it worked, or an error message she can read if it didn't.
    """
    folder = Path(folder_text).expanduser()
    # Create the folder if needed, but only when the folder it goes in exists. (If a
    # Dropbox or USB drive folder is missing, creating it here would hide the problem.)
    if not folder.exists() and folder.parent.is_dir():
        try:
            folder.mkdir()
        except OSError:
            pass  # Reported just below.
    if not folder.is_dir():
        return (
            f"The backup folder {folder} could not be found, so no copy was saved there. "
            "If it is in Dropbox or iCloud Drive, or on a USB drive, make sure that is "
            "turned on or plugged in."
        )
    target = folder / zip_path.name
    partial = folder / (zip_path.name + ".partial")
    try:
        shutil.copyfile(zip_path, partial)
        partial.replace(target)
    except OSError as error:
        return f"The backup could not be copied to {folder}: {error}"

    try:
        remove_old_backups(folder)
    except OSError:
        pass  # Not serious: the new copy is safe, and the next backup will try again.
    return ""


def check_backup_folder(text: str, data_folder: Path) -> tuple[Path | None, str]:
    """Check the backup folder she typed or picked in Settings, creating it if needed.

    Returns (folder, "") if it can be used, or (None, a message she can read) if not.
    """
    # Remove quotes, in case the location was pasted with them.
    folder = Path(text.strip().strip("\"'")).expanduser()
    if not folder.is_absolute():
        return None, (
            "Please enter the folder's full location, such as "
            f"/Users/yourname/Dropbox/{SUGGESTED_FOLDER_NAME}, or pick one of the suggestions."
        )
    if folder.resolve().is_relative_to(data_folder.resolve()):
        return None, (
            "Please choose a folder outside the Property Manager data folder, "
            "so the copy is kept somewhere else."
        )
    if not folder.exists():
        if not folder.parent.is_dir():
            return None, (
                f"We couldn't find the folder {folder.parent}. "
                "Please check the spelling, or pick one of the suggestions."
            )
        try:
            folder.mkdir()
        except OSError as error:
            return None, f"We couldn't create the folder {folder}: {error}"
    if not folder.is_dir():
        return None, f"{folder} is a file, not a folder."
    return folder, ""


def suggested_backup_folders() -> list[tuple[str, str]]:
    """Find cloud-drive folders on this computer (iCloud Drive, Dropbox, ...) to suggest in Settings.

    Returns a list of (name, folder location) pairs, for example
    ("Dropbox", "/Users/jane/Dropbox/Property Manager Backups").
    """
    home = Path.home()
    candidates = [
        ("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"),  # Mac
        ("iCloud Drive", home / "iCloudDrive"),  # Windows
        ("Dropbox", home / "Dropbox"),
        ("OneDrive", home / "OneDrive"),
    ]
    # Newer Macs keep Dropbox, Google Drive and OneDrive here, in folders named
    # like "Dropbox" or "GoogleDrive-jane@example.com". (Other folders here, such
    # as old iCloud Drive archives, are skipped.)
    cloud_storage = home / "Library" / "CloudStorage"
    try:
        for folder in sorted(cloud_storage.iterdir()):
            name = folder.name.split("-")[0]
            if name == "GoogleDrive":
                candidates.append(("Google Drive", folder / "My Drive"))
            elif name in ("Dropbox", "OneDrive", "Box"):
                candidates.append((name, folder))
    except OSError:
        pass  # No such folder on this computer.

    suggestions = []
    seen = set()
    for name, folder in candidates:
        try:
            usable = folder.is_dir() and os.access(folder, os.W_OK)
            if usable and folder.resolve() not in seen:
                seen.add(folder.resolve())
                suggestions.append((name, str(folder / SUGGESTED_FOLDER_NAME)))
        except OSError:
            continue
    return suggestions
