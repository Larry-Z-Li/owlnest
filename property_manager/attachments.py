"""Files attached to records: a lease, its walkthroughs and extensions, insurances, maintenance
requests, and transactions; and photos of each property.

She can attach several files at once. PDFs are kept exactly as they are; photos
(including iPhone photos) are turned upright and shrunk, like receipt pictures
(see receipts.py). The files are saved in the data folder's documents/ folder, so
every backup includes them. Removing an attachment only archives it: the file
stays, and the Archive page can bring it back.

The records that can have attachments are listed in OWNERS. views/files.py
has the pages that add, open and remove them.
"""

from datetime import date
from pathlib import Path

from . import receipts
from .database import db_session
from .models import Attachment, Insurance, Lease, LeaseExtension, MaintenanceRequest, Property, Transaction, Walkthrough

# Several photos at once add up, so these uploads may be big (each file is still at most 20 MB).
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_FILES = 30

# What the file pickers offer: PDFs and photos (iPhone photos too).
ACCEPT = "application/pdf,.pdf,image/*,.heic,.heif"

# The records that can have attachments: {name in links: (table, the attachment's link to it)}.
OWNERS = {
    "lease": (Lease, "lease"),
    "walkthrough": (Walkthrough, "walkthrough"),
    "extension": (LeaseExtension, "extension"),
    "insurance": (Insurance, "insurance"),
    "request": (MaintenanceRequest, "request"),
    "transaction": (Transaction, "transaction"),
    "property": (Property, "property"),  # its photos
}


def owner_of(attachment: Attachment):
    """The record a file is attached to."""
    return (
        attachment.lease
        or attachment.walkthrough
        or attachment.extension
        or attachment.insurance
        or attachment.request
        or attachment.transaction
        or attachment.property
    )


def save_uploads(
    uploads, documents_folder: Path, label: str, owner, photos_only: bool = False, max_files: int = MAX_FILES
) -> list[Attachment]:
    """Check every uploaded file, then attach them all to owner (a record from OWNERS). The caller commits.

    label goes in the files' names, such as "elm st" (see receipts.store_receipt).
    With photos_only (for tenants), PDFs aren't accepted, and every photo is redrawn,
    which also drops hidden details such as where it was taken. Raises
    receipts.ReceiptError, with a message to read, if any file is wrong or there are
    too many; then nothing is saved.
    """
    uploads = [upload for upload in uploads if upload and upload.filename]
    if len(uploads) > max_files:
        raise receipts.ReceiptError(f"Please choose at most {max_files} files at a time.")

    # Check every file before saving any, so a bad one doesn't leave half an upload.
    waiting = []
    for upload in uploads:
        try:
            processed = receipts.process_picture(upload) if photos_only else receipts.process_any(upload)
            waiting.append((processed, upload.filename))
        except receipts.ReceiptError as problem:
            for name, _original in waiting:
                receipts.discard_pending(name)
            message = str(problem)
            if message.startswith(receipts.NOT_A_PICTURE):
                message = "It isn't a photo that can be opened. Please choose " + (
                    "a photo." if photos_only else "a PDF or a photo."
                )
            raise receipts.ReceiptError(f"“{Path(upload.filename).name}”: {message}") from None

    link = next(link for model, link in OWNERS.values() if isinstance(owner, model))
    saved = []
    for name, original in waiting:
        stored = receipts.store_receipt(name, documents_folder, date.today(), label)
        attachment = Attachment(
            file_path=stored,
            # Photos become JPGs, so "IMG_1234.HEIC" is downloaded as "IMG_1234.jpg".
            original_name=(Path(original).stem[:150] or "file") + Path(stored).suffix,
            **{link: owner},
        )
        db_session.add(attachment)
        saved.append(attachment)
    return saved


def active(attachments: list[Attachment]) -> list[Attachment]:
    """The attachments that haven't been removed (archived)."""
    return [attachment for attachment in attachments if attachment.archived_at is None]
