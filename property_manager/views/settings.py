"""The Settings page: her contact details (shown to tenants in the portal), who can sign in, and backups."""

from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from sqlalchemy import select

from .. import accounts, backup, bank_link, home_values, records, startup
from ..database import db_session, get_settings
from ..forms import Form
from ..models import BackupCopy

bp = Blueprint("settings", __name__, url_prefix="/settings")

@bp.get("/")
def index():
    settings = get_settings()
    folder_form = Form(initial={"backup_folder": settings.backup_folder})
    return _show_page(_details_form(settings), folder_form)


@bp.post("/")
def save_details():
    form = Form(request.form)
    business_name = form.text("business_name")
    mailing_address = form.text("mailing_address", max_length=500)
    phone = form.text("phone", max_length=50)

    if not form.is_valid:
        folder_form = Form(initial={"backup_folder": get_settings().backup_folder})
        return _show_page(form, folder_form, status=400)

    settings = get_settings()
    settings.business_name = business_name
    settings.mailing_address = mailing_address
    settings.phone = phone
    db_session.commit()
    flash("Your details were saved.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/backup-folder")
def save_backup_folder():
    if current_app.config["ON_SERVER"]:
        abort(404)  # The server has no second folder; each computer keeps its own copies.
    paths = current_app.config["DATA_PATHS"]
    settings = get_settings()
    form = Form(request.form)
    typed = form.text("backup_folder", max_length=1000)

    if typed:
        folder, error = backup.check_backup_folder(typed, paths.root)
        if error:
            form.add_error("backup_folder", error)
        if not form.is_valid:
            return _show_page(_details_form(settings), form, status=400)
        settings.backup_folder = str(folder)
    else:
        # An empty box means "go back to the standard folder in Documents".
        settings.backup_folder = ""
        folder = backup.second_folder("")
    db_session.commit()

    # Copy the newest backup there straight away, so she knows it works.
    newest = backup.list_backups(paths.backups)[:1]
    copy_error = backup.copy_to_extra_folder(newest[0], str(folder)) if newest else ""
    if copy_error:
        flash(f"The folder was saved, but a test copy didn't work: {copy_error}", "error")
    else:
        flash(f"Saved. From now on, every backup is also copied to {folder}.", "success")
    return redirect(url_for("settings.index"))


@bp.post("/home-values/look-up")
def look_up_home_values():
    """Run the hourly check right away, with the same rules (see home_values.py), and say what happened.

    No page links here: it's called with the server's key, when Larry asks (see the README).
    """
    if not home_values.lookups_are_on(current_app):
        abort(404)
    lines = home_values.look_up_due_values(current_app._get_current_object())
    for line in lines:
        print(f"{datetime.now():%Y-%m-%d %H:%M} {line}")
    return "\n".join(lines) or "No home is due, so nothing was looked up.", 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.get("/failed-logins")
def failed_logins():
    """How many wrong logins there have been (see accounts.py).

    No page links here: Larry asks Claude, who reads it with the server's key (see the README).
    """
    return accounts.failed_login_report(datetime.now()), 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.post("/bank/check")
def check_the_bank():
    """The "Fetch now" button: check the bank right away, within its daily limit (see bank_link.py)."""
    if not bank_link.is_connected(current_app):
        abort(404)
    lines = bank_link.check_now(current_app._get_current_object())
    for line in lines:
        print(f"{datetime.now():%Y-%m-%d %H:%M} {line}")
    problem = bank_link.last_problem(current_app)
    words = problem or lines[0].removeprefix("Bank: ")
    if problem:
        flash(words, "error")
    elif bank_link.TOO_MANY_CHECKS in lines:
        flash(words, "warning")
    else:
        flash(f"Fetched from the bank: {words}", "success")
    return redirect(url_for("settings.index", _anchor="bank"))


@bp.post("/backup-now")
def backup_now():
    result = startup.backup_now(current_app._get_current_object())

    if not result.succeeded:
        flash(result.error, "error")
    elif result.extra_folder and result.extra_copy_error:
        flash(f"The backup was saved on this computer. However: {result.extra_copy_error}", "warning")
    else:
        flash("Backup saved.", "success")
    return redirect(url_for("settings.index"))


@bp.get("/backups/newest")
def newest_backup():
    """Send the newest backup zip, making a fresh one first if it's a day old.

    The app on each computer downloads this once a day, so every computer keeps
    copies of her records off the server (see relay.py).
    """
    app = current_app._get_current_object()
    startup.backup_if_due(app)
    newest = backup.list_backups(app.config["DATA_PATHS"].backups)[:1]
    if not newest:
        abort(404)
    return send_file(newest[0], mimetype="application/zip", as_attachment=True, download_name=newest[0].name)


@bp.post("/backups/copied")
def backup_copied():
    """The app on a computer saying it has a copy of the newest backup (see relay.copy_newest_backup).

    copied_at says when that copy was made, if it isn't from just now.
    """
    computer = request.form.get("computer", "").strip()[:100] or "A computer"
    now = datetime.now()
    try:
        copied_at = min(datetime.fromisoformat(request.form.get("copied_at", "")), now)
    except ValueError:
        copied_at = now
    record = db_session.scalar(select(BackupCopy).where(BackupCopy.computer == computer))
    if record is None:
        db_session.add(BackupCopy(computer=computer, last_copied_at=copied_at))
    elif copied_at > record.last_copied_at:
        record.last_copied_at = copied_at
    db_session.commit()
    return "", 204


def _details_form(settings) -> Form:
    return Form(
        initial={
            "business_name": settings.business_name,
            "mailing_address": settings.mailing_address,
            "phone": settings.phone,
        }
    )


def _bank_status() -> dict:
    """How the link to her bank is going (see bank_link.py)."""
    return {
        "set_up": bank_link.is_set_up(current_app),
        "connected": bank_link.is_connected(current_app),
        "name": bank_link.bank_name(current_app),
        "last_check": bank_link.last_good_check(),
        "problem": bank_link.last_problem(current_app),
        "key_variables": (bank_link.CLIENT_ID_VARIABLE, bank_link.SECRET_VARIABLE),
    }


def _home_values_status() -> dict:
    """How the monthly home-value lookups are going (see home_values.py)."""
    homes = [a_property for a_property in records.active_properties() if a_property.look_up_value]
    return {
        "on": home_values.lookups_are_on(current_app),
        "key_variable": home_values.API_KEY_VARIABLE,
        "stopped": current_app.config.get("VALUE_LOOKUPS_STOPPED", ""),
        "recent": home_values.lookups_since(datetime.now() - home_values.LIMIT_PERIOD),
        "most": home_values.MOST_LOOKUPS,
        "homes": homes,
        "no_zip": [a_property for a_property in homes if not home_values.has_zip_code(a_property.address)],
    }


def _show_page(details_form: Form, folder_form: Form, status: int = 200):
    paths = current_app.config["DATA_PATHS"]
    on_server = current_app.config["ON_SERVER"]
    settings = get_settings()
    return (
        render_template(
            "settings.html",
            details_form=details_form,
            folder_form=folder_form,
            settings=settings,
            data_folder=paths.root,
            backups_folder=paths.backups,
            what_is_kept=backup.WHAT_IS_KEPT,
            last_backup=backup.last_successful_backup(paths.backups),
            second_folder=backup.second_folder(settings.backup_folder),
            # (The server's own folders mean nothing to her, so don't look for any there.)
            suggestions=[] if on_server else backup.suggested_backup_folders(),
            backup_copies=list(db_session.scalars(select(BackupCopy).order_by(BackupCopy.last_copied_at.desc()))),
            people=accounts.everyone() if on_server else [],
            public_address=current_app.config["PUBLIC_ADDRESS"],
            stays_signed_in_days=accounts.STAYS_SIGNED_IN_FOR.days,
            values=_home_values_status() if on_server else None,
            bank=_bank_status() if on_server or bank_link.is_set_up(current_app) else None,
        ),
        status,
    )
