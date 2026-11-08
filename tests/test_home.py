"""The Home page: the blue band, the four tiles, and the property cards."""

import html
import re
from datetime import date

from test_transactions_and_maintenance import add_request
from helpers import add_lease, add_property


def words(client, path="/") -> str:
    """Just the words on the page, as one line."""
    page = html.unescape(client.get(path).get_data(as_text=True))
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def add_money(client, direction, amount, category, property_id):
    client.post(
        "/transactions/new",
        data={
            "transaction_date": date.today().isoformat(),
            "direction": direction,
            "amount": amount,
            "category": category,
            "property_id": str(property_id),
        },
    )


def test_the_band_counts_the_homes_until_they_have_values(client):
    add_lease(client, add_property(client))
    add_property(client, nickname="Oak Ave", address="22 Oak Ave")
    assert "Your properties 2 homes 1 with a current lease" in words(client)


def test_the_tiles_add_up_this_months_rent_and_the_years_cash_flow(client):
    elm = add_property(client)
    add_lease(client, elm, rent="2,400")
    oak = add_property(client, nickname="Oak Ave", address="22 Oak Ave")
    add_lease(client, oak, tenants="Bob Smith", rent="2,150")
    add_money(client, "in", "2,400", "Rental Income", elm)
    add_money(client, "out", "400", "Repair & Maintenance", elm)

    home = words(client)
    month, year = f"{date.today():%B}", date.today().year
    assert f"Rent in {month} $2,400 of $4,550 a month from current leases" in home
    assert f"Net cash flow, {year} $2,000 January 1 to today" in home
    assert "Open repairs 0 Nothing to fix" in home

    add_money(client, "in", "2,150", "Rental Income", oak)
    assert f"Rent in {month} $4,550 All of this month's rent is in" in words(client)


def test_property_cards(client):
    elm = add_property(client)
    add_lease(client, elm, rent="2,400", end="2026-12-31")
    for n in range(1, 7):
        add_property(client, nickname=f"Home {n}", address=f"{n} Pine St")
    home = words(client)
    assert "Leased Elm St 149 Elm St Jane Doe · until Dec 31, 2026 Est. value — Rent $2,400.00" in home
    assert "Vacant Home 1 1 Pine St No current lease" in home
    # A card for every home, and no links to all of them or to add one.
    assert "Home 5" in home and "Home 6" in home
    assert "All 7 properties" not in home
    assert "Add a property" not in home


def test_the_homes_have_a_slider_above_them_too(client):
    add_property(client)
    page = client.get("/").get_data(as_text=True)
    assert 'data-scrolls="home-cards"' in page and 'id="home-cards"' in page and "top_slider.js" in page


def test_home_has_no_link_to_all_requests(client):
    assert "All requests" not in client.get("/").get_data(as_text=True)


def test_open_requests_on_home_are_clickable_sections_with_a_count(client):
    home = add_property(client)
    add_request(client, home)
    page = client.get("/").get_data(as_text=True)
    section = page.split('class="card attention requests"')[1].split("</section>")[0]
    assert 'class="list row-links request-rows"' in section and 'class="row-link"' in section
    assert 'class="alert-title"' in section and 'class="alert-detail"' in section
    assert '<span class="count-badge" data-counts=' in section and 'status-menu' in section
    # (The status menu's fade and Undo need these.)
    assert "data-shows=" in section and "<tbody>" in section
