"""Reading and checking what she types into forms.

Typical use in a screen:

    form = Form(request.form)
    name = form.text("full_name", required=True)
    rent = form.money("monthly_rent")
    if not form.is_valid:
        # Show the page again. Each error appears right under its field, and
        # everything she typed is still filled in (see templates/_macros.html).
        ...

When a page first opens, pass the saved values instead: Form(initial={"full_name": ...}).

Error messages don't name the field, because they're shown right under it.
"""

import re
from datetime import date

from .money import MoneyError, parse_money


def email_problem(email: str) -> str | None:
    """A plain-words message if an email address clearly isn't one, else None."""
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1] or " " in email):
        return "This doesn't look like an email address. It should look like jane@example.com."
    return None


class Form:
    def __init__(self, submitted=None, initial: dict | None = None):
        # What she submitted (request.form), or None when the page first opens.
        self.submitted = submitted
        # Values to fill in when the page first opens, such as a record's saved details.
        self.initial = initial or {}
        # Plain-words error messages, by field name.
        self.errors: dict[str, str] = {}

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def raw(self, name: str) -> str:
        """The text to show in a field: what she typed, or the saved value when the page first opens."""
        if self.submitted is not None:
            return self.submitted.get(name, "")
        value = self.initial.get(name)
        return "" if value is None else str(value)

    def add_error(self, name: str, message: str) -> None:
        # Keep only the first problem found for each field, so messages stay short.
        self.errors.setdefault(name, message)

    def text(self, name: str, *, required: bool = False, max_length: int = 200) -> str:
        value = self.raw(name).strip()
        if required and not value:
            self.add_error(name, "Please fill this in.")
        elif len(value) > max_length:
            self.add_error(name, f"This is too long. Please use at most {max_length} characters.")
        return value

    def money(self, name: str, *, required: bool = True, allow_zero: bool = False) -> int | None:
        """Read an amount, in whole cents. Returns None if it's empty or not valid."""
        value = self.raw(name).strip()
        if not value:
            if required:
                self.add_error(name, "Please enter an amount.")
            return None
        try:
            return parse_money(value, allow_zero=allow_zero)
        except MoneyError as error:
            self.add_error(name, str(error))
            return None

    def date(self, name: str, *, required: bool = True) -> date | None:
        """Read a date typed as YYYY-MM-DD (the format the browser's date picker sends)."""
        value = self.raw(name).strip()
        if not value:
            if required:
                self.add_error(name, "Please pick a date.")
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.add_error(name, "This is not a date. Please pick one from the calendar.")
            return None

    def whole_number(
        self,
        name: str,
        *,
        required: bool = False,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int | None:
        value = self.raw(name).strip().replace(",", "")  # "1,916" is fine too
        if not value:
            if required:
                self.add_error(name, "Please fill this in.")
            return None
        if not value.isdecimal():
            self.add_error(name, "Please enter a whole number, such as 3.")
            return None
        number = int(value)
        too_small = minimum is not None and number < minimum
        too_big = maximum is not None and number > maximum
        if too_small or too_big:
            if minimum is not None and maximum is not None:
                self.add_error(name, f"Please enter a number from {minimum} to {maximum}.")
            elif too_small:
                self.add_error(name, f"Please enter {minimum} or more.")
            else:
                self.add_error(name, f"Please enter {maximum} or less.")
            return None
        return number

    def half_number(self, name: str, *, maximum: int) -> float | None:
        """Read an optional number that may end in .5 or such, like 2.5 bathrooms."""
        value = self.raw(name).strip()
        if not value:
            return None
        if not re.fullmatch(r"\d{1,3}(\.\d{1,2})?", value):
            self.add_error(name, "Please enter a number, such as 2 or 2.5.")
            return None
        number = float(value)
        if number > maximum:
            self.add_error(name, f"Please enter {maximum} or less.")
            return None
        return number
