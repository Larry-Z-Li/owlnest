"""The Help page: plain-words answers to common questions, in English or Simplified Chinese.

The two versions are templates/help.html and templates/help_zh.html. When one changes,
change the other too; a test checks they have the same sections.
"""

from flask import Blueprint, make_response, render_template, request

bp = Blueprint("help", __name__, url_prefix="/help")

# Language code -> template. The menus and buttons stay in English in both.
LANGUAGES = {"en": "help.html", "zh": "help_zh.html"}

# Remembers the language she picked, so the Help link opens it next time.
COOKIE_NAME = "help_language"
ONE_YEAR = 365 * 24 * 60 * 60


@bp.get("/")
def index():
    chosen = request.args.get("lang")
    language = chosen or request.cookies.get(COOKIE_NAME, "en")
    if language not in LANGUAGES:
        language = "en"
    response = make_response(render_template(LANGUAGES[language], language=language))
    if chosen in LANGUAGES:
        response.set_cookie(COOKIE_NAME, chosen, max_age=ONE_YEAR, samesite="Lax")
    return response
