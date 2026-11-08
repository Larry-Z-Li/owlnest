"""Insurances, and the reminders on the Home page (leases, insurances, and her own)."""

from datetime import date, timedelta
from io import BytesIO

from helpers import PDF, add_lease, add_property, get

from property_manager.database import db_session
from property_manager.models import Insurance, Lease


def days_from_now(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def add_insurance(client, property_id, kind="Property", end=None, **extra):
    data = {
        "property_id": str(property_id),
        "kind": kind,
        "company": "State Farm",
        "start_date": "2026-01-01",
        "end_date": end or days_from_now(300),
        **extra,
    }
    return client.post("/insurances/new", data=data, content_type="multipart/form-data")


def test_adding_insurance_with_its_policy(client, app):
    property_id = add_property(client)
    page = add_insurance(
        client, property_id, kind="Renters", policy_number="12-345", files=[(BytesIO(PDF), "policy.pdf")]
    ).get_data(as_text=True)
    assert "The renters insurance was added." in page
    assert "Renters" in page and "State Farm" in page and "12-345" in page
    assert len(get(app, Insurance, 1, "attachments")) == 1


def test_a_property_can_have_several_insurances(client):
    property_id = add_property(client)
    add_insurance(client, property_id, kind="Property")
    add_insurance(client, property_id, kind="Renters")
    page = client.get(f"/properties/{property_id}").get_data(as_text=True)
    assert page.count('/edit">Property</a>') == 1 and page.count('/edit">Renters</a>') == 1


def test_insurance_dates_are_checked(client):
    response = add_insurance(client, add_property(client), end="2025-06-01")
    assert response.status_code == 400
    assert "The end date must be after the start date." in response.get_data(as_text=True)


def test_insurance_ending_within_60_days_is_a_reminder_until_renewed(client):
    elm = add_property(client)
    add_insurance(client, elm, end=days_from_now(45))
    home = client.get("/").get_data(as_text=True)
    assert "The property insurance for Elm St (State Farm)" in home and "in 45 days" in home
    assert "Dismiss" not in home  # it can't be dismissed, only taken care of
    assert client.post("/insurances/1/dismiss-end-alert").status_code == 404
    # The property's page shows it too.
    assert "property insurance for Elm St" in client.get(f"/properties/{elm}").get_data(as_text=True)

    # Renewed: its dates change, and the reminder goes away.
    client.post(
        "/insurances/1/edit",
        data={"kind": "Property", "company": "State Farm", "start_date": days_from_now(45), "end_date": days_from_now(410)},
        content_type="multipart/form-data",
    )
    assert "property insurance for Elm St" not in client.get("/").get_data(as_text=True)
    assert "property insurance for Elm St" not in client.get(f"/properties/{elm}").get_data(as_text=True)


def test_insurance_ending_later_is_not_a_reminder_yet(client):
    add_insurance(client, add_property(client), end=days_from_now(61))
    assert "insurance for Elm St" not in client.get("/").get_data(as_text=True)


def test_lease_ending_within_60_days_is_a_reminder_until_extended(client):
    lease_id = add_lease(client, add_property(client), start="2025-06-01", end=days_from_now(30))
    home = client.get("/").get_data(as_text=True)
    assert "The lease for Elm St (Jane Doe)" in home and "Ends on" in home

    client.post(f"/leases/{lease_id}/extend", data={"new_end_date": days_from_now(400)})
    assert "The lease for Elm St" not in client.get("/").get_data(as_text=True)


def test_reminders_cant_be_dismissed_or_archived_only_done(client):
    elm = add_property(client)
    lease_id = add_lease(client, elm, start="2025-06-01", end=days_from_now(30))
    assert client.post(f"/leases/{lease_id}/dismiss-end-alert").status_code == 404
    client.post(
        "/reminders/new",
        data={"property_id": str(elm), "kind": "Property tax due", "title": "Second installment",
              "due_date": days_from_now(5), "remind_days_before": "30", "repeat": "Once"},
    )
    assert client.post("/reminders/1/archive").status_code == 404
    assert "Archive reminder" not in client.get("/reminders/1/edit").get_data(as_text=True)

    page = client.get(f"/properties/{elm}").get_data(as_text=True)
    warning = page.split('class="message message-warning property-warnings"')[1].split("</section>")[0]
    assert "The lease for Elm St (Jane Doe)" in warning and "Property tax due: Second installment" in warning
    assert "Dismiss" not in warning and ">Done<" in warning

    client.post("/reminders/1/done")
    assert "Second installment" not in client.get(f"/properties/{elm}").get_data(as_text=True).split("property-warnings")[1].split("</section>")[0]


def test_a_lease_past_its_end_date_is_urgent(client):
    add_lease(client, add_property(client), start="2025-01-01", end=days_from_now(-3))
    home = client.get("/").get_data(as_text=True)
    assert "but is still current" in home
    assert "alert-pill urgent" in home


def test_her_own_reminders_still_work(client):
    property_id = add_property(client)
    client.post(
        "/reminders/new",
        data={
            "property_id": str(property_id),
            "kind": "Property tax due",
            "title": "Second installment",
            "due_date": days_from_now(10),
            "remind_days_before": "30",
            "repeat": "Every year",
        },
    )
    assert "Property tax due: Second installment (Elm St)" in client.get("/").get_data(as_text=True)
    assert "Property tax due" in client.get(f"/properties/{property_id}").get_data(as_text=True)


# ---------- The Reminders page ----------


def page_at(client, address) -> str:
    return client.get(address).get_data(as_text=True)


def add_reminder(client, kind="Property tax", days=200, repeat="Once", property_id="", remind_days_before=30):
    client.post(
        "/reminders/new",
        data={"property_id": str(property_id), "kind": kind, "due_date": days_from_now(days), "remind_days_before": str(remind_days_before), "repeat": repeat},
    )


def test_the_needs_attention_button_opens_reminders(client):
    add_insurance(client, add_property(client), end=days_from_now(10))
    assert 'href="/reminders/">' in page_at(client, "/properties/").split("need", 1)[0].rsplit("<a", 1)[1]
    home = page_at(client, "/")
    assert 'href="#alerts"' not in home and '<a class="tile" href="/reminders/">' not in home


def test_reminders_lists_what_the_button_counts_and_links_to_each_page(client):
    elm = add_property(client)
    lease = add_lease(client, elm, end=days_from_now(10))
    add_insurance(client, elm, end=days_from_now(10))
    page = page_at(client, "/reminders/")
    assert "The lease for" in page and f'href="/leases/{lease}"' in page
    assert "insurance for Elm St" in page and 'href="/insurances/1/edit"' in page


def test_rent_thats_due_links_to_the_leases_rent(client, app):
    elm = add_property(client)
    due = date.today() - timedelta(days=7)
    lease = add_lease(client, elm, start=due.isoformat(), end="", rent="3,000")
    with app.app_context():
        db_session.get(Lease, lease).rent_checked_from = due
        db_session.commit()
    page = page_at(client, "/reminders/")
    assert "rent for Elm St" in page and f'href="/leases/{lease}#rent"' in page


def test_one_taken_care_of_just_goes_away(client):
    elm = add_property(client)
    add_insurance(client, elm, end=days_from_now(10))
    assert "insurance for Elm St" in page_at(client, "/reminders/")
    client.post("/insurances/1/edit", data={"property_id": str(elm), "kind": "Property", "company": "State Farm", "start_date": "2026-01-01", "end_date": days_from_now(300)}, content_type="multipart/form-data")
    page = page_at(client, "/reminders/")
    assert "insurance for Elm St" not in page and "Finished" not in page


def test_her_own_reminder_not_close_yet_is_listed_for_later(client):
    add_reminder(client, repeat="Every year")
    page = page_at(client, "/reminders/")
    assert "Coming up later" in page and "Needs attention</h2>" not in page
    assert "Property tax" in page and "Every year" in page and 'action="/reminders/1/done"' in page


def test_there_is_no_finished_list(client):
    add_reminder(client, days=3)
    client.post("/reminders/1/done")
    for address in ("/reminders/", "/reminders/?show=finished", "/reminders/?show=all"):
        page = page_at(client, address)
        assert "Property tax" not in page and "Finished" not in page and 'name="show"' not in page


def test_a_repeating_one_stays_after_done(client):
    add_reminder(client, days=3, repeat="Every year")
    client.post("/reminders/1/done")
    assert "Property tax" in page_at(client, "/reminders/")


def test_reminders_can_show_one_property(client):
    elm = add_property(client, "Elm St")
    oak = add_property(client, "Oak Ave")
    add_reminder(client, kind="Elm tax", property_id=elm)
    add_reminder(client, kind="Oak tax", days=3, property_id=oak)
    page = page_at(client, f"/reminders/?property_id={elm}")
    assert "Elm tax" in page and "Oak tax" not in page
    page = page_at(client, f"/reminders/?property_id={oak}")
    assert "Oak tax" in page and "Elm tax" not in page


def test_on_home_the_whole_panel_is_the_link_with_done_beside_it(client):
    add_reminder(client, days=3)
    home = page_at(client, "/")
    panel = home.split('<li class="alert-panel">')[1].split("</li>")[0]
    link = panel.split('<a class="alert-link" href="/reminders/1/edit">')[1].split("</a>")[0]
    assert "Property tax" in link and 'class="alert-title"' in link and 'class="alert-detail"' in link
    assert 'action="/reminders/1/done"' not in link and 'action="/reminders/1/done"' in panel
