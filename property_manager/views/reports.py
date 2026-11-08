"""The Reports screen: the cash-flow report for her accountant, on screen, in Excel, or as a PDF.

She chooses all properties, an LLC or one property, the dates, and whether the columns
are months, properties or LLCs. The numbers come from cash_flow.py, the files from
report_files.py, and the charts under it from report_charts.py.
"""

from datetime import date

from flask import Blueprint, render_template, request

from .. import cash_flow, records, report_charts, report_files
from ..database import db_session, get_settings
from ..models import Llc, Property
from ._shared import as_id, download

bp = Blueprint("reports", __name__, url_prefix="/reports")

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@bp.get("/")
def index():
    choices, problem = _choices()
    report = None if problem else cash_flow.build(*choices)
    scope = _scope_of(choices[2])
    return render_template(
        "reports.html",
        report=report,
        problem=problem,
        charts=report_charts.build(report) if report else None,
        llcs=records.active_llcs(),
        property_groups=records.property_choices(),
        scope=scope,
        start=choices[0],
        end=choices[1],
        interval=choices[3],
        description=report_files.describe(report) if report else "",
        # The same choices, for the download links.
        link_args={"scope": scope, "from": choices[0].isoformat(), "to": choices[1].isoformat(), "interval": choices[3]},
    )


@bp.get("/excel")
def excel():
    choices, problem = _choices()
    if problem:
        return index()
    report = cash_flow.build(*choices)
    return download(report_files.excel(report), report_files.file_name(report, "xlsx"), _XLSX)


@bp.get("/pdf")
def pdf():
    choices, problem = _choices()
    if problem:
        return index()
    report = cash_flow.build(*choices)
    contents = report_files.pdf(report, get_settings().business_name.strip())
    return download(contents, report_files.file_name(report, "pdf"), "application/pdf")


def _choices() -> tuple[tuple, str | None]:
    """(start, end, LLC or property or None, interval) from the address, and a problem with them (or None).

    Unless she chose otherwise: this calendar year, all properties, by month.
    """
    year = date.today().year
    start = _date_arg("from") or date(year, 1, 1)
    end = _date_arg("to") or date(year, 12, 31)
    if end < start:
        start, end = end, start
    interval = request.args.get("interval", cash_flow.BY_MONTH)
    if interval not in (cash_flow.BY_MONTH, cash_flow.BY_PROPERTY, cash_flow.BY_LLC):
        interval = cash_flow.BY_MONTH
    problem = None
    if interval == cash_flow.BY_MONTH and len(cash_flow.months_between(start, end)) > cash_flow.MOST_MONTHS:
        problem = (
            f"That's more than {cash_flow.MOST_MONTHS} months, too many to show side by side. "
            "Please choose shorter dates, or show it by property or by LLC."
        )
    return (start, end, _chosen_scope(), interval), problem


def _chosen_scope() -> Llc | Property | None:
    """What the report is for: "llc-3", "property-4", or nothing for all properties."""
    kind, _dash, number = request.args.get("scope", "").partition("-")
    record_id = as_id(number)
    if kind == "llc" and record_id is not None:
        return db_session.get(Llc, record_id)
    if kind == "property" and record_id is not None:
        return db_session.get(Property, record_id)
    # (A link from before LLCs says property_id.)
    property_id = as_id(request.args.get("property_id"))
    return db_session.get(Property, property_id) if property_id is not None else None


def _scope_of(where: Llc | Property | None) -> str:
    if where is None:
        return ""
    return f"llc-{where.id}" if isinstance(where, Llc) else f"property-{where.id}"


def _date_arg(name: str) -> date | None:
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return None
