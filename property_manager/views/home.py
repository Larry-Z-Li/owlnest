"""The Home page (dashboard): what her homes are worth, this month's rent, new deposits from
the bank, what needs her attention, open repair requests, her properties at a glance, and backups."""

from datetime import date, datetime

from flask import Blueprint, abort, current_app, render_template
from sqlalchemy import func, select

from .. import alerts, attachments, backup, bank_link, cash_flow, categories, home_values, records
from ..config import HEALTH_TEXT
from ..database import db_session, get_settings
from ..models import Lease, MaintenanceRequest, Property, Transaction

bp = Blueprint("home", __name__)


@bp.get("/")
def index():
    paths = current_app.config["DATA_PATHS"]
    today = date.today()
    properties = records.active_properties()
    leases = {a_property.id: records.current_lease(a_property) for a_property in properties}
    return render_template(
        "home.html",
        today=today,
        greeting=_greeting(datetime.now().hour),
        alerts=alerts.gather(today, paths, current_app.config["LAST_BACKUP_RESULT"]),
        new_deposits=bank_link.new_deposits_from_latest_fetch(),
        settings=get_settings(),
        last_backup=backup.last_successful_backup(paths.backups),
        property_count=len(properties),
        leased_count=sum(1 for lease in leases.values() if lease),
        total_value=home_values.total_value(properties),
        rent=_rent_this_month(today, [lease for lease in leases.values() if lease]),
        net_cash_flow_cents=_net_cash_flow_this_year(today),
        cards=[_card(a_property, leases[a_property.id]) for a_property in properties],
        # Open and in-progress requests, newest first.
        open_requests=list(
            db_session.scalars(
                select(MaintenanceRequest)
                .where(MaintenanceRequest.archived_at.is_(None), MaintenanceRequest.status != "Closed")
                .order_by(MaintenanceRequest.reported_on.desc(), MaintenanceRequest.id.desc())
            )
        ),
    )


def _greeting(hour: int) -> str:
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 18 else "Good evening"


def _rent_this_month(today: date, leases: list[Lease]) -> dict:
    """Rent received so far this month, and the monthly rent of the current leases (what's expected)."""
    received = db_session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(
            Transaction.archived_at.is_(None),
            Transaction.category == categories.RENT,
            Transaction.amount_cents > 0,
            Transaction.transaction_date >= today.replace(day=1),
            Transaction.transaction_date <= today,
        )
    )
    expected = sum(lease.rent_cents for lease in leases)
    return {
        "received_cents": received,
        "expected_cents": expected,
        # How full the bar is (0 to 100).
        "percent": min(100, round(received * 100 / expected)) if expected else 0,
    }


def _net_cash_flow_this_year(today: date) -> int:
    """Net cash flow from January 1 to today, as in Reports."""
    report = cash_flow.build(date(today.year, 1, 1), today, None, cash_flow.BY_MONTH)
    for section in report.sections:
        for row in section.rows:
            if row.kind == "result" and row.label == "Net cash flow":
                return row.values[-1]
    return 0


def _card(a_property: Property, lease: Lease | None) -> dict:
    """What a property's card on Home shows."""
    values = home_values.latest_values(a_property, how_many=2)
    change = None
    if len(values) == 2:
        change = (values[0].value_cents - values[1].value_cents) * 100 / values[1].value_cents
    return {
        "property": a_property,
        "lease": lease,
        "value": values[0] if values else None,
        "change_percent": change,
        "since": values[1].asked_at if len(values) == 2 else None,
        # Its first photo, or else one of the three drawings of a house (each property keeps its own).
        "photo": next(iter(attachments.active(a_property.attachments)), None),
        "drawing": a_property.id % 3,
    }


@bp.post("/shutdown")
def shutdown():
    """The "Close the app" button (and the Mac Dock's Quit) stop the app here."""
    if current_app.config["ON_SERVER"]:
        # The server keeps running. The app on her computer shows this page, then closes itself.
        return render_template("closed.html")
    stop_server = current_app.config.get("STOP_SERVER")
    if stop_server is None:
        abort(404)  # Not started by run.py (for example, during tests).
    stop_server()
    return render_template("closed.html")


@bp.get("/health")
def health():
    """Lets the launcher check whether the app is already running (and Render check the server)."""
    return HEALTH_TEXT, 200, {"Content-Type": "text/plain"}
