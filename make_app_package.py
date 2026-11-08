"""Makes the app files to send to a computer that may use the app.

    .venv/bin/python make_app_package.py --server https://property-manager-ab12.onrender.com --key KEY

This saves the server's address and its key (PROPERTY_MANAGER_KEY, from Render's
Environment page) in connection.json in this folder, checks that the server
accepts the key, and makes app-package/OwlNest.zip.

Anyone who has the zip can open her records, so send it only to the computers that
may use the app, in a private way (AirDrop, a USB stick, or a private message;
not a shared or public link). On that computer, unzip it and follow "One-time
setup" in the README inside.

Running it again without --server and --key uses the saved connection.json.

    .venv/bin/python make_app_package.py --new-key

just prints a new random key, for changing the key (see README, "If a computer is lost").
"""

import argparse
import http.client
import json
import secrets
import sys
import time
import zipfile
from pathlib import Path

from property_manager.config import APP_NAME, CODE_FOLDER, CONNECTION_FILE, CONNECTION_FILE_NAME, HEALTH_TEXT
from property_manager.relay import Connection, ConnectionProblem, check_server_address, load_connection

PACKAGE_FOLDER = CODE_FOLDER / "app-package"
# The folder the zip unpacks into.
FOLDER_IN_ZIP = "PropertyManager"

# What goes in the zip: only what a computer needs to run the app. Left out: the tests,
# her spreadsheets, the server's files, and .venv (it only works on the computer that made it).
INCLUDED = [
    "property_manager",
    "run.py",
    "install_launcher.py",
    "requirements.txt",
    "README.md",
    "Set Up Property Manager.command",
    "Set Up Property Manager.bat",
    "Start Property Manager.command",
    "Start Property Manager.bat",
]
LEFT_OUT_NAMES = {"__pycache__", ".DS_Store"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", help="The server's address, such as https://property-manager-ab12.onrender.com")
    parser.add_argument("--key", help="The server's key: PROPERTY_MANAGER_KEY on Render's Environment page.")
    parser.add_argument("--new-key", action="store_true", help="Just print a new random key.")
    parser.add_argument("--no-check", action="store_true", help="Don't check that the server accepts the key.")
    options = parser.parse_args()

    if options.new_key:
        print(new_key())
        return 0

    if options.server or options.key:
        if not (options.server and options.key):
            parser.error("give both --server and --key")
        connection = Connection(server=options.server.strip().rstrip("/"), key=options.key.strip())
        problem = check_server_address(connection.server)
        if problem:
            parser.error(problem)
        save_connection(connection, CONNECTION_FILE)
        print(f"Saved the server's address and key in {CONNECTION_FILE}.")
    elif CONNECTION_FILE.exists():
        try:
            connection = load_connection(CONNECTION_FILE)
        except ConnectionProblem as problem:
            parser.error(str(problem))
        print(f"Using the server and key saved in {CONNECTION_FILE}.")
    else:
        parser.error("give --server and --key (the first time)")

    if not options.no_check:
        print(f"Checking {connection.server} ...")
        problem = check_server(connection)
        if problem:
            print(f"Stopped: {problem}")
            return 1
        print("The server is working and accepts the key.")

    package = make_package(connection, PACKAGE_FOLDER / f"{APP_NAME}.zip")
    print()
    print(f"Made {package}")
    print("Send it only to computers that may use the app: anyone who has it can open her records.")
    print(f"(This folder's {CONNECTION_FILE.name} also connects the app on this computer to the server.)")
    return 0


def new_key() -> str:
    """A long random key that can't be guessed (43 letters, numbers, - and _)."""
    return secrets.token_urlsafe(32)


def save_connection(connection: Connection, path: Path) -> None:
    path.write_text(connection_text(connection), encoding="utf-8")
    path.chmod(0o600)  # only this computer's user may read the key


def connection_text(connection: Connection) -> str:
    return json.dumps({"server": connection.server, "key": connection.key}, indent=2) + "\n"


def check_server(connection: Connection) -> str:
    """Return "" if the server answers and accepts the key, or what's wrong."""
    try:
        conn, answer = connection.open("GET", "/health")
        text = answer.read().decode(errors="replace")
        conn.close()
        if answer.status != 200 or text != HEALTH_TEXT:
            return f"{connection.server} answered, but it isn't the app's server (or it's still starting)."

        conn, answer = connection.open("GET", "/")
        answer.read()
        conn.close()
    except (OSError, http.client.HTTPException) as error:
        return f"couldn't reach {connection.server} ({error})."
    if answer.status == 401:
        return "the server didn't accept the key. Copy PROPERTY_MANAGER_KEY from Render's Environment page again."
    if answer.status != 200:
        return f"the server answered with an error ({answer.status}). Check its log on Render."
    return ""


def make_package(connection: Connection, zip_path: Path) -> Path:
    """Write the zip: the files in INCLUDED, plus connection.json. Returns where it was saved."""
    zip_path.parent.mkdir(exist_ok=True)
    partial = zip_path.with_name(zip_path.name + ".partial")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as package:
        for name in INCLUDED:
            path = CODE_FOLDER / name
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            for file in files:
                parts = file.relative_to(CODE_FOLDER).parts
                if file.is_file() and not LEFT_OUT_NAMES.intersection(parts) and file.suffix != ".pyc":
                    _add(package, file.read_bytes(), "/".join(parts), file.stat().st_mode)
        _add(package, connection_text(connection).encode(), CONNECTION_FILE_NAME, 0o600)
    partial.replace(zip_path)
    return zip_path


def _add(package: zipfile.ZipFile, data: bytes, name: str, mode: int) -> None:
    info = zipfile.ZipInfo(f"{FOLDER_IN_ZIP}/{name}", date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    # Keep each file's permissions, so the Mac .command files can still be double-clicked.
    info.external_attr = (0o100000 | (mode & 0o777)) << 16
    package.writestr(info, data)


if __name__ == "__main__":
    sys.exit(main())
