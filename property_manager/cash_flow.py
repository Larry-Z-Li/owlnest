"""The cash-flow report: transactions added up by purpose, by month, by property or by LLC.

This is the report for her accountant. Rows follow the client's category groups
(categories.py), each with its total; columns are the months in the chosen dates,
the properties or the LLCs, plus a Total column. Amounts keep their signs: money in is
positive, money out negative, so every line simply adds up:

    Total income
    + total operating expenses     = net operating income
    + total mortgage & loan expenses = net cash flow

Transfers (security deposits, owner contributions and so on) are listed after net
cash flow, but aren't part of it.

It can be for all properties, one LLC, or one property. An LLC's report has its homes'
money and the money for the LLC itself, such as its accounting fee.

The Reports screen, the Excel file and the PDF all use build(), so they always agree.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from . import categories, records
from .database import db_session
from .models import Llc, Property, Transaction

BY_MONTH = "month"
BY_PROPERTY = "property"
BY_LLC = "llc"
# More months than this would make the table too wide to read (or print).
MOST_MONTHS = 36


@dataclass
class Row:
    label: str
    values: list[int]  # one amount (in cents) per column, then the total
    kind: str = "category"  # "category", "total" (a group's total), or "result" (such as net cash flow)


@dataclass
class Section:
    heading: str
    rows: list[Row] = field(default_factory=list)


@dataclass
class Report:
    start: date
    end: date
    interval: str  # BY_MONTH, BY_PROPERTY or BY_LLC
    property_name: str  # "All properties", or the name of the one property or LLC
    columns: list[str]  # the column headings (months, properties or LLCs), then "Total"
    sections: list[Section]
    transaction_count: int
    transactions: list[Transaction]  # the transactions it adds up, by date (for the Excel file)
    where: Llc | Property | None = None  # what it's for (None: all properties)


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from start's month to end's month."""
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def build(start: date, end: date, where: Llc | Property | None, interval: str) -> Report:
    """Add up the transactions from start to end (both included), for one LLC, one property, or (None) all."""
    query = select(Transaction).where(
        Transaction.archived_at.is_(None), Transaction.transaction_date >= start, Transaction.transaction_date <= end
    )
    if isinstance(where, Llc):
        query = query.where(records.in_llc(where.id))
    elif where is not None:
        query = query.where(Transaction.property_id == where.id)
    transactions = list(db_session.scalars(query.order_by(Transaction.transaction_date, Transaction.id)))

    if interval == BY_MONTH:
        keys = months_between(start, end)
        labels = [f"{date(year, month, 1):%b %Y}" for year, month in keys]

        def column_of(transaction):
            return (transaction.transaction_date.year, transaction.transaction_date.month)

    elif interval == BY_LLC:
        keys, labels = _llc_columns(transactions, where)
        column_of = llc_id_of

    else:
        keys, labels = _property_columns(transactions, where)

        def column_of(transaction):
            return transaction.property_id

    index = {key: number for number, key in enumerate(keys)}
    width = len(keys) + 1  # the columns, then the total

    # {category: [amount per column]}
    sums: dict[str, list[int]] = {}
    for transaction in transactions:
        values = sums.setdefault(transaction.category, [0] * width)
        values[index[column_of(transaction)]] += transaction.amount_cents
    for values in sums.values():
        values[-1] = sum(values[:-1])

    def section(group: str) -> tuple[Section, list[int]]:
        names = dict(categories.GROUPS)[group]
        # (Every purpose is listed, even with nothing in these dates.)
        rows = [Row(name, sums.get(name, [0] * width)) for name in names]
        # (Categories that aren't on the client's list, if any, go with the transfers.)
        if group == categories.TRANSFERS:
            rows += [Row(name, values) for name, values in sums.items() if name not in categories.ALL]
        total = _add(width, *(row.values for row in rows))
        rows.append(Row(f"Total {group.lower()}", total, "total"))
        return Section(group, rows), total

    income, income_total = section(categories.INCOME)
    operating, operating_total = section(categories.OPERATING)
    operating.rows.append(Row("Net operating income", _add(width, income_total, operating_total), "result"))
    loans, loans_total = section(categories.MORTGAGE_AND_LOAN)
    loans.rows.append(Row("Net cash flow", _add(width, income_total, operating_total, loans_total), "result"))
    transfers, _transfers_total = section(categories.TRANSFERS)
    transfers.heading = "Transfers (not part of cash flow)"

    return Report(
        start=start,
        end=end,
        interval=interval,
        property_name=_name_of(where),
        columns=labels + ["Total"],
        sections=[income, operating, loans, transfers],
        transaction_count=len(transactions),
        transactions=transactions,
        where=where,
    )


def _name_of(where: Llc | Property | None) -> str:
    if isinstance(where, Llc):
        return where.name
    return where.nickname if where is not None else "All properties"


def llc_id_of(transaction: Transaction) -> int | None:
    """The LLC a transaction's money belongs to: its home's, or the one it's for itself. (None: no LLC.)"""
    return transaction.property.llc_id if transaction.property else transaction.llc_id


def _property_columns(transactions, where: Llc | Property | None) -> tuple[list, list[str]]:
    """The columns of a report by property: each property (by name), then money for no one property."""
    if isinstance(where, Property):
        return [where.id], [where.nickname]
    query = select(Property)
    if isinstance(where, Llc):
        query = query.where(Property.llc_id == where.id)
    used = {transaction.property_id for transaction in transactions}
    # An archived property still gets a column if it has money in these dates.
    properties = [one for one in db_session.scalars(query) if not one.archived_at or one.id in used]
    properties.sort(key=lambda one: one.nickname.casefold())
    keys = [one.id for one in properties]
    labels = [one.nickname for one in properties]
    if None in used:
        keys.append(None)
        labels.append("The LLC itself" if isinstance(where, Llc) else "No one property")
    return keys, labels


def _llc_columns(transactions, where: Llc | Property | None) -> tuple[list, list[str]]:
    """The columns of a report by LLC: each LLC (by name), then money in no LLC."""
    if isinstance(where, Llc):
        return [where.id], [where.name]
    used = {llc_id_of(transaction) for transaction in transactions}
    llcs = records.active_llcs() if where is None else []
    known = {llc.id for llc in llcs}
    llcs += [db_session.get(Llc, llc_id) for llc_id in used if llc_id is not None and llc_id not in known]
    keys = [llc.id for llc in llcs]
    labels = [llc.name for llc in llcs]
    if None in used or not keys:
        keys.append(None)
        labels.append(records.NOT_IN_AN_LLC)
    return keys, labels


def _add(width: int, *lists: list[int]) -> list[int]:
    return [sum(values[number] for values in lists) for number in range(width)]
