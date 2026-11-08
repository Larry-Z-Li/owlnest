"""The Notes screen: a list of plain text notes. She can add one, or move one to the Archive."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from ..database import db_session
from ..forms import Form
from ..models import Note
from ._shared import archive_record, get_active_or_404

bp = Blueprint("notes", __name__, url_prefix="/notes")


@bp.get("/")
def index():
    return _show_list(Form())


@bp.post("/")
def create():
    form = Form(request.form)
    text = form.text("text", required=True, max_length=5000)
    if not form.is_valid:
        return _show_list(form, status=400)
    db_session.add(Note(text=text))
    db_session.commit()
    flash("Your note was added.", "success")
    return redirect(url_for("notes.index"))


@bp.post("/<int:note_id>/archive")
def archive(note_id: int):
    note = get_active_or_404(Note, note_id)
    archive_record(note, "note", "The note")
    return redirect(url_for("notes.index"))


def _show_list(form: Form, status: int = 200):
    query = select(Note).where(Note.archived_at.is_(None)).order_by(Note.created_at.desc(), Note.id.desc())
    return render_template("notes/list.html", form=form, notes=list(db_session.scalars(query))), status
