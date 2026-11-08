"""Backups: what goes in the zip, which ones are kept, and the extra copy folder."""

import sqlite3
import zipfile
from datetime import date, datetime, timedelta

from property_manager import backup
from property_manager.backup import (
    is_overdue,
    last_successful_backup,
    list_backups,
    make_backup,
    remove_old_backups,
)


def add_sample_records(paths):
    """Create a tiny database, one receipt and one invoice PDF in the data folder."""
    paths.create_folders()
    conn = sqlite3.connect(paths.database)
    conn.execute("CREATE TABLE rent (amount_cents INTEGER)")
    conn.execute("INSERT INTO rent VALUES (125000)")
    conn.commit()
    conn.close()
    (paths.receipts / "2026-10-03_elm-st_a1b2c3.pdf").write_bytes(b"%PDF receipt")
    (paths.invoices / "INV-2026-0001.pdf").write_bytes(b"%PDF invoice")
    (paths.documents / "2026-09-24_joes-plumbing_a1b2c3.pdf").write_bytes(b"%PDF W-9")


def rows_in_backup(zip_path, tmp_path):
    """Open the app.db inside a backup zip and return the rows of its rent table."""
    restored = tmp_path / "restored.db"
    with zipfile.ZipFile(zip_path) as zf:
        restored.write_bytes(zf.read("app.db"))
    conn = sqlite3.connect(restored)
    try:
        return conn.execute("SELECT amount_cents FROM rent").fetchall()
    finally:
        conn.close()


def test_backup_zip_holds_the_database_receipts_and_invoices(paths, tmp_path):
    add_sample_records(paths)

    result = make_backup(paths, now=datetime(2026, 9, 24, 15, 10, 2))

    assert result.succeeded
    assert result.zip_path == paths.backups / "backup_2026-09-24_15-10-02.zip"
    with zipfile.ZipFile(result.zip_path) as zf:
        names = zf.namelist()
    assert "app.db" in names
    assert "receipts/2026-10-03_elm-st_a1b2c3.pdf" in names
    assert "invoices/INV-2026-0001.pdf" in names
    assert "documents/2026-09-24_joes-plumbing_a1b2c3.pdf" in names
    assert "BACKUP-INFO.txt" in names
    assert rows_in_backup(result.zip_path, tmp_path) == [(125000,)]


def test_empty_folders_still_appear_in_the_zip(paths):
    paths.create_folders()
    result = make_backup(paths)
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "receipts/" in zf.namelist()
        assert "invoices/" in zf.namelist()


def test_database_copy_is_correct_even_while_something_is_being_saved(paths, tmp_path):
    add_sample_records(paths)
    # Another connection is in the middle of saving a change it hasn't finished yet.
    writer = sqlite3.connect(paths.database)
    writer.execute("INSERT INTO rent VALUES (99)")

    result = make_backup(paths)
    writer.rollback()
    writer.close()

    # The backup has only the finished (committed) records.
    assert result.succeeded
    assert rows_in_backup(result.zip_path, tmp_path) == [(125000,)]


def backed_up_each_day(folder, first_day, last_day, after_each=None):
    """Stand-ins for a backup a day (only their names matter), removing old ones after each, as the app does."""
    day = first_day
    while day <= last_day:
        (folder / f"backup_{day:%Y-%m-%d}_09-00-00.zip").touch()
        remove_old_backups(folder)
        if after_each:
            after_each(day)
        day += timedelta(days=1)


def days_kept(folder):
    return sorted(date.fromisoformat(path.name[7:17]) for path in list_backups(folder))


def test_it_keeps_one_from_every_other_day_and_one_from_each_of_the_last_5_months(tmp_path):
    backed_up_each_day(tmp_path, date(2026, 1, 1), date(2026, 9, 16))
    months = [date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31)]
    every_other_day = [date(2026, 9, 8), date(2026, 9, 10), date(2026, 9, 12), date(2026, 9, 14), date(2026, 9, 16)]
    assert days_kept(tmp_path) == months + every_other_day


def test_a_kept_backup_stays_kept_and_no_gaps_grow(tmp_path):
    """A year of daily backups. Counting pairs of days back from today would leave growing gaps."""

    def check(today):
        if today < date(2026, 1, 12):
            return
        kept = days_kept(tmp_path)
        assert len(kept) <= backup.DAY_PAIRS_KEPT + backup.MONTHS_KEPT
        # (Just after a month ends, its kept backup may be in these 10 days too.)
        recent = [day for day in kept if today - day < timedelta(days=10)]
        assert len(recent) >= backup.DAY_PAIRS_KEPT and recent[-1] == today
        assert all(later - earlier <= timedelta(days=2) for earlier, later in zip(recent, recent[1:]))

    backed_up_each_day(tmp_path, date(2026, 1, 1), date(2026, 12, 31), check)


def test_a_months_backup_goes_after_5_months(tmp_path):
    backed_up_each_day(tmp_path, date(2026, 1, 1), date(2026, 6, 30))
    assert date(2026, 1, 31) in days_kept(tmp_path)
    backed_up_each_day(tmp_path, date(2026, 7, 1), date(2026, 7, 1))
    assert date(2026, 1, 31) not in days_kept(tmp_path) and date(2026, 2, 28) in days_kept(tmp_path)


def test_a_day_keeps_only_its_last_backup(paths):
    add_sample_records(paths)
    for minute in range(3):
        make_backup(paths, now=datetime(2026, 1, 1, 9, minute, 0))
    assert [path.name for path in list_backups(paths.backups)] == ["backup_2026-01-01_09-02-00.zip"]


def test_time_passing_removes_nothing_without_a_new_backup(tmp_path):
    # (It goes by the newest backup's date, so a folder that sat unused isn't emptied.)
    backed_up_each_day(tmp_path, date(2026, 1, 1), date(2026, 1, 8))
    before = days_kept(tmp_path)
    assert remove_old_backups(tmp_path) == [] and days_kept(tmp_path) == before


def test_a_name_that_isnt_a_real_date_is_left_alone(tmp_path):
    odd = tmp_path / "backup_2026-13-99_09-00-00.zip"
    odd.touch()
    backed_up_each_day(tmp_path, date(2026, 1, 1), date(2026, 1, 20))
    assert odd.exists() and odd not in list_backups(tmp_path)


def test_removing_old_backups_never_touches_other_files(paths):
    paths.create_folders()
    notes = paths.backups / "my notes.txt"
    notes.write_text("keep me")
    renamed_copy = paths.backups / "backup_before_moving.zip"
    renamed_copy.write_bytes(b"keep me too")

    for minute in range(3):
        make_backup(paths, now=datetime(2026, 1, 1, 9, minute, 0))

    assert notes.exists()
    assert renamed_copy.exists()
    assert len(list_backups(paths.backups)) == 1


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(paths):
    paths.create_folders()
    moment = datetime(2026, 9, 24, 15, 0, 0)
    first = make_backup(paths, now=moment)
    second = make_backup(paths, now=moment)

    assert first.zip_path != second.zip_path and second.zip_path.name.endswith("_2.zip")
    # The second one counts as the newest, so it's the one kept.
    assert list_backups(paths.backups) == [second.zip_path]


def test_backup_is_also_copied_to_the_chosen_backup_folder(paths, tmp_path):
    add_sample_records(paths)
    dropbox = tmp_path / "Dropbox" / "Property Manager Backups"
    dropbox.mkdir(parents=True)

    result = make_backup(paths, extra_folder=str(dropbox))

    assert result.extra_copy_error == ""
    copy = dropbox / result.zip_path.name
    assert copy.read_bytes() == result.zip_path.read_bytes()
    assert not list(dropbox.glob("*.partial"))


def test_second_folder_keeps_the_same_ones(paths, tmp_path):
    paths.create_folders()
    second = tmp_path / "Documents" / "Property Manager Backups"
    second.parent.mkdir()
    other_file = second / "my notes.txt"
    for day in range(35):
        make_backup(paths, extra_folder=str(second), now=datetime(2026, 1, 1) + timedelta(days=day))
        if day == 0:
            other_file.write_text("keep me")

    assert list_backups(second)[0].name == "backup_2026-02-04_00-00-00.zip"
    assert [path.name for path in list_backups(second)] == [path.name for path in list_backups(paths.backups)]
    assert 5 <= len(list_backups(second)) <= 6
    assert other_file.exists()


def test_second_folder_is_created_when_the_folder_it_goes_in_exists(paths, tmp_path):
    paths.create_folders()
    documents = tmp_path / "Documents"
    documents.mkdir()

    result = make_backup(paths, extra_folder=str(documents / "Property Manager Backups"))

    assert result.extra_copy_error == ""
    assert (documents / "Property Manager Backups" / result.zip_path.name).exists()


def test_standard_second_folder_is_used_unless_she_chose_another(tmp_path):
    chosen = tmp_path / "Dropbox" / "PM"
    assert backup.second_folder(str(chosen)) == chosen
    # During tests the standard folder sits next to the test data folder, never in the real Documents.
    assert backup.second_folder("") == tmp_path / "Property Manager Backups"


def test_missing_backup_folder_is_reported_but_the_backup_still_works(paths, tmp_path):
    paths.create_folders()
    result = make_backup(paths, extra_folder=str(tmp_path / "unplugged USB drive" / "Backups"))

    assert result.succeeded
    assert result.zip_path.exists()
    assert "could not be found" in result.extra_copy_error


def test_failed_backup_is_reported_and_older_backups_are_kept(paths, monkeypatch):
    add_sample_records(paths)
    for day in range(30):
        make_backup(paths, now=datetime(2026, 1, 1) + timedelta(days=day))
    kept = list_backups(paths.backups)

    def disk_full(_db_path):
        raise OSError("No space left on device")

    monkeypatch.setattr(backup, "copy_database", disk_full)
    result = make_backup(paths, now=datetime(2026, 3, 1))

    assert not result.succeeded
    assert "No space left on device" in result.error
    assert list_backups(paths.backups) == kept
    assert last_successful_backup(paths.backups) == datetime(2026, 1, 30)


def test_damaged_database_is_reported_instead_of_backed_up(paths):
    paths.create_folders()
    paths.database.write_bytes(b"this is not a database" * 100)

    result = make_backup(paths)

    assert not result.succeeded
    assert result.error.startswith("The backup could not be made")
    assert list_backups(paths.backups) == []


def test_last_backup_date_and_seven_day_warning(paths):
    paths.create_folders()
    assert last_successful_backup(paths.backups) is None
    assert is_overdue(None)

    make_backup(paths, now=datetime(2026, 9, 1, 8, 0, 0))
    last = last_successful_backup(paths.backups)

    assert last == datetime(2026, 9, 1, 8, 0, 0)
    assert not is_overdue(last, now=datetime(2026, 9, 8, 7, 59))
    assert is_overdue(last, now=datetime(2026, 9, 8, 8, 1))


def test_backup_folder_check(paths, tmp_path):
    paths.create_folders()
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()

    # A new folder inside an existing one is created.
    folder, error = backup.check_backup_folder(str(dropbox / "PM Backups"), paths.root)
    assert error == "" and folder.is_dir()

    # Quotes around a pasted location are ignored.
    folder, error = backup.check_backup_folder(f'"{dropbox}"', paths.root)
    assert error == "" and folder == dropbox

    _, error = backup.check_backup_folder(str(tmp_path / "nowhere" / "PM Backups"), paths.root)
    assert "couldn't find the folder" in error

    _, error = backup.check_backup_folder("Dropbox/PM Backups", paths.root)
    assert "full location" in error

    _, error = backup.check_backup_folder(str(paths.backups), paths.root)
    assert "outside the Property Manager data folder" in error


def test_cloud_drive_folders_are_suggested(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs").mkdir(parents=True)
    cloud_storage = home / "Library" / "CloudStorage"
    (cloud_storage / "Dropbox").mkdir(parents=True)
    (cloud_storage / "GoogleDrive-jane@example.com" / "My Drive").mkdir(parents=True)
    # An old iCloud Drive archive that macOS leaves behind; it's not a real sync folder.
    (cloud_storage / "iCloudDrive-iCloudDrive (10-2-25 6:10 PM)").mkdir()
    monkeypatch.setattr(backup.Path, "home", lambda: home)

    suggestions = backup.suggested_backup_folders()

    assert [name for name, _folder in suggestions] == ["iCloud Drive", "Dropbox", "Google Drive"]
    assert suggestions[1][1] == str(cloud_storage / "Dropbox" / "Property Manager Backups")
