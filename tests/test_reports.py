"""The cash-flow report for the accountant, on screen, in Excel, and as a PDF."""

import re
from datetime import date
from io import BytesIO

import openpyxl
from helpers import add_property

from property_manager import cash_flow
from property_manager.database import db_session
from property_manager.models import Property


def add_money(client, day, direction, amount, category, property_id=None, details=""):
    response = client.post(
        "/transactions/new",
        data={
            "transaction_date": day,
            "direction": direction,
            "amount": amount,
            "category": category,
            "property_id": str(property_id or ""),
            "details": details,
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)


def a_year_of_money(client):
    """Elm St and Oak Ave, with some of everything in 2026 (and a bit outside it)."""
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave", "12 Oak Ave")
    add_money(client, "2026-01-01", "in", "2,000", "Rental Income", elm)
    add_money(client, "2026-02-01", "in", "2,000", "Rental Income", elm)
    add_money(client, "2026-02-01", "in", "1,500", "Rental Income", oak)
    add_money(client, "2026-02-10", "out", "300", "Repair & Maintenance", elm)
    add_money(client, "2026-02-20", "out", "100", "Office & Admin")  # not for one property
    add_money(client, "2026-03-01", "out", "1,000", "Loan Payments", elm)
    add_money(client, "2026-03-15", "out", "2,500", "Appliances & Furnishings", oak)
    add_money(client, "2026-03-20", "in", "1,500", "Security Deposits", oak)  # a transfer
    add_money(client, "2025-12-31", "in", "9,999", "Rental Income", elm)  # outside 2026
    return elm, oak


def row(report, label):
    return next(found for section in report.sections for found in section.rows if found.label == label)


def test_the_report_adds_up_by_group_and_leaves_out_transfers(client, app):
    a_year_of_money(client)
    with app.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_MONTH)
    assert row(report, "Total income").values[-1] == 550000
    # (Appliances & Furnishings was a capital expense; it's an operating one now.)
    assert row(report, "Total operating expenses").values[-1] == -290000
    assert row(report, "Net operating income").values[-1] == 260000
    assert row(report, "Total mortgage & loan expenses").values[-1] == -100000
    assert not any("capital" in section.heading.lower() for section in report.sections)
    assert row(report, "Net cash flow").values[-1] == 160000
    assert row(report, "Total transfers").values[-1] == 150000  # listed, but not in net cash flow
    assert report.transaction_count == 8


def test_by_month_puts_each_amount_in_its_month(client, app):
    a_year_of_money(client)
    with app.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_MONTH)
    assert report.columns[:3] == ["Jan 2026", "Feb 2026", "Mar 2026"] and report.columns[-1] == "Total"
    assert len(report.columns) == 13
    assert row(report, "Rental Income").values[:3] == [200000, 350000, 0]


def test_by_property_has_a_column_each_and_one_for_no_property(client, app):
    a_year_of_money(client)
    with app.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_PROPERTY)
    assert report.columns == ["Elm St", "Oak Ave", "No one property", "Total"]
    assert row(report, "Net cash flow").values == [270000, -100000, -10000, 160000]


def test_one_property_only(client, app):
    elm, _oak = a_year_of_money(client)
    with app.app_context():
        report = cash_flow.build(
            date(2026, 1, 1), date(2026, 12, 31), db_session.get(Property, elm), cash_flow.BY_MONTH
        )
    assert row(report, "Net cash flow").values[-1] == 270000
    assert report.property_name == "Elm St"


def test_archived_transactions_dont_count(client, app):
    a_year_of_money(client)
    client.post("/transactions/1/archive")  # the January rent
    with app.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_MONTH)
    assert row(report, "Total income").values[-1] == 350000


def test_the_report_page(client):
    a_year_of_money(client)
    page = client.get("/reports/?from=2026-01-01&to=2026-12-31").get_data(as_text=True)
    assert "Net operating income" in page and "Net cash flow" in page
    assert "$1,600.00" in page  # net cash flow for the year
    assert "Transfers (not part of cash flow)" in page
    assert "Download Excel" in page and "Download PDF" in page


def test_too_many_months_side_by_side_is_explained(client):
    page = client.get("/reports/?from=2020-01-01&to=2026-12-31").get_data(as_text=True)
    assert "more than 36 months" in page


def test_the_excel_file_has_the_report_and_its_transactions(client):
    a_year_of_money(client)
    response = client.get("/reports/excel?from=2026-01-01&to=2026-12-31&interval=property")
    assert "Cash flow - All properties - 2026-01-01 to 2026-12-31.xlsx" in response.headers["Content-Disposition"]
    book = openpyxl.load_workbook(BytesIO(response.data))
    assert book.sheetnames == ["Cash flow", "Transactions"]
    sheet = book["Cash flow"]
    net = next(cells for cells in sheet.iter_rows(values_only=True) if cells[0] == "Net cash flow")
    assert list(net[1:]) == [2700, -1000, -100, 1600]
    assert book["Transactions"].max_row == 1 + 8


def test_the_pdf_fits_its_columns_on_the_page(client):
    a_year_of_money(client)
    by_month = client.get("/reports/pdf?from=2026-01-01&to=2026-12-31").data
    assert by_month.startswith(b"%PDF")
    assert re.search(rb"/MediaBox \[0 0 792\.00 612\.00\]", by_month)  # landscape Letter

    for number in range(20):
        add_property(client, f"House {number}", f"{number} Main St")
    by_property = client.get("/reports/pdf?from=2026-01-01&to=2026-12-31&interval=property").data
    assert re.search(rb"/MediaBox \[0 0 1224\.00 792\.00\]", by_property)  # landscape Tabloid
