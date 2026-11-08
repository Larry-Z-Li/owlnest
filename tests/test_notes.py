"""The Notes page: plain text notes she can add or archive."""

from property_manager.database import db_session
from property_manager.models import Note


def test_notes_is_in_the_top_links_next_to_archive(client):
    page = client.get("/").get_data(as_text=True)
    assert page.index('href="/notes/"') < page.index('href="/archive/"')
    assert "No notes yet." in client.get("/notes/").get_data(as_text=True)


def test_adding_notes_newest_first(client):
    client.post("/notes/", data={"text": "Call the roofer"})
    page = client.post("/notes/", data={"text": "Pay the HOA\nbefore Friday"}).get_data(as_text=True)
    assert "Your note was added." in page
    assert page.index("Pay the HOA") < page.index("Call the roofer")


def test_a_note_needs_text(client, app):
    response = client.post("/notes/", data={"text": "   "})
    assert response.status_code == 400 and "Please fill this in." in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.query(Note).count() == 0


def test_a_note_can_be_archived_restored_and_deleted_forever(client, app):
    client.post("/notes/", data={"text": "Call the roofer"})
    page = client.post("/notes/1/archive").get_data(as_text=True)
    assert "moved to the Archive" in page and "Call the roofer" not in page.split("Add note")[1]

    archive = client.get("/archive/").get_data(as_text=True)
    assert "Notes" in archive and "Call the roofer" in archive

    client.post("/archive/restore/note/1")
    assert "Call the roofer" in client.get("/notes/").get_data(as_text=True)

    client.post("/notes/1/archive")
    client.post("/archive/delete/note/1")
    with app.app_context():
        assert db_session.query(Note).count() == 0


def test_a_long_note_is_cut_short_in_the_archive(client):
    client.post("/notes/", data={"text": "x" * 200})
    client.post("/notes/1/archive")
    page = client.get("/archive/").get_data(as_text=True)
    assert "x" * 80 + "…" in page and "x" * 81 not in page
