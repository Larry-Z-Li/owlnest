"""The server (where her records live) and the relay (the app on each computer that talks to it)."""

import json
import threading
import zipfile
from collections import namedtuple
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from helpers import PDF, add_lease, add_property
from werkzeug.serving import make_server
from werkzeug.test import Client

import make_app_package
from property_manager import alerts, create_app, relay, server
from property_manager.backup import list_backups
from property_manager.config import HEALTH_TEXT, KEY_HEADER
from property_manager.relay import Connection, ConnectionProblem, copy_newest_backup, load_connection
from property_manager.startup import backup_now


KEY = "a-test-key-that-is-long-enough-to-be-accepted"


@pytest.fixture
def server_app(tmp_path):
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def with_key(server_app):
    """A test client that sends the key, like the relay does."""
    client = server_app.test_client()
    client.environ_base[f"HTTP_{KEY_HEADER.upper().replace('-', '_')}"] = KEY
    return client


@pytest.fixture
def server_address(server_app):
    """Run the server for real (on this computer), so the relay can talk to it."""
    running = make_server("127.0.0.1", 0, server_app, threaded=True)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}"
    running.shutdown()
    running.server_close()


class BrowserLikeClient(Client):
    """Follows redirects like a browser, so each "Saved." message shows on the next page."""

    def open(self, *args, **kwargs):
        kwargs.setdefault("follow_redirects", True)
        return super().open(*args, **kwargs)


def relay_client(server_address, key=KEY, on_close=None) -> Client:
    return BrowserLikeClient(relay.make_relay(Connection(server_address, key), on_close))


def nowhere() -> str:
    """The address of a server that isn't running."""
    unused = make_server("127.0.0.1", 0, lambda environ, start_response: [])
    address = f"http://127.0.0.1:{unused.server_port}"
    unused.server_close()
    return address


# ---------- The server ----------


def test_the_server_sends_anyone_without_the_key_to_sign_in(server_app):
    client = server_app.test_client()
    assert client.get("/").headers["Location"] == "/sign-in"
    assert client.get("/properties/").headers["Location"] == "/sign-in?next=/properties/"
    assert client.post("/properties/new", data={"nickname": "Elm St"}).status_code == 303
    assert "Elm St" not in client.get("/properties/", headers={KEY_HEADER: KEY}).get_data(as_text=True)


def test_a_wrong_key_is_refused_so_the_app_can_explain(server_app):
    assert server_app.test_client().get("/", headers={KEY_HEADER: "wrong"}).status_code == 401


def test_the_server_answers_the_app_and_its_health_check(server_app, with_key):
    assert with_key.get("/").status_code == 200
    health = server_app.test_client().get("/health")  # without the key: Render checks this
    assert health.status_code == 200
    assert health.get_data(as_text=True) == HEALTH_TEXT


def test_the_server_wont_start_without_a_proper_key(monkeypatch):
    monkeypatch.delenv("PROPERTY_MANAGER_KEY", raising=False)
    with pytest.raises(SystemExit, match="PROPERTY_MANAGER_KEY"):
        server.build_server_app()
    monkeypatch.setenv("PROPERTY_MANAGER_KEY", "short")
    with pytest.raises(SystemExit, match="at least 32"):
        server.build_server_app()


def test_after_a_move_the_old_address_only_passes_people_on(tmp_path, monkeypatch):
    data = tmp_path / "old-disk"
    monkeypatch.setenv("PROPERTY_MANAGER_DATA", str(data))
    monkeypatch.setenv(server.MOVED_TO_VARIABLE, "https://new-address.example.com/")
    monkeypatch.delenv("PROPERTY_MANAGER_KEY", raising=False)  # (It isn't needed: no records are served.)
    client = server.build_server_app().test_client()
    new = "https://new-address.example.com"
    for address in ("/", "/properties/", "/static/style.css", "/portal/link/abc?from=text"):
        response = client.get(address)
        assert response.status_code == 308 and response.headers["Location"] == new + address
    sent = client.post("/portal/link/abc", data={"message": "The sink leaks"})
    assert sent.status_code == 308 and sent.headers["Location"] == new + "/portal/link/abc"
    health = client.get("/health")
    assert health.status_code == 200 and health.get_data(as_text=True) == HEALTH_TEXT
    # It never opens the old disk, so nothing there changes.
    assert not data.exists()


def test_on_the_server_closing_the_app_only_shows_the_closed_page(with_key):
    assert "Close the app" in with_key.get("/").get_data(as_text=True)
    page = with_key.post("/shutdown").get_data(as_text=True)
    assert "is closed" in page


def test_the_server_removes_old_backups_and_has_no_second_copy(server_app, tmp_path):
    start = datetime.now()
    for day in range(20):
        assert backup_now(server_app, now=start + timedelta(days=day)).succeeded
    # 5 from every other day, and one more if those days crossed into a new month.
    assert 5 <= len(list_backups(server_app.config["DATA_PATHS"].backups)) <= 6
    # The standard second folder (next to the test data folder) was never used.
    assert not (tmp_path / "Property Manager Backups").exists()


def test_settings_on_the_server_explain_the_copies_on_each_computer(with_key):
    page = with_key.get("/settings/").get_data(as_text=True)
    assert "Copies on your computers" in page
    assert "Second copy of your backups" not in page
    assert with_key.post("/settings/backup-folder", data={"backup_folder": "/tmp"}).status_code == 404


DiskUsage = namedtuple("DiskUsage", "total used free")


def test_a_nearly_full_server_disk_is_an_alert(with_key, monkeypatch):
    monkeypatch.setattr(alerts.shutil, "disk_usage", lambda _path: DiskUsage(100, 90, 10))
    assert "disk is 90% full" in with_key.get("/").get_data(as_text=True)


def test_portal_pages_dont_work_out_her_alerts(server_app, monkeypatch):
    """Anyone can open a portal page, so it mustn't cost the work of her "Needs attention" list."""
    worked_out = []
    monkeypatch.setattr(alerts, "gather", lambda *args: worked_out.append(1) or [])
    assert server_app.test_client().get("/portal/link/not-a-link").status_code == 403
    assert worked_out == []


def test_two_backups_asked_for_at_once_make_one_good_zip(server_app):
    import zipfile

    from property_manager.startup import backup_if_due

    later = datetime.now() + timedelta(days=2)
    askers = [threading.Thread(target=backup_if_due, args=(server_app, later)) for _ in range(5)]
    for asker in askers:
        asker.start()
    for asker in askers:
        asker.join()
    zips = list_backups(server_app.config["DATA_PATHS"].backups)
    assert len(zips) == 2  # the one made at startup, and one more
    for one in zips:
        with zipfile.ZipFile(one) as backup_zip:
            assert backup_zip.testzip() is None


def test_a_full_disk_on_her_own_computer_is_not_an_alert(client, monkeypatch):
    # With run.py --local, a fairly full disk is normal and not the app's business.
    monkeypatch.setattr(alerts.shutil, "disk_usage", lambda _path: DiskUsage(100, 90, 10))
    assert "disk is" not in client.get("/").get_data(as_text=True)


# ---------- The relay ----------


def test_pages_come_from_the_server_through_the_relay(server_address):
    page = relay_client(server_address).get("/").get_data(as_text=True)
    assert "OwlNest" in page
    assert "Close the app" in page


def test_records_saved_through_the_relay_are_on_the_server(server_address, with_key):
    browser = relay_client(server_address)
    property_id = add_property(browser, "Elm St")
    page = with_key.get(f"/properties/{property_id}").get_data(as_text=True)
    assert "Elm St" in page


def test_a_status_picked_on_a_list_is_saved_through_the_relay(server_address, with_key):
    browser = relay_client(server_address)
    property_id = add_property(browser, "Elm St")
    browser.post("/maintenance/new", data={"property_id": str(property_id), "title": "Leaky sink", "category": "Repair & Maintenance",
                                           "status": "Open", "reported_on": "2026-10-29"}, content_type="multipart/form-data")
    # (status.js sends it the way a browser sends a FormData: as multipart/form-data.)
    answer = browser.post("/maintenance/1/status", data={"status": "Closed", "stay_on_page": "1"},
                          content_type="multipart/form-data")
    assert answer.status_code == 204
    assert "Leaky sink" in with_key.get("/maintenance/?show=Closed").get_data(as_text=True)


def test_files_go_up_and_come_back_through_the_relay(server_address):
    browser = relay_client(server_address)
    lease_id = add_lease(browser, add_property(browser, "Elm St"))
    page = browser.post(
        f"/files/lease/{lease_id}", data={"files": (BytesIO(PDF), "lease.pdf")}, content_type="multipart/form-data"
    ).get_data(as_text=True)
    assert "1 file was added." in page
    address = page.split('href="/files/')[1].split('"')[0]
    download = browser.get(f"/files/{address}?download=1")
    assert download.data == PDF
    assert "attachment" in download.headers["Content-Disposition"]


def test_the_relay_refuses_other_websites(server_address, with_key):
    browser = relay_client(server_address)
    assert browser.get("/", base_url="http://evil.example").status_code == 400
    response = browser.post(
        "/properties/new", data={"nickname": "Elm St"}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert "Elm St" not in with_key.get("/properties/").get_data(as_text=True)


def test_the_relay_answers_health_checks_itself_and_explains_when_offline():
    browser = relay_client(nowhere())
    health = browser.get("/health")
    assert health.get_data(as_text=True) == HEALTH_TEXT
    response = browser.get("/")
    assert response.status_code == 502
    assert "Can't reach your records right now" in response.get_data(as_text=True)


def test_the_relay_explains_a_wrong_key(server_address):
    response = relay_client(server_address, key="not-the-key").get("/")
    assert response.status_code == 403
    assert "isn't allowed in" in response.get_data(as_text=True)


def test_closing_through_the_relay_closes_the_app_even_when_offline(server_address):
    closed = []
    page = relay_client(server_address, on_close=lambda: closed.append(1)).post("/shutdown")
    assert "is closed" in page.get_data(as_text=True)
    page = relay_client(nowhere(), on_close=lambda: closed.append(2)).post("/shutdown")
    assert page.status_code == 200 and "is closed" in page.get_data(as_text=True)
    assert closed == [1, 2]


def test_the_relay_refuses_files_too_big_for_any_page(server_address, monkeypatch):
    monkeypatch.setattr(relay, "LARGEST_UPLOAD", 1000)  # instead of sending 200 MB in a test
    big = BytesIO(b"x" * 2000)
    response = relay_client(server_address).post(
        "/files/lease/1", data={"files": (big, "big.pdf")}, content_type="multipart/form-data"
    )
    assert response.status_code == 413


def test_several_files_bigger_than_other_uploads_get_through(server_address):
    """Attached files may add up to 200 MB in one upload; other pages allow 21 MB."""
    browser = relay_client(server_address)
    lease_id = add_lease(browser, add_property(browser, "Elm St"))
    pdf = b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024)  # two of these make 22 MB
    files = [(BytesIO(pdf), "page1.pdf"), (BytesIO(pdf), "page2.pdf")]
    page = browser.post(
        f"/files/lease/{lease_id}", data={"files": files}, content_type="multipart/form-data"
    ).get_data(as_text=True)
    assert "2 files were added" in page


# ---------- Backup copies on each computer ----------


def test_each_computer_keeps_a_copy_of_the_newest_backup(server_address, server_app, tmp_path):
    folder = tmp_path / "Property Manager Backups"
    connection = Connection(server_address, KEY)

    message = copy_newest_backup(connection, folder)
    assert "Saved a copy" in message
    copies = list_backups(folder)
    newest_on_server = list_backups(server_app.config["DATA_PATHS"].backups)[0]
    assert [copy.name for copy in copies] == [newest_on_server.name]
    assert copies[0].read_bytes() == newest_on_server.read_bytes()

    # Already copied today: nothing more is downloaded.
    assert copy_newest_backup(connection, folder) == ""
    assert len(list_backups(folder)) == 1


def test_the_server_knows_when_each_computer_last_saved_a_copy(server_address, with_key, tmp_path, monkeypatch):
    # A brand-new server: no computer has saved a copy yet.
    assert "No computer has saved a backup copy yet" in with_key.get("/").get_data(as_text=True)

    monkeypatch.setattr(relay.platform, "node", lambda: "Cindys-iMac.local")
    copy_newest_backup(Connection(server_address, KEY), tmp_path / "copies")

    assert "No computer has saved a backup copy" not in with_key.get("/").get_data(as_text=True)
    assert "<strong>Cindys-iMac</strong>: last copy saved" in with_key.get("/settings/").get_data(as_text=True)


def test_a_computer_with_a_recent_copy_says_so_without_downloading(server_address, with_key, tmp_path, monkeypatch):
    """Such as right after the server has started over: the alert shouldn't wait a day."""
    folder = tmp_path / "copies"
    folder.mkdir()
    made = datetime.now() - timedelta(hours=3)
    (folder / f"backup_{made:%Y-%m-%d_%H-%M-%S}.zip").write_bytes(b"a copy from earlier today")
    monkeypatch.setattr(relay.platform, "node", lambda: "Cindys-iMac.local")

    assert copy_newest_backup(Connection(server_address, KEY), folder) == ""  # nothing downloaded
    assert len(list_backups(folder)) == 1
    assert "No computer has saved a backup copy" not in with_key.get("/").get_data(as_text=True)
    assert "Cindys-iMac" in with_key.get("/settings/").get_data(as_text=True)


def test_a_week_without_copies_is_an_urgent_alert(server_app, with_key):
    from property_manager.database import db_session
    from property_manager.models import BackupCopy

    with server_app.app_context():
        db_session.add(BackupCopy(computer="Cindys-iMac", last_copied_at=datetime.now() - timedelta(days=8)))
        db_session.commit()
    page = with_key.get("/").get_data(as_text=True)
    assert "No computer has saved a backup copy since" in page
    assert "alert-pill urgent" in page


def test_unchanged_files_get_a_short_answer_through_the_relay(server_address):
    """Browsers ask "has style.css changed?"; the answer "no" (304) has no body and must still get through."""
    browser = relay_client(server_address)
    first = browser.get("/static/style.css")
    again = browser.get("/static/style.css", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    assert again.data == b""


def test_each_computer_removes_old_copies_too(server_address, tmp_path):
    folder = tmp_path / "Property Manager Backups"
    folder.mkdir()
    for day in range(1, 31):
        (folder / f"backup_2020-01-{day:02d}_09-00-00.zip").write_bytes(b"old")
    (folder / "notes.txt").write_text("not a backup")

    copy_newest_backup(Connection(server_address, KEY), folder)

    copies = list_backups(folder)
    assert len(copies) == 1 and not copies[0].name.startswith("backup_2020")  # the old ones are too old to keep
    assert (folder / "notes.txt").exists()  # other files are never touched


# ---------- connection.json and the app files ----------


def test_connection_file_is_read(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"server": "https://example.onrender.com/", "key": KEY}))
    assert load_connection(path) == Connection("https://example.onrender.com", KEY)


@pytest.mark.parametrize(
    "contents, message",
    [
        (None, "isn't connected to the server"),
        ("not json", "couldn't be read"),
        (json.dumps({"key": KEY}), "doesn't say where the server is"),
        (json.dumps({"server": "https://example.onrender.com"}), "doesn't contain the key"),
        # The key must never travel unencrypted over the internet.
        (json.dumps({"server": "http://example.onrender.com", "key": KEY}), "must start with https://"),
    ],
)
def test_a_missing_or_wrong_connection_file_is_explained(tmp_path, contents, message):
    path = tmp_path / "connection.json"
    if contents is not None:
        path.write_text(contents)
    with pytest.raises(ConnectionProblem, match=message):
        load_connection(path)


def test_the_app_files_have_what_a_computer_needs_and_nothing_private(tmp_path):
    zip_path = make_app_package.make_package(Connection("https://example.onrender.com", KEY), tmp_path / "app.zip")

    with zipfile.ZipFile(zip_path) as package:
        names = package.namelist()
        connection = json.loads(package.read("PropertyManager/connection.json"))
        setup = package.getinfo("PropertyManager/Set Up Property Manager.command")
    assert connection == {"server": "https://example.onrender.com", "key": KEY}
    assert "PropertyManager/run.py" in names
    assert "PropertyManager/property_manager/relay.py" in names
    assert "PropertyManager/property_manager/templates/base.html" in names
    assert setup.external_attr >> 16 & 0o111  # can still be double-clicked on a Mac
    assert not [name for name in names if name.endswith((".xlsx", ".pyc")) or "/tests/" in name or "/.venv/" in name]


def test_new_keys_are_long_and_different():
    first, second = make_app_package.new_key(), make_app_package.new_key()
    assert first != second
    assert len(first) >= server.SHORTEST_KEY
