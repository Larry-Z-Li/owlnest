"""The charts under the report on the Reports screen.

They add up the same transactions as the report, so they always agree with it, and they
leave out transfers, as its net cash flow does. The bars are plain HTML and each pie slice
one SVG shape (templates/_report_charts.html), so there's no chart library to keep up.

Colors: money in is always blue and money out always orange. A pie slice's color goes with
what it is, not how big it is, so a purpose doesn't change color when the dates change. These
colors are checked for color blindness, and every slice is named with its amount beside it.
"""

import math
import zlib

from . import cash_flow, categories, records
from .models import Property
from .money import format_money

SLICE_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
# What's left over, and homes in no LLC, are plain gray: they aren't one thing.
GRAY = "#9aa6b5"
OTHER = "Other"
NO_PROPERTY = "Not for one property"

# How many slices of a pie have their own color, before the rest becomes "Other".
MOST_SLICES = 5
# How many ranked bars, before the rest becomes "Other".
MOST_BARS = 8
# Fewer parts than this says more as plain numbers.
FEWEST_SLICES = 3


def build(report: cash_flow.Report) -> dict | None:
    """Everything the charts show for this report, or None if it has no money to chart."""
    groups = dict(categories.GROUPS)
    income_names = set(groups[categories.INCOME])
    spending_names = {name for group, names in groups.items() for name in names if group not in (categories.INCOME, categories.TRANSFERS)}
    income = [item for item in report.transactions if item.category in income_names]
    spending = [item for item in report.transactions if item.category in spending_names]
    if not income and not spending:
        return None
    money_in = sum(item.amount_cents for item in income)
    money_out = sum(item.amount_cents for item in spending)
    return {
        "money_in": money_in,
        "money_out": money_out,
        "net": money_in + money_out,
        "months": _months(report, income, spending),
        "homes": _homes(report, income + spending),
        "spending": _where_it_goes(spending),
        "shares": _money_in_shares(report, income),
    }


def _text(cents: int) -> str:
    return ("−" if cents < 0 else "") + format_money(abs(cents))


def _short(cents: int) -> str:
    """For an axis: $50K, −$100K."""
    dollars = abs(cents) / 100
    words = f"${dollars / 1000:g}K" if dollars >= 1000 else f"${dollars:g}"
    return ("−" if cents < 0 else "") + words


def _nice_step(largest: int) -> int:
    """A round distance between gridlines (1, 2 or 5 of something), for about 3 lines up to the largest amount."""
    rough = max(largest, 100) / 3
    power = 10 ** math.floor(math.log10(rough))
    return next((nice * power for nice in (1, 2, 5, 10) if nice * power >= rough), 10 * power)


def _months(report, income: list, spending: list) -> dict | None:
    """Money in going up and money out going down, month by month, from one baseline."""
    keys = cash_flow.months_between(report.start, report.end)
    if not 2 <= len(keys) <= cash_flow.MOST_MONTHS:
        return None
    ins = dict.fromkeys(keys, 0)
    outs = dict.fromkeys(keys, 0)
    for items, totals in ((income, ins), (spending, outs)):
        for item in items:
            totals[(item.transaction_date.year, item.transaction_date.month)] += item.amount_cents
    highest = max(0, *ins.values())
    lowest = max(0, *(-value for value in outs.values()))
    step = _nice_step(max(highest, lowest))
    axis_top = math.ceil(highest / step) * step
    axis_bottom = math.ceil(lowest / step) * step
    span = (axis_top + axis_bottom) or 1
    columns = []
    for number, (year, month) in enumerate(keys):
        first = cash_flow.date(year, month, 1)
        money_in, money_out = ins[(year, month)], outs[(year, month)]
        long_range = len(keys) > 12
        columns.append(
            {
                "label": (f"{first:%b %y}" if number % 3 == 0 else "") if long_range else f"{first:%b}",
                "name": f"{first:%B %Y}",
                "in_height": max(money_in, 0) / span * 100,
                "out_height": max(-money_out, 0) / span * 100,
                "tip": f"Money in {_text(money_in)}\nMoney out {_text(money_out)}\nNet {_text(money_in + money_out)}",
            }
        )
    ticks = [
        {"top": (axis_top - value) / span * 100, "label": _short(value), "zero": value == 0}
        for value in range(-axis_bottom, axis_top + 1, step)
    ]
    return {"columns": columns, "ticks": ticks, "zero": axis_top / span * 100}


def _homes(report, transactions: list) -> list[dict] | None:
    """Each home's net cash flow, best first, from one baseline. (Not for just one home: a lone bar says nothing.)"""
    if isinstance(report.where, Property):
        return None
    totals = {}
    for item in transactions:
        name = _owner_name(item)
        totals[name] = totals.get(name, 0) + item.amount_cents
    ranked = sorted(((name, cents) for name, cents in totals.items() if cents), key=lambda part: -part[1])
    if len(ranked) < 2:
        return None
    high = max(0, ranked[0][1])
    low = max(0, -ranked[-1][1])
    span = (high + low) or 1
    zero = low / span * 100
    rows = []
    for name, cents in ranked:
        width = abs(cents) / span * 100
        rows.append({"name": name, "text": _text(cents), "good": cents > 0, "left": zero if cents > 0 else zero - width, "width": width, "zero": zero})
    return rows


def _where_it_goes(spending: list) -> list[dict] | None:
    """Money out by purpose, biggest first, as ranked bars."""
    totals = {}
    for item in spending:
        totals[item.category] = totals.get(item.category, 0) - item.amount_cents
    ranked = sorted(((name, cents) for name, cents in totals.items() if cents > 0), key=lambda part: -part[1])
    if not ranked:
        return None
    largest = _folded(ranked, MOST_BARS)
    biggest = max(cents for _name, cents in largest)
    return [{"name": name, "text": _text(cents), "width": cents / biggest * 100, "share": _share(cents, ranked)} for name, cents in largest]


def _money_in_shares(report, income: list) -> dict | None:
    """Who brings the money in: by LLC when it's for all properties and there are LLCs, otherwise by home."""
    if isinstance(report.where, Property):
        return None
    by_llc = report.where is None and bool(records.active_llcs())
    totals = {}
    for item in income:
        if by_llc:
            llc = item.property.llc if item.property else item.llc
            name = llc.name if llc else records.NOT_IN_AN_LLC
        else:
            name = _owner_name(item)
        totals[name] = totals.get(name, 0) + item.amount_cents
    ranked = sorted(((name, cents) for name, cents in totals.items() if cents > 0), key=lambda part: -part[1])
    if len(ranked) < FEWEST_SLICES:
        return None
    return {"title": "Money in by LLC" if by_llc else "Money in by home", "pie": _pie(ranked)}


def _owner_name(item) -> str:
    """Whose money it is: the home's, or else the LLC's itself."""
    if item.property:
        return item.property.nickname
    return item.llc.name if item.llc else NO_PROPERTY


def _share(cents: int, parts: list) -> int:
    return round(cents / sum(amount for _name, amount in parts) * 100)


def _folded(ranked: list, most: int) -> list[tuple[str, int]]:
    """The biggest parts, with the rest added up as "Other"."""
    if len(ranked) <= most + 1:
        return ranked
    return ranked[:most] + [(OTHER, sum(cents for _name, cents in ranked[most:]))]


def _pie(ranked: list) -> dict:
    """The slices, biggest first from the top."""
    parts = _folded(ranked, MOST_SLICES)
    total = sum(cents for _name, cents in parts)
    colors = _colors_for([name for name, _cents in parts])
    slices = []
    angle = -math.pi / 2
    for name, cents in parts:
        sweep = cents / total * 2 * math.pi
        slices.append(
            {"name": name, "text": _text(cents), "share": round(cents / total * 100), "color": colors[name], "shape": _slice_shape(angle, angle + sweep)}
        )
        angle += sweep
    return {"slices": slices, "total": _text(total)}


def _colors_for(names: list[str]) -> dict[str, str]:
    """A color for each name that stays with it from page to page, unless two would otherwise be the same."""
    colors = {}
    taken = set()
    for name in sorted(names):
        if name in (OTHER, records.NOT_IN_AN_LLC, NO_PROPERTY):
            colors[name] = GRAY
            continue
        start = zlib.crc32(name.encode())
        slot = next((start + step) % len(SLICE_COLORS) for step in range(len(SLICE_COLORS)) if (start + step) % len(SLICE_COLORS) not in taken)
        taken.add(slot)
        colors[name] = SLICE_COLORS[slot]
    return colors


def _slice_shape(start: float, end: float) -> str:
    """The SVG outline of one slice, in a box 200 across."""
    center, radius = 100, 96
    end = min(end, start + 2 * math.pi - 0.001)  # (A whole circle can't be drawn as one arc.)
    large = 1 if end - start > math.pi else 0

    def point(angle: float) -> str:
        return f"{center + radius * math.cos(angle):.2f} {center + radius * math.sin(angle):.2f}"

    return f"M{center} {center} L{point(start)} A{radius} {radius} 0 {large} 1 {point(end)} Z"
