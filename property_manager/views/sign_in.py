"""The sign-in page (on the server only), and signing out. See accounts.py for how signing in works."""

from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .. import accounts
from ..database import db_session
from ..forms import Form
from ._shared import own_page

bp = Blueprint("sign_in", __name__)


@bp.before_request
def _only_on_the_server():
    if not current_app.config["ON_SERVER"]:
        # On one computer (run.py --local) nobody signs in.
        return redirect(url_for("home.index"))
    return None


@bp.get("/sign-in")
def page():
    next_page = own_page(request.args.get("next", ""))
    if accounts.sign_in_for(request.cookies.get(accounts.COOKIE), datetime.now()) is not None:
        return redirect(next_page)  # already signed in
    return _show(Form(), next_page)


@bp.post("/sign-in")
def sign_in():
    form = Form(request.form)
    next_page = own_page(form.raw("next"))
    name = form.text("name", required=True, max_length=100)
    password = request.form.get("password", "")
    if not password:
        form.add_error("password", "Please type your password.")
    if not form.is_valid:
        return _show(form, next_page, status=400)

    now = datetime.now()
    account, problem = accounts.check_password(name, password, now)
    db_session.commit()  # (saves that the wrong tries start over, if they were right)
    if problem:
        print(f"Sign-in refused for the name {name!r}: {problem}")
        form.add_error("password", problem)
        return _show(form, next_page, status=400)

    code = accounts.start(account, request.headers.get("User-Agent", ""), now)
    db_session.commit()
    response = redirect(next_page, 303)
    accounts.put_cookie(response, code)
    return response


@bp.post("/sign-out")
def sign_out():
    sign_in = accounts.sign_in_for(request.cookies.get(accounts.COOKIE), datetime.now())
    if sign_in is not None:
        db_session.delete(sign_in)
        db_session.commit()
    flash("You're signed out.", "success")
    response = redirect(url_for("sign_in.page"), 303)
    accounts.remove_cookie(response)
    return response


def _show(form: Form, next_page: str, status: int = 200):
    response = current_app.make_response((render_template("sign_in.html", form=form, next_page=next_page), status))
    response.headers["Cache-Control"] = "no-store"
    return response
