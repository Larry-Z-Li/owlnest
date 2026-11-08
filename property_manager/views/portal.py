"""The tenant portal: the pages tenants open on their phones, at /portal on the server.

Each lease gets its own link when it's made (/portal/link/...; see leasing.py).
Tenants open it once; the browser then remembers it (in a cookie that lasts
about a year). While the lease is current, tenants can send maintenance requests
(with photos) and see the status of each one; once the lease has ended, the
portal shows a "sorry" page instead, until she revives the lease.

Every page checks the link again. Tenants never reach her other records: the rest
of the app needs the key that only her computers have (see security.py). These
pages show her business name from Settings, never the app's own name.
"""

from datetime import date, datetime

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .. import attachments, categories, leasing, receipts
from ..database import db_session, get_settings
from ..forms import Form
from ..models import MaintenanceRequest
from .files import owner_label

bp = Blueprint("portal", __name__, url_prefix="/portal")

COOKIE = "portal"
COOKIE_SECONDS = 400 * 24 * 60 * 60  # browsers keep a cookie for at most about 400 days

# Limits, so a link that falls into the wrong hands can't fill the server's disk.
MAX_PHOTOS = 10  # per request
REQUESTS_PER_DAY = 5  # per lease


@bp.before_request
def _find_lease():
    """Every page except the link itself needs a working link, remembered in this browser."""
    if request.endpoint in ("portal.open_link", "portal.leave"):
        return None
    g.lease = leasing.lease_for_token(request.cookies.get(COOKIE))
    if g.lease is None:
        return render_template("portal/no_access.html"), 403
    if g.lease.status != "Active":
        g.lease = None  # (so the page doesn't show her details: see _landlord_details)
        return render_template("portal/sorry.html"), 403
    return None


@bp.after_request
def _private_pages(response):
    # These pages are about someone's home: a shared computer must not keep copies,
    # and only the portal's own stylesheet and photos may load (no scripts at all).
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    # Tell other sites nothing about these pages. (Not "no-referrer": with that, browsers say
    # the portal's own forms come from "null", and the server refuses them as coming from elsewhere.)
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@bp.context_processor
def _landlord_details():
    """Her name and phone, for the top of every portal page (only for tenants with a working link)."""
    if getattr(g, "lease", None) is None:
        return {}
    try:
        settings = get_settings()
        return {"landlord_name": settings.business_name.strip(), "landlord_phone": settings.phone.strip()}
    except Exception:
        return {}  # never let this stop an error page from showing


@bp.get("/link/<token>")
def open_link(token: str):
    """A lease's portal link: remember it in this browser, then show the portal."""
    lease = leasing.lease_for_token(token)
    if lease is None:
        return render_template("portal/no_access.html"), 403
    if lease.status != "Active":
        return render_template("portal/sorry.html"), 403
    response = redirect(url_for("portal.home"))
    response.set_cookie(
        COOKIE,
        token,
        max_age=COOKIE_SECONDS,
        path="/portal",
        httponly=True,  # page scripts can't read it
        secure=current_app.config["ON_SERVER"],  # only sent over HTTPS
        samesite="Lax",  # not sent with forms from other websites
    )
    return response


@bp.post("/leave")
def leave():
    """ "Forget me on this device", for a shared computer. The link itself keeps working."""
    response = current_app.make_response(render_template("portal/left.html"))
    response.delete_cookie(COOKIE, path="/portal")
    return response


@bp.get("/")
def home():
    lease = g.lease
    address_lines = [line.strip() for line in lease.property.address.splitlines() if line.strip()]
    sent = db_session.scalars(
        select(MaintenanceRequest)
        .where(MaintenanceRequest.lease_id == lease.id, MaintenanceRequest.archived_at.is_(None))
        .order_by(MaintenanceRequest.id.desc())
    )
    return render_template(
        "portal/home.html",
        lease=lease,
        home=address_lines[0] if address_lines else lease.property.nickname,
        settings=get_settings(),
        maintenance_requests=list(sent),
    )


@bp.get("/requests/new")
def new_request():
    return _show_form(Form())


@bp.post("/requests/new")
def send_request():
    request.max_content_length = MAX_PHOTOS * receipts.MAX_BYTES + 1024 * 1024  # before the photos are read
    lease = g.lease
    form = Form(request.form)
    title = form.text("title", required=True, max_length=150)
    description = form.text("description", required=True, max_length=5000)
    if _sent_today(lease) >= REQUESTS_PER_DAY:
        # (The form page says so too, but the limit may be reached in another tab meanwhile.)
        form.add_error("title", f"You can send up to {REQUESTS_PER_DAY} requests a day. Please try again tomorrow.")
    if not form.is_valid:
        return _show_form(form, status=400)

    maintenance_request = MaintenanceRequest(
        property=lease.property,
        lease=lease,
        title=title,
        description=description,
        category=categories.DEFAULT_REQUEST_TYPE,
        status="Open",
        reported_on=date.today(),
        from_tenant=True,
        unread=True,
    )
    db_session.add(maintenance_request)
    db_session.flush()
    try:
        attachments.save_uploads(
            request.files.getlist("photos"),
            current_app.config["DATA_PATHS"].documents,
            owner_label(maintenance_request),
            maintenance_request,
            photos_only=True,
            max_files=MAX_PHOTOS,
        )
    except receipts.ReceiptError as problem:
        db_session.rollback()
        form.add_error("photos", f"{problem} Nothing was sent yet; please choose the photos again.")
        return _show_form(form, status=400)
    db_session.commit()
    flash("Your request was sent. You'll see its status here.", "success")
    return redirect(url_for("portal.home"))


def _sent_today(lease) -> int:
    """How many requests the tenants of this lease have sent since midnight (Pacific time on the server)."""
    start_of_today = datetime.combine(date.today(), datetime.min.time())
    return db_session.scalar(
        select(func.count())
        .select_from(MaintenanceRequest)
        .where(MaintenanceRequest.lease_id == lease.id, MaintenanceRequest.created_at >= start_of_today)
    )


def _show_form(form: Form, status: int = 200):
    return (
        render_template(
            "portal/request_form.html",
            form=form,
            accept="image/*,.heic,.heif",
            max_photos=MAX_PHOTOS,
            per_day=REQUESTS_PER_DAY,
            # Say so before they type anything, rather than after.
            limit_reached=status == 200 and _sent_today(g.lease) >= REQUESTS_PER_DAY,
        ),
        status,
    )
