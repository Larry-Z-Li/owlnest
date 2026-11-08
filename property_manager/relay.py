"""The app on each computer: passes pages between her browser and the server.

Her records live on the server (server.py). On each computer, run.py starts this
small "relay" at http://127.0.0.1:8642/ and opens the browser there. For every
page she opens, the relay:

1. Checks the request came from the app's own pages (the same checks as in
   security.py), because other websites open in her browser could try to use it.
2. Sends it on to the server over an encrypted (HTTPS) connection, adding the
   secret key that lets it in.
3. Passes the server's answer back to the browser.

So the pages look the same as when the app ran on her computer, and the key
never reaches the browser. The relay also saves a copy of the newest backup from
the server once a day (see copy_newest_backup).

connection.json, in the code folder, tells the relay where the server is and holds
the key. make_app_package.py writes it:

    {"server": "https://property-manager-ab12.onrender.com", "key": "..."}
"""

import http.client
import json
import platform
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import Message
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import certifi
from werkzeug.datastructures import Headers
from werkzeug.wrappers import Request, Response

from . import attachments, backup
from .config import APP_NAME, CONNECTION_FILE, HEALTH_TEXT, KEY_HEADER
from .errors import MESSAGES
from .security import browser_problem

# How long to wait for the server: to connect, and then for each part of its answer.
# (Some pages, such as a big export, take a while to make.)
CONNECT_SECONDS = 20
ANSWER_SECONDS = 300

# The largest upload any page accepts: several attached files at once. (Other pages
# accept less, and the server itself refuses anything bigger than each page allows.)
LARGEST_UPLOAD = attachments.MAX_UPLOAD_BYTES

# The parts of a request (headers) passed on to the server, and of its answer passed back.
# Anything else, such as headers about the connection itself, stays behind.
REQUEST_HEADERS = {
    "accept", "accept-encoding", "accept-language", "content-type", "cookie",
    "if-modified-since", "if-none-match", "range", "user-agent",
}
ANSWER_HEADERS = {
    "accept-ranges", "cache-control", "content-disposition", "content-encoding", "content-length",
    "content-range", "content-security-policy", "content-type", "etag", "expires", "last-modified",
    "location", "referrer-policy", "set-cookie", "vary", "x-content-type-options", "x-frame-options",
}

# The app on a computer keeps a copy of the server's newest backup at least this often.
COPY_EVERY = timedelta(days=1)

_ASK_FOR_NEW_FILES = "Ask whoever looks after the app for new app files."


class ConnectionProblem(Exception):
    """connection.json is missing or wrong. The message says so in words she can read."""


@dataclass(frozen=True)
class Connection:
    """Where the server is (such as https://property-manager-ab12.onrender.com), and the key that lets the app in."""

    server: str
    key: str

    def open(self, method: str, target: str, headers=(), body: bytes | None = None):
        """Send one request to the server, with the key. Returns (connection, answer).

        The caller reads the answer, then closes the connection. Raises OSError or
        http.client.HTTPException if the server can't be reached.
        """
        parts = urlsplit(self.server)
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=CONNECT_SECONDS, context=_secure_context()
            )
        else:  # only for testing on one computer (see check_server_address)
            conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=CONNECT_SECONDS)
        try:
            conn.connect()
            conn.sock.settimeout(ANSWER_SECONDS)
            # (skip_accept_encoding: pass on the browser's Accept-Encoding instead of adding one.)
            conn.putrequest(method, target, skip_accept_encoding=True)
            for name, value in headers:
                conn.putheader(name, value)
            conn.putheader(KEY_HEADER, self.key)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            return conn, conn.getresponse()
        except BaseException:
            conn.close()
            raise


@lru_cache(maxsize=1)
def _secure_context() -> ssl.SSLContext:
    """Checks that the server really is the server, using certifi's list of trusted certificates.

    (Python from python.org on a Mac doesn't come with such a list, so it can't check by itself.)
    """
    return ssl.create_default_context(cafile=certifi.where())


def check_server_address(server: str) -> str:
    """Return "" if the server's address can be used, or what's wrong with it."""
    parts = urlsplit(server)
    if parts.path not in ("", "/") or parts.query:
        return f"The server address should be just its name, such as https://property-manager-ab12.onrender.com, not {server}."
    if parts.scheme == "https" and parts.hostname:
        return ""
    # Plain http:// only to this same computer (for testing), because the key would travel unencrypted.
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"):
        return ""
    return f"The server address must start with https://, but it is {server}."


def load_connection(path: Path = CONNECTION_FILE) -> Connection:
    """Read connection.json. Raises ConnectionProblem if it's missing or wrong."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConnectionProblem(
            f"This copy of {APP_NAME} isn't connected to the server: the file {path.name} is missing "
            f"from its folder ({path.parent}). {_ASK_FOR_NEW_FILES}"
        ) from None
    except (OSError, ValueError) as error:
        raise ConnectionProblem(f"The file {path} couldn't be read ({error}). {_ASK_FOR_NEW_FILES}") from None

    if not isinstance(data, dict):
        data = {}
    server = str(data.get("server", "")).strip().rstrip("/")
    key = str(data.get("key", "")).strip()
    if not server:
        problem = "It doesn't say where the server is."
    elif not key:
        problem = "It doesn't contain the key."
    else:
        problem = check_server_address(server)
    if problem:
        raise ConnectionProblem(f"Something is wrong with the file {path}: {problem} {_ASK_FOR_NEW_FILES}")
    return Connection(server=server, key=key)


# ---------- Passing pages along ----------


def make_relay(connection: Connection, on_close=None):
    """Build the relay. run.py serves it at http://127.0.0.1:8642/.

    on_close is called after the "Close the app" button (or Quit in the Dock) is used.
    """

    @Request.application
    def relay(request: Request) -> Response:
        problem = browser_problem(
            request.host, request.method, request.headers.get("Origin"), request.headers.get("Referer")
        )
        if problem:
            return _message_page(problem, *MESSAGES[problem])

        # The launcher checks this to see whether the app is already running on this computer.
        if request.path == "/health":
            return Response(HEALTH_TEXT, mimetype="text/plain")

        response = _pass_to_server(connection, request)
        if request.path == "/shutdown" and request.method == "POST":
            if response.status_code != 200:
                # The server couldn't be reached, but the app on this computer still closes.
                response = _message_page(
                    200, f"{APP_NAME} is closed", "To open it again, double-click its icon on the Desktop."
                )
            if on_close:
                on_close()
        return response

    return relay


def _pass_to_server(connection: Connection, request: Request) -> Response:
    """Send the browser's request on to the server, and return the server's answer for the browser."""
    if (request.content_length or 0) > LARGEST_UPLOAD:
        return _message_page(413, *MESSAGES[413])

    has_body = request.method not in ("GET", "HEAD") or request.content_length
    body = request.get_data() if has_body else None
    headers = [(name, value) for name, value in request.headers.items() if name.lower() in REQUEST_HEADERS]
    try:
        conn, answer = connection.open(request.method, _target(request), headers, body)
    except (OSError, http.client.HTTPException):
        return _message_page(
            502,
            "Can't reach your records right now",
            "Please check that this computer is connected to the internet, then reload the page. "
            "If the internet works and this keeps happening, ask whoever looks after the app.",
        )

    if answer.status == 401:
        conn.close()
        return _message_page(
            403,
            "This copy of the app isn't allowed in",
            f"The server didn't accept this computer's key. It may have been changed. {_ASK_FOR_NEW_FILES}",
        )
    if answer.status in (502, 503, 504):
        # Render answers these while the server restarts, such as after an update.
        conn.close()
        return _message_page(
            503,
            "The app's server is restarting",
            "This usually takes a minute or two, for example after an update. "
            "Please wait a moment, then reload the page.",
        )

    if request.method == "HEAD" or answer.status in (204, 304) or answer.status < 200:
        # No page comes with these answers (304 means "the copy you have is still good"), so
        # close the connection now: the browser would never ask for the rest, which would close it.
        body = b""
        conn.close()
    else:
        body = _read_in_pieces(answer, conn)
    response = Response(body, status=answer.status, direct_passthrough=True)
    response.headers = Headers(_answer_headers(answer, connection.server))
    return response


def _target(request: Request) -> str:
    """The page's address on the server, such as /tenants/3?show=all."""
    target = quote(request.path)
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")
    return target


def _answer_headers(answer, server: str) -> list[tuple[str, str]]:
    headers = []
    for name, value in answer.getheaders():
        if name.lower() not in ANSWER_HEADERS:
            continue
        # "Go to this page next" should stay on this computer's address, not the server's.
        if name.lower() == "location" and value.startswith(server):
            value = value[len(server):] or "/"
        headers.append((name, value))
    return headers


def _read_in_pieces(answer, conn):
    """Pass the server's answer on a piece at a time, so big downloads never fill the memory."""
    try:
        while piece := answer.read(64 * 1024):
            yield piece
    finally:
        conn.close()


def _message_page(status: int, title: str, text: str) -> Response:
    """A plain page for when the relay can't show the server's page. (Its look matches style.css.)"""
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · {escape(APP_NAME)}</title>
  <style>
    body {{ margin: 0; padding: 48px 16px; background: #f5f6f8; color: #1f2933;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 18px; }}
    main {{ max-width: 600px; margin: 0 auto; padding: 24px 32px; background: #ffffff;
      border: 1px solid #d3d9e0; border-radius: 10px; }}
    h1 {{ margin-top: 0; color: #1d4e89; font-size: 26px; }}
    p {{ line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(title, quote=False)}</h1>
    <p>{escape(text, quote=False)}</p>
  </main>
</body>
</html>
"""
    response = Response(page, status=status, mimetype="text/html")
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------- A copy of the newest backup on each computer ----------


def copy_newest_backup(connection: Connection, folder: Path, now: datetime | None = None) -> str:
    """Save the server's newest backup in folder, if this computer's newest copy is a day old or more.

    Keeps the same ones the server does there; only files named like backup zips are ever
    removed (see backup.remove_old_backups). Either way, tells the server when this
    computer's newest copy was made, so it can warn if copies stop (see alerts.py).
    Returns a short message for the log, or "" if nothing needed doing.
    """
    now = now or datetime.now()
    last = backup.last_successful_backup(folder) if folder.is_dir() else None
    if last is not None and now - last < COPY_EVERY:
        # (Told even though nothing was downloaded, so a server that has started
        # over knows straight away that this computer has a recent copy.)
        _tell_server_a_copy_was_saved(connection, last)
        return ""

    conn, answer = connection.open("GET", "/settings/backups/newest")
    try:
        if answer.status != 200:
            return f"Couldn't copy the newest backup from the server (it answered {answer.status})."
        disposition = Message()
        disposition["Content-Disposition"] = answer.getheader("Content-Disposition", "")
        name = Path(disposition.get_filename() or "").name
        if not backup.is_backup_name(name):
            return f"Couldn't copy the newest backup from the server (it sent a file named {name!r})."
        target = folder / name
        if target.exists():
            _tell_server_a_copy_was_saved(connection)
            return ""  # This computer already has the server's newest backup.

        folder.mkdir(parents=True, exist_ok=True)
        # Save it under another name until it's complete, so a half-downloaded file never looks like a backup.
        partial = folder / (name + ".partial")
        with open(partial, "wb") as file:
            while piece := answer.read(1024 * 1024):
                file.write(piece)
        partial.replace(target)
    finally:
        conn.close()

    backup.remove_old_backups(folder)
    _tell_server_a_copy_was_saved(connection)
    return f"Saved a copy of the server's newest backup: {target}"


def _tell_server_a_copy_was_saved(connection: Connection, copied_at: datetime | None = None) -> None:
    """Tell the server this computer has a copy, made at copied_at (just now, if not given)."""
    details = {"computer": this_computer()}
    if copied_at is not None:
        details["copied_at"] = copied_at.isoformat(timespec="seconds")
    body = urlencode(details).encode()
    conn, answer = connection.open(
        "POST", "/settings/backups/copied", [("Content-Type", "application/x-www-form-urlencoded")], body
    )
    answer.read()
    conn.close()


def this_computer() -> str:
    """This computer's name, such as "Cindys-iMac", for the list of copies in Settings."""
    return platform.node().removesuffix(".local") or "A computer"


def start_backup_copies(connection: Connection, folder: Path) -> None:
    """Copy the newest backup now (if due), then check again every hour while the app is open."""

    def copy_every_hour():
        while True:
            try:
                message = copy_newest_backup(connection, folder)
            except Exception as error:
                message = f"Couldn't copy the newest backup from the server: {error}"
            if message:
                print(f"{datetime.now():%Y-%m-%d %H:%M} {message}")
            time.sleep(60 * 60)

    threading.Thread(target=copy_every_hour, daemon=True).start()
