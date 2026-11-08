"""Shortcuts for tests: add records through the app's own forms, and look them up."""

from property_manager.database import db_session

PDF = b"%PDF-1.4\n% a test document\n%%EOF"


def _new_id(response) -> int:
    """After adding a record, the app shows its page, such as /leases/3. Return the 3."""
    assert response.status_code == 200, response.get_data(as_text=True)
    return int(response.request.path.rstrip("/").split("/")[-1])


def add_property(client, nickname="Elm St", address="149 Elm St") -> int:
    return _new_id(client.post("/properties/new", data={"nickname": nickname, "address": address}))


def post_lease(client, property_id, tenants="Jane Doe", start="2026-01-01", end="2026-12-31", rent="2,400", **extra):
    data = {
        "property_id": str(property_id),
        "tenant_names": tenants,
        "start_date": start,
        "end_date": end,
        "monthly_rent": rent,
        **extra,
    }
    return client.post("/leases/new", data=data)


def add_lease(client, property_id, **fields) -> int:
    return _new_id(post_lease(client, property_id, **fields))


def get(app, model, record_id, *attributes):
    """Read some attributes of one record, e.g. get(app, Lease, 1, "status", "end_date")."""
    with app.app_context():
        record = db_session.get(model, record_id)
        values = tuple(getattr(record, attribute) for attribute in attributes)
        return values[0] if len(values) == 1 else values
