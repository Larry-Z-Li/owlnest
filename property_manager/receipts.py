"""Checking uploaded files, turning photos into tidy JPGs, and naming the files.

Every file attached to a record goes through here (see attachments.py):

* A photo, including iPhone photos (HEIC), is turned upright (using the camera's
  orientation data), shrunk to about 2,000 pixels on the long side if it's very
  large, and saved as a new JPG (which also drops hidden details, such as where
  it was taken).
* A PDF is kept exactly as it is.

Each file can be up to 20 MB. It's saved under a new, unique name such as
2026-10-03_elm-st_a1b2c3.jpg, so two files never overwrite each other. While a
form is being checked, an upload waits in the computer's temporary folder (not
the data folder). (The module is called receipts.py because receipts were the
first files the app took.)
"""

import re
import secrets
import shutil
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

# Lets Pillow open iPhone photos (HEIC / HEIF).
pillow_heif.register_heif_opener()

MAX_BYTES = 20 * 1024 * 1024
LONGEST_SIDE = 2000
JPG_QUALITY = 85

# For the file boxes on the form: which files the computer's file picker offers.
PICTURE_ACCEPT = "image/*,.heic,.heif"
FILE_ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"

# A waiting upload's name: 32 random letters and numbers, then .jpg, .png or .pdf.
_PENDING_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|pdf)$")

PICTURE_EXTENSIONS = {".jpg", ".png"}


# The start of the message for a file that isn't a picture (the tenant portal words the rest its own way).
NOT_A_PICTURE = "This file couldn't be read as a picture."


class ReceiptError(ValueError):
    """A problem with an uploaded receipt. The message is written for her to read."""


def is_picture(file_name: str | None) -> bool:
    """True if a saved receipt is a picture (shown as a preview), False for a PDF."""
    return bool(file_name) and Path(file_name).suffix.lower() in PICTURE_EXTENSIONS


def process_picture(upload) -> str:
    """Check a photo, turn it upright, shrink it if very large, and keep it as a JPG.

    Returns the waiting upload's name (see store_receipt()).
    """
    data = _read_upload(upload)
    try:
        with Image.open(BytesIO(data)) as original:
            picture = ImageOps.exif_transpose(original)  # turn it upright
            if picture.mode in ("RGBA", "LA", "P"):
                # Put see-through areas (for example in a PNG screenshot) on white.
                picture = picture.convert("RGBA")
                background = Image.new("RGB", picture.size, "white")
                background.paste(picture, mask=picture.getchannel("A"))
                picture = background
            else:
                picture = picture.convert("RGB")
            picture.thumbnail((LONGEST_SIDE, LONGEST_SIDE))  # only ever makes it smaller
            output = BytesIO()
            picture.save(output, "JPEG", quality=JPG_QUALITY, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ReceiptError(f"{NOT_A_PICTURE} Please choose a photo, or use “Upload a file” for a PDF.") from None
    return _save_pending(output.getvalue(), ".jpg")


def process_file(upload) -> str:
    """Check a PDF, JPG or PNG file and keep it exactly as it is. Returns the waiting upload's name."""
    extension = Path(upload.filename or "").suffix.lower()
    if extension in (".heic", ".heif"):
        raise ReceiptError("For iPhone photos, please use “Upload a picture” instead.")
    if extension not in (".pdf", ".jpg", ".jpeg", ".png"):
        raise ReceiptError("Please choose a PDF, JPG, or PNG file.")
    data = _read_upload(upload)

    if extension == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ReceiptError("This file doesn't look like a PDF. Please check it opens on your computer.")
        return _save_pending(data, ".pdf")

    try:
        with Image.open(BytesIO(data)) as image:
            kind = image.format
            image.verify()
    except Exception:
        kind = None
    if kind not in ("JPEG", "PNG"):
        raise ReceiptError("This file doesn't look like a JPG or PNG picture.")
    return _save_pending(data, ".jpg" if kind == "JPEG" else ".png")


def process_any(upload) -> str:
    """For a single upload box that takes either a photo or a file (such as a W-9):
    photos (including iPhone HEIC) are tidied like pictures; PDFs are kept as they are."""
    extension = Path(upload.filename or "").suffix.lower()
    if extension == ".pdf":
        return process_file(upload)
    if extension in (".jpg", ".jpeg", ".png", ".heic", ".heif"):
        return process_picture(upload)
    raise ReceiptError("Please choose a PDF, or a photo (JPG, PNG, or iPhone HEIC).")


def is_waiting(pending_name: str) -> bool:
    """True if pending_name is a valid, still-waiting upload."""
    return bool(_PENDING_NAME.match(pending_name or "")) and (_pending_folder() / pending_name).exists()


def store_receipt(pending_name: str, receipts_folder: Path, expense_date: date, property_nickname: str) -> str:
    """Move a waiting upload into the receipts folder under a new, unique name, and return that name."""
    if not is_waiting(pending_name):
        raise ReceiptError("The receipt upload has expired. Please choose the file again.")
    source = _pending_folder() / pending_name
    receipts_folder.mkdir(parents=True, exist_ok=True)
    while True:
        name = f"{expense_date.isoformat()}_{_short_name(property_nickname)}_{secrets.token_hex(3)}{source.suffix}"
        try:
            # "x" means "create a new file": it fails instead of overwriting an existing one.
            with open(receipts_folder / name, "xb") as target, open(source, "rb") as waiting:
                shutil.copyfileobj(waiting, target)
            break
        except FileExistsError:
            continue  # astonishingly unlikely: pick another random name
    source.unlink()  # the waiting copy, in the temporary folder (not the data folder)
    return name


def discard_pending(pending_name: str) -> None:
    """Remove a waiting upload that won't be saved. (It's in the temporary folder, not the data folder.)"""
    if _PENDING_NAME.match(pending_name or ""):
        (_pending_folder() / pending_name).unlink(missing_ok=True)


def _read_upload(upload) -> bytes:
    data = upload.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ReceiptError("This file is larger than 20 MB. Please choose a smaller file.")
    if not data:
        raise ReceiptError("This file is empty. Please choose another file.")
    return data


def _save_pending(data: bytes, extension: str) -> str:
    name = secrets.token_hex(16) + extension
    (_pending_folder() / name).write_bytes(data)
    return name


def _pending_folder() -> Path:
    folder = Path(tempfile.gettempdir()) / "property-manager-receipts"
    folder.mkdir(exist_ok=True)
    return folder


def _short_name(text: str) -> str:
    """ "Elm St" becomes "elm-st", for use in file names."""
    short = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:30].strip("-")
    return short or "property"
