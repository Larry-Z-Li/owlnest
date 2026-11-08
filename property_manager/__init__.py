"""Property Manager: a small app for managing rental properties.

create_app() builds the app. It runs on the server (server.py), where her records
live; the app on each computer passes pages to and from it (relay.py). For
testing, run.py --local runs it on one computer instead.
"""

import secrets
from datetime import date
from pathlib import Path

from flask import Flask, g, has_request_context, request

from . import alerts, errors, security, startup
from .config import APP_NAME, DataPaths, default_data_dir, public_address
from .database import db_session
from .formatting import register_template_filters
from .views import (
    NAV_ITEMS,
    NAV_PARENTS,
    UTILITY_ITEMS,
    archive,
    bank,
    files,
    help_page,
    home,
    insurances,
    leases,
    maintenance,
    notes,
    people,
    portal,
    properties,
    reminders,
    reports,
    settings,
    sign_in,
    transactions,
)


# Fonts never change, so browsers may keep them this long (a year) without asking again.
# (If a font file is ever replaced, give it a new name, or browsers keep the old one.)
FONT_CACHE_SECONDS = 365 * 24 * 60 * 60


class _App(Flask):
    def get_send_file_max_age(self, filename: str | None) -> int | None:
        """How long browsers may keep a file from static/ without asking again.

        Fonts: a year, so each page has the font at once and its text doesn't jump when
        the font arrives. Everything else is checked each time, so an update shows at once.
        """
        if filename and filename.startswith("fonts/"):
            return FONT_CACHE_SECONDS
        return super().get_send_file_max_age(filename)


def create_app(data_dir: Path | None = None, server_key: str | None = None) -> Flask:
    """Build the app.

    This also runs the startup steps: it makes a backup and creates or upgrades
    the database (see startup.py). data_dir is the data folder to use; normally
    it's left out, which means PropertyManager/data in the home folder (on the
    server, the PROPERTY_MANAGER_DATA environment variable chooses it).

    server_key is given only on the server (see server.py): every request must then
    carry this key, which only the app installed on her computers has, or come from
    a browser that has signed in (see accounts.py).
    """
    on_server = bool(server_key)
    paths = DataPaths(Path(data_dir) if data_dir else default_data_dir())
    engine, startup_backup = startup.prepare_data_folder(paths, on_server=on_server)

    app = _App(__name__)
    # Used to sign the short "Saved!" messages shown after a form is submitted.
    # A new random key each time the app starts is fine: signing in doesn't use it (see accounts.py).
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.config["SERVER_KEY"] = server_key
    app.config["ON_SERVER"] = on_server
    # The server's address on the internet, which tenants' portal links start with ("" if not on a server).
    app.config["PUBLIC_ADDRESS"] = public_address() if on_server else ""
    # The largest file she can upload (receipts and import files are up to 20 MB).
    app.config["MAX_CONTENT_LENGTH"] = 21 * 1024 * 1024
    app.config["DATA_PATHS"] = paths
    # The most recent backup's result. "Back up now" in Settings replaces it.
    app.config["LAST_BACKUP_RESULT"] = startup_backup

    # Connect the screens' database session to this data folder's app.db.
    db_session.remove()
    db_session.configure(bind=engine)

    @app.teardown_appcontext
    def _close_database_session(_error=None):
        # (A step partway through a request may open a context of its own, as "Fetch now" does. The
        # request isn't over when that one ends, and it still needs its session: it holds who's signed in.
        # Flask clears the request before closing a request's own context, so this runs at the very end.)
        if not has_request_context():
            db_session.remove()

    @app.context_processor
    def _values_for_every_page():
        if security.is_portal_path(request.path):
            # Tenants' pages (portal/base.html) use none of this, and anyone can open them,
            # so don't work out her alerts for them.
            return {}
        if not g.get("access"):
            # The sign-in page, or an error page for someone not signed in: no menus, and nothing of her records.
            return {"app_name": APP_NAME, "plain_layout": True}
        alert_count, alert_urgent = _count_alerts(app)
        signed_in = g.get("sign_in")
        return {
            "app_name": APP_NAME,
            "nav_items": NAV_ITEMS,
            "nav_parents": NAV_PARENTS,
            "utility_items": UTILITY_ITEMS,
            "alert_count": alert_count,
            "alert_urgent": alert_urgent,
            "last_backup_result": app.config["LAST_BACKUP_RESULT"],
            "on_server": on_server,
            # The "Close the app" button only works when run.py started the app, or for the app
            # on her computer (which shows the server's pages, and closes itself). Not in a web browser.
            "can_close_app": "STOP_SERVER" in app.config or g.access == "key",
            # Who is signed in, in a web browser (None for the app on a computer).
            "signed_in_as": signed_in.account.name if signed_in else None,
        }

    security.init_app(app)
    errors.init_app(app)
    register_template_filters(app)
    screens = (
        home, properties, leases, insurances, transactions, bank, maintenance, reports, files, reminders, notes, archive, settings,
        people, help_page, portal, sign_in,
    )
    for screen in screens:
        app.register_blueprint(screen.bp)
    return app


def _count_alerts(app: Flask) -> tuple[int, bool]:
    """How many alerts there are (and whether any is urgent), for the top bar of every page."""
    try:
        return alerts.count(date.today(), app.config["DATA_PATHS"], app.config["LAST_BACKUP_RESULT"])
    except Exception:
        # Never let the count stop a page (such as an error page) from showing.
        return 0, False
