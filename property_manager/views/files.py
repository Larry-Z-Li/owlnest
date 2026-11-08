"""Adding, opening and removing files attached to records (see attachments.py, which has the rules)."""

from flask import Blueprint, abort, current_app, flash, redirect, request, send_from_directory, url_for

from .. import attachments, receipts
from ..database import db_session
from ..models import Attachment, Insurance, Lease, LeaseExtension, MaintenanceRequest, Property, Transaction, Walkthrough
from ._shared import archive_record

bp = Blueprint("files", __name__, url_prefix="/files")


@bp.post("/<owner_kind>/<int:owner_id>")
def add(owner_kind: str, owner_id: int):
    """Attach the chosen files to a record, then go back to its page."""
    if owner_kind not in attachments.OWNERS:
        abort(404)
    model, _link = attachments.OWNERS[owner_kind]
    owner = db_session.get(model, owner_id)
    if owner is None or getattr(owner, "archived_at", None) is not None:
        abort(404)
    request.max_content_length = attachments.MAX_UPLOAD_BYTES  # before the files are read

    files = request.files.getlist("files")
    if not any(upload.filename for upload in files):
        flash("Please choose a file first.", "error")
        return redirect(owner_page(owner))
    try:
        saved = attachments.save_uploads(files, current_app.config["DATA_PATHS"].documents, owner_label(owner), owner)
    except receipts.ReceiptError as problem:
        flash(f"Nothing was added. {problem}", "error")
        return redirect(owner_page(owner))
    db_session.commit()
    flash(f"{len(saved)} {'file was' if len(saved) == 1 else 'files were'} added.", "success")
    return redirect(owner_page(owner))


@bp.get("/<int:attachment_id>")
def show(attachment_id: int):
    """Open a file in the browser, or download it with ?download=1."""
    attachment = _get(attachment_id)
    return send_from_directory(
        current_app.config["DATA_PATHS"].documents,
        attachment.file_path,
        as_attachment=bool(request.args.get("download")),
        download_name=attachment.original_name or attachment.file_path,
    )


@bp.post("/<int:attachment_id>/archive")
def archive(attachment_id: int):
    attachment = _get(attachment_id)
    archive_record(attachment, "attachment", f"The file “{attachment.original_name or attachment.file_path}”")
    return redirect(owner_page(attachments.owner_of(attachment)))


def attach_chosen_files(owner, form) -> bool:
    """Attach the files chosen in a form's "files" box to owner, a record being added or changed.

    On a problem with a file, undo everything (the record's other changes too), mark
    the form, and return False. The caller commits when it returns True.
    """
    db_session.flush()  # so a new owner has its number
    try:
        attachments.save_uploads(
            request.files.getlist("files"), current_app.config["DATA_PATHS"].documents, owner_label(owner), owner
        )
    except receipts.ReceiptError as problem:
        db_session.rollback()
        form.add_error("files", f"{problem} Nothing was saved yet; please choose the files again.")
        return False
    return True


def owner_page(owner) -> str:
    """The page that shows a record's attachments."""
    if isinstance(owner, Lease):
        return url_for("leases.detail", lease_id=owner.id, _anchor="documents")
    if isinstance(owner, Walkthrough):
        return url_for("leases.walkthrough", lease_id=owner.lease_id, kind=owner.kind.lower())
    if isinstance(owner, LeaseExtension):
        return url_for("leases.detail", lease_id=owner.lease_id, _anchor="extensions")
    if isinstance(owner, Insurance):
        return url_for("insurances.edit", insurance_id=owner.id, _anchor="files")
    if isinstance(owner, MaintenanceRequest):
        return url_for("maintenance.edit", request_id=owner.id, _anchor="files")
    if isinstance(owner, Transaction):
        return url_for("transactions.edit", transaction_id=owner.id, _anchor="files")
    if isinstance(owner, Property):
        return url_for("properties.photos", property_id=owner.id)
    raise ValueError(f"Not something with attachments: {owner!r}")


def owner_label(owner) -> str:
    """Words for the files' names, such as "elm st move-in"."""
    if isinstance(owner, Lease):
        return f"{owner.property.nickname} lease"
    if isinstance(owner, Walkthrough):
        return f"{owner.lease.property.nickname} {owner.kind}"
    if isinstance(owner, LeaseExtension):
        return f"{owner.lease.property.nickname} extension"
    if isinstance(owner, Insurance):
        return f"{owner.property.nickname} {owner.kind} insurance"
    if isinstance(owner, MaintenanceRequest):
        return f"{owner.property.nickname} request"
    if isinstance(owner, Property):
        return f"{owner.nickname} photo"
    return f"{owner.property.nickname if owner.property else 'general'} {owner.category}"


def _get(attachment_id: int) -> Attachment:
    attachment = db_session.get(Attachment, attachment_id)
    if attachment is None or attachment.archived_at is not None:
        abort(404)
    return attachment
