"""Starts the app (OwlNest, "Property Manager" in the code) and opens it in the web browser.

Her records live on the server. This starts the relay (see property_manager/relay.py)
at http://127.0.0.1:8642/, which passes each page between the browser and the
server. connection.json, in this folder, says where the server is.

With --local, it runs the whole app on this computer instead, with the records in
PropertyManager/data in the home folder: the way the app worked before it had a
server. It's for testing (see README, "For the person maintaining the code").

It can be started two ways:

* From the app's icon on the Desktop (the normal way). The icon runs
  "run.py --background": no window appears, messages are written to
  logs/property-manager.log in this folder, and problems are shown in a pop-up
  message. The app stops when she quits it from the Dock (Mac), clicks
  "Close the app" in the browser, or shuts down the computer.
  See install_launcher.py, which makes the icon.

* From "Start Property Manager.command" (Mac) or ".bat" (Windows). Messages are
  shown in a window, and closing that window stops the app. Useful for
  troubleshooting.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from werkzeug.serving import make_server

from property_manager import create_app, relay
from property_manager.config import APP_NAME, HEALTH_TEXT, HOST, PORT, default_second_backup_folder
from property_manager.database import StartupError
from property_manager.startup import start_hourly_checks

APP_ADDRESS = f"http://{HOST}:{PORT}/"

# Where messages go when there is no window to show them in.
LOG_FILE = Path(__file__).resolve().parent / "logs" / "property-manager.log"


def main() -> int:
    background = "--background" in sys.argv[1:]
    local = "--local" in sys.argv[1:]
    if background:
        _send_messages_to_log_file()
    else:
        # Show each message in the window straight away, not in batches.
        sys.stdout.reconfigure(line_buffering=True)

    # If she opens the app twice, just show the copy that's already running.
    if is_already_running():
        print(f"{APP_NAME} is already running. Opening it in your browser...")
        open_browser()
        return 0

    # Claim the app's port before doing anything else. If she opens the app twice at
    # once, the second copy stops here, instead of also making a backup and this
    # month's draft invoices (which would make duplicates).
    try:
        server = make_server(HOST, PORT, _starting_up, threaded=True)
    except (OSError, SystemExit):
        # (When the port is busy, werkzeug prints a message and exits instead of raising OSError.)
        # Most likely the other copy, still starting up: wait for it, then just show it.
        if wait_until_running(seconds=90):
            print(f"{APP_NAME} is already running. Opening it in your browser...")
            open_browser()
            return 0
        tell_her(
            f"{APP_NAME} couldn't start",
            f"Another program is using port {PORT}, which {APP_NAME} needs. "
            "Restarting the computer usually fixes this.",
            background,
        )
        return 1

    def stop_server():
        # Wait a moment first, so the "... is closed" page reaches the browser.
        threading.Timer(0.5, server.shutdown).start()

    print(f"Starting {APP_NAME}...")
    app = start_local_app(stop_server, background) if local else start_relay(stop_server, background)
    if app is None:
        server.server_close()
        return 1
    server.app = app  # from now on, requests go to the real app

    # Stop cleanly when the computer shuts down.
    signal.signal(signal.SIGTERM, lambda _signal, _frame: stop_server())

    # Only show problems, not a line for every page she opens.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    print()
    print("=" * 64)
    print(f"  {APP_NAME} is running.")
    print(f"  If your browser didn't open, go to: {APP_ADDRESS}")
    if not background:
        print()
        print("  Keep this window open while you use the app.")
        print("  To stop the app, close this window.")
    print("=" * 64)

    # The server is already listening, so the browser can connect straight away.
    open_browser()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print(f"{APP_NAME} has stopped.")
    return 0


def start_relay(stop_server, background: bool):
    """Start passing pages to and from the server. Returns None if this copy isn't connected to one."""
    try:
        connection = relay.load_connection()
    except relay.ConnectionProblem as problem:
        tell_her(f"{APP_NAME} couldn't start", str(problem), background)
        return None
    print(f"Your records are on the server at {connection.server}")
    # A copy of the newest backup, once a day, in Documents/Property Manager Backups.
    relay.start_backup_copies(connection, default_second_backup_folder())
    # The "Close the app" button (and the Mac Dock's Quit) stop the relay.
    return relay.make_relay(connection, on_close=stop_server)


def start_local_app(stop_server, background: bool):
    """Run the whole app on this computer (run.py --local). Returns None if it can't start."""
    print("Backing up your records first. This can take a moment.")
    try:
        app = create_app()
    except StartupError as error:
        tell_her(f"{APP_NAME} couldn't start", str(error), background)
        return None
    print(app.config["LAST_BACKUP_RESULT"].summary())
    # The "Close the app" button (and the Mac Dock's Quit) use this.
    app.config["STOP_SERVER"] = stop_server
    # A backup once a day while the app is open, and the monthly draft invoices.
    start_hourly_checks(app)
    return app


def _starting_up(environ, start_response):
    """Stands in for the app while it starts (requests wait until it's ready, so this is rarely seen)."""
    start_response("503 Service Unavailable", [("Content-Type", "text/plain; charset=utf-8")])
    return [f"{APP_NAME} is starting. Please reload in a moment.".encode()]


def wait_until_running(seconds: int) -> bool:
    """Wait up to `seconds` for another copy of the app (still starting up) to answer."""
    for _ in range(seconds):
        if is_already_running():
            return True
        time.sleep(1)
    return False


def is_already_running() -> bool:
    """True if the app is already answering at its address."""
    # Ignore any proxy settings: the app is on this computer.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(APP_ADDRESS + "health", timeout=2) as response:
            return response.read().decode() == HEALTH_TEXT
    except Exception:
        # Nothing answered (the usual case), or something else is using the port.
        return False


def open_browser() -> None:
    # Developers can set PROPERTY_MANAGER_NO_BROWSER=1 to start the app without a browser window.
    if not os.environ.get("PROPERTY_MANAGER_NO_BROWSER"):
        webbrowser.open(APP_ADDRESS)


def tell_her(title: str, message: str, background: bool) -> None:
    """Show a problem: in the window, or (with no window) in a pop-up message."""
    print()
    print(f"{title}:")
    print(message)
    if background and not os.environ.get("PROPERTY_MANAGER_NO_BROWSER"):
        show_popup(title, message)


def show_popup(title: str, message: str) -> None:
    """Show a simple message box with an OK button, using the computer's own tools."""
    try:
        if sys.platform == "darwin":
            # The text is passed in environment variables, so quotes in it can't break the script.
            script = (
                'display dialog (system attribute "PM_MESSAGE") with title (system attribute "PM_TITLE") '
                'buttons {"OK"} default button "OK" with icon caution'
            )
            subprocess.run(
                ["osascript", "-e", script],
                env={**os.environ, "PM_TITLE": title, "PM_MESSAGE": message},
                check=False,
            )
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x30)  # 0x30 = warning icon
    except Exception:
        pass  # The message is in the log file anyway.


def _send_messages_to_log_file() -> None:
    """With no window to print to, write messages (and any errors) to the log file instead."""
    LOG_FILE.parent.mkdir(exist_ok=True)
    # Keep the log file small: when it passes 1 MB, keep only its most recent half.
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
        recent = LOG_FILE.read_bytes()[-500_000:]
        LOG_FILE.write_bytes(recent)
    log = open(LOG_FILE, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = log
    print(f"\n----- {datetime.now():%Y-%m-%d %H:%M:%S} -----")


if __name__ == "__main__":
    sys.exit(main())
