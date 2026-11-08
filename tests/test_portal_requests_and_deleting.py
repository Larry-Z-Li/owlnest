"""Requests sent from the tenant portal, and deleting records forever from the Archive."""

from datetime import date
from io import BytesIO

import pytest
from conftest import BrowserLikeClient
from helpers import PDF, add_lease, add_property, get
from PIL import Image
from sqlalchemy import select
from test_receipts import picture_bytes

from property_manager import create_app, portal
from property_manager.config import KEY_HEADER
from property_manager.database import db_session
from property_manager.models import Attachment, Lease, MaintenanceRequest, Property, Transaction, Walkthrough

KEY = "a-test-key-that-is-long-enough-to-be-accepted"
ADDRESS = "https://example.onrender.com"


@pytest.fixture
def server_app(tmp_path):
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    app.config["PUBLIC_ADDRESS"] = ADDRESS
    app.test_client_class = BrowserLikeClient
    return app


@pytest.fixture
def landlord(server_app):
    client = server_app.test_client()
    client.environ_base["HTTP_" + KEY_HEADER.upper().replace("-", "_")] = KEY
    return client


@pytest.fixture
def tenant(server_app, landlord):
    """Jane Doe's phone, with her lease's portal link opened. Returns (browser, lease id)."""
    lease_id = add_lease(landlord, add_property(landlord))
    browser = server_app.test_client()
    browser.get(f"/portal/link/{get(server_app, Lease, lease_id, 'portal_token')}", base_url=ADDRESS)
    return browser, lease_id


def send(browser, title="Kitchen sink is leaking", photos=()):
    data = {"title": title, "description": "Water under the sink."}
    if photos:
        data["photos"] = [(BytesIO(content), name) for content, name in photos]
    return browser.post("/portal/requests/new", data=data, content_type="multipart/form-data", base_url=ADDRESS)


def all_requests(app):
    with app.app_context():
        return list(db_session.scalars(select(MaintenanceRequest)))


# ---------- Requests from the tenant portal ----------


def test_a_tenant_sends_a_request_and_sees_its_status(server_app, landlord, tenant):
    browser, lease_id = tenant
    page = send(browser, photos=[(picture_bytes(format="HEIF"), "IMG_1.HEIC")]).get_data(as_text=True)
    assert "Your request was sent." in page
    assert "Kitchen sink is leaking" in page and "Open" in page

    sent = all_requests(server_app)[0]
    assert (sent.lease_id, sent.from_tenant, sent.unread, sent.category) == (lease_id, True, True, "Repair & Maintenance")
    assert len(get(server_app, MaintenanceRequest, sent.id, "attachments")) == 1

    # She's told, and opening it marks it read.
    assert "New request from Jane Doe (Elm St)" in landlord.get("/").get_data(as_text=True)
    landlord.get(f"/maintenance/{sent.id}")
    assert "New request from" not in landlord.get("/").get_data(as_text=True)

    # Her changes show in the portal.
    landlord.post(
        f"/maintenance/{sent.id}",
        data={"property_id": str(sent.property_id), "title": sent.title, "category": "Repair & Maintenance",
              "status": "In progress", "reported_on": date.today().isoformat()},
        content_type="multipart/form-data",
    )
    assert "In progress" in browser.get("/portal/", base_url=ADDRESS).get_data(as_text=True)


def test_tenants_can_only_send_photos_and_their_location_is_dropped(server_app, tenant):
    browser, _lease_id = tenant
    page = send(browser, photos=[(PDF, "lease.pdf")]).get_data(as_text=True)
    assert "“lease.pdf”" in page and "Nothing was sent yet" in page
    assert all_requests(server_app) == []

    with_gps = BytesIO()
    exif = Image.Exif()
    exif[0x8825] = {2: (37.0, 46.0, 30.0)}  # the camera's GPS details
    Image.new("RGB", (300, 200), "red").save(with_gps, "JPEG", exif=exif)
    send(browser, photos=[(with_gps.getvalue(), "photo.jpg")])
    with server_app.app_context():
        stored = db_session.scalars(select(Attachment)).one().file_path
    with Image.open(server_app.config["DATA_PATHS"].documents / stored) as image:
        assert not image.getexif()


def test_tenants_only_see_their_own_leases_requests(server_app, landlord, tenant):
    browser, _lease_id = tenant
    oak = add_property(landlord, "Oak Ave", "12 Oak Ave")
    landlord.post(
        "/maintenance/new",
        data={"property_id": str(oak), "title": "Oak roof", "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-01"},
        content_type="multipart/form-data",
    )
    assert "Oak roof" not in browser.get("/portal/", base_url=ADDRESS).get_data(as_text=True)


def test_an_archived_request_disappears_from_the_portal(server_app, landlord, tenant):
    browser, _lease_id = tenant
    send(browser, title="Doorbell broken")
    landlord.post("/maintenance/1/archive")
    assert "Doorbell broken" not in browser.get("/portal/", base_url=ADDRESS).get_data(as_text=True)


def test_a_tenant_can_send_only_five_requests_a_day(server_app, tenant):
    browser, _lease_id = tenant
    assert portal.REQUESTS_PER_DAY == 5
    for number in range(5):
        assert "Your request was sent." in send(browser, title=f"Request {number}").get_data(as_text=True)
    response = send(browser, title="Sixth")
    assert response.status_code == 400
    assert "You can send up to 5 requests a day." in response.get_data(as_text=True)
    assert len(all_requests(server_app)) == 5

    # The form says so before they type anything.
    page = browser.get("/portal/requests/new", base_url=ADDRESS).get_data(as_text=True)
    assert "You've sent 5 requests today" in page and "Send request" not in page


def test_forms_from_other_websites_are_refused(server_app, tenant):
    browser, _lease_id = tenant
    response = browser.post(
        "/portal/requests/new",
        data={"title": "x", "description": "y"},
        headers={"Origin": "https://evil.example"},
        base_url=ADDRESS,
    )
    assert response.status_code == 403
    assert all_requests(server_app) == []


# ---------- Deleting forever ----------


def test_only_archived_records_can_be_deleted_forever(client):
    property_id = add_property(client)
    assert client.post(f"/archive/delete/property/{property_id}").status_code == 404


def test_deleting_a_lease_forever_takes_its_walkthroughs_and_files(client, app):
    property_id = add_property(client)
    lease_id = add_lease(client, property_id)
    client.post(
        f"/leases/{lease_id}/walkthroughs/move-in",
        data={"walk_date": "2026-01-01", "files": [(BytesIO(PDF), "report.pdf")]},
        content_type="multipart/form-data",
    )
    client.post(
        "/maintenance/new",
        data={"property_id": str(property_id), "title": "Sink", "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-01"},
        content_type="multipart/form-data",
    )
    with app.app_context():
        db_session.get(MaintenanceRequest, 1).lease_id = lease_id  # as if sent from the portal
        db_session.commit()
        stored = db_session.scalars(select(Attachment)).one().file_path

    client.post(f"/leases/{lease_id}/archive")
    page = client.post(f"/archive/delete/lease/{lease_id}").get_data(as_text=True)
    assert "was deleted forever" in page
    with app.app_context():
        assert db_session.get(Lease, lease_id) is None
        assert db_session.scalars(select(Walkthrough)).all() == []
        assert db_session.scalars(select(Attachment)).all() == []
        assert db_session.get(MaintenanceRequest, 1).lease_id is None  # the request stays
    assert (app.config["DATA_PATHS"].documents / stored).exists()  # files are never deleted


def test_a_request_deleted_forever_leaves_its_transactions(client, app):
    property_id = add_property(client)
    client.post(
        "/maintenance/new",
        data={"property_id": str(property_id), "title": "Sink", "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-01"},
        content_type="multipart/form-data",
    )
    client.post(
        "/transactions/new",
        data={"transaction_date": "2026-03-02", "direction": "out", "amount": "80", "request_id": "1"},
        content_type="multipart/form-data",
    )
    client.post("/maintenance/1/archive")
    client.post("/archive/delete/request/1")
    assert get(app, Transaction, 1, "request_id", "amount_cents") == (None, -8000)


def test_deleting_a_property_forever_takes_its_own_records_but_not_its_requests(client, app):
    property_id = add_property(client)
    lease_id = add_lease(client, property_id, end="2026-06-30")
    client.post(f"/leases/{lease_id}/end", data={"ended_on": "2026-06-30", "confirm_early": "1"})
    client.post(
        "/maintenance/new",
        data={"property_id": str(property_id), "title": "Sink", "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-01"},
        content_type="multipart/form-data",
    )
    client.post(f"/properties/{property_id}/archive")
    page = client.post(f"/archive/delete/property/{property_id}").get_data(as_text=True)
    assert "still has 1 request" in page
    assert get(app, Property, property_id, "nickname") == "Elm St"

    client.post("/archive/delete/request/1")  # (it was archived with the property)
    assert "was deleted forever" in client.post(f"/archive/delete/property/{property_id}").get_data(as_text=True)
    with app.app_context():
        assert db_session.get(Property, property_id) is None
        assert db_session.get(Lease, lease_id) is None
