"""Money helpers.

All money is stored as whole cents (integers): $1,250.50 is stored as 125050.
Decimal numbers ("floats") are never used for money, because they cause rounding
errors: in Python, 0.1 + 0.2 is 0.30000000000000004, but 10 + 20 cents is exactly 30.

Use parse_money() to turn what she types into cents, and format_money() to show cents as "$1,250.50".
"""

import re

# Anything bigger than this is almost certainly a typo (for example, an extra zero or two).
LARGEST_AMOUNT_CENTS = 99_999_999_99  # $99,999,999.99

# Digits, optionally followed by a dot and more digits: "1250", "1250.5", "1250.", ".50".
_AMOUNT_PATTERN = re.compile(r"^(\d*)(?:\.(\d*))?$")


class MoneyError(ValueError):
    """Raised when typed-in text is not a valid amount. The message is written for her to read."""


def parse_money(text: str, *, allow_zero: bool = False) -> int:
    """Turn typed-in text such as "$1,250.50" into whole cents (125050).

    Dollar signs, commas and spaces are ignored. Negative amounts, more than two
    digits after the decimal point, and anything that isn't a number are refused
    with a plain-words MoneyError. Zero is refused unless allow_zero is True.
    """
    typed = (text or "").strip()
    cleaned = typed.replace("$", "").replace(",", "").replace(" ", "")

    if cleaned == "":
        raise MoneyError("Please enter an amount.")
    if cleaned.startswith("-"):
        raise MoneyError("The amount can't be negative.")

    match = _AMOUNT_PATTERN.match(cleaned)
    if match is None:
        raise MoneyError(f'"{typed}" is not an amount. Type a number, such as 1250 or 1,250.00.')

    dollars_text = match.group(1)
    cents_text = match.group(2) or ""
    if dollars_text == "" and cents_text == "":
        raise MoneyError(f'"{typed}" is not an amount. Type a number, such as 1250 or 1,250.00.')
    if len(cents_text) > 2:
        raise MoneyError("Amounts can have at most two digits after the decimal point (cents).")

    # "12.5" means 12 dollars and 50 cents, so pad the cents part to two digits.
    cents = int(dollars_text or "0") * 100 + int(cents_text.ljust(2, "0"))

    if cents == 0 and not allow_zero:
        raise MoneyError("The amount must be more than $0.00.")
    if cents > LARGEST_AMOUNT_CENTS:
        raise MoneyError("That amount is too large. Please check for extra digits.")
    return cents


def format_money(cents: int | None) -> str:
    """Show cents as dollars for people to read: 125050 becomes "$1,250.50"."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def format_whole_dollars(cents: int | None) -> str:
    """Like format_money, rounded to whole dollars, for big round numbers: 46800000 becomes "$468,000"."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    return f"{sign}${round(abs(cents) / 100):,}"


def format_short_money(cents: int | None) -> str:
    """Like format_money, without the cents when there are none: "$3,500", but "$2,222.22"."""
    if cents is None:
        return ""
    return format_whole_dollars(cents) if cents % 100 == 0 else format_money(cents)


def format_amount(cents: int | None) -> str:
    """Like format_money, without the "$": 125050 becomes "1,250.50". Used in wide tables."""
    if cents is None:
        return ""
    return format_money(cents).replace("$", "")


def cents_to_text(cents: int | None) -> str:
    """Show cents in a form field, without "$" or commas: 125050 becomes "1250.50"."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}{dollars}.{remainder:02d}"
