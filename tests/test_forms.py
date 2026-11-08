"""The form helper reads typed-in values and collects plain-words error messages."""

from datetime import date

from property_manager.forms import Form


def test_required_text_that_is_empty_gets_an_error():
    form = Form({"full_name": "   "})
    assert form.text("full_name", required=True) == ""
    assert form.errors == {"full_name": "Please fill this in."}


def test_text_is_trimmed_and_checked_for_length():
    form = Form({"name": "  Jane  ", "prefix": "ABCDEFGHIJK"})
    assert form.text("name") == "Jane"
    form.text("prefix", max_length=10)
    assert "at most 10 characters" in form.errors["prefix"]


def test_money_fields_are_read_in_cents():
    form = Form({"rent": "$1,250.50", "deposit": "12.345", "fee": ""})
    assert form.money("rent") == 125050
    assert form.money("deposit") is None
    assert form.money("fee", required=False) is None
    assert "two digits" in form.errors["deposit"]
    assert "fee" not in form.errors


def test_dates_are_read_from_the_date_picker_format():
    form = Form({"start": "2026-10-01", "end": "next week", "optional": ""})
    assert form.date("start") == date(2026, 10, 1)
    assert form.date("end") is None
    assert form.date("optional", required=False) is None
    assert "not a date" in form.errors["end"]


def test_whole_numbers_are_checked_against_limits():
    form = Form({"due_day": "31", "bedrooms": "two", "units": "3"})
    assert form.whole_number("due_day", minimum=1, maximum=28) is None
    assert form.whole_number("bedrooms") is None
    assert form.whole_number("units", minimum=1) == 3
    assert form.errors["due_day"] == "Please enter a number from 1 to 28."
    assert form.errors["bedrooms"] == "Please enter a whole number, such as 3."


def test_page_shows_saved_values_before_anything_is_typed():
    form = Form(initial={"full_name": "Jane Doe", "bedrooms": 2, "notes": None})
    assert form.raw("full_name") == "Jane Doe"
    assert form.raw("bedrooms") == "2"
    assert form.raw("notes") == ""
    assert form.is_valid
