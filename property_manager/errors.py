"""Friendly error pages, in place of the web server's technical ones."""

from flask import Flask, render_template, request

from .security import is_portal_path

# For each kind of error: a page title and an explanation in plain words.
MESSAGES = {
    400: (
        "Something about that request wasn't right",
        "Please use the menu at the top to go back and try again.",
    ),
    403: (
        "That action was blocked for safety",
        "The app only accepts changes made on its own pages. "
        "Please go back, reload the page, and try again.",
    ),
    404: (
        "We couldn't find that page",
        "It may have been moved or archived. Please use the menu at the top.",
    ),
    405: (
        "That page can't be used that way",
        "Please use the menu at the top to go back and try again.",
    ),
    413: (
        "That file is too big",
        "Files can be up to 20 MB each. Please go back and choose a smaller file, or fewer files at once.",
    ),
    500: (
        "Something went wrong",
        "The last change may not have been saved, but your other records are safe. "
        "Please go back and try again. If it keeps happening, note what you were doing "
        "and ask for help.",
    ),
}


# The same, for tenants in the tenant portal (who don't see the app's menus).
PORTAL_MESSAGES = {
    403: ("That was blocked for safety", "Please go back, reload the page, and try again."),
    404: ("We couldn't find that page", "Please go back to your requests."),
    413: (
        "Those photos are too big",
        "Each photo can be up to 20 MB, and you can add up to 10 at a time. "
        "Please go back and choose fewer or smaller photos.",
    ),
    500: (
        "Something went wrong",
        "Your last message may not have been sent. Please go back and try again. "
        "If it keeps happening, please call instead.",
    ),
}


def init_app(app: Flask) -> None:
    for code in MESSAGES:
        app.register_error_handler(code, _show_error_page)


def _show_error_page(error):
    code = getattr(error, "code", 500) or 500
    if is_portal_path(request.path):
        title, message = PORTAL_MESSAGES.get(code, PORTAL_MESSAGES[500])
        return render_template("portal/error.html", title=title, message=message), code
    title, message = MESSAGES.get(code, MESSAGES[500])
    return render_template("error.html", title=title, message=message), code
