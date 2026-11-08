"""Rent reminders: what's paid and what's owed each month, late fees, waiving, and where it shows."""

from datetime import date, timedelta

import pytest
from helpers import add_lease, add_property
from test_transactions_and_maintenance import add_transaction

from property_manager import rent_ledger
from property_manager.database import db_session
from property_manager.models import Lease, RentPeriod, RentWaiver


def pay(client, property_id, day, amount, category="Rental Income"):
    add_transaction(client, direction="in", amount=amount, category=category, property_id=str(property_id), transaction_date=day)


def set_up(client, app, start="2026-01-01", rent="3,000", **lease_fields):
    """A property with a lease whose rent is checked from its start. Returns the property's and lease's ids."""
    home = add_property(client)
    lease_id = add_lease(client, home, start=start, end="", rent=rent, **lease_fields)
    with app.app_context():
        db_session.get(Lease, lease_id).rent_checked_from = date.fromisoformat(start)
        db_session.commit()
    return home, lease_id


def months_on(app, lease_id, today):
    """The months on a day, each as (month's first day, what's owed, late fee, days late)."""
    with app.app_context():
        months, _ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), today)
        return [(month.month, month.owed_cents, month.fee_cents, month.days_late) for month in months]


def owed_on(app, lease_id, today):
    return [row[1] for row in months_on(app, lease_id, today)]


def add_rent_change(client, lease_id, starts_on, amount, **fields):
    return client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": starts_on, "amount": amount, **fields})


# ---------- When rent is due ----------


def test_rent_is_owed_from_its_due_date(client, app):
    _home, lease_id = set_up(client, app, start="2026-01-05")
    assert months_on(app, lease_id, date(2026, 1, 4)) == []
    assert months_on(app, lease_id, date(2026, 1, 5)) == [(date(2026, 1, 1), 300000, 0, 0)]


def test_a_short_month_uses_its_last_day(client, app):
    _home, lease_id = set_up(client, app, start="2026-01-31")
    with app.app_context():
        months, _ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), date(2026, 3, 1))
        assert [month.due_on for month in months] == [date(2026, 1, 31), date(2026, 2, 28)]


def test_rent_is_due_on_the_day_of_the_month_the_lease_started(client, app):
    _home, lease_id = set_up(client, app, start="2026-01-15")
    with app.app_context():
        months, _ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), date(2026, 2, 15))
        assert [month.due_on for month in months] == [date(2026, 1, 15), date(2026, 2, 15)]


def test_the_rent_and_late_fee_come_from_the_rent_list_on_the_due_date(client, app):
    _home, lease_id = set_up(client, app)
    add_rent_change(client, lease_id, "2026-02-15", "4,000", late_percent="10", late_after_days="3")
    with app.app_context():
        months, _ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), date(2026, 3, 7))
        assert [month.rent_cents for month in months] == [300000, 300000, 400000]
        # March: 6 days late, so 10% of $4,000.
        assert months[2].fee_cents == 40000


def test_rent_before_the_check_date_isnt_owed_and_payments_before_it_dont_count(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-02-10", "3,000")
    with app.app_context():
        db_session.get(Lease, lease_id).rent_checked_from = date(2026, 3, 1)
        db_session.commit()
    assert months_on(app, lease_id, date(2026, 3, 2)) == [(date(2026, 3, 1), 300000, 0, 1)]


def test_a_new_lease_is_checked_from_this_month_at_the_earliest(client, app):
    today = date.today()
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="")
    with app.app_context():
        assert db_session.get(Lease, lease_id).rent_checked_from == today.replace(day=1)


# ---------- Late fees ----------


@pytest.mark.parametrize(
    "day, fee",
    [(1, 0), (5, 0), (6, 15000), (11, 15000), (31, 15000)],
)
def test_the_late_fee_is_added_once_at_5_days_late_on_the_whole_rent(client, app, day, fee):
    _home, lease_id = set_up(client, app)
    assert months_on(app, lease_id, date(2026, 1, day))[0][2] == fee


def test_a_payment_logged_late_goes_by_the_day_it_was_paid(client, app):
    home, lease_id = set_up(client, app)
    today = date(2026, 1, 25)
    assert months_on(app, lease_id, today)[0][2] == 15000  # 24 days late, so far
    # Typed in on the 25th, but paid on the 3rd: on time, nothing owed.
    pay(client, home, "2026-01-03", "3,000")
    assert months_on(app, lease_id, today) == [(date(2026, 1, 1), 0, 0, 2)]


def test_fixing_a_payments_date_changes_the_late_fee(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-01-13", "3,000")
    assert months_on(app, lease_id, date(2026, 1, 25))[0][1:] == (15000, 15000, 12)
    client.post(
        "/transactions/1",
        data={"transaction_date": "2026-01-03", "direction": "in", "amount": "3,000", "category": "Rental Income", "property_id": str(home)},
        content_type="multipart/form-data",
    )
    assert months_on(app, lease_id, date(2026, 1, 25))[0][1:] == (0, 0, 2)


def test_the_banner_says_when_the_late_fee_will_come(client, app):
    _home, lease_id, due = recently_due(client, app, days_ago=2)
    page = client.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert "$3,000.00 still owed" in page
    fee_day = due + timedelta(days=5)
    assert f"A $150.00 late fee is added on {fee_day:%b} {fee_day.day}, {fee_day.year}" in page


def test_the_reminder_stays_until_the_rent_and_late_fee_are_all_paid(client, app):
    home, lease_id = set_up(client, app)
    today = date(2026, 1, 20)
    pay(client, home, "2026-01-05", "1,000")
    pay(client, home, "2026-01-13", "2,000")
    # The rent is all in, 12 days late: 5% of the whole $3,000 rent.
    assert owed_on(app, lease_id, today) == [15000]
    pay(client, home, "2026-01-18", "100", category="Late Fees")
    assert owed_on(app, lease_id, today) == [5000]
    pay(client, home, "2026-01-19", "50")
    assert owed_on(app, lease_id, today) == [0]


def test_a_partial_payment_still_has_the_fee_on_the_whole_rent(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-01-02", "2,900")
    assert months_on(app, lease_id, date(2026, 1, 7))[0][1:] == (10000 + 15000, 15000, 6)


def test_payments_go_to_the_oldest_rent_and_can_pay_ahead(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-01-01", "7,000")
    with app.app_context():
        months, ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), date(2026, 2, 1))
        assert [month.owed_cents for month in months] == [0, 0] and ahead == 100000


def test_an_unpaid_late_fee_never_makes_a_later_month_late(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-01-13", "3,000")  # late, with a $150 fee
    pay(client, home, "2026-02-01", "3,000")  # February, on time
    today = date(2026, 2, 20)
    assert months_on(app, lease_id, today) == [
        (date(2026, 1, 1), 15000, 15000, 12),
        (date(2026, 2, 1), 0, 0, 0),
    ]


def test_a_rent_can_have_no_late_fee(client, app):
    _home, lease_id = set_up(client, app, late_percent="0")
    assert months_on(app, lease_id, date(2026, 1, 25))[0][2] == 0


@pytest.mark.parametrize(
    "where",
    ["other property", "other type", "money out", "archived", "in the future"],
)
def test_what_doesnt_count_as_a_payment(client, app, where):
    home, lease_id = set_up(client, app)
    if where == "other property":
        pay(client, add_property(client, "Oak Ave"), "2026-01-02", "3,000")
    elif where == "other type":
        pay(client, home, "2026-01-02", "3,000", category="Tenant Fees")
    elif where == "money out":
        add_transaction(client, direction="out", amount="3,000", category="Rental Income", property_id=str(home), transaction_date="2026-01-02")
    elif where == "archived":
        pay(client, home, "2026-01-02", "3,000")
        client.post("/transactions/1/archive")
    else:
        pay(client, home, "2026-01-02", "3,000")
    today = date(2026, 1, 1) if where == "in the future" else date(2026, 1, 2)
    assert owed_on(app, lease_id, today) == [300000]


def test_the_next_leases_money_isnt_the_last_ones(client, app):
    home, old = set_up(client, app, start="2026-01-01")
    client.post(f"/leases/{old}/end", data={"ended_on": "2026-01-20"})
    add_lease(client, home, tenants="New People", start="2026-02-01", end="")
    pay(client, home, "2026-02-03", "3,000")
    assert owed_on(app, old, date(2026, 2, 5)) == [300000 + 15000]


def test_rent_paid_before_the_lease_began_counts_if_it_names_the_tenants(client, app):
    home, lease_id = set_up(client, app, start="2026-03-01")
    pay(client, home, "2026-02-20", "3,000")
    add_transaction(client, direction="in", amount="3,000", category="Rental Income", property_id=str(home), transaction_date="2026-02-21", tenant_name="Jane Doe")
    # The first one names nobody (no lease covered that day), so only the second counts.
    with app.app_context():
        months, ahead = rent_ledger.work_out(db_session.get(Lease, lease_id), date(2026, 3, 1))
        assert months[0].owed_cents == 0 and ahead == 0


def test_an_ended_lease_stops_adding_rent_and_fees(client, app):
    _home, lease_id = set_up(client, app)
    client.post(f"/leases/{lease_id}/end", data={"ended_on": "2026-01-08"})
    assert months_on(app, lease_id, date(2026, 6, 1)) == [(date(2026, 1, 1), 315000, 15000, 7)]


# ---------- Waiving ----------


def test_waiving_the_late_fee_keeps_the_rent_owed(client, app):
    _home, lease_id = set_up(client, app)
    with app.app_context():
        lease = db_session.get(Lease, lease_id)
        month = rent_ledger.work_out(lease, date(2026, 1, 20))[0][0]
        rent_ledger.waive_fee(lease, month)
        db_session.commit()
    assert months_on(app, lease_id, date(2026, 1, 30))[0][1:] == (300000, 0, 29)


def test_waiving_the_rest_keeps_what_was_paid_with_that_month(client, app):
    home, lease_id = set_up(client, app)
    pay(client, home, "2026-01-02", "1,000")
    with app.app_context():
        lease = db_session.get(Lease, lease_id)
        month = rent_ledger.work_out(lease, date(2026, 1, 20))[0][0]
        rent_ledger.waive_rest(lease, month)
        db_session.commit()
    # January is settled, and the $1,000 stayed with it instead of moving to February.
    assert owed_on(app, lease_id, date(2026, 2, 1)) == [0, 300000]


# ---------- On the pages ----------


def recently_due(client, app, days_ago=7):
    """A lease with rent due days_ago days ago, and nothing paid. Returns (property id, lease id, the month)."""
    due = date.today() - timedelta(days=days_ago)
    home = add_property(client)
    lease_id = add_lease(client, home, start=due.isoformat(), end="", rent="3,000")
    with app.app_context():
        db_session.get(Lease, lease_id).rent_checked_from = due
        db_session.commit()
    return home, lease_id, due


def test_the_reminder_shows_on_home_and_the_property_and_the_lease(client, app):
    home, lease_id, due = recently_due(client, app)
    page = client.get("/").get_data(as_text=True)
    assert f"{due:%B} rent for Elm St" in page and "Isn&#39;t fully paid" in page
    assert f"{due:%B} rent for Elm St" in client.get(f"/properties/{home}").get_data(as_text=True)
    lease_page = client.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert "rent-due" in lease_page and "$3,150.00 still owed" in lease_page
    assert "Waive late fee" in lease_page and "Waive rest of rent" in lease_page


def test_paying_in_full_clears_the_reminder(client, app):
    home, lease_id, due = recently_due(client, app)
    pay(client, home, due.isoformat(), "3,000")
    assert "rent for Elm St" not in client.get("/").get_data(as_text=True)
    assert 'class="message message-warning rent-due"' not in client.get(f"/leases/{lease_id}").get_data(as_text=True)


def test_the_waive_buttons_and_undo(client, app):
    home, lease_id, due = recently_due(client, app)
    month = f"{due:%Y-%m}"
    page = client.post(f"/leases/{lease_id}/rent/waive", data={"month": month, "what": "fee"}).get_data(as_text=True)
    assert "late fee was waived" in page and "$3,000.00 still owed" in page and "Waive late fee</button>" not in page

    page = client.post(f"/leases/{lease_id}/rent/waive", data={"month": month, "what": "rest"}).get_data(as_text=True)
    assert "rent-due" not in page
    assert "rent for Elm St" not in client.get("/").get_data(as_text=True)

    page = client.post(f"/leases/{lease_id}/rent/waive/undo", data={"month": month}).get_data(as_text=True)
    assert "$3,150.00 still owed" in page
    with app.app_context():
        assert db_session.query(RentWaiver).count() == 0


def test_only_a_month_thats_owed_can_be_waived(client, app):
    _home, lease_id, _due = recently_due(client, app)
    assert client.post(f"/leases/{lease_id}/rent/waive", data={"month": "2001-01", "what": "fee"}).status_code == 404


def test_each_rent_has_its_own_late_fee_fields(client, app):
    assert 'name="late_percent"' in client.get(f"/leases/new?property_id={add_property(client)}").get_data(as_text=True)
    lease_id = add_lease(client, add_property(client, "Oak Ave"), start="2020-01-01", end="", rent="1,000", late_percent="8", late_after_days="10")
    first = "/leases/{}/rent/{}"
    with app.app_context():
        period = db_session.get(Lease, lease_id).rent_periods[0]
        assert (period.late_percent, period.late_after_days) == (8, 10)
        first = first.format(lease_id, period.id)
    assert "8% after 10 days late" in client.get(f"/leases/{lease_id}").get_data(as_text=True)

    client.post(first, data={"amount": "1,000", "late_percent": "2.5", "late_after_days": "7"})
    with app.app_context():
        assert db_session.query(RentPeriod).one().late_percent == 2.5

    assert client.post(first, data={"amount": "1,000", "late_percent": "lots"}).status_code == 400
    assert client.post(first, data={"amount": "1,000", "late_after_days": "0"}).status_code == 400
    # A rent change, and a new rent from an extension, start with the rule before them.
    assert 'value="2.5"' in client.get(f"/leases/{lease_id}/rent/new").get_data(as_text=True)
    client.post(f"/leases/{lease_id}/extend", data={"new_end_date": "2040-01-01", "new_rent": "1,100", "rent_starts_on": "2030-01-01"})
    with app.app_context():
        assert db_session.get(Lease, lease_id).rent_periods[1].late_percent == 2.5


def test_edit_lease_doesnt_ask_for_a_due_day_or_check_date(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="")
    page = client.get(f"/leases/{lease_id}/edit").get_data(as_text=True)
    assert 'name="rent_due_day"' not in page and 'name="rent_checked_from"' not in page
    assert "Rent is due on this day each month." in page


def test_saving_a_lease_keeps_its_check_date_and_moves_the_due_day_with_the_start_date(client, app):
    # (If saving cleared the date, months of old "unpaid" rent would come back.)
    today = date.today()
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="")
    client.post(f"/leases/{lease_id}/edit", data={"tenant_names": "Jane Doe", "start_date": "2020-01-15", "rent_due_day": "20", "rent_checked_from": ""})
    with app.app_context():
        lease = db_session.get(Lease, lease_id)
        assert lease.start_date == date(2020, 1, 15) and lease.rent_due_day == 15
        assert lease.rent_checked_from == today.replace(day=1)


def test_deleting_a_lease_forever_deletes_its_waivers(client, app):
    _home, lease_id, due = recently_due(client, app)
    client.post(f"/leases/{lease_id}/rent/waive", data={"month": f"{due:%Y-%m}", "what": "fee"})
    client.post(f"/leases/{lease_id}/archive")
    client.post(f"/archive/delete/lease/{lease_id}")
    with app.app_context():
        assert db_session.get(Lease, lease_id) is None and db_session.query(RentWaiver).count() == 0


def test_the_lease_page_has_the_rent_rate_and_no_rent_payments_or_rent_line(client, app):
    _home, lease_id = set_up(client, app)
    page = client.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert "<h2>Rent rate</h2>" in page and 'id="rent"' in page
    assert "Rent payments" not in page and "<dt>Rent</dt>" not in page and "a month</a>" not in page


def test_rent_turns_red_the_day_its_late_fee_is_added(client, app):
    _home, lease_id = set_up(client, app, start="2026-03-01")
    with app.app_context():
        lease = db_session.get(Lease, lease_id)
        assert not rent_ledger.owing(lease, date(2026, 3, 2))[0].past_grace
        assert not rent_ledger.owing(lease, date(2026, 3, 5))[0].past_grace  # 4 days late
        assert rent_ledger.owing(lease, date(2026, 3, 6))[0].past_grace  # 5 days late: the fee is added
        for period in lease.rent_periods:
            period.late_after_days = 10
        assert not rent_ledger.owing(lease, date(2026, 3, 10))[0].past_grace  # a longer wait for this rent


def first_on_home(client, words) -> str:
    """The section of Home's "Needs attention" with these words."""
    page = client.get("/").get_data(as_text=True)
    return next(part for part in page.split('<li class="alert-panel">')[1:] if words in part)


def test_rent_that_has_a_while_yet_shows_in_yellow(client, app):
    home, lease_id = set_up(client, app, start="2026-03-01")
    with app.app_context():
        # (So many days that it's still before the late fee, whatever day the tests run.)
        for period in db_session.get(Lease, lease_id).rent_periods:
            period.late_after_days = 100000
        db_session.commit()
    section = first_on_home(client, "rent for Elm St")
    assert 'class="chip chip-gold"' in section and 'tag-warning tag-small">Due</span>' in section
    assert "alert-pill urgent" not in client.get("/").get_data(as_text=True)
    reminders = client.get("/reminders/").get_data(as_text=True)
    assert 'class="chip chip-gold"' in reminders and 'tag-warning tag-small">Due</span>' in reminders
    assert 'tag-warning">Due</span>' in client.get(f"/properties/{home}").get_data(as_text=True)
    assert "message-warning rent-due" in client.get(f"/leases/{lease_id}").get_data(as_text=True)


def test_rent_past_its_late_fee_shows_in_red(client, app):
    home, lease_id = set_up(client, app, start="2026-03-01")
    section = first_on_home(client, "March rent for Elm St")
    assert 'class="chip chip-red"' in section and 'tag-late tag-small">Now</span>' in section
    assert "alert-pill urgent" in client.get("/").get_data(as_text=True)
    reminders = client.get("/reminders/").get_data(as_text=True)
    assert 'class="chip chip-red"' in reminders and 'tag-late tag-small">Now</span>' in reminders
    assert 'tag-late">Now</span>' in client.get(f"/properties/{home}").get_data(as_text=True)
    assert "message-error rent-due" in client.get(f"/leases/{lease_id}").get_data(as_text=True)
