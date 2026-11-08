"""Money is stored as whole cents. These tests check typing, showing, and adding amounts."""

import pytest

from property_manager.money import MoneyError, cents_to_text, format_money, parse_money


@pytest.mark.parametrize(
    "typed, cents",
    [
        ("1250", 125000),
        ("1,250.00", 125000),
        ("$1,250.5", 125050),
        ("  12.34 ", 1234),
        ("$ 7", 700),
        (".99", 99),
        ("0.10", 10),
        ("0.01", 1),
        ("12.", 1200),
        ("99999999.99", 9999999999),
    ],
)
def test_common_ways_of_typing_amounts_are_understood(typed, cents):
    assert parse_money(typed) == cents


@pytest.mark.parametrize(
    "typed, message_part",
    [
        ("", "Please enter an amount"),
        ("   ", "Please enter an amount"),
        ("abc", "is not an amount"),
        ("12.345", "two digits after the decimal point"),
        ("-5", "can't be negative"),
        ("1e3", "is not an amount"),
        ("1.2.3", "is not an amount"),
        (".", "is not an amount"),
        ("$", "Please enter an amount"),
        ("0", "more than $0.00"),
        ("0.00", "more than $0.00"),
        ("100000000.00", "too large"),
    ],
)
def test_invalid_amounts_are_refused_in_plain_words(typed, message_part):
    with pytest.raises(MoneyError) as error:
        parse_money(typed)
    assert message_part in str(error.value)


def test_zero_is_allowed_only_when_asked_for():
    assert parse_money("0", allow_zero=True) == 0
    assert parse_money("$0.00", allow_zero=True) == 0


@pytest.mark.parametrize(
    "cents, shown",
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (99, "$0.99"),
        (100, "$1.00"),
        (125050, "$1,250.50"),
        (123456789, "$1,234,567.89"),
        (-2500, "-$25.00"),
    ],
)
def test_amounts_are_shown_as_dollars(cents, shown):
    assert format_money(cents) == shown


def test_amounts_in_wide_tables_leave_out_the_dollar_sign():
    from property_manager.money import format_amount

    assert format_amount(125050) == "1,250.50"
    assert format_amount(-610808) == "-6,108.08"


def test_empty_amount_is_shown_as_blank():
    assert format_money(None) == ""
    assert cents_to_text(None) == ""


@pytest.mark.parametrize("cents", [0, 1, 10, 99, 100, 125050, 9999999999])
def test_amounts_survive_being_shown_in_a_form_and_typed_back(cents):
    assert parse_money(cents_to_text(cents), allow_zero=True) == cents


def test_adding_amounts_in_cents_is_exact():
    # With decimals, 0.10 + 0.10 + 0.10 is 0.30000000000000004, not 0.30.
    assert 0.10 + 0.10 + 0.10 != 0.30
    # In whole cents it's exact.
    total = sum(parse_money("0.10") for _ in range(3))
    assert total == parse_money("0.30") == 30


def test_a_year_of_rent_adds_up_exactly():
    monthly_rent = parse_money("1,333.33")
    assert monthly_rent * 12 == parse_money("15,999.96")
    assert format_money(monthly_rent * 12) == "$15,999.96"
