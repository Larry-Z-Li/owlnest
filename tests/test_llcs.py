"""LLCs: adding them, putting homes in them, the lists and menus grouped by them, and reports for them."""

from datetime import date

from helpers import add_property
from test_transactions_and_maintenance import add_transaction

from property_manager import cash_flow, report_charts
from property_manager.database import db_session
from property_manager.models import Llc, Property, Transaction

LABEL_ALL = '<optgroup label="Maple Grove LLC">'


def add_llc(client, name="Maple Grove LLC"):
    return client.post("/properties/llcs/new", data={"name": name}, follow_redirects=True)


def page(client, address) -> str:
    return client.get(address).get_data(as_text=True)


def put_in_llc(client, home, llc=1, nickname="Elm St"):
    client.post(f"/properties/{home}/edit", data={"nickname": nickname, "llc_id": str(llc)})


def homes_in_order(client) -> list[str]:
    text = page(client, "/properties/")
    return [part.split("</span>")[0].strip(" >\n") for part in text.split('class="llc-name"')[1:]]


def test_adding_an_llc_is_just_a_name(client, app):
    assert "Add LLC" in page(client, "/properties/")
    response = add_llc(client)
    assert response.request.path == "/properties/" and "Maple Grove LLC was added." in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.query(Llc).one().name == "Maple Grove LLC"


def test_an_llc_needs_a_name_of_its_own(client, app):
    assert client.post("/properties/llcs/new", data={"name": ""}).status_code == 400
    add_llc(client)
    response = client.post("/properties/llcs/new", data={"name": "maple grove llc"})
    assert response.status_code == 400 and "already an LLC" in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.query(Llc).count() == 1


def test_renaming_an_llc(client, app):
    add_llc(client)
    response = client.post("/properties/llcs/1/edit", data={"name": "Maple Grove Holdings LLC"})
    assert response.request.path == "/properties/" and "Maple Grove Holdings LLC" in response.get_data(as_text=True)
    # Saving it under its own name is fine.
    response = client.post("/properties/llcs/1/edit", data={"name": "Maple Grove Holdings LLC"})
    assert response.request.path == "/properties/"


def test_renaming_is_a_pen_icon_with_a_name_for_screen_readers(client):
    add_llc(client)
    text = page(client, "/properties/")
    link = text.split('class="llc-rename"')[1].split("</a>")[0]
    assert 'href="/properties/llcs/1/edit"' in link and 'aria-label="Rename Maple Grove LLC"' in link and "<svg" in link
    assert ">Rename<" not in text


def test_adding_a_home_to_an_llc(client, app):
    add_llc(client)
    assert 'name="llc_id"' in page(client, "/properties/new") and ">Maple Grove LLC</option>" in page(client, "/properties/new")
    client.post("/properties/new", data={"nickname": "Elm St", "address": "149 Elm St", "llc_id": "1"})
    with app.app_context():
        assert db_session.query(Property).one().llc.name == "Maple Grove LLC"
    assert '<p class="property-llc">' in page(client, "/properties/1")


def test_editing_a_homes_llc(client, app):
    add_llc(client)
    home = add_property(client)
    assert "property-llc" not in page(client, f"/properties/{home}")
    put_in_llc(client, home)
    assert '<option value="1" selected>Maple Grove LLC</option>' in page(client, f"/properties/{home}/edit")
    client.post(f"/properties/{home}/edit", data={"nickname": "Elm St", "llc_id": ""})
    with app.app_context():
        assert db_session.get(Property, home).llc_id is None


def test_a_made_up_llc_is_refused(client):
    home = add_property(client)
    response = client.post(f"/properties/{home}/edit", data={"nickname": "Elm St", "llc_id": "99"})
    assert response.status_code == 400 and "no longer available" in response.get_data(as_text=True)


def test_properties_are_listed_by_llc_with_the_rest_at_the_end(client):
    add_llc(client, "Zebra LLC")
    add_llc(client, "Acme LLC")
    add_llc(client, "Empty LLC")
    put_in_llc(client, add_property(client, "Elm St"), llc=1)
    put_in_llc(client, add_property(client, "Oak Ave", "12 Oak Ave"), llc=2, nickname="Oak Ave")
    add_property(client, "Pine Rd", "7 Pine Rd")
    assert homes_in_order(client) == ["Acme LLC", "Empty LLC", "Zebra LLC", "Not in an LLC"]
    text = page(client, "/properties/")
    assert "No homes yet." in text
    assert text.index("Acme LLC") < text.index("Oak Ave") < text.index("Zebra LLC") < text.index("Elm St") < text.index("Not in an LLC") < text.index("Pine Rd")


def test_properties_are_one_sheet_with_no_folding(client):
    add_llc(client, "Acme LLC")
    add_llc(client, "Zebra LLC")
    put_in_llc(client, add_property(client, "Elm St"), llc=1)
    put_in_llc(client, add_property(client, "Oak Ave", "12 Oak Ave"), llc=2, nickname="Oak Ave")
    add_property(client, "Pine Rd", "7 Pine Rd")
    text = page(client, "/properties/")
    assert text.count("<table") == 1 and text.count("<thead>") == 1 and "<details" not in text
    assert text.count('class="llc-band"') == 3 and text.count('colspan="4" scope="rowgroup"') == 3
    # A band only says how many homes.
    band = text.split('class="llc-totals"')[1].split("</span>")[0]
    assert "1 home" in band and "a month" not in band and "worth about" not in band


def test_with_no_llcs_every_home_is_not_in_an_llc(client):
    add_property(client)
    assert homes_in_order(client) == ["Not in an LLC"]


def test_with_every_home_in_an_llc_there_is_no_group_for_the_rest(client):
    add_llc(client)
    put_in_llc(client, add_property(client))
    assert homes_in_order(client) == ["Maple Grove LLC"]


def test_filtering_shows_only_the_groups_with_a_match(client):
    add_llc(client, "Acme LLC")
    add_llc(client, "Empty LLC")
    put_in_llc(client, add_property(client, "Elm St"), llc=1)
    add_property(client, "Pine Rd", "7 Pine Rd")
    text = page(client, "/properties/?q=Elm")
    assert "Acme LLC" in text and "Empty LLC" not in text and "Not in an LLC" not in text


def test_no_properties_at_all_still_says_so(client):
    assert "No properties yet." in page(client, "/properties/")


def test_every_menu_of_properties_is_grouped_by_llc(client):
    add_llc(client)
    put_in_llc(client, add_property(client))
    add_property(client, "Pine Rd", "7 Pine Rd")
    for address in ("/transactions/", "/transactions/new", "/maintenance/", "/maintenance/new", "/reminders/", "/reminders/new", "/reports/"):
        text = page(client, address)
        assert LABEL_ALL in text and '<optgroup label="Not in an LLC">' in text, address


def test_with_no_llcs_a_menu_is_just_the_list(client):
    add_property(client)
    for address in ("/transactions/new", "/maintenance/new", "/reminders/new", "/reports/"):
        assert "Not in an LLC" not in page(client, address), address


def test_a_chosen_home_stays_chosen_in_a_grouped_menu(client):
    add_llc(client)
    home = add_property(client)
    put_in_llc(client, home)
    assert f'<option value="{home}" selected>Elm St</option>' in page(client, f"/transactions/?property_id={home}")
    assert f'<option value="property-{home}" selected>Elm St</option>' in page(client, f"/reports/?scope=property-{home}")
    assert '<option value="llc-1" selected>Maple Grove LLC</option>' in page(client, "/reports/?scope=llc-1")


def money_for_two_llcs(client):
    add_llc(client, "Acme LLC")
    add_llc(client, "Zebra LLC")
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave", "12 Oak Ave")
    pine = add_property(client, "Pine Rd", "7 Pine Rd")
    put_in_llc(client, elm, llc=1)
    put_in_llc(client, oak, llc=2, nickname="Oak Ave")
    for home, amount in ((elm, "1,000"), (oak, "2,000"), (pine, "4,000")):
        add_transaction(client, direction="in", amount=amount, category="Rental Income", property_id=str(home), transaction_date="2026-03-01")
    add_transaction(client, direction="out", amount="50", category="License", transaction_date="2026-03-02")
    return elm, oak, pine


YEAR = "from=2026-01-01&to=2026-12-31"


def test_a_report_can_be_for_all_an_llc_or_one_home(client):
    _elm, oak, _pine = money_for_two_llcs(client)
    assert "$7,000.00" in page(client, f"/reports/?{YEAR}")
    text = page(client, f"/reports/?{YEAR}&scope=llc-1")
    assert "Acme LLC, Jan 1, 2026" in text and "$1,000.00" in text and "$7,000.00" not in text
    text = page(client, f"/reports/?{YEAR}&scope=property-{oak}")
    assert "Oak Ave, Jan 1, 2026" in text and "$2,000.00" in text
    # A link from before LLCs still works.
    assert "Oak Ave, Jan 1, 2026" in page(client, f"/reports/?{YEAR}&property_id={oak}")


def test_money_for_no_home_and_no_llc_isnt_in_an_llcs_report(client):
    money_for_two_llcs(client)
    assert "$50.00" in page(client, f"/reports/?{YEAR}")
    assert "$50.00" not in page(client, f"/reports/?{YEAR}&scope=llc-1")


def for_the_llc_itself(client, amount="75", llc="1"):
    add_transaction(client, direction="out", amount=amount, category="Accounting", llc_id=llc, transaction_date="2026-03-02", details="Fee")


def test_a_transaction_can_be_for_an_llc_itself(client, app):
    money_for_two_llcs(client)
    assert 'name="llc_id"' in page(client, "/transactions/new")
    for_the_llc_itself(client)
    with app.app_context():
        made = db_session.query(Transaction).filter_by(details="Fee").one()
        assert made.llc.name == "Acme LLC" and made.property is None
        made_id = made.id
    assert '<option value="1" selected>Acme LLC</option>' in page(client, f"/transactions/{made_id}")
    assert "Acme LLC · <span" in page(client, "/transactions/").replace("\n", "")


def test_a_transaction_for_a_property_goes_by_the_propertys_llc(client, app):
    elm, _oak, _pine = money_for_two_llcs(client)
    add_transaction(client, direction="out", amount="75", category="HOA", property_id=str(elm), llc_id="2", transaction_date="2026-03-02", details="Fee")
    with app.app_context():
        assert db_session.query(Transaction).filter_by(details="Fee").one().llc_id is None
    # It's Acme's, as Elm St is, whatever was sent for the LLC.
    assert "$75.00" in page(client, f"/reports/?{YEAR}&scope=llc-1")
    assert "$75.00" not in page(client, f"/reports/?{YEAR}&scope=llc-2")


def test_the_form_shows_the_propertys_llc_greyed_out(client):
    elm, _oak, _pine = money_for_two_llcs(client)
    add_transaction(client, direction="out", amount="75", category="HOA", property_id=str(elm), transaction_date="2026-03-02", details="Fee")
    text = page(client, "/transactions/5")
    field = text.split('id="llc_id"')[1].split("</select>")[0]
    assert "disabled" in field.split(">")[0] and '<option value="1" selected>Acme LLC</option>' in field
    assert 'id="llc-by-property"' in text


def test_with_no_llcs_a_transaction_has_no_llc_menu(client):
    assert 'name="llc_id"' not in page(client, "/transactions/new")
    add_transaction(client, details="Fine without one")
    assert "Fine without one" in page(client, "/transactions/")


def test_a_made_up_llc_on_a_transaction_is_refused(client):
    add_llc(client)
    response = add_transaction(client, llc_id="99")
    assert response.status_code == 400 and "no longer available" in response.get_data(as_text=True)


def test_an_llcs_report_has_the_money_for_the_llc_itself(client):
    money_for_two_llcs(client)
    for_the_llc_itself(client)
    assert "$75.00" in page(client, f"/reports/?{YEAR}&scope=llc-1")
    assert "$75.00" not in page(client, f"/reports/?{YEAR}&scope=llc-2")
    head = page(client, f"/reports/?{YEAR}&scope=llc-1&interval=property").split("<thead>")[1].split("</thead>")[0]
    assert "The LLC itself" in head and "Elm St" in head
    # In the column for its LLC, not with money in no LLC.
    with client.application.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_LLC)
        accounting = next(row for section in report.sections for row in section.rows if row.label == "Accounting")
        assert report.columns[0] == "Acme LLC" and accounting.values[0] == -7500


def test_transactions_can_show_a_whole_llc(client):
    money_for_two_llcs(client)
    for_the_llc_itself(client)
    text = page(client, "/transactions/?property_id=llc-1")
    assert '<option value="llc-1" selected>Acme LLC</option>' in text
    assert "$1,000.00" in text and "$75.00" in text and "$2,000.00" not in text


def test_a_transaction_for_an_llc_lands_in_its_chart_slice(client, app):
    money_for_two_llcs(client)
    add_transaction(client, direction="in", amount="9", category="Other Income", llc_id="2", transaction_date="2026-03-02")
    charts, _net = charts_for(app)
    shares = {one["name"]: one["text"] for one in charts["shares"]["pie"]["slices"]}
    assert shares["Zebra LLC"] == "$2,009.00"


def test_the_report_has_a_jump_to_the_charts(client):
    money_for_two_llcs(client)
    text = page(client, f"/reports/?{YEAR}")
    assert 'href="#charts"' in text and 'id="charts"' in text
    assert 'href="#charts"' not in page(client, "/reports/?from=2020-01-01&to=2020-12-31")


def test_a_report_can_have_a_column_for_each_llc(client):
    money_for_two_llcs(client)
    text = page(client, f"/reports/?{YEAR}&interval=llc")
    head = text.split("<thead>")[1].split("</thead>")[0]
    assert "Acme LLC" in head and "Zebra LLC" in head and "Not in an LLC" in head
    assert "by LLC" in text


def test_a_report_for_an_llc_by_property_shows_only_its_homes(client):
    money_for_two_llcs(client)
    head = page(client, f"/reports/?{YEAR}&scope=llc-1&interval=property").split("<thead>")[1].split("</thead>")[0]
    assert "Elm St" in head and "Oak Ave" not in head and "No one property" not in head


def test_the_report_files_follow_the_llc(client):
    money_for_two_llcs(client)
    response = client.get(f"/reports/excel?{YEAR}&scope=llc-1")
    assert response.status_code == 200 and "Acme LLC" in response.headers["Content-Disposition"]
    assert client.get(f"/reports/pdf?{YEAR}&scope=llc-1&interval=llc").data.startswith(b"%PDF")
    text = page(client, f"/reports/?{YEAR}&scope=llc-1")
    assert "scope=llc-1" in text.split("Download Excel")[0]


def test_the_report_updates_itself_with_no_year_links(client):
    text = page(client, "/reports/")
    assert "data-auto-filter" in text and "data-show-button" in text and "filters.js" in text
    assert "This year" not in text and "Last year" not in text


def test_every_purpose_shows_even_at_zero(client, app):
    with app.app_context():
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), None, cash_flow.BY_MONTH)
    labels = [row.label for section in report.sections for row in section.rows]
    assert {"Rental Income", "Late Fees", "Property Tax", "Insurance"} <= set(labels)
    text = page(client, f"/reports/?{YEAR}")
    assert "Late Fees" in text and "Property Tax" in text


def test_the_report_has_a_scroll_bar_above_it(client):
    add_property(client)
    text = page(client, "/reports/")
    assert 'data-scrolls="report-table"' in text and 'id="report-table"' in text and "top_slider.js" in text


def test_the_reports_two_scroll_bars_look_the_same(client):
    style = client.get("/static/style.css").get_data(as_text=True)
    for part in ("", "-track", "-thumb"):
        assert f".top-slider::-webkit-scrollbar{part},\n.table-scroll::-webkit-scrollbar{part},\n.home-cards::-webkit-scrollbar{part} {{" in style


# ---------- The charts ----------


def charts_for(app, where=None, interval=cash_flow.BY_MONTH):
    with app.app_context():
        chosen = db_session.get(Llc, where) if isinstance(where, int) else None
        report = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), chosen, interval)
        net = next(row for section in report.sections for row in section.rows if row.label == "Net cash flow").values[-1]
        return report_charts.build(report), net


def test_the_charts_agree_with_the_report(client, app):
    money_for_two_llcs(client)
    add_transaction(client, direction="in", amount="9,999", category="Security Deposits", transaction_date="2026-03-03")
    charts, net = charts_for(app)
    # (Transfers such as security deposits aren't in cash flow, so they aren't in the charts.)
    assert charts["net"] == net == 695000
    assert charts["money_in"] == 700000 and charts["money_out"] == -5000


def test_no_money_means_no_charts(client, app):
    assert charts_for(app)[0] is None
    assert "chart-section" not in page(client, "/reports/")


def test_the_charts_are_on_the_page(client):
    money_for_two_llcs(client)
    text = page(client, f"/reports/?{YEAR}")
    for words in ("Money in and out, by month", "Where the money goes", "Money in by LLC", "Net cash flow by home"):
        assert words in text, words


def test_a_chart_of_homes_needs_more_than_one(client, app):
    money_for_two_llcs(client)
    with app.app_context():
        one = cash_flow.build(date(2026, 1, 1), date(2026, 12, 31), db_session.get(Property, 1), cash_flow.BY_MONTH)
        charts = report_charts.build(one)
    assert charts["homes"] is None and charts["shares"] is None and charts["months"] is not None


def test_a_pie_folds_small_slices_into_other(client, app):
    for number in range(8):
        home = add_property(client, f"Home {number}", f"{number} Pine St")
        add_transaction(client, direction="in", amount=f"{100 + number}", category="Rental Income", property_id=str(home), transaction_date="2026-03-02")
    pie = charts_for(app)[0]["shares"]["pie"]
    names = [one["name"] for one in pie["slices"]]
    assert len(names) == report_charts.MOST_SLICES + 1 and names[-1] == "Other"
    colors = [one["color"] for one in pie["slices"]]
    assert colors[-1] == report_charts.GRAY and len(set(colors)) == len(colors)


def test_the_charts_have_no_first_try_wording_and_no_pie_of_the_spending(client):
    money_for_two_llcs(client)
    text = page(client, f"/reports/?{YEAR}")
    assert "first try" not in text and "as a pie" not in text and "as bars" not in text
    assert text.count('class="pie"') == 1


def test_a_slice_keeps_its_color_when_others_change(app):
    with_some = report_charts._colors_for(["Insurance", "HOA"])
    alone = report_charts._colors_for(["Insurance"])
    assert with_some["Insurance"] == alone["Insurance"] or with_some["HOA"] != alone["Insurance"]
    assert report_charts._colors_for(["Insurance", "HOA"]) == with_some


def test_too_few_pieces_is_not_a_pie(client, app):
    add_transaction(client, direction="in", amount="100", category="Rental Income", property_id=str(add_property(client, "Elm St")), transaction_date="2026-03-02")
    add_transaction(client, direction="in", amount="50", category="Rental Income", property_id=str(add_property(client, "Oak Ave", "12 Oak Ave")), transaction_date="2026-03-02")
    assert charts_for(app)[0]["shares"] is None


def test_the_bars_of_spending_go_biggest_first(client, app):
    add_transaction(client, direction="out", amount="50", category="HOA", transaction_date="2026-03-02")
    add_transaction(client, direction="out", amount="100", category="Insurance", transaction_date="2026-03-02")
    spending = charts_for(app)[0]["spending"]
    assert [bar["name"] for bar in spending] == ["Insurance", "HOA"]
    assert spending[0]["width"] == 100 and spending[1]["width"] == 50


def test_a_home_losing_money_goes_the_other_way(client, app):
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave", "12 Oak Ave")
    add_transaction(client, direction="in", amount="100", category="Rental Income", property_id=str(elm), transaction_date="2026-03-01")
    add_transaction(client, direction="out", amount="300", category="HOA", property_id=str(oak), transaction_date="2026-03-01")
    first, last = charts_for(app)[0]["homes"]
    assert first["name"] == "Elm St" and first["good"] and last["name"] == "Oak Ave" and not last["good"]
    assert last["left"] + last["width"] == first["left"] == first["zero"]


def test_the_month_chart_goes_from_one_baseline(client, app):
    add_transaction(client, direction="in", amount="1,000", category="Rental Income", transaction_date="2026-03-01")
    add_transaction(client, direction="out", amount="500", category="HOA", transaction_date="2026-03-02")
    months = charts_for(app)[0]["months"]
    march = months["columns"][2]
    assert len(months["columns"]) == 12 and march["label"] == "Mar" and march["in_height"] == 2 * march["out_height"]
    assert any(tick["zero"] for tick in months["ticks"])


def test_names_on_the_charts_are_kept_safe(client):
    add_llc(client, "<script>alert(1)</script> LLC")
    assert "<script>alert(1)" not in page(client, "/properties/")
    home = add_property(client)
    put_in_llc(client, home)
    add_transaction(client, direction="in", amount="100", category="Rental Income", property_id=str(home), transaction_date="2026-03-01")
    assert "<script>alert(1)" not in page(client, f"/reports/?{YEAR}")
