"""Ways to show values on the pages. Templates use these as filters, e.g. {{ amount_cents|money }}."""

from datetime import date, datetime

from flask import Flask

from .money import format_amount, format_money, format_short_money, format_whole_dollars
from .receipts import is_picture
from .records import lease_name


def nice_date(value: date | None) -> str:
    """Sep 24, 2026"""
    if value is None:
        return ""
    return f"{value:%b} {value.day}, {value.year}"


def nice_datetime(value: datetime | None) -> str:
    """Sep 24, 2026 at 3:10 PM"""
    if value is None:
        return ""
    hour = value.hour % 12 or 12
    am_pm = "AM" if value.hour < 12 else "PM"
    return f"{nice_date(value)} at {hour}:{value.minute:02d} {am_pm}"


def short_date(value: date | None) -> str:
    """A date without its year, for lists grouped by month: "Oct 15"."""
    return "" if value is None else f"{value:%b} {value.day}"


def days_ago(value: date | datetime | None, today: date | None = None) -> str:
    """"today", "yesterday", or "5 days ago"."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    days = ((today or date.today()) - value).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def register_template_filters(app: Flask) -> None:
    app.add_template_filter(format_money, "money")
    app.add_template_filter(format_whole_dollars, "dollars")
    app.add_template_filter(format_short_money, "short_money")
    app.add_template_filter(short_date, "short_date")
    app.add_template_filter(format_amount, "amount")
    app.add_template_filter(nice_date, "nice_date")
    app.add_template_filter(is_picture, "is_picture")
    app.add_template_filter(nice_datetime, "nice_datetime")
    app.add_template_filter(days_ago, "days_ago")
    app.add_template_filter(lease_name, "lease_name")
