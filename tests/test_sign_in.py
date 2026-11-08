"""Signing in to the app in a web browser, on the server, and the people who can."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from conftest import BrowserLikeClient
from helpers import add_property
from sqlalchemy import select

from property_manager import accounts, alerts, create_app
from property_manager.config import KEY_HEADER
from property_manager.database import db_session
from property_manager.models import Account, Property, SignIn

KEY = "a-test-key-that-is-long-enough-to-be-accepted"
ADDRESS = "https://example.onrender.com"
PASSWORD = "tofu ledger on elm street"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1"


@pytest.fixture
def server_app(tmp_path):
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    app.config["PUBLIC_ADDRESS"] = ADDRESS
    app.test_client_class = BrowserLikeClient
    return app


@pytest.fixture
def with_key(server_app):
    """The app installed on a computer, which sends the key instead of signing in."""
    client = server_app.test_client()
    client.environ_base["HTTP_" + KEY_HEADER.upper().replace("-", "_")] = KEY
    return client


def add_person(client, name="Anna", password=PASSWORD, again=None):
    return client.post(
        "/settings/people/new",
        data={"name": name, "password": password, "password_again": password if again is None else again},
        base_url=ADDRESS,
    )


def browser(server_app):
    return server_app.test_client()


def sign_in(client, name="Anna", password=PASSWORD, next_page="", user_agent=IPHONE):
    return client.post(
        "/sign-in",
        data={"name": name, "password": password, "next": next_page},
        headers={"User-Agent": user_agent},
        base_url=ADDRESS,
    )


def signed_in_browser(server_app, **details):
    client = browser(server_app)
    assert sign_in(client, **details).request.path == "/"
    return client


def in_database(app, model):
    with app.app_context():
        return list(db_session.scalars(select(model)))


# ---------- Not signed in ----------


def test_someone_not_signed_in_sees_only_the_sign_in_page(server_app, with_key):
    add_property(with_key, "Elm St")
    response = browser(server_app).get("/properties/", base_url=ADDRESS)
    page = response.get_data(as_text=True)
    assert response.request.path == "/sign-in"
    assert "Sign in" in page and "Elm St" not in page
    assert "main-nav" not in page and "Archive" not in page  # no menus
    assert response.headers["Cache-Control"] == "no-store"


def test_forms_sent_without_signing_in_change_nothing(server_app):
    response = browser(server_app).post("/properties/new", data={"nickname": "Oak Ave"}, base_url=ADDRESS)
    assert response.request.path == "/sign-in"
    assert in_database(server_app, Property) == []


def test_the_sign_in_page_doesnt_work_out_her_alerts(server_app, monkeypatch):
    worked_out = []
    monkeypatch.setattr(alerts, "gather", lambda *args: worked_out.append(1) or [])
    browser(server_app).get("/", base_url=ADDRESS)
    assert worked_out == []


# ---------- Signing in ----------


def test_signing_in_shows_her_records_and_who_is_signed_in(server_app, with_key):
    add_person(with_key)
    add_property(with_key, "Elm St")
    client = browser(server_app)
    page = sign_in(client, name="  anna ", next_page="/properties/").get_data(as_text=True)  # capitals don't matter
    assert "Elm St" in page
    assert "Sign out" in page and "Anna" in page
    assert "Close the app" not in page  # there's no app to close in a browser

    client.post("/properties/new", data={"nickname": "Oak Ave"}, base_url=ADDRESS)
    assert "Oak Ave" in with_key.get("/properties/").get_data(as_text=True)


def test_the_sign_in_cookie_is_kept_safe(server_app, with_key):
    add_person(with_key)
    response = browser(server_app).post(
        "/sign-in", data={"name": "Anna", "password": PASSWORD}, base_url=ADDRESS, follow_redirects=False
    )
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith(f"{accounts.COOKIE}=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Max-Age=2592000" in cookie  # 30 days


def test_passwords_and_codes_are_stored_scrambled(server_app, with_key):
    add_person(with_key)
    signed_in_browser(server_app)
    stored = in_database(server_app, Account)[0].password_hash
    assert PASSWORD not in stored and stored.startswith("scrypt:")
    device = in_database(server_app, SignIn)[0]
    assert device.device == "iPhone (Safari)" and len(device.token_hash) == 64


def test_a_wrong_name_or_password_gets_the_same_answer(server_app, with_key):
    add_person(with_key)
    client = browser(server_app)
    wrong_password = sign_in(client, password="not the password").get_data(as_text=True)
    wrong_name = sign_in(client, name="Nobody").get_data(as_text=True)
    assert accounts.WRONG_NAME_OR_PASSWORD.replace("'", "&#39;") in wrong_password
    assert accounts.WRONG_NAME_OR_PASSWORD.replace("'", "&#39;") in wrong_name
    assert PASSWORD not in wrong_password  # what was typed isn't shown again
    assert client.get("/", base_url=ADDRESS).request.path == "/sign-in"


def test_ten_wrong_passwords_lock_the_name_until_someone_unlocks_it(server_app, with_key):
    add_person(with_key)
    phone = signed_in_browser(server_app)
    client = browser(server_app)
    for _ in range(9):
        assert "don&#39;t match" in sign_in(client, password="a wrong guess").get_data(as_text=True)
    assert "This name is locked" in sign_in(client, password="a wrong guess").get_data(as_text=True)
    # Even the right password doesn't work while it's locked, however long it's been.
    with server_app.app_context():
        db_session.scalars(select(Account)).one().locked_at = datetime.now() - timedelta(days=30)
        db_session.commit()
    assert "This name is locked" in sign_in(client).get_data(as_text=True)

    # The device that was already signed in still works, and the Home page says so.
    home = phone.get("/", base_url=ADDRESS).get_data(as_text=True)
    assert "Anna&#39;s sign-in is locked" in home

    page = with_key.post("/settings/people/1/unlock").get_data(as_text=True)
    assert "Anna can sign in again." in page
    assert sign_in(client).request.path == "/"
    assert "sign-in is locked" not in phone.get("/", base_url=ADDRESS).get_data(as_text=True)


def test_a_new_password_also_unlocks_the_name(server_app, with_key):
    add_person(with_key)
    for _ in range(accounts.WRONG_TRIES_ALLOWED):
        sign_in(browser(server_app), password="a wrong guess")
    new_password = "a different password"
    with_key.post("/settings/people/1/password", data={"password": new_password, "password_again": new_password})
    assert sign_in(browser(server_app), password=new_password).request.path == "/"


def test_a_right_password_starts_the_count_again(server_app, with_key):
    add_person(with_key)
    for _ in range(9):
        sign_in(browser(server_app), password="a wrong guess")
    signed_in_browser(server_app)
    for _ in range(9):
        sign_in(browser(server_app), password="a wrong guess")
    assert sign_in(browser(server_app)).request.path == "/"


def test_after_signing_in_it_only_goes_to_the_apps_own_pages(server_app, with_key):
    add_person(with_key)
    for elsewhere in ("//evil.example/", "https://evil.example/", "/\\evil.example"):
        assert sign_in(browser(server_app), next_page=elsewhere).request.path == "/"


def test_forms_from_other_websites_are_refused(server_app, with_key):
    add_person(with_key)
    client = signed_in_browser(server_app)
    response = client.post(
        "/properties/new", data={"nickname": "Oak Ave"}, headers={"Origin": "https://evil.example"}, base_url=ADDRESS
    )
    assert response.status_code == 403 and in_database(server_app, Property) == []
    # Nor can another website sign someone in.
    response = browser(server_app).post(
        "/sign-in", data={"name": "Anna", "password": PASSWORD}, headers={"Origin": "https://evil.example"}, base_url=ADDRESS
    )
    assert response.status_code == 403


# ---------- Staying signed in, and signing out ----------


def test_signing_out_ends_that_sign_in_for_good(server_app, with_key):
    add_person(with_key)
    client = signed_in_browser(server_app)
    code = client.get_cookie(accounts.COOKIE, domain="example.onrender.com").value
    page = client.post("/sign-out", base_url=ADDRESS).get_data(as_text=True)
    assert "You&#39;re signed out." in page
    assert client.get_cookie(accounts.COOKIE, domain="example.onrender.com") is None
    assert in_database(server_app, SignIn) == []

    # A copy of the old cookie doesn't work either.
    copied = browser(server_app)
    copied.set_cookie(accounts.COOKIE, code, domain="example.onrender.com")
    assert copied.get("/", base_url=ADDRESS).request.path == "/sign-in"


def test_a_device_not_used_for_30_days_is_signed_out(server_app, with_key):
    add_person(with_key)
    client = signed_in_browser(server_app)
    with server_app.app_context():
        db_session.scalars(select(SignIn)).one().last_used_at = datetime.now() - timedelta(days=31)
        db_session.commit()
    assert client.get("/", base_url=ADDRESS).request.path == "/sign-in"


def test_using_the_app_keeps_it_signed_in(server_app, with_key):
    add_person(with_key)
    client = signed_in_browser(server_app)
    with server_app.app_context():
        db_session.scalars(select(SignIn)).one().last_used_at = datetime.now() - timedelta(days=29)
        db_session.commit()
    response = client.get("/", base_url=ADDRESS, follow_redirects=False)
    assert "Max-Age=2592000" in response.headers["Set-Cookie"]  # renewed for another 30 days
    assert datetime.now() - in_database(server_app, SignIn)[0].last_used_at < timedelta(minutes=1)


# ---------- People who can sign in ----------


def test_settings_lists_the_people_and_their_devices(server_app, with_key):
    add_person(with_key)
    signed_in_browser(server_app)
    page = with_key.get("/settings/").get_data(as_text=True)
    assert "People who can sign in" in page and f'href="{ADDRESS}"' in page
    assert "Anna" in page and "iPhone (Safari), last used today" in page


def test_new_passwords_must_be_long_and_typed_twice(server_app, with_key):
    short = add_person(with_key, password="anna")
    assert short.status_code == 400 and "at least 8 characters" in short.get_data(as_text=True)
    assert add_person(with_key, password="cindy308").status_code == 200
    different = add_person(with_key, name="Larry", again="tofu ledger on oak street")
    assert different.status_code == 400 and "aren&#39;t the same" in different.get_data(as_text=True)
    assert [account.name for account in in_database(server_app, Account)] == ["Anna"]


def test_two_people_cant_have_the_same_name(server_app, with_key):
    add_person(with_key)
    response = add_person(with_key, name="ANNA")
    assert response.status_code == 400 and "already called “Anna”" in response.get_data(as_text=True)


def test_a_new_password_signs_them_out_everywhere(server_app, with_key):
    add_person(with_key)
    phone = signed_in_browser(server_app)
    new_password = "a different password this time"
    with_key.post("/settings/people/1/password", data={"password": new_password, "password_again": new_password})
    assert phone.get("/", base_url=ADDRESS).request.path == "/sign-in"
    assert "don&#39;t match" in sign_in(browser(server_app)).get_data(as_text=True)
    assert sign_in(browser(server_app), password=new_password).request.path == "/"


def test_changing_my_own_password_keeps_this_device_signed_in(server_app, with_key):
    add_person(with_key)
    phone = signed_in_browser(server_app)
    laptop = signed_in_browser(server_app, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140.0 Safari/537.36")
    new_password = "a different password this time"
    page = laptop.post(
        "/settings/people/1/password", data={"password": new_password, "password_again": new_password}, base_url=ADDRESS
    ).get_data(as_text=True)
    assert "You&#39;re signed out on your other devices." in page
    assert laptop.get("/", base_url=ADDRESS).request.path == "/"
    assert phone.get("/", base_url=ADDRESS).request.path == "/sign-in"
    assert [device.device for device in in_database(server_app, SignIn)] == ["Mac (Chrome)"]


def test_removing_someone_signs_them_out_but_not_yourself(server_app, with_key):
    add_person(with_key, "Anna")
    add_person(with_key, "Larry")
    anna = signed_in_browser(server_app, name="Anna")
    larry = signed_in_browser(server_app, name="Larry")

    page = larry.post("/settings/people/2/remove", base_url=ADDRESS).get_data(as_text=True)
    assert "can&#39;t remove yourself" in page
    larry.post("/settings/people/1/remove", base_url=ADDRESS)
    assert anna.get("/", base_url=ADDRESS).request.path == "/sign-in"
    assert [account.name for account in in_database(server_app, Account)] == ["Larry"]


def test_on_one_computer_nobody_signs_in(client):
    assert "People who can sign in" not in client.get("/settings/").get_data(as_text=True)
    assert client.get("/settings/people/new").status_code == 404
    assert client.get("/sign-in").request.path == "/"


def test_a_name_with_quotes_cant_break_the_remove_button(server_app, with_key):
    add_person(with_key, "O'Brien \"<b>\"")
    page = with_key.get("/settings/").get_data(as_text=True)
    assert "return confirm(\"Stop O\\u0027Brien \\\"\\u003cb\\u003e\\\" from signing in?" in page


def test_back_up_now_still_renews_the_sign_in(server_app, with_key):
    # (Making a backup opens a context of its own partway through the request.)
    add_person(with_key)
    client = signed_in_browser(server_app)
    a_while_ago = datetime.now() - timedelta(hours=2)
    with server_app.app_context():
        db_session.scalar(select(SignIn)).last_used_at = a_while_ago
        db_session.commit()
    client.post("/settings/backup-now", base_url=ADDRESS, follow_redirects=False)
    assert in_database(server_app, SignIn)[0].last_used_at > a_while_ago


# ---------- Counting wrong logins ----------


def failed_logins(with_key) -> str:
    return with_key.get("/settings/failed-logins").get_data(as_text=True)


def counts_file(server_app):
    return server_app.config["DATA_PATHS"].failed_logins


def guess_at_the_same_moment(server_app, how_many, **details) -> list[int]:
    """Many wrong logins at once, as someone guessing would send them. Returns each one's status."""

    def one_guess(_number):
        response = browser(server_app).post(
            "/sign-in", data={"password": "a wrong guess", **details}, base_url=ADDRESS, follow_redirects=False
        )
        return response.status_code

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(one_guess, range(how_many)))


def test_wrong_logins_are_counted_by_name_and_what_was_typed_isnt_kept(server_app, with_key):
    add_person(with_key)
    assert "Today: 0" in failed_logins(with_key) and "All time: 0" in failed_logins(with_key) and "Latest: none" in failed_logins(with_key)
    client = browser(server_app)
    for _ in range(2):
        sign_in(client, password="a wrong guess")
    sign_in(client, name="Somebody Else", password="another guess")
    report = failed_logins(with_key)
    assert "Today: 3" in report and "All time: 3" in report and "Latest: none" not in report
    assert "Anna: 2" in report and f"{accounts.NO_SUCH_NAME}: 1" in report
    saved = counts_file(server_app).read_text()
    for words in (report, saved):
        assert "Somebody Else" not in words and "guess" not in words


def test_the_right_password_isnt_counted_and_doesnt_clear_the_count(server_app, with_key):
    add_person(with_key)
    client = browser(server_app)
    sign_in(client, password="a wrong guess")
    assert in_database(server_app, Account)[0].wrong_tries == 1
    assert sign_in(client).request.path == "/"
    assert "All time: 1" in failed_logins(with_key)
    assert in_database(server_app, Account)[0].wrong_tries == 0


def test_tries_on_a_locked_name_are_counted_and_it_says_who_is_locked(server_app, with_key):
    add_person(with_key)
    client = browser(server_app)
    assert "Locked names: none" in failed_logins(with_key)
    for _ in range(11):
        sign_in(client, password="a wrong guess")
    report = failed_logins(with_key)
    assert "All time: 11" in report and "Locked names: Anna" in report


def test_the_counts_add_up_over_time(server_app, with_key):
    today = datetime.now().date()
    days = {(today - timedelta(days=ago)).isoformat(): {"Anna": count} for ago, count in ((0, 1), (6, 2), (7, 4), (29, 8), (30, 16))}
    counts_file(server_app).write_text(json.dumps({"days": days, "latest": "2026-11-04 09:30"}))
    report = failed_logins(with_key)
    assert "Today: 1" in report and "Last 7 days: 3" in report and "Last 30 days: 15" in report and "All time: 31" in report
    assert f"{today}  1  (Anna: 1)" in report and "Latest: 2026-11-04 09:30" in report


def test_a_wrong_name_doesnt_write_to_the_database(server_app, with_key):
    database = server_app.config["DATA_PATHS"].database
    before = database.stat().st_mtime_ns
    guess_at_the_same_moment(server_app, 3, name="Nobody")
    assert "All time: 3" in failed_logins(with_key) and database.stat().st_mtime_ns == before


def test_a_damaged_count_starts_over_and_doesnt_stop_a_login_being_refused(server_app, with_key):
    counts_file(server_app).write_text("not what it should be")
    assert "All time: 0" in failed_logins(with_key)
    assert guess_at_the_same_moment(server_app, 1, name="Nobody") == [400]
    assert "All time: 1" in failed_logins(with_key)


def test_someone_not_signed_in_cant_read_the_count(server_app, with_key):
    add_person(with_key)
    page = browser(server_app).get("/settings/failed-logins", base_url=ADDRESS).get_data(as_text=True)
    assert "Wrong logins" not in page and "Sign in" in page


def test_wrong_logins_at_the_same_moment_are_all_counted(server_app, with_key):
    assert guess_at_the_same_moment(server_app, 24, name="Nobody") == [400] * 24
    assert "All time: 24" in failed_logins(with_key)


def test_wrong_passwords_at_the_same_moment_still_lock_the_name(server_app, with_key):
    add_person(with_key)
    assert guess_at_the_same_moment(server_app, 24, name="Anna") == [400] * 24
    report = failed_logins(with_key)
    assert "All time: 24" in report and "Locked names: Anna" in report
