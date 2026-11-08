"""Running without a window: daily backups, the Close button, and the Mac app icon."""

import plistlib
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import install_launcher
from property_manager.backup import list_backups
from property_manager.startup import backup_if_due


def test_a_daily_backup_is_made_only_when_the_last_one_is_a_day_old(app):
    backups = app.config["DATA_PATHS"].backups
    started = datetime.now()
    assert len(list_backups(backups)) == 1  # the backup made when the app started

    assert backup_if_due(app, now=started + timedelta(hours=23)) is None
    assert len(list_backups(backups)) == 1

    result = backup_if_due(app, now=started + timedelta(hours=25))
    assert result.succeeded
    # (The older one may or may not be kept, depending on which days these are: see backup.backups_to_keep.)
    assert list_backups(backups)[0] == result.zip_path
    assert app.config["LAST_BACKUP_RESULT"] is result


def test_close_button_only_appears_when_the_app_can_be_closed(client, app):
    assert "Close the app" not in client.get("/").get_data(as_text=True)
    assert client.post("/shutdown").status_code == 404

    app.config["STOP_SERVER"] = lambda: None
    assert "Close the app" in client.get("/").get_data(as_text=True)


def test_closing_stops_the_server_and_says_so(client, app):
    stopped = []
    app.config["STOP_SERVER"] = lambda: stopped.append(True)

    page = client.post("/shutdown").get_data(as_text=True)

    assert stopped == [True]
    assert "OwlNest is closed" in page
    assert "Everything you entered is saved." in page


def test_other_websites_cannot_close_the_app(client, app):
    stopped = []
    app.config["STOP_SERVER"] = lambda: stopped.append(True)
    response = client.post("/shutdown", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert stopped == []


def test_applescript_strings_are_escaped():
    assert install_launcher.applescript_string('say "hi" \\ bye') == '"say \\"hi\\" \\\\ bye"'


@pytest.mark.skipif(sys.platform != "darwin", reason="The app icon is built with macOS tools.")
def test_mac_app_is_built_with_the_house_icon(tmp_path):
    app = install_launcher.build_mac_app(tmp_path, {"PROPERTY_MANAGER_PORT": "8799"})

    resources = app / "Contents" / "Resources"
    assert (resources / "applet.icns").exists()
    assert (resources / install_launcher.MARKER_FILE).exists()
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["OSAAppletStayOpen"] is True  # stays in the Dock while the app runs
    assert "CFBundleIconName" not in info  # so the house icon is used
    assert info["CFBundleIdentifier"] == "local.property-manager.launcher"
    subprocess.run(["codesign", "--verify", str(app)], check=True)

    script = subprocess.run(
        ["osadecompile", str(resources / "Scripts" / "main.scpt")], capture_output=True, text=True, check=True
    ).stdout
    assert "run.py --background" in script
    assert str(install_launcher.CODE_FOLDER) in script
    assert "http://127.0.0.1:8799/shutdown" in script

    # Building again replaces the app it made...
    install_launcher.build_mac_app(tmp_path, {"PROPERTY_MANAGER_PORT": "8799"})


@pytest.mark.skipif(sys.platform != "darwin", reason="The app icon is built with macOS tools.")
def test_an_app_it_did_not_make_is_never_replaced(tmp_path):
    other_app = tmp_path / f"{install_launcher.APP_NAME}.app"
    (other_app / "Contents").mkdir(parents=True)

    with pytest.raises(SystemExit) as error:
        install_launcher.build_mac_app(tmp_path)
    assert "already a different app" in str(error.value)
    assert other_app.exists()


def test_every_page_shows_the_apps_name(client):
    page = client.get("/properties/").get_data(as_text=True)
    assert "<title>Properties · OwlNest</title>" in page
    assert 'src="/static/logo.svg" alt="OwlNest"' in page  # the owl and the name, as one picture
    assert 'rel="icon" href="/static/icon.png"' in page


@pytest.mark.skipif(sys.platform != "darwin", reason="The app icon is built with macOS tools.")
def test_the_icon_has_the_new_name_and_the_old_one_is_removed(tmp_path):
    old_app = install_launcher.build_mac_app(tmp_path)  # stands in for an icon made before the rename
    old_app.rename(tmp_path / "Property Manager.app")

    app = install_launcher.build_mac_app(tmp_path)
    install_launcher.remove_old_mac_icon(tmp_path, desktop=False)

    assert app.name == "OwlNest.app"
    assert not (tmp_path / "Property Manager.app").exists()

    # So is one from when it was called 我妈的豆腐账.
    other_old_app = install_launcher.build_mac_app(tmp_path)
    other_old_app.rename(tmp_path / "我妈的豆腐账.app")
    install_launcher.remove_old_mac_icon(tmp_path, desktop=False)
    assert not (tmp_path / "我妈的豆腐账.app").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="The app icon is built with macOS tools.")
def test_an_old_named_app_it_did_not_make_is_left_alone(tmp_path):
    someone_elses = tmp_path / "Property Manager.app" / "Contents"
    someone_elses.mkdir(parents=True)
    install_launcher.remove_old_mac_icon(tmp_path, desktop=False)
    assert someone_elses.exists()


def test_the_logo_is_white_and_gold_and_needs_no_font():
    logo = (Path(install_launcher.__file__).parent / "property_manager" / "static" / "logo.svg").read_text()
    colors = set(re.findall(r"#[0-9a-fA-F]{3,8}", logo))
    assert colors == {"#ffffff", "#f2c14e", "#0f2d52"} and "<text" not in logo and "font" not in logo
    owl_word, nest_word = re.findall(r'<path d="[^"]{200,}" fill="(#[0-9a-f]+)"/>', logo)
    assert (owl_word, nest_word) == ("#ffffff", "#f2c14e")
