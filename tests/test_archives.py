"""Where archived records are listed: the Archive page has properties, requests and transactions;
a property's own leases, insurance, photos and files are under Archives on its page; a request's or
transaction's removed files are on its own page."""

import html
import json
import re
from io import BytesIO

from helpers import PDF, add_lease, add_property, get

from property_manager.models import Transaction


def text_of(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def section(page: str, opening: str) -> str:
    return page.split(opening)[1].split("</section>")[0]


def questions(page: str) -> list[str]:
    """The questions the page's buttons ask first (OK or Cancel), as the browser shows them."""
    return [json.loads(found) for found in re.findall(r"onsubmit='return confirm\((\".*?\")\);'", page)]


def add_request(client, property_id, title="Leaky sink"):
    client.post(
        "/maintenance/new",
        data={"property_id": str(property_id), "title": title, "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-01"},
        content_type="multipart/form-data",
    )


def test_a_propertys_own_archived_records_are_on_its_page(client):
    elm = add_property(client)
    mistake = add_lease(client, elm)
    client.post(f"/files/lease/{mistake}", data={"files": [(BytesIO(PDF), "signed lease.pdf")]}, content_type="multipart/form-data")
    client.post("/files/1/archive")
    client.post(f"/leases/{mistake}/archive")
    client.post("/insurances/new", data={"property_id": str(elm), "kind": "Flood", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    client.post("/insurances/1/archive")

    archives = text_of(section(client.get(f"/properties/{elm}").get_data(as_text=True), 'id="archives"'))
    assert "Leases Name Archived on Restore or delete Elm St (Jane Doe)" in archives
    assert "Insurance Name Archived on Restore or delete Flood insurance, Elm St" in archives
    assert "Files Name Archived on Restore or delete signed lease.pdf" in archives

    archive_page = client.get("/archive/").get_data(as_text=True)
    assert "Jane Doe" not in archive_page and "Flood" not in archive_page and "signed lease.pdf" not in archive_page
    assert "The Archive is empty." in archive_page


def test_properties_requests_and_transactions_are_on_the_archive_page(client):
    elm = add_property(client)
    oak = add_property(client, nickname="Oak Ave", address="22 Oak Ave")
    add_request(client, elm)
    client.post("/transactions/new", data={"transaction_date": "2026-03-02", "direction": "out", "amount": "80", "category": "Repair & Maintenance"})
    client.post("/maintenance/1/archive")
    client.post("/transactions/1/archive")
    client.post(f"/properties/{oak}/archive")
    page = text_of(client.get("/archive/").get_data(as_text=True))
    assert "Properties Name Archived on Restore or delete Oak Ave" in page
    assert "Maintenance requests Name Archived on Restore or delete Elm St: Leaky sink" in page
    assert "Transactions Name Archived on Restore or delete Mar 2, 2026: Repair & Maintenance" in page


def test_restore_and_delete_forever_are_the_same_size_and_come_back_to_the_same_page(client):
    elm = add_property(client)
    mistake = add_lease(client, elm)
    client.post(f"/leases/{mistake}/archive")
    add_lease(client, elm, tenants="Bob Smith")
    page = client.get(f"/properties/{elm}").get_data(as_text=True)
    buttons = re.findall(r'<button type="submit" class="([^"]+)">(Restore|Delete forever)</button>', section(page, 'id="archives"'))
    assert [classes.split()[:2] for classes, _label in buttons] == [["button", "button-small"]] * 2

    # Restoring it over the new current lease isn't possible: the message shows on the property's page.
    response = client.post(f"/archive/restore/lease/{mistake}", data={"back": f"/properties/{elm}#archives"})
    assert response.request.path == f"/properties/{elm}"
    assert "already has a current lease (Bob Smith)" in response.get_data(as_text=True)
    response = client.post(f"/archive/delete/lease/{mistake}", data={"back": f"/properties/{elm}#archives"})
    assert response.request.path == f"/properties/{elm}" and "was deleted forever" in response.get_data(as_text=True)
    assert client.post("/archive/delete/property/1", data={"back": "//elsewhere.example"}).status_code == 404


def test_removed_files_of_a_request_are_on_its_page(client):
    add_request(client, add_property(client))
    client.post("/files/request/1", data={"files": [(BytesIO(PDF), "invoice.pdf")]}, content_type="multipart/form-data")
    client.post("/files/1/archive")
    page = client.get("/maintenance/1").get_data(as_text=True)
    assert "Removed files" in page and "invoice.pdf" in page
    assert "invoice.pdf" not in client.get("/archive/").get_data(as_text=True)
    client.post("/archive/restore/attachment/1")
    assert "Removed files" not in client.get("/maintenance/1").get_data(as_text=True)


def test_restoring_a_transaction_whose_request_is_archived_asks_first_and_unlinks_it(client, app):
    add_request(client, add_property(client))
    client.post("/transactions/new", data={"transaction_date": "2026-03-02", "direction": "out", "amount": "80", "request_id": "1"})
    client.post("/maintenance/1/archive")
    undo = client.post("/transactions/1/archive").get_data(as_text=True)
    question = "linked to the request “Elm St: Leaky sink”, which is in the Archive"
    assert any(question in asked for asked in questions(undo))  # the Undo button asks too
    assert any(question in asked for asked in questions(client.get("/archive/").get_data(as_text=True)))
    page = client.post("/archive/restore/transaction/1").get_data(as_text=True)
    assert "no longer linked to the request “Elm St: Leaky sink”" in html.unescape(page)
    assert get(app, Transaction, 1, "request_id", "archived_at") == (None, None)


def test_restoring_a_transaction_whose_request_is_not_archived_asks_nothing(client, app):
    add_request(client, add_property(client))
    client.post("/transactions/new", data={"transaction_date": "2026-03-02", "direction": "out", "amount": "80", "request_id": "1"})
    client.post("/transactions/1/archive")
    assert not any("which is in the Archive" in asked for asked in questions(client.get("/archive/").get_data(as_text=True)))
    client.post("/archive/restore/transaction/1")
    assert get(app, Transaction, 1, "request_id") == 1
