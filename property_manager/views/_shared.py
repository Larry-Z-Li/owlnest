"""Small helpers that several screens use."""

from datetime import datetime
from itertools import groupby
from urllib.parse import quote

from flask import Response, abort, flash, request, url_for
from werkzeug.wsgi import wrap_file

from ..database import db_session


def get_active_or_404(model, record_id: int):
    """Load a record by id, or show the "We couldn't find that page" page if it's missing or archived."""
    record = db_session.get(model, record_id)
    if record is None or record.archived_at is not None:
        abort(404)
    return record


def own_page(address: str) -> str:
    """Where to go next (such as after signing in): the page asked for, if it's one of the app's own pages."""
    # ("//elsewhere.com" or "/\\elsewhere.com" would lead to another website.)
    if address.startswith("/") and not address.startswith(("//", "/\\")) and address.isprintable():
        return address
    return url_for("home.index")


def as_id(text) -> int | None:
    """A record number from a link or a drop-down list ("12"), or None if the text isn't one.

    Only plain digits count, and at most 9 of them, so odd input (such as "²" or a
    huge number) can't cause an error.
    """
    text = (text or "").strip()
    return int(text) if text.isdecimal() and len(text) <= 9 else None


def read_choice(form, field: str, model, required_message: str | None = None, keep=()):
    """Read a drop-down list's choice and load that record. Returns None if nothing valid was chosen.

    Problems are added to the form. keep lists records that may be chosen even though
    they're archived: the ones already saved on the record being edited, so editing
    it (for example, fixing a note) never quietly drops them.
    """
    value = form.raw(field).strip()
    if not value:
        if required_message:
            form.add_error(field, required_message)
        return None
    record_id = as_id(value)
    record = db_session.get(model, record_id) if record_id is not None else None
    if record is None or (record.archived_at is not None and record not in keep):
        form.add_error(field, "That choice is no longer available. Please choose again.")
        return None
    return record


def archive_record(record, kind: str, name: str, before_saving=None, undo_question: str = "") -> None:
    """Move a record to the Archive, and show a message with an Undo button.

    kind is the record type used by the Archive page's restore link, such as "tenant".
    before_saving, if given, runs after the record is marked archived and before
    saving, for anything that must change along with it. undo_question, if given,
    is asked (OK or Cancel) before Undo restores it.
    """
    record.archived_at = datetime.now()
    if before_saving is not None:
        before_saving()
    db_session.commit()
    # base.html shows "undo" messages with an Undo button (see templates/base.html).
    flash({"text": f"{name} was moved to the Archive.", "kind": kind, "id": record.id, "question": undo_question}, "undo")


def download(data, file_name: str, mimetype: str) -> Response:
    """Send data as a file to save, under a name that works in every browser, even with Chinese letters.

    data is the file's bytes, or an open file, which is sent a piece at a time and closed afterwards.
    """
    plain = file_name.encode("ascii", "replace").decode("ascii").replace('"', "")
    disposition = f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(file_name)}"
    if isinstance(data, bytes):
        return Response(data, mimetype=mimetype, headers={"Content-Disposition": disposition})
    size = data.seek(0, 2)
    data.seek(0)
    return Response(
        wrap_file(request.environ, data),
        mimetype=mimetype,
        headers={"Content-Disposition": disposition, "Content-Length": str(size)},
        direct_passthrough=True,
    )


# How many of the newest months start open in a list grouped by month.
OPEN_MONTHS = 3


def money_in(items: list) -> int:
    return sum(item.amount_cents for item in items if item.amount_cents > 0)


def money_out(items: list) -> int:
    return sum(item.amount_cents for item in items if item.amount_cents < 0)


def months_of(items: list, date_of, all_open: bool = False) -> list[dict]:
    """Records with an amount (newest first) grouped by month, with each month's totals.

    The newest OPEN_MONTHS months start open, and older ones folded. all_open: when she's
    filtering or searching, so nothing she looked for is hidden.
    """
    months = []
    for first_day, group in groupby(items, key=lambda item: date_of(item).replace(day=1)):
        rows = list(group)
        months.append(
            {
                "name": f"{first_day:%B %Y}",
                "rows": rows,
                "in": money_in(rows),
                "out": money_out(rows),
                "open": all_open or len(months) < OPEN_MONTHS,
            }
        )
    return months
