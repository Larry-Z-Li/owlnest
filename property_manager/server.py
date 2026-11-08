"""Runs the app on the server: the one place her records live, which every installed app talks to.

Render starts it with gunicorn (see the Dockerfile and render.yaml), which calls
build_server_app() to build the app:

    gunicorn --workers 1 --threads 8 "property_manager.server:build_server_app()"

There is only one worker (one copy of the app), because the app keeps a little
in memory (such as the last backup's result) and runs the hourly checks in the
background. Its 8 threads are plenty for two computers.

The server needs two environment variables (render.yaml and the Dockerfile set them):

* PROPERTY_MANAGER_KEY: the secret key. Every request must carry it (see security.py),
  and only the app files made by make_app_package.py contain it.
* PROPERTY_MANAGER_DATA: the data folder, on the server's disk that survives restarts.

Once the records have moved to another service, PROPERTY_MANAGER_MOVED_TO on the old one says
where to (see moved_app and the README). It then only passes people on, and leaves its disk alone.
"""

import os

from flask import Flask, redirect, request

from . import create_app
from .config import HEALTH_TEXT, SERVER_KEY_VARIABLE
from .startup import start_hourly_checks

# A short key could be guessed; Render's generated keys and make_app_package.py --new-key are much longer.
SHORTEST_KEY = 32
# On a service the records have moved away from: their new address, such as https://new-address.example.com.
MOVED_TO_VARIABLE = "PROPERTY_MANAGER_MOVED_TO"


def moved_app(new_address: str) -> Flask:
    """What's left at the old address: every page passes on to the same page at the new one.

    So tenants' links and bookmarks keep working. It doesn't open the data folder: no backups, no
    hourly checks, and nothing on the old disk changes, so it can go back if the move has to be undone.
    """
    app = Flask(__name__, static_folder=None)

    @app.get("/health")
    def health():
        return HEALTH_TEXT, 200, {"Content-Type": "text/plain"}

    @app.route("/", defaults={"path": ""}, methods=["GET", "HEAD", "POST"])
    @app.route("/<path:path>", methods=["GET", "HEAD", "POST"])
    def pass_on(path):  # noqa: ARG001 (it's in request.path)
        query = request.query_string.decode()
        # (308: a form sent here is sent there too, unchanged.)
        return redirect(f"{new_address}{request.path}{'?' + query if query else ''}", 308)

    return app


def build_server_app():
    moved_to = os.environ.get(MOVED_TO_VARIABLE, "").strip().rstrip("/")
    if moved_to:
        print(f"The records have moved to {moved_to}, so this only passes people on.")
        return moved_app(moved_to)
    key = os.environ.get(SERVER_KEY_VARIABLE, "").strip()
    if len(key) < SHORTEST_KEY:
        # Never run without a proper key: anyone on the internet could then read her records.
        raise SystemExit(
            f"The server won't start: set {SERVER_KEY_VARIABLE} to a secret key of at least "
            f"{SHORTEST_KEY} characters (see the README)."
        )
    if not os.environ.get("PROPERTY_MANAGER_DATA"):
        # Without it, records would go in the home folder, which Render wipes on every restart.
        raise SystemExit("The server won't start: set PROPERTY_MANAGER_DATA to a folder on the server's disk.")

    server_app = create_app(server_key=key)
    print(server_app.config["LAST_BACKUP_RESULT"].summary())
    start_hourly_checks(server_app)
    return server_app
