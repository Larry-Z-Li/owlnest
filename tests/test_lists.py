"""The Transactions, Properties and Maintenance lists: their groups, filters, tabs and totals."""

import json
import re
from datetime import date, timedelta

from helpers import add_lease, add_property
from test_transactions_and_maintenance import add_request, add_transaction


def page_of(client, path):
    return client.get(path).get_data(as_text=True)


def open_months(page):
    """The names of the month groups that start open."""
    return re.findall(r'<details class="month" open>\s*<summary>\s*<h2 class="month-name">.*?</svg>(.*?)</h2>', page, re.S)


def all_months(page):
    return re.findall(r'<h2 class="month-name">.*?</svg>(.*?)</h2>', page, re.S)


def on(day):
    return {"transaction_date": day.isoformat()}


# ---------- Transactions ----------


def test_transactions_are_grouped_by_month_with_each_months_totals(client):
    add_transaction(client, direction="in", amount="3,000", category="Rental Income", transaction_date="2026-03-15")
    add_transaction(client, direction="out", amount="500", transaction_date="2026-03-20")
    add_transaction(client, direction="out", amount="100", transaction_date="2026-01-05")
    page = page_of(client, "/transactions/")
    assert all_months(page) == ["March 2026", "January 2026"]
    # The whole list, then March's own totals.
    assert "3 transactions" in page
    assert '<strong class="money-in">$3,000.00</strong>' in page and "<strong>$600.00</strong>" in page
    assert "<strong>$500.00</strong>" in page and "<strong>$2,500.00</strong>" in page


def test_the_three_newest_months_start_open_and_older_ones_start_folded(client):
    for month in (1, 2, 3, 4, 5):
        add_transaction(client, transaction_date=f"2020-0{month}-15")
    page = page_of(client, "/transactions/")
    assert all_months(page) == ["May 2020", "April 2020", "March 2020", "February 2020", "January 2020"]
    assert open_months(page) == ["May 2020", "April 2020", "March 2020"]
    # The folded months are still there, with all their rows, one click away.
    assert page.count('class="ledger-row"') == 5


def test_filtering_opens_every_month(client):
    add_transaction(client, details="roof", transaction_date="2020-03-15")
    add_transaction(client, details="roof", transaction_date="2019-03-15")
    page = page_of(client, "/transactions/?q=roof")
    assert open_months(page) == ["March 2020", "March 2019"]


def test_the_period_menu(client):
    this_year = date.today().year
    add_transaction(client, **on(date(this_year, 1, 2)))
    add_transaction(client, **on(date(this_year - 1, 1, 2)))
    add_transaction(client, **on(date(this_year - 2, 1, 2)))
    assert len(all_months(page_of(client, "/transactions/"))) == 3
    assert all_months(page_of(client, "/transactions/?period=this-year")) == [f"January {this_year}"]
    assert all_months(page_of(client, "/transactions/?period=last-year")) == [f"January {this_year - 1}"]
    dates = f"from={this_year - 2}-01-01&to={this_year - 2}-12-31"
    assert all_months(page_of(client, f"/transactions/?period=custom&{dates}")) == [f"January {this_year - 2}"]
    # A link with dates and no period still works.
    assert all_months(page_of(client, f"/transactions/?{dates}")) == [f"January {this_year - 2}"]


def test_a_transaction_marked_check_has_a_tag(client):
    add_transaction(client, details="CHECK: which home?")
    add_transaction(client, details="Plain")
    assert page_of(client, "/transactions/").count("tag-check") == 1


def test_other_income_is_a_type_of_income(client):
    assert "Other Income" in page_of(client, "/transactions/new")
    response = add_transaction(client, direction="in", amount="1,700", category="Other Income", transaction_date=f"{date.today().year}-01-02")
    assert response.status_code == 200
    assert "+$1,700.00" in page_of(client, "/transactions/")
    assert "Other Income" in page_of(client, "/reports/")


def test_the_merged_types_are_offered_only_under_one_name(client):
    for page in (page_of(client, "/transactions/new"), page_of(client, "/maintenance/new")):
        for name in (">Rental Income<", ">Repair &amp; Maintenance<", ">HOA<", ">Cleaning<"):
            assert page.count(name) == 1
        for name in (">Rents<", ">Repairs<", ">HOA Dues<", ">Cleaning &amp; Upkeep<"):
            assert name not in page


def test_rental_income_counts_as_rent_on_home(client):
    add_transaction(client, direction="in", amount="2,400", category="Rental Income", **on(date.today()))
    assert "$2,400" in page_of(client, "/")


# ---------- Properties ----------


def test_the_properties_summary_counts_homes_and_rent(client):
    add_lease(client, add_property(client, "Elm St"), rent="2,400")
    add_property(client, "Oak Ave")
    page = page_of(client, "/properties/")
    assert "2 homes, 1 leased" in page and "$2,400 a month" in page


def test_showing_only_leased_or_vacant_homes(client):
    add_lease(client, add_property(client, "Elm St"))
    add_property(client, "Oak Ave")
    leased = page_of(client, "/properties/?show=leased")
    vacant = page_of(client, "/properties/?show=vacant")
    assert "Elm St</a>" in leased and "Oak Ave</a>" not in leased
    assert "Oak Ave</a>" in vacant and "Elm St</a>" not in vacant
    # The summary is still about all of them.
    assert "2 homes, 1 leased" in leased


def test_searching_properties_by_tenant(client):
    add_lease(client, add_property(client, "Elm St"), tenants="Jane Doe")
    add_property(client, "Oak Ave")
    page = page_of(client, "/properties/?q=jane")
    assert "Elm St</a>" in page and "Oak Ave</a>" not in page
    assert "No properties match." in page_of(client, "/properties/?q=nobody")


def test_sorting_properties_by_rent_and_by_lease(client):
    add_lease(client, add_property(client, "Elm St"), rent="1,000", end="2031-01-01")
    add_lease(client, add_property(client, "Oak Ave"), rent="3,000", end="2032-01-01")
    add_property(client, "Pine St")

    def order(sort):
        return re.findall(r'class="row-link"[^>]*>(.*?)</a>', page_of(client, f"/properties/?sort={sort}"))

    assert order("rent") == ["Oak Ave", "Elm St", "Pine St"]
    assert order("lease") == ["Elm St", "Oak Ave", "Pine St"]


def test_the_lease_tag_says_how_a_home_stands(client):
    today = date.today()
    add_property(client, "Vacant home")
    add_lease(client, add_property(client, "Monthly"), tenants="A", end="")
    add_lease(client, add_property(client, "Soon"), tenants="B", end=(today + timedelta(days=10)).isoformat())
    add_lease(client, add_property(client, "Far"), tenants="C", end=(today + timedelta(days=400)).isoformat())
    add_lease(client, add_property(client, "Over"), tenants="D", start="2020-01-01", end="2020-12-31")
    page = page_of(client, "/properties/")
    assert ">Vacant</span>" in page and "Month-to-month</span>" in page
    assert re.search(r'tag-warning">Ends ', page) and re.search(r'tag-active">Until ', page)
    assert 'tag-late">Ended Dec 31, 2020</span>' in page


def test_the_properties_list_shows_the_town_not_the_street(client):
    add_property(client, "Elm St", "149 Elm St, Springfield, IL 62704")
    page = page_of(client, "/properties/")
    assert "Springfield, IL 62704" in page and "149 Elm St" not in page


# ---------- Maintenance ----------


def tab_counts(page):
    """The numbers in the tabs, keyed by the statuses each one counts."""
    return {
        tuple(json.loads(statuses.replace("&#34;", '"'))): int(number)
        for statuses, number in re.findall(r"data-counts='(.*?)'>(\d+)<", page)
    }


def test_the_tabs_count_and_show_their_requests(client):
    elm = add_property(client)
    add_request(client, elm, title="First", status="Open")
    add_request(client, elm, title="Second", status="In progress")
    add_request(client, elm, title="Third", status="Closed")

    page = page_of(client, "/maintenance/")
    assert tab_counts(page) == {("Open", "In progress"): 2, ("Closed",): 1}
    assert "First</a>" in page and "Second</a>" in page and "Third</a>" not in page

    closed = page_of(client, "/maintenance/?show=Closed")
    assert "Third</a>" in closed and "First</a>" not in closed
    assert tab_counts(closed) == {("Open", "In progress"): 2, ("Closed",): 1}

    everything = page_of(client, "/maintenance/?show=all")
    assert "First</a>" in everything and "Third</a>" in everything


def test_the_tab_counts_follow_the_property_menu(client):
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave")
    add_request(client, elm, title="First")
    add_request(client, oak, title="Second")
    page = page_of(client, f"/maintenance/?property_id={elm}")
    assert tab_counts(page) == {("Open", "In progress"): 1, ("Closed",): 0}
    # Changing tabs keeps the property chosen.
    assert f"show=Closed&amp;property_id={elm}" in page or f"property_id={elm}&amp;show=Closed" in page


def test_a_row_opens_without_a_part_of_its_link_stretched_over_the_window(client):
    # (In some versions of Safari that covered the whole window, so any click near the top opened the last row.)
    add_property(client)
    assert "row_links.js" in client.get("/properties/").get_data(as_text=True)
    style = client.get("/static/style.css").get_data(as_text=True)
    assert ".row-link::after" not in style


def test_a_house_thumbnail_links_to_its_page(client):
    home = add_property(client)
    page = client.get("/properties/").get_data(as_text=True)
    assert f'<a class="home-thumb-link" href="/properties/{home}"' in page


def test_the_request_tabs_keep_their_spaces(client):
    # (Words split across a flex box's parts lose their spaces: "Openand in progress".)
    page = page_of(client, "/maintenance/")
    tabs = page.split('class="list-tabs"')[1].split("</nav>")[0]
    assert re.search(r'aria-current="true"><span>Open<span class="wide-only"> and in progress</span> \(', tabs)
    assert ">Closed (" in tabs and tabs.count("</span></a>") >= 2


def test_a_rows_words_dont_turn_blue_and_underlined_on_hover(client):
    style = client.get("/static/style.css").get_data(as_text=True)
    assert "tr:hover .row-link" not in style


def test_a_propertys_page_has_a_transactions_button_and_an_all_requests_row(client):
    home = add_property(client)
    page = page_of(client, f"/properties/{home}")
    assert f'<a class="button button-secondary button-with-icon" href="/transactions/?property_id={home}"><span class="button-dollar">' in page
    assert "Property transactions</a>" in page
    assert "Transactions for" not in page
    assert 'class="see-all"' not in page  # (No requests yet, so nothing to list.)

    add_request(client, home)
    page = page_of(client, f"/properties/{home}")
    row = page.split('class="see-all"')[1].split("</a>")[0]
    assert f'href="/maintenance/?property_id={home}&amp;show=all"' in row
    assert "All requests" in row and '<span class="see-all-count">1</span>' in row
    assert "All requests for" not in page


def test_a_tenants_name_on_a_propertys_page_is_plain_words(client):
    home = add_property(client)
    lease = add_lease(client, home, tenants="Jane Doe", start="2020-01-01", end="")
    page = page_of(client, f"/properties/{home}")
    assert "<dd>Jane Doe</dd>" in page
    assert f'<a class="button" href="/leases/{lease}">Open lease</a>' in page
