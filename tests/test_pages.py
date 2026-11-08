"""The Home and Settings pages, and the safety checks on every request."""

import re
from property_manager.backup import list_backups
from property_manager.config import HEALTH_TEXT
from property_manager.database import get_settings


def test_home_page_shows_the_last_backup(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Your records were last backed up on" in response.data
    assert b"No backup has worked in the last 7 days" not in response.data


def test_health_page_lets_the_launcher_find_the_app(client):
    response = client.get("/health")
    assert response.get_data(as_text=True) == HEALTH_TEXT


def test_settings_page_opens(client):
    response = client.get("/settings/")
    assert response.status_code == 200
    assert b"Back up now" in response.data


def test_saving_her_details(client, app):
    response = client.post(
        "/settings/",
        data={"business_name": "Jane Doe Rentals", "mailing_address": "1 Main St\nSpringfield", "phone": "555-0100"},
        follow_redirects=True,
    )
    assert b"Your details were saved." in response.data
    with app.app_context():
        settings = get_settings()
        assert (settings.business_name, settings.phone) == ("Jane Doe Rentals", "555-0100")


def test_problems_are_shown_next_to_the_field_and_nothing_is_saved(client, app):
    response = client.post("/settings/", data={"business_name": "Changed name", "phone": "5" * 60})
    assert response.status_code == 400
    assert b"This is too long" in response.data
    # What she typed is still filled in.
    assert b'value="Changed name"' in response.data
    with app.app_context():
        assert get_settings().business_name == ""


def test_back_up_now_makes_a_new_backup(client, app):
    backups_folder = app.config["DATA_PATHS"].backups
    before = list_backups(backups_folder)[0]

    response = client.post("/settings/backup-now", follow_redirects=True)

    assert b"Backup saved." in response.data
    assert list_backups(backups_folder)[0] != before


def test_choosing_a_backup_folder_copies_a_backup_there(client, app, tmp_path):
    dropbox = tmp_path / "Dropbox"
    dropbox.mkdir()
    folder = dropbox / "Property Manager Backups"

    response = client.post(
        "/settings/backup-folder", data={"backup_folder": str(folder)}, follow_redirects=True
    )

    assert b"every backup is also copied to" in response.data
    assert len(list(folder.glob("backup_*.zip"))) == 1
    with app.app_context():
        assert get_settings().backup_folder == str(folder)

    # The next backup is copied there too.
    client.post("/settings/backup-now")
    assert (folder / list_backups(app.config["DATA_PATHS"].backups)[0].name).exists()


def test_backup_folder_that_does_not_exist_is_refused(client, app, tmp_path):
    response = client.post(
        "/settings/backup-folder", data={"backup_folder": str(tmp_path / "nowhere" / "Backups")}
    )
    assert response.status_code == 400
    assert b"couldn&#39;t find the folder" in response.data
    with app.app_context():
        assert get_settings().backup_folder == ""


def test_backups_go_to_the_standard_second_folder_without_any_setup(app, tmp_path):
    # During tests, the standard folder is next to the test data folder (see config.py).
    standard = tmp_path / "Property Manager Backups"
    startup_zip = app.config["LAST_BACKUP_RESULT"].zip_path
    assert (standard / startup_zip.name).exists()


def test_she_can_go_back_to_the_standard_second_folder(client, app, tmp_path):
    client.post("/settings/backup-folder", data={"backup_folder": str(tmp_path / "Dropbox")})
    response = client.post(
        "/settings/backup-folder", data={"backup_folder": ""}, follow_redirects=True
    )
    assert b"every backup is also copied to" in response.data
    assert b"the standard folder" in response.data
    with app.app_context():
        assert get_settings().backup_folder == ""


def test_requests_addressed_to_other_names_are_refused(client):
    response = client.get("/", base_url="http://evil.example")
    assert response.status_code == 400


def test_forms_sent_from_other_websites_are_refused(client, app):
    backups_folder = app.config["DATA_PATHS"].backups
    before = len(list_backups(backups_folder))

    response = client.post("/settings/backup-now", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    response = client.post("/settings/backup-now", headers={"Referer": "https://evil.example/x"})
    assert response.status_code == 403
    assert len(list_backups(backups_folder)) == before


def test_forms_sent_from_the_apps_own_pages_are_accepted(client):
    response = client.post("/settings/backup-now", headers={"Origin": "http://localhost"})
    assert response.status_code == 200


def test_fonts_are_kept_by_the_browser_but_the_styles_are_checked_each_time(client):
    font = client.get("/static/fonts/plus-jakarta-sans-latin.woff2")
    assert font.status_code == 200
    assert "max-age=31536000" in font.headers["Cache-Control"]
    assert "max-age=31536000" not in client.get("/static/style.css").headers.get("Cache-Control", "")


def test_pages_cannot_be_shown_inside_other_websites(client):
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "DENY"


def test_unknown_page_shows_a_friendly_message(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert b"We couldn&#39;t find that page" in response.data


def test_screen_files_dont_share_names_with_the_apps_other_files():
    """A screen named like another file (views/reports.py and reports.py) hides that file from the rest of the app."""
    from pathlib import Path

    import property_manager

    package = Path(property_manager.__file__).parent
    screens = {file.stem for file in (package / "views").glob("*.py")} - {"__init__"}
    others = {file.stem for file in package.glob("*.py")} - {"__init__"}
    assert screens & others == set()


def test_help_opens_in_both_languages_with_the_same_sections(client, app):
    import re
    from pathlib import Path

    templates = Path(app.root_path) / "templates"

    def section_ids(name):
        return re.findall(r'<section class="card" id="([a-z-]+)"', (templates / name).read_text())

    assert section_ids("help.html") == section_ids("help_zh.html")
    assert len(section_ids("help.html")) == 17
    assert client.get("/help/?lang=en").status_code == 200
    assert "租客页面" in client.get("/help/?lang=zh").get_data(as_text=True)


def test_every_page_has_the_same_width(client):
    style = client.get("/static/style.css").get_data(as_text=True)
    assert "main {\n  max-width: 1200px;" in style
    # The logo and menus line up with it too.
    for bar in (".topbar-row", ".main-nav"):
        assert re.search(re.escape(bar) + r" \{[^}]*max-width: 1200px", style), bar
    # (A page of its own width is what made the tabs jump about.)
    assert not re.search(r"main\.[a-z-]+ \{[^}]*max-width", style)
