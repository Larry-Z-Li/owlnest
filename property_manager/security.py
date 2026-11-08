"""Safety checks on every request.

On the server, every request needs one of these:

* The secret key, in the KEY_HEADER header. Only the app installed on her
  computers sends it (see relay.py).
* A browser that has signed in with a name and password (see accounts.py).
  Anyone else is sent to the sign-in page, which shows nothing of her records.

The exceptions, open to anyone:

* /health, which Render uses to check that the server is up. It shows nothing but a short text.
* The tenant portal (/portal/...), which tenants open on their phones. It checks
  each tenant's personal link itself (see views/portal.py).
* /static/..., the stylesheet and icon.
* The sign-in page itself.

Forms (anything that changes records) are only accepted from the app's own pages,
which stops another website from quietly submitting a form while she browses.
(The app on her computer checks this itself, below.)

On her computer (relay.py, or run.py --local), the app only listens on 127.0.0.1.
These checks stop web pages from other sites (opened in the same browser) from
reaching it:

1. Only requests addressed to 127.0.0.1 or localhost are accepted. This blocks a
   trick called "DNS rebinding", where a web page pretends to be this computer.
2. Form submissions are only accepted from the app's own pages, as above.
3. Other sites may not show the app's pages inside their own page.
"""

import hmac
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import Flask, Response, abort, current_app, g, redirect, request, url_for

from . import accounts
from .config import KEY_HEADER
from .database import db_session

ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}

# Requests that only read pages. Everything else (POST) changes something.
READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

# How often a signed-in browser's cookie (and its "last used" time) is renewed.
RENEW_SIGN_IN_EVERY = timedelta(hours=1)


def init_app(app: Flask) -> None:
    app.before_request(check_request)
    app.after_request(add_safety_headers)
    app.after_request(_renew_sign_in)


def check_request():
    """Who is asking, saved in g.access: "key" (the app on a computer), "account" (signed in
    in a browser; g.sign_in says who), or "local" (run.py --local). Not set for pages anyone can open."""
    server_key = current_app.config.get("SERVER_KEY")
    if server_key:
        return _check_server_access(server_key)
    problem = browser_problem(
        request.host, request.method, request.headers.get("Origin"), request.headers.get("Referer")
    )
    if problem:
        abort(problem)
    g.access = "local"
    return None


def _check_server_access(server_key: str):
    """On the server: turn away anyone without the key or a sign-in (except for the pages listed above)."""
    if request.endpoint == "home.health":
        return None
    # (Decided by the page the address leads to, not by how the address starts, so an
    # address like /portal/../settings can't sneak past. An address under /portal that
    # leads to no page at all gets the portal's "couldn't find that page".)
    portal_page = request.blueprint == "portal" or (request.endpoint is None and is_portal_path(request.path))
    if portal_page or request.endpoint == "static" or request.blueprint == "sign_in":
        _refuse_forms_from_elsewhere()
        return None

    given = request.headers.get(KEY_HEADER)
    if given is not None:
        # compare_digest takes the same time whether the key is nearly right or completely wrong,
        # so the time it takes gives nothing away.
        if not hmac.compare_digest(given.encode(), server_key.encode()):
            return Response("Unauthorized", 401, mimetype="text/plain")
        g.access = "key"
        return None

    sign_in = accounts.sign_in_for(request.cookies.get(accounts.COOKIE), datetime.now())
    if sign_in is None:
        # Not signed in (or no longer): go to the sign-in page, then come back here.
        wanted = request.full_path.rstrip("?") if request.method in READ_ONLY_METHODS else None
        return redirect(url_for("sign_in.page", next=wanted if wanted != "/" else None), 303)
    g.access = "account"
    g.sign_in = sign_in
    _refuse_forms_from_elsewhere()
    return None


def _refuse_forms_from_elsewhere() -> None:
    if form_from_elsewhere(request.host, request.method, request.headers.get("Origin"), request.headers.get("Referer")):
        abort(403)


def _renew_sign_in(response):
    """Keep a browser that's in use signed in: STAYS_SIGNED_IN_FOR counts from when it was last used."""
    sign_in = g.get("sign_in")
    now = datetime.now()
    if sign_in is not None and now - sign_in.last_used_at > RENEW_SIGN_IN_EVERY:
        try:
            sign_in.last_used_at = now
            db_session.commit()
        except Exception:
            db_session.rollback()  # (for example, it was signed out on another device meanwhile)
            return response
        accounts.put_cookie(response, request.cookies[accounts.COOKIE])
    return response


def is_portal_path(path: str) -> bool:
    """True for the tenant portal's pages."""
    return path == "/portal" or path.startswith("/portal/")


def browser_problem(host: str, method: str, origin: str | None, referer: str | None) -> int | None:
    """Checks 1 and 2 above, for a request from a browser on her computer.

    Returns None if the request is fine, or the error code to refuse it with (400 or 403).
    """
    # 1. Refuse requests addressed to any other name, such as a look-alike website.
    if urlsplit("//" + host).hostname not in ALLOWED_HOSTNAMES:
        return 400
    if form_from_elsewhere(host, method, origin, referer):
        return 403
    return None


def form_from_elsewhere(host: str, method: str, origin: str | None, referer: str | None) -> bool:
    """Check 2 above: True for a form submitted from another website.

    Browsers say which site a form came from, in the Origin header (or, failing
    that, the Referer header). (Programs that aren't browsers, such as the tests,
    send neither; they're allowed.)
    """
    if method in READ_ONLY_METHODS:
        return False
    source = origin or referer
    return source is not None and urlsplit(source).netloc != host


def add_safety_headers(response):
    # 3. Don't let other sites show the app's pages inside their own page.
    # (setdefault: the tenant portal sets stricter rules of its own, which are kept.)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    # Browsers must not guess file types (this matters for uploaded receipts).
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response
