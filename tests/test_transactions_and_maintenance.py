"""Transactions (money in and out) and maintenance requests, and how they link up."""

import json
from io import BytesIO

from helpers import PDF, add_lease, add_property, get

from property_manager.models import MaintenanceRequest, Transaction


def add_transaction(client, direction="out", amount="120.50", category="Repair & Maintenance", **extra):
    data = {
        "transaction_date": "2026-03-15",
        "direction": direction,
        "amount": amount,
        "category": category,
        **extra,
    }
    return client.post("/transactions/new", data=data, content_type="multipart/form-data")


def add_request(client, property_id, title="Kitchen sink is leaking", category="Repair & Maintenance", status="Open", **extra):
    data = {
        "property_id": str(property_id),
        "title": title,
        "category": category,
        "status": status,
        "reported_on": "2026-03-10",
        **extra,
    }
    return client.post("/maintenance/new", data=data, content_type="multipart/form-data")


# ---------- Transactions ----------


def test_money_out_is_negative_and_money_in_positive(client, app):
    elm = add_property(client)
    add_transaction(client, direction="out", amount="120.50", property_id=str(elm))
    add_transaction(client, direction="in", amount="2,400", category="Rental Income", property_id=str(elm))
    assert get(app, Transaction, 1, "amount_cents") == -12050
    assert get(app, Transaction, 2, "amount_cents") == 240000

    page = client.get("/transactions/").get_data(as_text=True)
    assert "−$120.50" in page and "+$2,400.00" in page
    assert "$2,279.50" in page  # the net


def test_the_list_is_newest_first(client):
    add_transaction(client, details="January bill", transaction_date="2026-01-05")
    add_transaction(client, details="February bill", transaction_date="2026-02-05")
    page = client.get("/transactions/").get_data(as_text=True)
    assert page.index("February bill") < page.index("January bill")


def test_the_tenant_is_filled_in_from_the_lease_on_that_date(client, app):
    elm = add_property(client)
    add_lease(client, elm, tenants="Jane Doe", start="2026-01-01", end="2026-12-31")
    add_transaction(client, direction="in", category="Rental Income", property_id=str(elm), transaction_date="2026-03-01")
    add_transaction(client, direction="in", category="Rental Income", property_id=str(elm), tenant_name="Someone Else")
    assert get(app, Transaction, 1, "tenant_name") == "Jane Doe"
    assert get(app, Transaction, 2, "tenant_name") == "Someone Else"


def test_filtering_by_property_and_purpose(client):
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave", "12 Oak Ave")
    add_transaction(client, property_id=str(elm), details="elm repair")
    add_transaction(client, property_id=str(oak), details="oak repair")
    add_transaction(client, property_id=str(oak), category="Utilities", details="oak water")
    page = client.get(f"/transactions/?property_id={oak}&category=Repair%20%26%20Maintenance").get_data(as_text=True)
    assert "oak repair" in page and "elm repair" not in page and "oak water" not in page


def test_a_transaction_needs_the_basics(client):
    response = add_transaction(client, direction="", amount="", category="")
    assert response.status_code == 400
    page = response.get_data(as_text=True)
    assert "Please choose money in or money out." in page
    assert "Please enter an amount." in page
    assert "Please choose what it was for." in page


def test_editing_uses_the_same_form_and_keeps_receipts(client, app):
    add_transaction(client, files=[(BytesIO(PDF), "receipt.pdf")])
    page = client.get("/transactions/1").get_data(as_text=True)
    assert 'value="120.50"' in page and "receipt.pdf" in page
    client.post("/transactions/1", data={**_form_of(app, 1), "amount": "99"}, content_type="multipart/form-data")
    assert get(app, Transaction, 1, "amount_cents") == -9900


def _form_of(app, transaction_id) -> dict:
    day, category = get(app, Transaction, transaction_id, "transaction_date", "category")
    return {"transaction_date": day.isoformat(), "direction": "out", "category": category}


# ---------- Maintenance ----------


def test_adding_a_request_with_pictures(client, app):
    elm = add_property(client)
    page = add_request(
        client, elm, company="Joe's Plumbing", files=[(BytesIO(PDF), "quote.pdf")]
    ).get_data(as_text=True)
    assert "The request was added." in page and "quote.pdf" in page
    assert get(app, MaintenanceRequest, 1, "company", "status") == ("Joe's Plumbing", "Open")


def test_open_and_in_progress_requests_are_on_home_and_the_list(client):
    elm = add_property(client)
    add_request(client, elm, title="Leaky sink", status="Open")
    add_request(client, elm, title="New roof", status="In progress")
    add_request(client, elm, title="Old fence", status="Closed")
    for page in (client.get("/").get_data(as_text=True), client.get("/maintenance/").get_data(as_text=True)):
        assert "Leaky sink" in page and "New roof" in page and "Old fence" not in page
    assert "Old fence" in client.get("/maintenance/?show=Closed").get_data(as_text=True)


def test_the_status_can_be_changed_right_on_the_lists(client, app):
    elm = add_property(client)
    add_request(client, elm, title="Leaky sink", status="In progress")
    for address, shown in (("/", ["Open", "In progress"]), ("/maintenance/", ["Open", "In progress"]),
                           ("/maintenance/?show=Closed", ["Closed"]), ("/maintenance/?show=all", ["Open", "In progress", "Closed"])):
        client.post("/maintenance/1/status", data={"status": "Closed" if shown == ["Closed"] else "In progress"})
        page = client.get(address).get_data(as_text=True)
        # A menu on each request, and the statuses the list shows, for status.js.
        assert 'action="/maintenance/1/status"' in page and 'aria-label="Status of “Leaky sink”"' in page
        assert f"data-shows='{json.dumps(shown)}'" in page and "status.js" in page

    # status.js saves it without leaving the page.
    answer = client.post("/maintenance/1/status", data={"status": "Open", "stay_on_page": "1"})
    assert answer.status_code == 204 and get(app, MaintenanceRequest, 1, "status") == "Open"

    # Without it, the Save button goes back to the list.
    answer = client.post("/maintenance/1/status", data={"status": "Closed", "next": "/maintenance/?show=all"})
    assert answer.request.full_path == "/maintenance/?show=all"
    assert "“Leaky sink” is now closed." in answer.get_data(as_text=True)
    assert get(app, MaintenanceRequest, 1, "status") == "Closed"
    answer = client.post("/maintenance/1/status", data={"status": "Open", "next": "//elsewhere.example"})
    assert answer.request.path == "/"  # never another website

    assert client.post("/maintenance/1/status", data={"status": "Done"}).status_code == 400
    assert get(app, MaintenanceRequest, 1, "status") == "Open"
    client.post("/maintenance/1/archive")
    assert client.post("/maintenance/1/status", data={"status": "Closed"}).status_code == 404


def test_transactions_linked_to_a_request_take_its_type_and_property(client, app):
    elm = add_property(client)
    add_request(client, elm, category="Appliances & Furnishings", title="New fridge")
    add_transaction(client, category="Utilities", request_id="1", amount="899", details="Fridge")
    add_transaction(client, category="Utilities", request_id="1", amount="100", details="Delivery")
    assert get(app, Transaction, 1, "category", "property_id") == ("Appliances & Furnishings", elm)

    page = client.get("/maintenance/1").get_data(as_text=True)
    assert "Fridge" in page and "Delivery" in page and "$999.00" in page  # the cost so far

    # Changing the request's type changes its transactions' type too.
    client.post(
        "/maintenance/1",
        data={"property_id": str(elm), "title": "New fridge", "category": "Repair & Maintenance", "status": "Closed", "reported_on": "2026-03-10"},
        content_type="multipart/form-data",
    )
    assert get(app, Transaction, 2, "category") == "Repair & Maintenance"


def test_property_and_purpose_are_greyed_out_while_a_request_is_linked(client):
    add_property(client, nickname="Oak Ave")
    elm = add_property(client)
    add_request(client, elm, category="Appliances & Furnishings", title="New fridge")
    add_transaction(client, category="Utilities", request_id="1")
    add_transaction(client, category="Utilities")
    greyed = ['<select id="property_id" name="property_id" disabled', '<select id="category" name="category" disabled']
    shows_the_request = [f'<option value="{elm}" selected>Elm St</option>', '<option value="Appliances &amp; Furnishings" selected>']

    def shows(path_or_page, expected):
        page = path_or_page if "<" in path_or_page else client.get(path_or_page).get_data(as_text=True)
        return all(text in page for text in expected)

    assert shows("/transactions/1", greyed + shows_the_request)
    assert shows("/transactions/new?request_id=1", greyed + shows_the_request)
    assert not any(shows(path, [text]) for path in ("/transactions/2", "/transactions/new") for text in greyed)

    # A greyed-out menu isn't sent, so after a problem it still shows the request's property and type.
    response = client.post("/transactions/new", data={"transaction_date": "2026-03-15", "direction": "out", "request_id": "1"})
    assert response.status_code == 400 and shows(response.get_data(as_text=True), greyed + shows_the_request)


def test_archiving_a_request(client):
    elm = add_property(client)
    add_request(client, elm, title="Leaky sink")
    page = client.post("/maintenance/1/archive").get_data(as_text=True)
    assert "was moved to the Archive" in page
    assert "Leaky sink" not in client.get("/maintenance/").get_data(as_text=True)


def test_a_lease_helper_still_works_with_requests(client):
    """(A request can be added for a property with or without a current lease.)"""
    elm = add_property(client)
    add_lease(client, elm)
    assert add_request(client, elm).status_code == 200


def test_her_own_spreadsheet_labels_are_offered():
    from property_manager import categories

    hers = ["Rental Income", "Insurance Roof Claim", "HOA", "Property Tax", "Insurance", "Repair & Maintenance",
            "Gardening", "Swimming Pool", "Cleaning", "Commission", "Contract Labor", "Prof Fee", "Accounting",
            "Supplies", "License", "Utilities"]
    assert set(hers) <= set(categories.ALL)
    assert len(categories.ALL) == len(set(categories.ALL))  # every name is different
    assert categories.group_of("Rental Income") == categories.INCOME
    assert categories.group_of("Prof Fee") == categories.OPERATING
