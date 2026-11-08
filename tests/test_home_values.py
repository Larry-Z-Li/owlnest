"""Each home's estimated value from RentCast, and the rules that keep lookups within its 50 free a month.

These tests never reach RentCast: they pass a stand-in that counts the questions.
"""

import html
import json
import re
import urllib.error
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from conftest import BrowserLikeClient
from helpers import _new_id, add_property
from sqlalchemy import select

from property_manager import create_app, home_values
from property_manager.config import KEY_HEADER
from property_manager.database import db_session
from property_manager.formatting import nice_date
from property_manager.home_values import Answer, LookupProblem, look_up_due_values
from property_manager.models import Property, ValueLookup

KEY = "a-test-key-that-is-long-enough-to-be-accepted"
# Lookups in the tests start now, because Settings counts the lookups in the 31 days before now.
START = datetime.now().replace(microsecond=0)


class FakeRentCast:
    """Stands in for RentCast: writes down each address it's asked about, and answers (or fails)."""

    def __init__(self, dollars=500_000, problem=None):
        self.dollars = dollars
        self.problem = problem
        self.asked = []

    def __call__(self, address, key):
        assert key == "test-rentcast-key"
        self.asked.append(address)
        if self.problem:
            raise self.problem
        return Answer(self.dollars * 100, self.dollars * 90, self.dollars * 110, bedrooms=3, bathrooms=2.5, square_feet=1916)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "test-rentcast-key")
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    app.test_client_class = BrowserLikeClient
    return app


@pytest.fixture
def landlord(server):
    client = server.test_client()
    client.environ_base["HTTP_" + KEY_HEADER.upper().replace("-", "_")] = KEY
    return client


def add_home(client, nickname, address, look_up=True) -> int:
    """Add a property, with "Look up its estimated value each month" ticked (unless look_up is False)."""
    data = {"nickname": nickname, "address": address, "look_up_value": "1" if look_up else ""}
    return _new_id(client.post("/properties/new", data=data))


def add_homes(client, how_many) -> list[int]:
    return [add_home(client, f"Home {n:02d}", f"{n} Elm St\nSpringfield, IL 62701") for n in range(1, how_many + 1)]


def lookups(server) -> list[ValueLookup]:
    with server.app_context():
        return list(db_session.scalars(select(ValueLookup)))


def page(client, path) -> str:
    """The page's text, with &#39; turned back into ' and so on."""
    return html.unescape(client.get(path).get_data(as_text=True))


def words(client, path) -> str:
    """Just the words on the page, as one line."""
    return " ".join(re.sub(r"<[^>]+>", " ", page(client, path)).split())


# ---------- When lookups happen ----------


def test_only_the_server_looks_values_up_and_only_with_a_key(server, landlord, client, app, monkeypatch):
    add_homes(landlord, 2)
    add_property(client, address="1 Elm St, Springfield, IL 62701")
    rentcast = FakeRentCast()

    look_up_due_values(app, now=START, ask=rentcast)  # the app on a computer, even with a key
    monkeypatch.delenv("RENTCAST_API_KEY")
    look_up_due_values(server, now=START, ask=rentcast)  # the server without a key
    assert rentcast.asked == []
    assert "Off. To look up each home's value" in page(landlord, "/settings/")


def test_fetching_on_request_follows_the_same_rules(server, landlord, monkeypatch):
    add_homes(landlord, 2)
    add_home(landlord, "Not ticked", "8 Maple Ct, Springfield, IL 62701", look_up=False)
    rentcast = FakeRentCast(dollars=400_000)
    monkeypatch.setattr(home_values, "ask_rentcast", rentcast)
    assert "look-up" not in page(landlord, "/settings/")  # no button: it's only called on request

    answer = landlord.post("/settings/home-values/look-up").get_data(as_text=True)
    assert answer == "Home values: Home 01: $400,000\nHome values: Home 02: $400,000"
    assert len(rentcast.asked) == 2

    # Asking again asks RentCast nothing: no home is due.
    assert "No home is due" in landlord.post("/settings/home-values/look-up").get_data(as_text=True)
    assert len(rentcast.asked) == 2

    # Without a key, there's nothing to call.
    monkeypatch.delenv("RENTCAST_API_KEY")
    assert landlord.post("/settings/home-values/look-up").status_code == 404
    assert len(rentcast.asked) == 2


def test_each_home_is_looked_up_once_then_again_32_days_later(server, landlord):
    add_homes(landlord, 3)
    add_home(landlord, "No ZIP", "5 Oak Ave")
    add_home(landlord, "Not ticked", "8 Maple Ct, Springfield, IL 62701", look_up=False)
    sold = add_home(landlord, "Sold", "9 Pine St, Springfield, IL 62701")
    landlord.post(f"/properties/{sold}/archive")
    rentcast = FakeRentCast()

    look_up_due_values(server, now=START, ask=rentcast)
    # One line each, and never an archived home, one without a ZIP code, or one not ticked.
    assert rentcast.asked == [f"{n} Elm St, Springfield, IL 62701" for n in (1, 2, 3)]
    settings = page(landlord, "/settings/")
    assert "Lookups in the last 31 days: <strong>3</strong>" in settings
    assert "skipped until their address has a ZIP code:\n        No ZIP" not in settings  # (it's a link)
    assert "skipped until their address has a ZIP code" in settings and ">No ZIP</a>" in settings
    assert ">Not ticked</a>" not in settings

    for later in (timedelta(hours=1), timedelta(days=10), timedelta(days=31, hours=23)):
        look_up_due_values(server, now=START + later, ask=rentcast)
    assert len(rentcast.asked) == 3  # nothing more within 32 days

    look_up_due_values(server, now=START + timedelta(days=32), ask=rentcast)
    assert len(rentcast.asked) == 6


def test_never_more_than_40_lookups_in_any_31_days(server, landlord):
    add_homes(landlord, 45)
    rentcast = FakeRentCast()
    asked_at = []
    for half_days in range(0, 2 * 100):  # twice a day for 100 days
        now = START + timedelta(hours=12 * half_days)
        before = len(rentcast.asked)
        look_up_due_values(server, now=now, ask=rentcast)
        asked_at += [now] * (len(rentcast.asked) - before)

    assert len(asked_at) > 45  # every home was looked up, some more than once
    assert len(set(rentcast.asked)) == 45
    for start in asked_at:
        in_31_days = [when for when in asked_at if start <= when < start + timedelta(days=31)]
        assert len(in_31_days) <= 40


def test_a_lookup_counts_even_if_the_app_stops_while_asking(server, landlord):
    add_homes(landlord, 2)

    def stops_while_asking(address, key):
        raise RuntimeError("the server restarted in the middle of the question")

    with pytest.raises(RuntimeError):
        look_up_due_values(server, now=START, ask=stops_while_asking)
    assert len(lookups(server)) == 1  # saved before it was sent

    rentcast = FakeRentCast()
    look_up_due_values(server, now=START + timedelta(hours=1), ask=rentcast)
    assert rentcast.asked == ["2 Elm St, Springfield, IL 62701"]  # home 1 waits a week, like a failed lookup


def test_a_failed_lookup_is_tried_again_a_week_later(server, landlord):
    home = add_homes(landlord, 1)[0]
    not_found = FakeRentCast(problem=LookupProblem("RentCast couldn't find this address."))
    look_up_due_values(server, now=START, ask=not_found)
    assert "didn't work: RentCast couldn't find this address." in page(landlord, f"/properties/{home}")

    look_up_due_values(server, now=START + timedelta(days=6, hours=23), ask=not_found)
    assert len(not_found.asked) == 1

    rentcast = FakeRentCast(dollars=468_000)
    look_up_due_values(server, now=START + timedelta(days=7), ask=rentcast)
    assert len(rentcast.asked) == 1
    shown = page(landlord, f"/properties/{home}")
    assert "$468,000" in shown and "Most likely between $421,200 and $514,800." in shown
    assert "didn't work" not in shown


def test_a_rejected_key_stops_every_lookup_until_the_server_restarts(server, landlord):
    add_homes(landlord, 3)
    rejected = FakeRentCast(problem=LookupProblem("RentCast didn't accept the API key.", stop_all=True))
    look_up_due_values(server, now=START, ask=rejected)
    look_up_due_values(server, now=START + timedelta(days=40), ask=rejected)
    assert len(rejected.asked) == 1
    assert "Lookups have stopped: RentCast didn't accept the API key." in page(landlord, "/settings/")

    server.config.pop("VALUE_LOOKUPS_STOPPED")  # what restarting the server does
    rentcast = FakeRentCast()
    look_up_due_values(server, now=START + timedelta(days=40), ask=rentcast)
    assert len(rentcast.asked) == 3


def test_if_rentcast_cant_be_reached_the_other_homes_wait_an_hour(server, landlord):
    add_homes(landlord, 3)
    unreachable = FakeRentCast(problem=LookupProblem("RentCast couldn't be reached.", stop_round=True))
    look_up_due_values(server, now=START, ask=unreachable)
    assert len(unreachable.asked) == 1

    rentcast = FakeRentCast()
    look_up_due_values(server, now=START + timedelta(hours=1), ask=rentcast)
    assert rentcast.asked == [f"{n} Elm St, Springfield, IL 62701" for n in (2, 3)]


def test_a_new_address_is_looked_up_right_away(server, landlord):
    home = add_homes(landlord, 1)[0]
    rentcast = FakeRentCast()
    look_up_due_values(server, now=START, ask=rentcast)
    landlord.post(
        f"/properties/{home}/edit",
        data={"nickname": "Home 01", "address": "7 Elm St, Springfield, IL 62701", "look_up_value": "1"},
    )
    look_up_due_values(server, now=START + timedelta(hours=1), ask=rentcast)
    assert rentcast.asked == ["1 Elm St, Springfield, IL 62701", "7 Elm St, Springfield, IL 62701"]


def test_deleting_a_home_forever_keeps_its_lookups_counted(server, landlord):
    home = add_homes(landlord, 1)[0]
    look_up_due_values(server, now=START, ask=FakeRentCast())
    landlord.post(f"/properties/{home}/archive")
    assert "was deleted forever" in landlord.post(f"/archive/delete/property/{home}").get_data(as_text=True)
    [lookup] = lookups(server)
    assert lookup.property_id is None
    with server.app_context():
        assert home_values.lookups_since(START - timedelta(days=1)) == 1


# ---------- What she sees ----------


def test_values_on_the_property_list_and_home(server, landlord):
    add_home(landlord, "Aspen", "410 Aspen Rd, Springfield, IL 62701")
    add_home(landlord, "Not ticked", "5 Oak Ave, Springfield, IL 62701", look_up=False)
    look_up_due_values(server, now=START, ask=FakeRentCast(dollars=332_000))
    assert "Aspen is worth about $332,000 An estimate from RentCast" in words(landlord, "/")
    assert "Est. value" in page(landlord, "/properties/")

    add_homes(landlord, 2)
    look_up_due_values(server, now=START + timedelta(hours=1), ask=FakeRentCast(dollars=400_000))
    assert "3 of your homes are worth about $1,132,000" in words(landlord, "/")

    look_up_due_values(server, now=START + timedelta(days=33), ask=FakeRentCast(dollars=410_000))
    home = words(landlord, "/")
    assert "3 of your homes are worth about $1,230,000" in home
    assert f"+$98,000 since {nice_date(START)}" in home


def test_a_home_not_ticked_says_how_to_turn_it_on(server, landlord):
    home = add_home(landlord, "Aspen", "410 Aspen Rd, Springfield, IL 62701", look_up=False)
    assert "tick “Look up its estimated value each month”" in page(landlord, f"/properties/{home}")
    landlord.post(
        f"/properties/{home}/edit",
        data={"nickname": "Aspen", "address": "410 Aspen Rd, Springfield, IL 62701", "look_up_value": "1"},
    )
    assert "checked" in page(landlord, f"/properties/{home}/edit").split('name="look_up_value"')[1][:40]
    assert "will be looked up within the next hour" in page(landlord, f"/properties/{home}")


# ---------- Asking RentCast ----------


def test_asking_rentcast(monkeypatch):
    sent = []

    def answers(request, timeout, context):
        sent.append(request)
        home = {"formattedAddress": "5 Rockingham Dr", "bedrooms": 4, "bathrooms": 3.5, "squareFootage": 2850}
        answer = {"price": 468000, "priceRangeLow": 440000, "priceRangeHigh": 495000, "subjectProperty": home}
        return BytesIO(json.dumps(answer).encode())

    monkeypatch.setattr(home_values.urllib.request, "urlopen", answers)
    assert home_values.ask_rentcast("5 Rockingham Dr, Newport Beach, CA 92660", "the-key") == Answer(
        46_800_000, 44_000_000, 49_500_000, bedrooms=4, bathrooms=3.5, square_feet=2850
    )
    [request] = sent
    assert request.full_url == "https://api.rentcast.io/v1/avm/value?address=5+Rockingham+Dr%2C+Newport+Beach%2C+CA+92660"
    assert request.get_header("X-api-key") == "the-key"


@pytest.mark.parametrize(
    "code, stop_round, stop_all, words",
    [
        (401, True, True, "didn't accept the API key"),
        (404, False, False, "couldn't find this address"),
        (429, True, False, "slow down"),
        (500, True, False, "RentCast had a problem"),
        (400, False, False, "error 400: Invalid address"),
    ],
)
def test_rentcast_errors(monkeypatch, code, stop_round, stop_all, words):
    def refuses(request, timeout, context):
        raise urllib.error.HTTPError(request.full_url, code, "No", {}, BytesIO(b'{"message": "Invalid address"}'))

    monkeypatch.setattr(home_values.urllib.request, "urlopen", refuses)
    with pytest.raises(LookupProblem) as caught:
        home_values.ask_rentcast("1 Elm St, Springfield, IL 62701", "the-key")
    assert words in str(caught.value)
    assert (caught.value.stop_round, caught.value.stop_all) == (stop_round, stop_all)


def test_no_answer_from_rentcast(monkeypatch):
    def unreachable(request, timeout, context):
        raise urllib.error.URLError("no internet")

    monkeypatch.setattr(home_values.urllib.request, "urlopen", unreachable)
    with pytest.raises(LookupProblem) as caught:
        home_values.ask_rentcast("1 Elm St, Springfield, IL 62701", "the-key")
    assert "couldn't be reached (no internet)" in str(caught.value) and caught.value.stop_round


def test_an_answer_without_details_of_the_home_still_gives_the_value():
    answer = home_values.read_answer(b'{"price": 300000, "subjectProperty": {"bedrooms": "three", "squareFootage": 0}}')
    assert answer == Answer(30_000_000, None, None)


def test_a_lookup_fills_in_only_the_details_she_left_empty(server, landlord):
    home = add_home(landlord, "Aspen", "410 Aspen Rd, Springfield, IL 62701")
    landlord.post(
        f"/properties/{home}/edit",
        data={"nickname": "Aspen", "address": "410 Aspen Rd, Springfield, IL 62701", "look_up_value": "1", "bedrooms": "4"},
    )
    look_up_due_values(server, now=START, ask=FakeRentCast())
    assert "4 beds · 2.5 baths · 1,916 sq ft" in page(landlord, f"/properties/{home}")  # her 4, RentCast's others


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"price": null}', b'{"price": "lots"}', b"[1, 2]"])
def test_an_answer_without_a_value(body):
    with pytest.raises(LookupProblem):
        home_values.read_answer(body)


# ---------- Her own value (Larry, 2026-11-05: "the custom value would override") ----------


def edit_home(client, home, **fields):
    data = {"nickname": "Aspen", "address": "410 Aspen Rd, Springfield, IL 62701", "look_up_value": "1", **fields}
    return client.post(f"/properties/{home}/edit", data=data)


def test_her_own_value_shows_instead_of_rentcasts_even_while_it_keeps_looking(server, landlord):
    home = add_home(landlord, "Aspen", "410 Aspen Rd, Springfield, IL 62701")
    look_up_due_values(server, now=START, ask=FakeRentCast(dollars=332_000))
    assert "Your own estimated value" in page(landlord, f"/properties/{home}/edit")

    edit_home(landlord, home, own_value="350,000")
    detail = words(landlord, f"/properties/{home}")
    assert "$350,000" in detail and "Your own value, set" in detail and "whose latest estimate is $332,000" in detail
    assert "Aspen is worth about $350,000 Your own value" in words(landlord, "/")
    assert "$350,000" in page(landlord, "/properties/")

    # Ticked, it's still looked up a month later, but her value stays the one shown.
    look_up_due_values(server, now=START + timedelta(days=33), ask=FakeRentCast(dollars=340_000))
    assert len(lookups(server)) == 2
    detail = words(landlord, f"/properties/{home}")
    assert "$350,000" in detail and "whose latest estimate is $340,000" in detail

    # Saving again without changing it keeps the day she set it.
    with server.app_context():
        set_at = db_session.get(Property, home).own_value_set_at
    edit_home(landlord, home, own_value="350,000")
    with server.app_context():
        assert db_session.get(Property, home).own_value_set_at == set_at

    # Emptied, RentCast's shows again.
    edit_home(landlord, home, own_value="")
    detail = words(landlord, f"/properties/{home}")
    assert "$340,000" in detail and "From RentCast" in detail and "Your own value" not in detail


def test_her_own_value_works_without_rentcast_too(server, landlord):
    home = add_home(landlord, "Not ticked", "5 Oak Ave, Springfield, IL 62701", look_up=False)
    edit_home(landlord, home, nickname="Not ticked", address="5 Oak Ave, Springfield, IL 62701", look_up_value="", own_value="210,500")
    assert "Not ticked is worth about $210,500 Your own value" in words(landlord, "/")
    assert 'aria-invalid="true"' in edit_home(landlord, home, own_value="lots").get_data(as_text=True)
