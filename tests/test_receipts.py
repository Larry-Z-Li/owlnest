"""Receipt uploads: pictures are turned upright, shrunk and saved as JPG; files are kept as they are."""

import re
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from property_manager import receipts
from property_manager.receipts import ReceiptError


def picture_bytes(size=(400, 200), format="JPEG", orientation=None, mode="RGB") -> bytes:
    image = Image.new(mode, size, "red")
    output = BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation  # the camera's "which way up" tag
        image.save(output, format, exif=exif)
    else:
        image.save(output, format)
    return output.getvalue()


def upload(data: bytes, file_name: str) -> FileStorage:
    return FileStorage(stream=BytesIO(data), filename=file_name)


def waiting_file(pending_name: str) -> Path:
    return receipts._pending_folder() / pending_name


def test_iphone_heic_photo_becomes_a_jpg():
    pending = receipts.process_picture(upload(picture_bytes(format="HEIF"), "IMG_1234.HEIC"))
    assert pending.endswith(".jpg")
    with Image.open(waiting_file(pending)) as saved:
        assert saved.format == "JPEG"
        assert saved.size == (400, 200)


def test_photo_is_turned_upright_using_the_camera_orientation():
    # Orientation 6 means "the camera was turned sideways": the picture must be rotated.
    pending = receipts.process_picture(upload(picture_bytes((400, 200), orientation=6), "receipt.jpg"))
    with Image.open(waiting_file(pending)) as saved:
        assert saved.size == (200, 400)


def test_very_large_photo_is_shrunk_to_about_2000_pixels():
    pending = receipts.process_picture(upload(picture_bytes((4032, 3024)), "big.jpg"))
    with Image.open(waiting_file(pending)) as saved:
        assert saved.size == (2000, 1500)


def test_small_photo_is_not_enlarged():
    pending = receipts.process_picture(upload(picture_bytes((300, 500)), "small.jpg"))
    with Image.open(waiting_file(pending)) as saved:
        assert saved.size == (300, 500)


def test_see_through_png_screenshot_becomes_a_jpg_on_white():
    transparent = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    output = BytesIO()
    transparent.save(output, "PNG")

    pending = receipts.process_picture(upload(output.getvalue(), "screenshot.png"))

    assert pending.endswith(".jpg")
    with Image.open(waiting_file(pending)) as saved:
        assert saved.getpixel((10, 10)) == (255, 255, 255)


def test_something_that_is_not_a_picture_is_refused():
    with pytest.raises(ReceiptError, match="couldn't be read as a picture"):
        receipts.process_picture(upload(b"%PDF-1.4 not a picture", "receipt.pdf"))


def test_pdf_file_is_kept_exactly_as_it_is():
    pdf = b"%PDF-1.4\n% an emailed receipt\n%%EOF"
    pending = receipts.process_file(upload(pdf, "Receipt from Home Depot.PDF"))
    assert pending.endswith(".pdf")
    assert waiting_file(pending).read_bytes() == pdf


def test_jpg_and_png_files_are_kept_exactly_as_they_are():
    jpg = picture_bytes((5000, 4000))  # not shrunk, because it was uploaded as a file
    pending = receipts.process_file(upload(jpg, "scan.jpeg"))
    assert pending.endswith(".jpg") and waiting_file(pending).read_bytes() == jpg

    png = picture_bytes(format="PNG")
    assert receipts.process_file(upload(png, "scan.png")).endswith(".png")


@pytest.mark.parametrize(
    "data, file_name, message",
    [
        (b"anything", "photo.HEIC", "use “Upload a picture”"),
        (b"anything", "receipt.docx", "Please choose a PDF, JPG, or PNG file."),
        (b"<html>not a pdf</html>", "receipt.pdf", "doesn't look like a PDF"),
        (b"not a picture at all", "receipt.jpg", "doesn't look like a JPG or PNG"),
        (b"", "receipt.pdf", "This file is empty."),
    ],
)
def test_wrong_files_are_refused_in_plain_words(data, file_name, message):
    with pytest.raises(ReceiptError, match=re.escape(message)):
        receipts.process_file(upload(data, file_name))


def test_files_over_20_mb_are_refused():
    too_big = b"%PDF" + b"0" * (20 * 1024 * 1024)
    with pytest.raises(ReceiptError, match="larger than 20 MB"):
        receipts.process_file(upload(too_big, "huge.pdf"))


def test_stored_receipts_get_unique_safe_names(tmp_path):
    names = set()
    for _ in range(3):
        pending = receipts.process_file(upload(b"%PDF-1.4", "same name.pdf"))
        names.add(receipts.store_receipt(pending, tmp_path, date(2026, 10, 3), "Elm St."))
        assert not waiting_file(pending).exists()
    assert len(names) == 3
    for name in names:
        assert re.fullmatch(r"2026-10-03_elm-st_[0-9a-f]{6}\.pdf", name)
        assert (tmp_path / name).read_bytes() == b"%PDF-1.4"


def test_expired_or_made_up_uploads_are_refused(tmp_path):
    with pytest.raises(ReceiptError, match="expired"):
        receipts.store_receipt("0" * 32 + ".pdf", tmp_path, date(2026, 1, 1), "Elm St")
    with pytest.raises(ReceiptError, match="expired"):
        receipts.store_receipt("../../app.db", tmp_path, date(2026, 1, 1), "Elm St")
