"""Properties, their current and previous leases, walkthroughs, extensions, and attached files."""

from datetime import date, timedelta
from io import BytesIO

from helpers import PDF, add_lease, add_property, get, post_lease
from test_receipts import picture_bytes

from property_manager.database import db_session
from property_manager.formatting import nice_date
from property_manager.models import Attachment, Lease, LeaseExtension, RentPeriod, Walkthrough


def files(*pairs):
    return [(BytesIO(data), name) for data, name in pairs]


# ---------- Properties ----------


def test_adding_a_property_and_seeing_it_in_the_list(client):
    property_id = add_property(client, "Elm St", "149 Elm St\nSpringfield")
    page = client.get("/properties/").get_data(as_text=True)
    # (The list shows the name, then the town and the rooms; the street is on the home's page.)
    assert "Elm St" in page and "Springfield" in page and "No current lease" in page
    assert "Add a lease" in client.get(f"/properties/{property_id}").get_data(as_text=True)


def test_property_names_must_be_unique(client):
    add_property(client, "Elm St")
    response = client.post("/properties/new", data={"nickname": "elm  st"})
    assert response.status_code == 400
    assert "There is already a property named" in response.get_data(as_text=True)


def test_a_property_with_a_current_lease_cant_be_archived(client):
    property_id = add_property(client)
    add_lease(client, property_id)
    page = client.post(f"/properties/{property_id}/archive").get_data(as_text=True)
    assert "End the lease first" in page


# ---------- The current lease ----------


def test_adding_a_lease_makes_its_portal_link(client, app):
    property_id = add_property(client)
    lease_id = add_lease(client, property_id, tenants="Jane Doe and John Doe", rent="2,400")
    token = get(app, Lease, lease_id, "portal_token")
    assert len(token) >= 40
    page = client.get(f"/properties/{property_id}").get_data(as_text=True)
    assert "Jane Doe and John Doe" in page and "$2,400.00 a month" in page


def test_a_property_has_only_one_current_lease(client):
    property_id = add_property(client)
    add_lease(client, property_id)
    page = post_lease(client, property_id, tenants="Bob Smith").get_data(as_text=True)
    assert "already has a current lease (Jane Doe)" in page
    assert "Bob Smith" not in client.get(f"/properties/{property_id}").get_data(as_text=True)


def test_a_lease_needs_its_basics(client):
    property_id = add_property(client)
    response = post_lease(client, property_id, tenants="", rent="", end="2025-01-01")
    assert response.status_code == 400
    page = response.get_data(as_text=True)
    assert "Please fill this in." in page
    assert "The end date must be after the start date." in page


def test_extending_a_lease_moves_its_end_date_and_keeps_the_agreement(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31")
    page = client.post(
        f"/leases/{lease_id}/extend",
        data={"new_end_date": "2027-12-31", "signed_on": "2026-11-15", "files": files((PDF, "extension.pdf"))},
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "The lease now ends on Dec 31, 2027." in page
    assert "Extended to Dec 31, 2027" in page and "extension.pdf" in page
    assert get(app, Lease, lease_id, "end_date") == date(2027, 12, 31)
    previous_end, attached = get(app, LeaseExtension, 1, "previous_end_date", "attachments")
    assert previous_end == date(2026, 12, 31) and len(attached) == 1


def test_an_extension_can_add_a_rent_that_starts_later(client, app):
    today = date.today()
    end = today + timedelta(days=30)
    new_term = end + timedelta(days=1)
    new_end = end + timedelta(days=365)
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end=end.isoformat(), rent="2,400")
    form = client.get(f"/leases/{lease_id}/extend").get_data(as_text=True)
    assert "New monthly rent" in form and "The rent is $2,400.00 a month now." in form
    # The new rent is ready to start when the new term does.
    assert f'value="{new_term.isoformat()}"' in form

    page = client.post(
        f"/leases/{lease_id}/extend",
        data={"new_end_date": new_end.isoformat(), "new_rent": "2,500", "rent_starts_on": new_term.isoformat()},
    ).get_data(as_text=True)
    assert f"The rent is $2,500.00 a month from {nice_date(new_term)}." in page
    # It hasn't started yet, so the rent stays as it is until then.
    assert get(app, Lease, lease_id, "rent_cents") == 240000
    assert "$2,400.00" in page and "$2,500.00" in page and "Upcoming" in page


def test_a_rent_change_already_started_is_the_rent_now(client, app):
    today = date.today()
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end=(today + timedelta(days=30)).isoformat(), rent="2,400")
    client.post(
        f"/leases/{lease_id}/extend",
        data={
            "new_end_date": (today + timedelta(days=400)).isoformat(),
            "new_rent": "2,500",
            "rent_starts_on": today.isoformat(),
        },
    )
    assert get(app, Lease, lease_id, "rent_cents") == 250000


def test_leaving_the_new_rent_empty_keeps_the_rent(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31", rent="2,400")
    page = client.post(
        f"/leases/{lease_id}/extend", data={"new_end_date": "2027-12-31", "new_rent": "", "rent_starts_on": "2027-01-01"}
    ).get_data(as_text=True)
    assert "a month from" not in page
    assert len(get(app, Lease, lease_id, "rent_periods")) == 1


def test_typing_the_same_rent_is_not_a_change(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31", rent="2,400")
    client.post(f"/leases/{lease_id}/extend", data={"new_end_date": "2027-12-31", "new_rent": "2400", "rent_starts_on": "2027-01-01"})
    assert len(get(app, Lease, lease_id, "rent_periods")) == 1


def test_a_new_rent_needs_a_good_date_and_amount(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31", rent="2,400")
    for rent, starts in (("lots", "2027-01-01"), ("2,500", ""), ("2,500", "2028-06-01"), ("2,500", "2025-01-01")):
        response = client.post(f"/leases/{lease_id}/extend", data={"new_end_date": "2027-12-31", "new_rent": rent, "rent_starts_on": starts})
        assert response.status_code == 400
    assert get(app, Lease, lease_id, "end_date") == date(2026, 12, 31)
    assert len(get(app, Lease, lease_id, "rent_periods")) == 1


# ---------- The rent list ----------


def rents(app, lease_id):
    return [(period.starts_on, period.amount_cents) for period in get(app, Lease, lease_id, "rent_periods")]


def test_a_new_lease_starts_its_rent_list(client, app):
    lease_id = add_lease(client, add_property(client), start="2026-01-01", rent="2,400")
    assert rents(app, lease_id) == [(date(2026, 1, 1), 240000)]
    page = client.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert "Add a rent change" in page and "Jan 1, 2026" in page and "$2,400.00" in page


def test_the_rent_in_effect_goes_by_todays_date(client, app):
    today = date.today()
    elm = add_property(client)
    lease_id = add_lease(client, elm, start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2021-01-01", "amount": "1,100"})
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": (today - timedelta(days=1)).isoformat(), "amount": "1,200"})
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": (today + timedelta(days=1)).isoformat(), "amount": "1,300"})
    assert get(app, Lease, lease_id, "rent_cents") == 120000

    # Every page goes by it.
    assert "$1,200 a month" in client.get("/properties/").get_data(as_text=True)
    assert "$1,200.00 a month" in client.get(f"/properties/{elm}").get_data(as_text=True)
    assert "$1,200.00" in client.get("/").get_data(as_text=True)
    page = client.get(f"/leases/{lease_id}").get_data(as_text=True)
    assert page.count("In effect") == 1 and page.count("Upcoming") == 1
    assert "$1,300.00" in page


def test_a_rent_starts_on_its_own_day_and_no_earlier(app, client, monkeypatch):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2030-06-01", "amount": "1,100"})
    with app.app_context():
        lease = db_session.get(Lease, lease_id)
        assert lease.rent_period_on(date(2030, 5, 31)).amount_cents == 100000
        assert lease.rent_period_on(date(2030, 6, 1)).amount_cents == 110000
        assert lease.rent_period_on(date(2019, 1, 1)).amount_cents == 100000


def test_a_lease_that_ended_stays_at_its_last_rent(client, app):
    today = date.today()
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": (today - timedelta(days=5)).isoformat(), "amount": "1,100"})
    client.post(f"/leases/{lease_id}/end", data={"ended_on": (today - timedelta(days=10)).isoformat()})
    assert get(app, Lease, lease_id, "status") == "Ended"
    assert get(app, Lease, lease_id, "rent_cents") == 100000


def test_editing_one_rent_changes_only_that_one(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2021-01-01", "amount": "1,100"})
    second = get(app, Lease, lease_id, "rent_periods")[1].id
    form = client.get(f"/leases/{lease_id}/rent/{second}").get_data(as_text=True)
    assert 'value="2021-01-01"' in form and 'value="1,100.00"' in form

    client.post(f"/leases/{lease_id}/rent/{second}", data={"starts_on": "2021-02-01", "amount": "1,150"})
    assert rents(app, lease_id) == [(date(2020, 1, 1), 100000), (date(2021, 2, 1), 115000)]


def test_the_first_rents_date_stays_with_the_lease(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    first = get(app, Lease, lease_id, "rent_periods")[0].id
    form = client.get(f"/leases/{lease_id}/rent/{first}").get_data(as_text=True)
    assert 'name="starts_on"' not in form and "edit the lease's start date" in form

    client.post(f"/leases/{lease_id}/rent/{first}", data={"starts_on": "2025-01-01", "amount": "900"})
    assert rents(app, lease_id) == [(date(2020, 1, 1), 90000)]


def test_a_rents_date_has_to_make_sense(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="2030-12-31", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2021-01-01", "amount": "1,100"})
    for starts, problem in (
        ("2021-01-01", "already a rent starting on that date"),
        ("2020-01-01", "after the lease's start date"),
        ("2019-01-01", "after the lease's start date"),
        ("2031-01-01", "extend the lease"),
    ):
        response = client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": starts, "amount": "1,200"})
        assert response.status_code == 400
        assert problem in response.get_data(as_text=True).replace("&#39;", "'")
    assert len(rents(app, lease_id)) == 2


def test_removing_a_rent_but_never_the_first(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2021-01-01", "amount": "1,100"})
    first, second = get(app, Lease, lease_id, "rent_periods")
    assert client.post(f"/leases/{lease_id}/rent/{first.id}/remove").status_code == 404
    client.post(f"/leases/{lease_id}/rent/{second.id}/remove")
    assert rents(app, lease_id) == [(date(2020, 1, 1), 100000)]


def test_a_rent_belongs_to_its_own_lease(client, app):
    elm_lease = add_lease(client, add_property(client, "Elm St"))
    oak_lease = add_lease(client, add_property(client, "Oak Ave"))
    elm_rent = get(app, Lease, elm_lease, "rent_periods")[0].id
    assert client.get(f"/leases/{oak_lease}/rent/{elm_rent}").status_code == 404


def test_editing_a_lease_no_longer_changes_the_rent(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    form = client.get(f"/leases/{lease_id}/edit").get_data(as_text=True)
    assert 'name="monthly_rent"' not in form and "Rent rate list" in form
    client.post(f"/leases/{lease_id}/edit", data={"tenant_names": "Jane Doe", "start_date": "2020-01-01", "monthly_rent": "9,999"})
    assert rents(app, lease_id) == [(date(2020, 1, 1), 100000)]


def test_moving_a_leases_start_moves_its_first_rent(client, app):
    lease_id = add_lease(client, add_property(client), start="2020-01-01", end="", rent="1,000")
    client.post(f"/leases/{lease_id}/rent/new", data={"starts_on": "2021-01-01", "amount": "1,100"})
    client.post(f"/leases/{lease_id}/edit", data={"tenant_names": "Jane Doe", "start_date": "2020-03-01"})
    assert rents(app, lease_id) == [(date(2020, 3, 1), 100000), (date(2021, 1, 1), 110000)]

    # But not past a later rent.
    response = client.post(f"/leases/{lease_id}/edit", data={"tenant_names": "Jane Doe", "start_date": "2021-01-01"})
    assert response.status_code == 400
    assert get(app, Lease, lease_id, "start_date") == date(2020, 3, 1)


def test_deleting_a_lease_forever_deletes_its_rents(client, app):
    lease_id = add_lease(client, add_property(client), rent="1,000")
    client.post(f"/leases/{lease_id}/archive")
    client.post(f"/archive/delete/lease/{lease_id}")
    with app.app_context():
        assert db_session.get(Lease, lease_id) is None
        assert db_session.query(RentPeriod).count() == 0


def test_an_extension_must_end_later(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31")
    response = client.post(f"/leases/{lease_id}/extend", data={"new_end_date": "2026-06-30"})
    assert response.status_code == 400
    assert get(app, Lease, lease_id, "end_date") == date(2026, 12, 31)


def test_a_bad_file_means_nothing_is_saved(client, app):
    lease_id = add_lease(client, add_property(client), end="2026-12-31")
    response = client.post(
        f"/leases/{lease_id}/extend",
        data={"new_end_date": "2027-12-31", "files": files((b"not a pdf", "notes.txt"))},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "“notes.txt”" in response.get_data(as_text=True)
    assert get(app, Lease, lease_id, "end_date") == date(2026, 12, 31)


# ---------- Ending and reviving ----------


def test_ending_early_needs_confirming(client, app):
    future_end = (date.today() + timedelta(days=90)).isoformat()
    property_id = add_property(client)
    lease_id = add_lease(client, property_id, start="2026-01-01", end=future_end)

    assert "Ending it now ends it early." in client.get(f"/leases/{lease_id}/end").get_data(as_text=True)
    response = client.post(f"/leases/{lease_id}/end", data={"ended_on": date.today().isoformat()})
    assert response.status_code == 400
    assert get(app, Lease, lease_id, "status") == "Active"

    page = client.post(
        f"/leases/{lease_id}/end", data={"ended_on": date.today().isoformat(), "confirm_early": "1"}
    ).get_data(as_text=True)
    assert "has ended" in page
    assert get(app, Lease, lease_id, "status", "ended_on") == ("Ended", date.today())


def test_an_ended_lease_moves_to_previous_leases_and_a_new_one_can_start(client):
    property_id = add_property(client)
    lease_id = add_lease(client, property_id, start="2025-01-01", end="2025-12-31")
    client.post(f"/leases/{lease_id}/end", data={"ended_on": "2025-12-31"})

    page = client.get(f"/properties/{property_id}").get_data(as_text=True)
    assert "has no current lease" in page and "Previous leases (1)" in page
    assert "Jane Doe" in client.get(f"/properties/{property_id}/previous-leases").get_data(as_text=True)
    add_lease(client, property_id, tenants="Bob Smith", start="2026-01-01")
    assert "Bob Smith" in client.get(f"/properties/{property_id}").get_data(as_text=True)


def test_reviving_a_lease_ended_by_mistake(client, app):
    property_id = add_property(client)
    lease_id = add_lease(client, property_id, start="2025-01-01", end="2025-12-31")
    client.post(f"/leases/{lease_id}/end", data={"ended_on": "2025-12-31"})

    page = client.post(f"/leases/{lease_id}/revive").get_data(as_text=True)
    assert "current again" in page
    assert get(app, Lease, lease_id, "status", "ended_on") == ("Active", None)


def test_a_lease_cant_be_revived_while_another_is_current(client, app):
    property_id = add_property(client)
    old = add_lease(client, property_id, start="2025-01-01", end="2025-12-31")
    client.post(f"/leases/{old}/end", data={"ended_on": "2025-12-31"})
    add_lease(client, property_id, tenants="Bob Smith", start="2026-01-01")

    page = client.post(f"/leases/{old}/revive").get_data(as_text=True)
    assert "already has a current lease (Bob Smith)" in page
    assert get(app, Lease, old, "status") == "Ended"


# ---------- Walkthroughs and files ----------


def test_move_in_walkthrough_with_photos_and_a_report(client, app):
    lease_id = add_lease(client, add_property(client))
    page = client.post(
        f"/leases/{lease_id}/walkthroughs/move-in",
        data={
            "walk_date": "2026-01-01",
            "notes": "Carpet stain in the bedroom.",
            "files": files((picture_bytes(format="HEIF"), "IMG_1.HEIC"), (PDF, "report.pdf")),
        },
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "The move-in walkthrough was saved." in page
    assert "IMG_1.jpg" in page and "report.pdf" in page  # the iPhone photo became a JPG

    walkthrough_kind, attached = get(app, Walkthrough, 1, "kind", "attachments")
    assert walkthrough_kind == "Move-in" and len(attached) == 2
    documents = app.config["DATA_PATHS"].documents
    assert all((documents / one.file_path).exists() for one in attached)
    assert "Move-in: done · Move-out: not yet" in client.get("/properties/1").get_data(as_text=True)


def test_saving_a_walkthrough_again_updates_it(client, app):
    lease_id = add_lease(client, add_property(client))
    client.post(f"/leases/{lease_id}/walkthroughs/move-out", data={"walk_date": "2026-12-31", "notes": "First"})
    client.post(f"/leases/{lease_id}/walkthroughs/move-out", data={"walk_date": "2026-12-31", "notes": "Second"})
    assert get(app, Lease, lease_id, "walkthroughs")[0].notes == "Second"
    assert len(get(app, Lease, lease_id, "walkthroughs")) == 1


def test_lease_documents_can_be_added_opened_and_removed(client, app):
    lease_id = add_lease(client, add_property(client))
    page = client.post(
        f"/files/lease/{lease_id}", data={"files": files((PDF, "signed lease.pdf"))}, content_type="multipart/form-data"
    ).get_data(as_text=True)
    assert "1 file was added." in page and "signed lease.pdf" in page

    opened = client.get("/files/1")
    assert opened.data == PDF and opened.mimetype == "application/pdf"
    page = client.post("/files/1/archive").get_data(as_text=True)
    assert "was moved to the Archive" in page
    assert get(app, Attachment, 1, "archived_at") is not None
    assert client.get("/files/1").status_code == 404


def test_bedrooms_bathrooms_and_square_feet(client):
    elm = add_property(client)
    assert "Add its bedrooms, bathrooms and square feet" in client.get(f"/properties/{elm}").get_data(as_text=True)

    data = {"nickname": "Elm St", "address": "149 Elm St", "bedrooms": "3", "bathrooms": "2.5", "square_feet": "1,916"}
    client.post(f"/properties/{elm}/edit", data=data)
    assert "3 beds · 2.5 baths · 1,916 sq ft" in client.get(f"/properties/{elm}").get_data(as_text=True)
    edit_page = client.get(f"/properties/{elm}/edit").get_data(as_text=True)
    assert 'value="2.5"' in edit_page and 'value="1916"' in edit_page

    client.post(f"/properties/{elm}/edit", data={**data, "bedrooms": "1", "bathrooms": "1", "square_feet": ""})
    assert "1 bed · 1 bath</p>" in client.get(f"/properties/{elm}").get_data(as_text=True)

    response = client.post(f"/properties/{elm}/edit", data={**data, "bathrooms": "two"})
    assert response.status_code == 400 and "Please enter a number, such as 2 or 2.5." in response.get_data(as_text=True)
