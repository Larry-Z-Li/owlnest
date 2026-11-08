"""People who can sign in (on the server only): adding someone, changing a password, unlocking, removing someone.

Everyone who can sign in can do all of this, from Settings. See accounts.py.
"""

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import accounts
from ..database import db_session
from ..forms import Form
from ..models import Account

bp = Blueprint("people", __name__, url_prefix="/settings/people")


@bp.before_request
def _only_on_the_server():
    if not current_app.config["ON_SERVER"]:
        abort(404)  # On one computer (run.py --local) nobody signs in.


@bp.get("/new")
def new():
    return _show_add_form(Form())


@bp.post("/new")
def add():
    form = Form(request.form)
    name = form.text("name", required=True, max_length=100)
    password, again = request.form.get("password", ""), request.form.get("password_again", "")
    if name and (problem := accounts.name_problem(name)):
        form.add_error("name", problem)
    if problem := accounts.password_problem(password, again):
        form.add_error("password", problem)
    if not form.is_valid:
        return _show_add_form(form, status=400)
    account = accounts.add(name, password)
    db_session.commit()
    flash(f"{account.name} can now sign in.", "success")
    return redirect(url_for("settings.index") + "#people")


@bp.get("/<int:account_id>/password")
def password(account_id: int):
    return _show_password_form(_get(account_id), Form())


@bp.post("/<int:account_id>/password")
def change_password(account_id: int):
    account = _get(account_id)
    form = Form(request.form)
    if problem := accounts.password_problem(request.form.get("password", ""), request.form.get("password_again", "")):
        form.add_error("password", problem)
        return _show_password_form(account, form, status=400)

    this_device = g.get("sign_in")
    if this_device is not None and this_device.account is account:
        accounts.change_password(account, request.form["password"], keep=this_device)
        flash("Your password was changed. You're signed out on your other devices.", "success")
    else:
        accounts.change_password(account, request.form["password"])
        flash(f"{account.name}'s password was changed. They're signed out everywhere and sign in again with the new one.",
              "success")
    db_session.commit()
    return redirect(url_for("settings.index") + "#people")


@bp.post("/<int:account_id>/unlock")
def unlock(account_id: int):
    account = _get(account_id)
    accounts.unlock(account)
    db_session.commit()
    flash(f"{account.name} can sign in again.", "success")
    return redirect(url_for("settings.index") + "#people")


@bp.post("/<int:account_id>/remove")
def remove(account_id: int):
    account = _get(account_id)
    this_device = g.get("sign_in")
    if this_device is not None and this_device.account is account:
        flash("You can't remove yourself while you're signed in. Someone else can remove you.", "error")
        return redirect(url_for("settings.index") + "#people")
    name = account.name
    # (An account is only a name and a password, with nothing else attached, so it's deleted, not archived.)
    db_session.delete(account)
    db_session.commit()
    flash(f"{name} can no longer sign in, and was signed out everywhere.", "success")
    return redirect(url_for("settings.index") + "#people")


def _get(account_id: int) -> Account:
    account = db_session.get(Account, account_id)
    if account is None:
        abort(404)
    return account


def _show_add_form(form: Form, status: int = 200):
    return render_template("people/add.html", form=form, shortest=accounts.SHORTEST_PASSWORD), status


def _show_password_form(account: Account, form: Form, status: int = 200):
    this_device = g.get("sign_in")
    return render_template(
        "people/password.html",
        account=account,
        form=form,
        shortest=accounts.SHORTEST_PASSWORD,
        is_me=this_device is not None and this_device.account is account,
    ), status
