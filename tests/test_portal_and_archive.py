"""The tenant portal link of each lease, and the Archive."""

import pytest
from conftest import BrowserLikeClient
from helpers import add_lease, add_property, get

from property_manager import create_app
from property_manager.config import KEY_HEADER
from property_manager.database import db_session, get_settings
from property_manager.models import Lease, MaintenanceRequest, Property, Transaction

KEY = "a-test-key-that-is-long-enough-to-be-accepted"
ADDRESS = "https://example.onrender.com"


@pytest.fixture
def server_app(tmp_path):
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    app.config["PUBLIC_ADDRESS"] = ADDRESS
    app.test_client_class = BrowserLikeClient
    with app.app_context():
        get_settings().business_name = "Anna Li Rentals"
        get_settings().phone = "555-0100"
        db_session.commit()
    return app


@pytest.fixture
def landlord(server_app):
    client = server_app.test_client()
    client.environ_base["HTTP_" + KEY_HEADER.upper().replace("-", "_")] = KEY
    return client


def open_portal(server_app, token):
    browser = server_app.test_client()
    browser.get(f"/portal/link/{token}", base_url=ADDRESS)
    return browser


def test_the_lease_page_shows_its_portal_link(server_app, landlord):
    lease_id = add_lease(landlord, add_property(landlord), tenant_email="jane@example.com")
    token = get(server_app, Lease, lease_id, "portal_token")
    page = landlord.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert f"{ADDRESS}/portal/link/{token}" in page
    assert "mailto:jane%40example.com?subject=" in page


def test_the_tenant_sees_their_home_and_her_contact(server_app, landlord):
    lease_id = add_lease(landlord, add_property(landlord, "Elm St", "149 Elm St"))
    page = open_portal(server_app, get(server_app, Lease, lease_id, "portal_token")).get(
        "/portal/", base_url=ADDRESS
    ).get_data(as_text=True)
    assert "Hello, Jane Doe" in page and "149 Elm St" in page
    assert "Anna Li Rentals" in page and "555-0100" in page
    assert "OwlNest" not in page


def test_an_ended_lease_shows_sorry_and_reviving_turns_the_link_back_on(server_app, landlord):
    lease_id = add_lease(landlord, add_property(landlord), start="2025-01-01", end="2025-12-31")
    token = get(server_app, Lease, lease_id, "portal_token")
    browser = open_portal(server_app, token)

    landlord.post(f"/leases/{lease_id}/end", data={"ended_on": "2025-12-31"})
    response = browser.get("/portal/", base_url=ADDRESS)
    assert response.status_code == 403
    assert "Sorry, this link no longer works" in response.get_data(as_text=True)
    assert "Anna Li Rentals" not in response.get_data(as_text=True)

    landlord.post(f"/leases/{lease_id}/revive")
    assert browser.get("/portal/", base_url=ADDRESS).status_code == 200
    # The same link works again, too.
    assert open_portal(server_app, token).get("/portal/", base_url=ADDRESS).status_code == 200


def test_a_new_link_replaces_the_old_one(server_app, landlord):
    lease_id = add_lease(landlord, add_property(landlord))
    old = get(server_app, Lease, lease_id, "portal_token")
    landlord.post(f"/leases/{lease_id}/portal-link")
    response = server_app.test_client().get(f"/portal/link/{old}", base_url=ADDRESS)
    assert response.status_code == 403 and "This link isn't working" in response.get_data(as_text=True)


def test_a_tenant_cant_reach_her_screens(server_app, landlord):
    lease_id = add_lease(landlord, add_property(landlord))
    browser = open_portal(server_app, get(server_app, Lease, lease_id, "portal_token"))
    for page in ("/properties/", f"/leases/{lease_id}"):
        response = browser.get(page, base_url=ADDRESS)
        assert response.request.path == "/sign-in" and "Jane Doe" not in response.get_data(as_text=True)


# ---------- The Archive ----------


def test_archived_property_comes_back(client):
    property_id = add_property(client)
    client.post(f"/properties/{property_id}/archive")
    assert "Elm St" not in client.get("/properties/").get_data(as_text=True)
    assert "Elm St" in client.get("/archive/").get_data(as_text=True)
    client.post(f"/archive/restore/property/{property_id}")
    assert "Elm St" in client.get("/properties/").get_data(as_text=True)


def test_a_property_takes_its_records_to_the_archive_but_not_its_transactions(client, app):
    elm = add_property(client)
    lease_id = add_lease(client, elm)
    client.post(f"/leases/{lease_id}/end", data={"ended_on": "2026-06-30", "confirm_early": "1"})
    client.post("/insurances/new", data={"property_id": str(elm), "kind": "Property", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    for title in ("Leaky sink", "Broken window"):
        client.post("/maintenance/new", data={"property_id": str(elm), "title": title, "category": "Repair & Maintenance", "status": "Open", "reported_on": "2026-03-10"})
    client.post("/transactions/new", data={"transaction_date": "2026-03-15", "direction": "out", "amount": "80", "request_id": "1"})
    client.post("/maintenance/2/archive")  # archived before the property, on its own

    page = client.post(f"/properties/{elm}/archive").get_data(as_text=True)
    assert "Elm St (with its 1 lease, 1 insurance and 1 request) was moved to the Archive." in page
    assert get(app, Lease, lease_id, "archived_at") == get(app, Property, elm, "archived_at")
    assert "Leaky sink" not in client.get("/maintenance/").get_data(as_text=True)
    assert get(app, Transaction, 1, "archived_at") is None  # the money still counts
    assert "$80.00" in client.get("/transactions/").get_data(as_text=True)

    # Restoring the property brings back what went with it, but not what was archived before.
    page = client.post(f"/archive/restore/property/{elm}").get_data(as_text=True)
    assert "Elm St (with its 1 lease, 1 insurance and 1 request) was restored." in page
    assert get(app, Lease, lease_id, "archived_at") is None
    assert get(app, MaintenanceRequest, 1, "archived_at") is None
    assert get(app, MaintenanceRequest, 2, "archived_at") is not None


def test_a_deleted_lease_cant_come_back_over_a_new_current_lease(client):
    property_id = add_property(client)
    mistake = add_lease(client, property_id)
    client.post(f"/leases/{mistake}/archive")
    add_lease(client, property_id, tenants="Bob Smith")
    page = client.post(f"/archive/restore/lease/{mistake}").get_data(as_text=True)
    assert "already has a current lease (Bob Smith)" in page
