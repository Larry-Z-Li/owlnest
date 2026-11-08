"""Photos of each property: the slideshow on its page, and the Photos page that adds and removes them."""

import re
from io import BytesIO

from helpers import PDF, add_property, get
from PIL import Image

from property_manager.models import Attachment


def photo(name="house.jpg", size=(1200, 800)):
    data = BytesIO()
    Image.new("RGB", size, "skyblue").save(data, "JPEG")
    data.seek(0)
    return data, name


def add_photos(client, property_id, *files):
    return client.post(
        f"/properties/{property_id}/photos", data={"files": list(files)}, content_type="multipart/form-data"
    ).get_data(as_text=True)


def slides(page) -> list[str]:
    return re.findall(r'<img class="slide-photo" src="([^"]+)"', page)


def test_a_property_without_photos_shows_its_drawing_and_add_photos(client):
    elm = add_property(client)
    page = client.get(f"/properties/{elm}").get_data(as_text=True)
    assert "No photos yet" in page and "Add photos" in page
    assert f'href="/properties/{elm}/photos"' in page
    assert "slideshow.js" not in page


def test_adding_photos_makes_a_slideshow(client):
    elm = add_property(client)
    assert "2 photos were added." in add_photos(client, elm, photo("front.jpg"), photo("IMG_1234.jpg", (800, 1200)))

    page = client.get(f"/properties/{elm}").get_data(as_text=True)
    assert slides(page) == ["/files/1", "/files/2"]  # in the order they were added
    assert '<span class="slide-number">1</span> of 2' in page
    assert "Edit photos" in page and "slideshow.js" in page
    assert client.get("/files/1").mimetype == "image/jpeg"

    photos_page = client.get(f"/properties/{elm}/photos").get_data(as_text=True)
    assert "2 photos" in photos_page and "1 · first" in photos_page


def test_only_photos_can_be_added(client):
    elm = add_property(client)
    page = add_photos(client, elm, (BytesIO(PDF), "lease.pdf"))
    assert "Nothing was added." in page and "Please choose a photo." in page
    assert "Please choose a photo first." in add_photos(client, elm)
    assert slides(client.get(f"/properties/{elm}").get_data(as_text=True)) == []


def test_removing_a_photo_moves_it_to_the_archive(client, app):
    elm = add_property(client)
    add_photos(client, elm, photo("front.jpg"), photo("back.jpg"))
    page = client.post("/files/1/archive").get_data(as_text=True)
    assert "was moved to the Archive" in page and "1 photo" in page  # back on the Photos page
    shown = client.get(f"/properties/{elm}").get_data(as_text=True)
    assert slides(shown) == ["/files/2"] and "slide-number" not in shown  # one photo: no arrows or count

    client.post("/archive/restore/attachment/1")
    assert get(app, Attachment, 1, "archived_at") is None


def test_an_archived_property_keeps_its_photos_out_of_reach(client):
    elm = add_property(client)
    add_photos(client, elm, photo())
    client.post(f"/properties/{elm}/archive")
    assert client.get(f"/properties/{elm}/photos").status_code == 404
    assert "was deleted forever" in client.post(f"/archive/delete/property/{elm}").get_data(as_text=True)


def test_home_shows_a_homes_first_photo(client):
    with_photo = add_property(client, "Elm St")
    add_property(client, "Oak Ave", "12 Oak Ave")
    add_photos(client, with_photo, photo("first.jpg"), photo("second.jpg"))
    page = client.get("/").get_data(as_text=True)
    assert page.count('class="home-card-photo"') == 1
    assert 'class="drawing drawing-' in page  # the home without a photo keeps its drawing
    first = page.split('class="home-card-photo" src="')[1].split('"')[0]
    assert client.get(first).status_code == 200
