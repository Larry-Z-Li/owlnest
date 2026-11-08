"""Her bank's activity through Plaid: connecting, staying on the free plan, fetching, and the "From the bank" page.

These tests never reach Plaid: they pass a stand-in, or replace the one function that sends.
"""

import io
import json
import stat
import urllib.error
import zipfile
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from conftest import BrowserLikeClient
from helpers import add_lease, add_property
from test_sign_in import ADDRESS, add_person, signed_in_browser
from test_transactions_and_maintenance import add_transaction

from property_manager import bank_link, create_app, rent_ledger, startup
from property_manager.config import KEY_HEADER
from property_manager.database import db_session
from property_manager.models import BankCheck, BankEntry, Lease, Transaction

KEY = "a-test-key-that-is-long-enough-to-be-accepted"
ACCESS_TOKEN = "access-secret-token"
PLAID_SECRET = "plaid-secret-value"
NOW = datetime(2026, 11, 20, 12, 0)


def money_in(item_id="T1", amount=-3500.0, day="2026-11-03", description="Zelle payment from SAM RIVERA", **extra):
    """A transaction as Plaid sends it. It counts money in as negative."""
    return {
        "transaction_id": item_id,
        "account_id": "ACC1",
        "amount": amount,
        "date": day,
        "authorized_date": None,
        "pending": False,
        "name": "ZELLE",
        "original_description": description,
        "merchant_name": None,
        **extra,
    }


def answer_with(*added, cursor="bookmark-1", **extra):
    return {
        "added": list(added),
        "modified": [],
        "removed": [],
        "accounts": [{"account_id": "ACC1", "name": "Checking", "official_name": "Adv Plus Banking", "mask": "1234"}],
        "next_cursor": cursor,
        "transactions_update_status": bank_link.HISTORY_READY,
        **extra,
    }


class FakePlaid:
    """Stands in for asking Plaid: writes down what it's asked, and answers (or fails)."""

    def __init__(self, answer=None, problem=None):
        self.answer = answer if answer is not None else answer_with()
        self.problem = problem
        self.asked = []

    def __call__(self, access_token, bookmark):
        self.asked.append((access_token, bookmark))
        if self.problem:
            raise self.problem
        return self.answer


class FakeLine:
    """Stands in for the one function that sends: answers each of Plaid's addresses, and keeps what was sent."""

    def __init__(self, monkeypatch, **answers):
        self.answers = {"/" + name.replace("__", "/"): answer for name, answer in answers.items()}
        self.sent = []
        monkeypatch.setattr(bank_link, "_send", self)

    def __call__(self, request):
        path = request.full_url.split(".plaid.com", 1)[1]
        self.sent.append((path, json.loads(request.data), request))
        answer = self.answers[path]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer).encode()

    def paths(self):
        return [path for path, _fields, _request in self.sent]

    def fields(self, path):
        return next(fields for sent_to, fields, _request in self.sent if sent_to == path)


def plaid_error(code, status=400):
    body = io.BytesIO(json.dumps({"error_code": code, "error_message": "details"}).encode())
    return urllib.error.HTTPError("https://production.plaid.com/x", status, "Bad", {}, body)


@pytest.fixture
def server(tmp_path):
    """The server, without Plaid's keys."""
    app = create_app(tmp_path / "server-data", server_key=KEY)
    app.config["TESTING"] = True
    app.test_client_class = BrowserLikeClient
    return app


@pytest.fixture
def keyed(server, monkeypatch):
    """The server, with Plaid's keys, not connected yet."""
    monkeypatch.setenv(bank_link.CLIENT_ID_VARIABLE, "plaid-client-id")
    monkeypatch.setenv(bank_link.SECRET_VARIABLE, PLAID_SECRET)
    return server


@pytest.fixture
def connected(keyed):
    """The server, connected to the bank."""
    bank_link._write_link(keyed.config["DATA_PATHS"], {"access_token": ACCESS_TOKEN, "bank": "Bank of America"})
    return keyed


def landlord_of(app):
    client = app.test_client()
    client.environ_base["HTTP_" + KEY_HEADER.upper().replace("-", "_")] = KEY
    return client


def saved_link(app) -> dict:
    return json.loads(app.config["DATA_PATHS"].bank_link.read_text())


def saved_connection(app, number=0) -> dict:
    return bank_link._read_link(app.config["DATA_PATHS"])["connections"][number]


def checks(app) -> list[BankCheck]:
    with app.app_context():
        found = list(db_session.query(BankCheck).order_by(BankCheck.id))
        db_session.expunge_all()
        return found


def entries(app) -> list[tuple]:
    with app.app_context():
        return [
            (one.bank_id, one.day, one.amount_cents, one.description, one.status)
            for one in db_session.query(BankEntry).order_by(BankEntry.id)
        ]


def add_entry(app, amount="3500.00", day=date(2026, 11, 3), description="Zelle payment from SAM RIVERA", bank_id="ACT/1") -> int:
    with app.app_context():
        entry = BankEntry(
            bank_id=bank_id,
            account_name="Checking",
            day=day,
            amount_cents=round(float(amount.replace(",", "")) * 100),
            description=description,
        )
        db_session.add(entry)
        db_session.commit()
        return entry.id


def enter(client, entry_id, day="2026-11-03", direction="in", amount="3,500.00", **fields):
    """Save the transaction form that "Enter into Transactions" opened."""
    data = {"bank_entry": str(entry_id), "transaction_date": day, "direction": direction, "amount": amount, "category": "Rental Income", **fields}
    return client.post("/transactions/new", data=data, content_type="multipart/form-data", follow_redirects=True)


# ---------- Who may talk to Plaid ----------


def test_only_the_server_uses_real_accounts(app, monkeypatch):
    monkeypatch.setenv(bank_link.CLIENT_ID_VARIABLE, "id")
    monkeypatch.setenv(bank_link.SECRET_VARIABLE, "secret")
    assert not bank_link.is_set_up(app)
    # Anything but "sandbox" means the real thing.
    monkeypatch.setenv(bank_link.ENVIRONMENT_VARIABLE, "something else")
    assert not bank_link.is_set_up(app)


def test_a_preview_can_use_the_made_up_bank(app, monkeypatch):
    monkeypatch.setenv(bank_link.CLIENT_ID_VARIABLE, "id")
    monkeypatch.setenv(bank_link.SECRET_VARIABLE, "secret")
    monkeypatch.setenv(bank_link.ENVIRONMENT_VARIABLE, "sandbox")
    line = FakeLine(monkeypatch, item__get={})
    assert bank_link.is_set_up(app)
    bank_link._post("/item/get", {})
    assert line.sent[0][2].full_url == "https://sandbox.plaid.com/item/get"


def test_a_server_without_keys_does_nothing(server):
    plaid = FakePlaid()
    assert not bank_link.is_set_up(server)
    assert bank_link.check_if_due(server, NOW, plaid) == [] and plaid.asked == []


def test_a_server_with_keys_but_no_connection_doesnt_check(keyed):
    plaid = FakePlaid()
    assert bank_link.is_set_up(keyed) and not bank_link.is_connected(keyed)
    assert bank_link.check_if_due(keyed, NOW, plaid) == [] and plaid.asked == []


# ---------- Connecting ----------


LINK_CREATED = {"link_token": "link-1", "hosted_link_url": "https://hosted.plaid.com/link/abc"}


def finished_session(*items):
    return {"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "results": {"item_add_results": list(items)}}]}


def added_item(public_token="public-1"):
    return {"public_token": public_token, "institution": {"name": "Bank of America", "institution_id": "ins_127989"}}


def test_connecting_opens_plaids_page(keyed, monkeypatch):
    line = FakeLine(monkeypatch, link__token__create=LINK_CREATED)
    response = landlord_of(keyed).post("/bank/connect", follow_redirects=False)
    assert response.status_code == 302 and response.headers["Location"] == LINK_CREATED["hosted_link_url"]
    fields = line.fields("/link/token/create")
    assert fields["products"] == ["transactions"] and fields["country_codes"] == ["US"]
    assert fields["transactions"]["days_requested"] == 730  # Plaid's most, so a longer history later needs no reconnecting
    assert fields["hosted_link"]["completion_redirect_uri"].endswith("/bank/connected")
    assert "access_token" not in fields
    assert saved_link(keyed)["pending_link_token"] == "link-1"


def test_finishing_keeps_the_key_private(keyed, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get=finished_session(added_item()),
        item__public_token__exchange={"access_token": ACCESS_TOKEN, "item_id": "item-1"},
    )
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    assert bank_link.finish_connecting(keyed) == "connected"
    assert line.fields("/item/public_token/exchange")["public_token"] == "public-1"
    link = saved_link(keyed)
    assert "pending_link_token" not in link
    assert saved_connection(keyed)["access_token"] == ACCESS_TOKEN and saved_connection(keyed)["bank"] == "Bank of America"
    assert bank_link.is_connected(keyed) and bank_link.bank_name(keyed) == "Bank of America"

    # Only the server can read it, and it's in no backup.
    link_file = keyed.config["DATA_PATHS"].bank_link
    assert stat.S_IMODE(link_file.stat().st_mode) == 0o600
    with keyed.app_context():
        result = startup.backup_now(keyed)
    backup = zipfile.ZipFile(result.zip_path)
    assert not [name for name in backup.namelist() if "bank-link" in name]
    assert ACCESS_TOKEN not in backup.read("app.db").decode("latin-1")


def test_not_finished_yet_stays_waiting(keyed, monkeypatch):
    FakeLine(monkeypatch, link__token__create=LINK_CREATED, link__token__get={"link_sessions": []})
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    assert bank_link.finish_connecting(keyed) == ""
    assert saved_link(keyed)["pending_link_token"] == "link-1" and not bank_link.is_connected(keyed)


def test_leaving_plaids_page_connects_nothing(keyed, monkeypatch):
    left = {"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "exit": {"status": "requires_credentials"}, "results": None}]}
    line = FakeLine(monkeypatch, link__token__create=LINK_CREATED, link__token__get=left)
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    assert bank_link.finish_connecting(keyed) == ""
    assert "/item/public_token/exchange" not in line.paths() and not bank_link.is_connected(keyed)


def test_a_waiting_token_too_old_to_ask_about_is_forgotten(keyed, monkeypatch):
    FakeLine(monkeypatch, link__token__create=LINK_CREATED, link__token__get=plaid_error("INVALID_LINK_TOKEN"))
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    assert bank_link.finish_connecting(keyed) == ""
    assert "pending_link_token" not in saved_link(keyed)


def test_nothing_waiting_asks_nothing(keyed, monkeypatch):
    line = FakeLine(monkeypatch)
    assert bank_link.finish_connecting(keyed) == "" and line.sent == []


# (Each of these protects the free plan: it allows 10 connections, ever.)


def test_with_a_connection_it_repairs_that_one(connected, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get={"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "results": None}]},
    )
    bank_link.start_connecting(connected, "https://example.com/bank/connected")
    fields = line.fields("/link/token/create")
    assert fields["access_token"] == ACCESS_TOKEN and "products" not in fields
    assert bank_link.finish_connecting(connected) == "repaired"
    # The same key: no new connection, so nothing to trade.
    assert "/item/public_token/exchange" not in line.paths()
    assert saved_connection(connected)["access_token"] == ACCESS_TOKEN


def test_adding_an_account_lets_her_choose_on_the_same_connection(connected, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get={"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "results": None}]},
        transactions__sync=answer_with(),
        item__get={"item": {}},
    )
    client = landlord_of(connected)
    response = client.post("/bank/connect", data={"add_account": "1"}, follow_redirects=False)
    assert response.headers["Location"] == LINK_CREATED["hosted_link_url"]
    fields = line.fields("/link/token/create")
    assert fields["access_token"] == ACCESS_TOKEN and fields["update"] == {"account_selection_enabled": True}
    assert "products" not in fields and fields["hosted_link"]["completion_redirect_uri"].endswith("/bank/connected?adding=1")
    # Coming back: the same key, no new connection, and back on the Accounts page.
    response = client.get("/bank/connected?adding=1", follow_redirects=False)
    assert response.headers["Location"].endswith("/bank/accounts")
    assert "/item/public_token/exchange" not in line.paths() and saved_connection(connected)["access_token"] == ACCESS_TOKEN
    assert "Your accounts were updated." in client.get("/bank/accounts").get_data(as_text=True)


def test_reconnecting_doesnt_ask_to_choose_accounts(connected, monkeypatch):
    line = FakeLine(monkeypatch, link__token__create=LINK_CREATED)
    landlord_of(connected).post("/bank/connect", follow_redirects=False)
    assert "update" not in line.fields("/link/token/create")


def test_leaving_a_repair_changes_nothing(connected, monkeypatch):
    left = {"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "exit": {"status": "x"}, "results": None}]}
    FakeLine(monkeypatch, link__token__create=LINK_CREATED, link__token__get=left)
    bank_link.start_connecting(connected, "https://example.com/bank/connected")
    assert bank_link.finish_connecting(connected) == ""


def test_only_one_connection_is_ever_kept_from_a_session(keyed, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get=finished_session(added_item("public-1"), added_item("public-2")),
        item__public_token__exchange={"access_token": ACCESS_TOKEN, "item_id": "item-1"},
    )
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    bank_link.finish_connecting(keyed)
    assert line.paths().count("/item/public_token/exchange") == 1


def test_only_the_by_hand_command_removes_a_connection_and_nothing_asks_for_the_paid_plan():
    from pathlib import Path

    source = Path(bank_link.__file__).read_text()
    # Plaid's /item/remove is only in disconnect(), and only bank_by_hand.py calls that (Larry decides, by hand).
    disconnect = source.split("def disconnect(")[1].split("\ndef ")[0]
    assert source.count('"/item/remove"') == 1 and '"/item/remove"' in disconnect
    package = Path(bank_link.__file__).parent
    callers = sorted(path.name for path in package.rglob("*.py") if "disconnect(" in path.read_text() and path.name != "bank_link.py")
    assert callers == ["bank_by_hand.py"]
    assert "/link/token/create" in source


def test_coming_back_from_plaid_connects_and_checks(keyed, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get=finished_session(added_item()),
        item__public_token__exchange={"access_token": ACCESS_TOKEN, "item_id": "item-1"},
        transactions__sync=answer_with(money_in()),
        item__get={"item": {}},
    )
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    page = landlord_of(keyed).get("/bank/connected").get_data(as_text=True)
    assert "Bank of America is connected." in page and "from SAM RIVERA" in page
    assert "/transactions/sync" in line.paths()


def test_the_page_finishes_if_she_didnt_come_back(keyed, monkeypatch):
    FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get=finished_session(added_item()),
        item__public_token__exchange={"access_token": ACCESS_TOKEN, "item_id": "item-1"},
        transactions__sync=answer_with(),
        item__get={"item": {}},
    )
    bank_link.start_connecting(keyed, "https://example.com/bank/connected")
    assert "is connected." in landlord_of(keyed).get("/bank/").get_data(as_text=True)


def test_a_problem_connecting_is_shown(keyed, monkeypatch):
    FakeLine(monkeypatch, link__token__create=plaid_error("INVALID_API_KEYS"))
    page = landlord_of(keyed).post("/bank/connect").get_data(as_text=True)
    assert "didn&#39;t accept the app&#39;s keys" in page or "didn't accept the app's keys" in page


def test_connecting_needs_keys(server):
    assert landlord_of(server).post("/bank/connect").status_code == 404
    assert landlord_of(server).get("/bank/connected").status_code == 404


# ---------- Asking Plaid ----------


def test_how_it_asks(keyed, monkeypatch):
    line = FakeLine(monkeypatch, transactions__sync=answer_with(cursor="next"), item__get={"item": {"consent_expiration_time": "2027-11-03T10:00:00Z"}})
    answer = bank_link.ask_plaid(ACCESS_TOKEN, "bookmark-0")
    path, fields, request = line.sent[0]
    assert path == "/transactions/sync" and request.full_url.startswith("https://production.plaid.com/")
    assert fields["cursor"] == "bookmark-0" and fields["options"]["include_original_description"] is True
    # The keys go in headers, not in anything that might be logged with the request's contents.
    assert PLAID_SECRET not in request.data.decode()
    assert request.get_header("Plaid-secret") == PLAID_SECRET and request.get_header("Plaid-client-id") == "plaid-client-id"
    assert request.get_header("Plaid-version") == bank_link.PLAID_VERSION and request.get_header("User-agent") == bank_link.USER_AGENT
    assert answer["consent_expires"] == "2027-11-03" and answer["next_cursor"] == "next"


def test_the_first_ask_has_no_bookmark(keyed, monkeypatch):
    line = FakeLine(monkeypatch, transactions__sync=answer_with(), item__get={"item": {}})
    bank_link.ask_plaid(ACCESS_TOKEN, "")
    assert "cursor" not in line.fields("/transactions/sync")


def test_it_reads_every_page(keyed, monkeypatch):
    pages = [
        answer_with(money_in("T1"), cursor="page-2", has_more=True),
        answer_with(money_in("T2"), cursor="page-3", has_more=True),
        answer_with(money_in("T3"), cursor="done", has_more=False),
    ]
    line = FakeLine(monkeypatch, transactions__sync=pages, item__get={"item": {}})
    answer = bank_link.ask_plaid(ACCESS_TOKEN, "")
    assert [item["transaction_id"] for item in answer["added"]] == ["T1", "T2", "T3"] and answer["next_cursor"] == "done"
    assert [fields.get("cursor") for path, fields, _request in line.sent if path == "/transactions/sync"] == [None, "page-2", "page-3"]


def test_if_the_activity_changes_while_reading_it_starts_over(keyed, monkeypatch):
    pages = [
        answer_with(money_in("T1"), cursor="page-2", has_more=True),
        plaid_error("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"),
        answer_with(money_in("T1"), cursor="done"),
    ]
    line = FakeLine(monkeypatch, transactions__sync=pages, item__get={"item": {}})
    answer = bank_link.ask_plaid(ACCESS_TOKEN, "start")
    # From the first bookmark, and nothing counted twice.
    assert [fields["cursor"] for path, fields, _request in line.sent if path == "/transactions/sync"] == ["start", "page-2", "start"]
    assert len(answer["added"]) == 1


def test_it_gives_up_if_the_activity_keeps_changing(keyed, monkeypatch):
    pages = [plaid_error("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION") for _try in range(bank_link.TRIES_WHILE_READING)]
    FakeLine(monkeypatch, transactions__sync=pages)
    with pytest.raises(bank_link.BankProblem, match="kept changing"):
        bank_link.ask_plaid(ACCESS_TOKEN, "")


def test_it_wont_follow_a_redirect():
    # (Following one could send the keys somewhere else.)
    assert bank_link._NoRedirects().redirect_request() is None


@pytest.mark.parametrize(
    "failure, words",
    [
        (plaid_error("ITEM_LOGIN_REQUIRED"), "sign in again"),
        (plaid_error("PENDING_DISCONNECT"), "sign in again"),
        (plaid_error("INVALID_API_KEYS"), "didn't accept the app's keys"),
        (plaid_error("ADDITION_LIMIT"), "no connections left"),
        (plaid_error("INTERNAL_SERVER_ERROR", 500), "error (INTERNAL_SERVER_ERROR)"),
        (urllib.error.HTTPError("https://x", 502, "Bad", {}, io.BytesIO(b"<html>")), "error (502)"),
        (urllib.error.URLError(f"cannot reach, token {ACCESS_TOKEN}"), "couldn't be reached"),
        (TimeoutError(), "couldn't be reached"),
    ],
)
def test_problems_are_in_plain_words_and_show_no_keys(keyed, monkeypatch, failure, words):
    FakeLine(monkeypatch, transactions__sync=failure)
    with pytest.raises(bank_link.BankProblem) as problem:
        bank_link.ask_plaid(ACCESS_TOKEN, "")
    assert words in str(problem.value)
    assert ACCESS_TOKEN not in str(problem.value) and PLAID_SECRET not in str(problem.value)


def test_an_answer_it_cant_read(keyed, monkeypatch):
    monkeypatch.setattr(bank_link, "_send", lambda request: b"<html>Sorry</html>")
    with pytest.raises(bank_link.BankProblem, match="couldn't be read"):
        bank_link.ask_plaid(ACCESS_TOKEN, "")


# ---------- How often ----------


def test_it_checks_once_a_day(connected):
    plaid = FakePlaid()
    bank_link.check_if_due(connected, NOW, plaid)
    bank_link.check_if_due(connected, NOW + timedelta(hours=23, minutes=59), plaid)
    assert len(plaid.asked) == 1
    bank_link.check_if_due(connected, NOW + timedelta(days=1), plaid)
    assert len(plaid.asked) == 2


@pytest.mark.parametrize("status", ["NOT_READY", "INITIAL_UPDATE_COMPLETE"])
def test_while_plaid_gathers_the_6_months_it_tries_again_in_an_hour(connected, status):
    plaid = FakePlaid({"added": [], "next_cursor": "", "transactions_update_status": status})
    lines = bank_link.check_if_due(connected, NOW, plaid)
    assert "still gathering" in lines[0]
    bank_link.check_if_due(connected, NOW + timedelta(minutes=59), plaid)
    assert len(plaid.asked) == 1
    bank_link.check_if_due(connected, NOW + timedelta(hours=1), plaid)
    assert len(plaid.asked) == 2
    # Once it's all there, it's once a day.
    plaid.answer = answer_with()
    bank_link.check_if_due(connected, NOW + timedelta(hours=2), plaid)
    assert len(plaid.asked) == 3
    bank_link.check_if_due(connected, NOW + timedelta(hours=3), plaid)
    assert len(plaid.asked) == 3


def test_it_tries_again_a_day_after_a_problem(connected):
    plaid = FakePlaid(problem=bank_link.BankProblem("Plaid couldn't be reached."))
    bank_link.check_if_due(connected, NOW, plaid)
    assert checks(connected)[0].problem == "Plaid couldn't be reached."
    bank_link.check_if_due(connected, NOW + timedelta(hours=23), plaid)
    assert len(plaid.asked) == 1
    bank_link.check_if_due(connected, NOW + timedelta(days=1), plaid)
    assert len(plaid.asked) == 2


def test_it_never_asks_more_than_24_times_in_a_day(connected):
    plaid = FakePlaid()
    for minutes in range(26):
        lines = bank_link.check_now(connected, NOW + timedelta(minutes=minutes), plaid)
    assert len(plaid.asked) == bank_link.MOST_CHECKS_A_DAY and "not fetched" in lines[0]
    bank_link.check_now(connected, NOW + timedelta(hours=25), plaid)
    assert len(plaid.asked) == bank_link.MOST_CHECKS_A_DAY + 1


def test_a_failed_check_counts_toward_the_limit(connected):
    plaid = FakePlaid(problem=bank_link.BankProblem("No."))
    for minutes in range(26):
        bank_link.check_now(connected, NOW + timedelta(minutes=minutes), plaid)
    assert len(plaid.asked) == bank_link.MOST_CHECKS_A_DAY


def test_it_picks_up_from_its_bookmark(connected):
    plaid = FakePlaid(answer_with(cursor="bookmark-1"))
    bank_link.check_now(connected, NOW, plaid)
    assert plaid.asked[-1] == (ACCESS_TOKEN, "")
    plaid.answer = answer_with(cursor="bookmark-2")
    bank_link.check_now(connected, NOW + timedelta(days=1), plaid)
    assert plaid.asked[-1][1] == "bookmark-1"
    # A failed check doesn't move it.
    plaid.problem = bank_link.BankProblem("No.")
    bank_link.check_now(connected, NOW + timedelta(days=2), plaid)
    plaid.problem = None
    bank_link.check_now(connected, NOW + timedelta(days=3), plaid)
    assert plaid.asked[-1][1] == "bookmark-2"


def test_it_remembers_when_the_consent_runs_out(connected):
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(consent_expires="2027-11-03")))
    assert saved_connection(connected)["consent_expires"] == "2027-11-03"
    assert saved_connection(connected)["access_token"] == ACCESS_TOKEN


# ---------- What it keeps ----------


def test_it_keeps_posted_money_in_and_out(connected):
    answer = answer_with(
        money_in("in"),
        money_in("out", amount=50.0),
        money_in("zero", amount=0),
        money_in("waiting", pending=True),
        money_in("nonsense", amount="lots"),
        money_in("no-date", day=None),
    )
    bank_link.check_now(connected, NOW, FakePlaid(answer))
    assert [(row[0], row[2]) for row in entries(connected)] == [("in", 350000), ("out", -5000)]
    assert checks(connected)[0].new_entries == 2


def test_it_keeps_only_the_past_6_months(connected):
    first_day = NOW.date() - timedelta(days=bank_link.DAYS_OF_HISTORY)
    answer = answer_with(
        money_in("too-old", day=(first_day - timedelta(days=1)).isoformat()),
        money_in("oldest-kept", day=first_day.isoformat()),
    )
    bank_link.check_now(connected, NOW, FakePlaid(answer))
    assert [row[0] for row in entries(connected)] == ["oldest-kept"]


def test_after_the_first_pull_it_keeps_whatever_came_since_the_last_check(connected):
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(money_in("first"))))
    # (Say the connection was broken for 8 months. Plaid now sends everything since the last check.)
    answer = answer_with(money_in("right-after", day=(NOW.date() + timedelta(days=1)).isoformat()), cursor="bookmark-2")
    bank_link.check_now(connected, NOW + timedelta(days=240), FakePlaid(answer))
    assert sorted(row[0] for row in entries(connected)) == ["first", "right-after"]


def test_an_entry_is_never_saved_twice(connected):
    plaid = FakePlaid(answer_with(money_in("T1")))
    bank_link.check_now(connected, NOW, plaid)
    bank_link.check_now(connected, NOW + timedelta(days=1), plaid)
    assert len(entries(connected)) == 1 and checks(connected)[1].new_entries == 0


def test_it_keeps_the_banks_words_and_the_real_date(connected):
    item = money_in(day="2026-11-04", authorized_date="2026-11-03", description='Zelle payment from JANE DOE for "Nov rent"; Conf# x1', merchant_name="Zelle")
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(item)))
    with connected.app_context():
        saved = db_session.query(BankEntry).one()
        assert (saved.day, saved.amount_cents, saved.status) == (date(2026, 11, 3), 350000, "New")
        assert saved.description == 'Zelle payment from JANE DOE for "Nov rent"; Conf# x1'
        assert saved.payee == "Zelle" and saved.account_name == "Adv Plus Banking ···1234"
        reading = bank_link.read_words(saved)
        assert (reading.method, reading.who, reading.memo) == ("Zelle", "from JANE DOE", "Nov rent")


def test_without_the_raw_words_it_uses_what_plaid_gives(connected):
    item = money_in(description=None, name="Deposit", merchant_name="Jane's Shop")
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(item)))
    with connected.app_context():
        saved = db_session.query(BankEntry).one()
        assert (saved.description, saved.payee) == ("Deposit", "Jane's Shop")


def test_the_bank_itself_isnt_who_paid(connected):
    # (Plaid names the bank as the other side of a mobile check deposit.)
    item = money_in(description="BKOFAMERICA MOBILE 10/24 3456 DEPOSIT", counterparties=[{"name": "Bank of America", "type": "financial_institution"}])
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(item)))
    with connected.app_context():
        assert bank_link.read_words(db_session.query(BankEntry).one()).who == ""


def test_a_change_updates_an_entry_still_waiting(connected):
    plaid = FakePlaid(answer_with(money_in("T1", description="OLD WORDS")))
    bank_link.check_now(connected, NOW, plaid)
    plaid.answer = answer_with(modified=[money_in("T1", description="NEW WORDS")], cursor="bookmark-2")
    bank_link.check_now(connected, NOW + timedelta(days=1), plaid)
    assert entries(connected)[0][3] == "NEW WORDS"
    assert checks(connected)[1].new_entries == 0


def test_a_change_leaves_an_entered_one_alone(connected):
    plaid = FakePlaid(answer_with(money_in("T1", description="OLD WORDS")))
    bank_link.check_now(connected, NOW, plaid)
    with connected.app_context():
        db_session.query(BankEntry).one().status = "Entered"
        db_session.commit()
    plaid.answer = answer_with(modified=[money_in("T1", description="NEW WORDS")], removed=[{"transaction_id": "T1"}], cursor="bookmark-2")
    bank_link.check_now(connected, NOW + timedelta(days=1), plaid)
    assert entries(connected)[0][3] == "OLD WORDS"


def test_a_removal_drops_an_entry_still_waiting(connected):
    plaid = FakePlaid(answer_with(money_in("T1")))
    bank_link.check_now(connected, NOW, plaid)
    plaid.answer = answer_with(removed=[{"transaction_id": "T1", "account_id": "ACC1"}], cursor="bookmark-2")
    bank_link.check_now(connected, NOW + timedelta(days=1), plaid)
    assert entries(connected) == []


def test_nothing_goes_in_her_books_by_itself(connected):
    bank_link.check_now(connected, NOW, FakePlaid(answer_with(money_in())))
    with connected.app_context():
        assert db_session.query(Transaction).count() == 0


# ---------- Reading the bank's words ----------


def reading_of(description="", payee="", memo="", amount=350000):
    reading = bank_link.read_words(BankEntry(description=description, payee=payee, memo=memo, amount_cents=amount))
    return (reading.method, reading.who, reading.memo)


@pytest.mark.parametrize(
    "words, expected",
    [
        # What Bank of America seems to send.
        ({"description": 'Zelle payment from JANE DOE for "Nov rent"; Conf# abc123'}, ("Zelle", "from JANE DOE", "Nov rent")),
        ({"description": "Zelle payment from JANE DOE Conf# abc123"}, ("Zelle", "from JANE DOE", "")),
        ({"description": "Zelle Transfer Conf# abc123; JANE DOE"}, ("Zelle", "from JANE DOE", "")),
        ({"description": "Zelle payment from JANE DOE"}, ("Zelle", "from JANE DOE", "")),
        # Curly quotes, and the memo in its own field.
        ({"description": "Zelle payment from JANE DOE for “RENT149”; Conf# x"}, ("Zelle", "from JANE DOE", "RENT149")),
        ({"description": "Zelle payment", "payee": "Jane Doe", "memo": "Nov rent"}, ("Zelle", "from Jane Doe", "Nov rent")),
        # Just "Zelle" as the payee isn't who paid, and a memo that repeats the description isn't its own memo.
        ({"description": "ZELLE", "payee": "zelle", "memo": "zelle"}, ("Zelle", "", "ZELLE")),
        # Other ways to pay: the bank's words are the memo.
        ({"description": "VENMO DES:CASHOUT ID:123"}, ("Venmo", "", "VENMO DES:CASHOUT ID:123")),
        ({"description": "BKOFAMERICA MOBILE 11/01 12345 DEPOSIT *MOBILE CA"}, ("Check", "", "BKOFAMERICA MOBILE 11/01 12345 DEPOSIT *MOBILE CA")),
        ({"description": "Check 1234"}, ("Check", "", "Check 1234")),
        ({"description": "WIRE TYPE:WIRE IN"}, ("Wire", "", "WIRE TYPE:WIRE IN")),
        # "from" here isn't a person.
        ({"description": "Online Banking transfer from CHK 1234 Confirmation# 99"}, ("Transfer", "", "Online Banking transfer from CHK 1234 Confirmation# 99")),
        # When it can't tell the method, the words are still kept.
        ({"description": "INTEREST EARNED"}, ("", "", "INTEREST EARNED")),
        ({}, ("", "", "")),
        # Money out.
        ({"description": 'Zelle payment to BOB PLUMBER for "repair"; Conf# zzz', "amount": -25000}, ("Zelle", "to BOB PLUMBER", "repair")),
        ({"description": "CHECKCARD 1102 HOME DEPOT #1234", "payee": "The Home Depot", "amount": -18696}, ("Card", "to The Home Depot", "CHECKCARD 1102 HOME DEPOT #1234")),
        ({"description": "CHECKCARD 1102 HOME DEPOT #1234", "payee": "Home Depot", "amount": -18696}, ("Card", "", "CHECKCARD 1102 HOME DEPOT #1234")),
        ({"description": "Check 1042", "amount": -120000}, ("Check", "", "Check 1042")),
    ],
)
def test_reading_the_banks_words(words, expected):
    assert reading_of(**words) == expected


@pytest.mark.parametrize(
    "description, amount, details",
    [
        ('Zelle payment from JANE DOE for "Nov rent"; Conf# abc123', 350000, "Zelle from JANE DOE, memo: Nov rent"),
        ("Zelle payment from JANE DOE; Conf# abc123", 350000, "Zelle from JANE DOE"),
        ('Zelle payment to BOB for "repair"', -25000, "Zelle to BOB, memo: repair"),
        ("Check 1042", -120000, "Check, memo: Check 1042"),
        ("INTEREST EARNED", 12, "memo: INTEREST EARNED"),
    ],
)
def test_what_a_transaction_gets_in_its_details(description, amount, details):
    assert bank_link.details_for(BankEntry(description=description, payee="", memo="", amount_cents=amount)) == details


# ---------- The page ----------


def test_the_page_before_connecting(client):
    page = client.get("/bank/").get_data(as_text=True)
    assert "The bank isn't connected." in page and "bank-row" not in page


def test_the_page_lists_money_in_and_out_by_month(client, app):
    add_entry(app, description='Zelle payment from JANE DOE for "Nov rent"; Conf# abc123')
    add_entry(app, amount="-186.96", day=date(2026, 10, 30), description="CHECKCARD 1102 HOME DEPOT", bank_id="ACT/2")
    page = client.get("/bank/").get_data(as_text=True)
    assert "+$3,500.00" in page and "−$186.96" in page
    assert "November 2026" in page and "October 2026" in page
    assert "<strong>Zelle</strong>" in page and "from JANE DOE, memo: Nov rent" in page
    assert "<strong>Card</strong>" in page and "memo: CHECKCARD 1102 HOME DEPOT" in page
    # The bank's own words stay there on hover.
    assert "Conf# abc123" in page


def test_without_a_method_it_says_which_way_the_money_went(client, app):
    add_entry(app, description="SOME DEPOSIT")
    add_entry(app, amount="-5.00", description="SOME FEE", bank_id="ACT/2")
    page = client.get("/bank/").get_data(as_text=True)
    assert "<strong>Money in</strong>" in page and "<strong>Money out</strong>" in page


def test_entries_cant_be_edited_on_the_page(client, app):
    add_entry(app)
    main = client.get("/bank/").get_data(as_text=True).split("<main")[1].split("</main>")[0]
    # (The menus above the list only choose which entries show.)
    entries = main[main.index('<details class="month"'):]
    assert "<select" not in entries and "<input" not in entries and "<textarea" not in entries and "<form" not in entries
    assert "Enter into Transactions</a>" in entries


def test_entering_fills_in_what_the_bank_says_and_leaves_the_rest_for_her(client, app):
    add_property(client, "Elm St")
    entry = add_entry(app, amount="-186.96", day=date(2026, 10, 30), description="CHECKCARD 1102 HOME DEPOT")
    page = client.get(f"/transactions/new?bank_entry={entry}").get_data(as_text=True)
    assert 'value="2026-10-30"' in page and 'value="186.96"' in page
    assert 'name="direction" value="out" checked' in page
    assert ">Card, memo: CHECKCARD 1102 HOME DEPOT</textarea>" in page
    assert " selected" not in page.split("<form", 1)[1].split("</form>")[0]
    assert f'name="bank_entry" value="{entry}"' in page
    # Opening the form moves nothing.
    assert entries(app)[0][4] == "New"


def test_saving_puts_it_in_transactions_and_marks_it_on_the_page(client, app):
    elm = add_property(client)
    entry = add_entry(app)
    response = enter(client, entry, property_id=str(elm), details="Zelle from SAM RIVERA")
    page = response.get_data(as_text=True)
    assert response.request.path == "/bank/" and "marked BOFA" in page
    with app.app_context():
        made = db_session.query(Transaction).one()
        assert (made.transaction_date, made.amount_cents, made.category, made.property_id) == (date(2026, 11, 3), 350000, "Rental Income", elm)
        kept = db_session.get(BankEntry, entry)
        assert (kept.status, kept.transaction_id) == ("Entered", made.id)
    # It's still listed, linking to its transaction, and can't be entered again.
    assert "In Transactions</a>" in page and f'href="/transactions/{made.id}"' in page
    assert "bank-row-entered" in page and f"bank_entry={entry}" not in page
    assert '<span class="tag tag-bofa">BOFA</span>' in client.get("/transactions/").get_data(as_text=True)


def test_the_button_counts_only_whats_not_in_transactions(client, app):
    enter(client, add_entry(app))
    add_entry(app, amount="-20.00", bank_id="ACT/2")
    assert '<b class="button-count">1</b>' in client.get("/transactions/").get_data(as_text=True)


def test_it_still_needs_a_purpose(client, app):
    entry = add_entry(app)
    response = enter(client, entry, category="")
    assert response.status_code == 400 and "choose what it was for" in response.get_data(as_text=True)
    assert f'name="bank_entry" value="{entry}"' in response.get_data(as_text=True)
    assert entries(app)[0][4] == "New"


def test_a_second_click_adds_nothing(client, app):
    entry = add_entry(app)
    enter(client, entry)
    response = enter(client, entry)
    assert "already entered" in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.query(Transaction).count() == 1


def test_an_entered_one_cant_be_opened_again(client, app):
    entry = add_entry(app)
    enter(client, entry)
    response = client.get(f"/transactions/new?bank_entry={entry}", follow_redirects=True)
    assert response.request.path == "/bank/" and "already entered" in response.get_data(as_text=True)


def test_a_transaction_she_typed_in_has_no_bofa_tag(client, app):
    add_transaction(client)
    assert "tag-bofa" not in client.get("/transactions/").get_data(as_text=True)


def test_the_check_tag_goes_by_her_note_not_the_banks_words(client):
    add_transaction(client, details="memo: CHECKCARD 1102 HOME DEPOT")
    add_transaction(client, details="Check, memo: CHECK 1042")
    assert "tag-check" not in client.get("/transactions/").get_data(as_text=True)
    add_transaction(client, details="CHECK: partial rent?")
    assert "tag-check" in client.get("/transactions/").get_data(as_text=True)


def test_one_from_the_bank_cant_be_archived(client, app):
    enter(client, add_entry(app))
    page = client.get("/transactions/1").get_data(as_text=True)
    assert "Return to From the bank</button>" in page and "Archive transaction" not in page
    response = client.post("/transactions/1/archive", follow_redirects=True)
    assert "can&#39;t be archived" in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.get(Transaction, 1).archived_at is None


def test_one_she_typed_in_is_still_archived_and_cant_skip_the_archive(client):
    add_transaction(client)
    page = client.get("/transactions/1").get_data(as_text=True)
    assert "Archive transaction" in page and "Return to From the bank" not in page
    assert client.post("/transactions/1/delete").status_code == 404
    assert "was moved to the Archive" in client.post("/transactions/1/archive", follow_redirects=True).get_data(as_text=True)


def test_deleting_it_permanently_lets_the_entry_be_entered_again(client, app):
    entry = add_entry(app)
    enter(client, entry)
    response = client.post("/transactions/1/delete", follow_redirects=True)
    assert "The transaction was returned to From the bank. You can enter it again there." in response.get_data(as_text=True)
    with app.app_context():
        assert db_session.query(Transaction).count() == 0
        kept = db_session.get(BankEntry, entry)
        assert (kept.status, kept.transaction_id) == ("New", None)
    page = client.get("/bank/").get_data(as_text=True)
    assert f"bank_entry={entry}" in page and "In Transactions</a>" not in page
    # The bank sending it again doesn't make a second one.
    with app.app_context():
        assert bank_link.save_entries(answer_with(money_in("ACT/1")), date(2026, 1, 1)) == 0
        db_session.commit()
    assert len(entries(app)) == 1
    enter(client, entry)
    with app.app_context():
        assert db_session.query(Transaction).count() == 1
        assert db_session.get(BankEntry, entry).status == "Entered"


def recently_due(client, app, rent="3,500"):
    due = date.today() - timedelta(days=7)
    home = add_property(client)
    lease = add_lease(client, home, tenants="Sam Rivera", start=due.isoformat(), end="", rent=rent)
    with app.app_context():
        db_session.get(Lease, lease).rent_checked_from = due
        db_session.commit()
    return home, lease, due


def test_entering_the_rent_clears_the_reminder_by_the_banks_date(client, app):
    home, lease, due = recently_due(client, app)
    assert "rent for Elm St" in client.get("/").get_data(as_text=True)
    entry = add_entry(app, day=due)
    enter(client, entry, day=due.isoformat(), property_id=str(home))
    assert "rent for Elm St" not in client.get("/").get_data(as_text=True)
    # Paid on its due date, so there's no late fee, though it was entered a week later.
    with app.app_context():
        months, _paid_ahead = rent_ledger.work_out(db_session.get(Lease, lease), date.today())
        assert not any(month.fee_cents for month in months)


def test_money_she_already_typed_in_is_pointed_out(client, app):
    add_transaction(client, direction="in", amount="3,500.00", transaction_date="2026-11-01")
    add_entry(app)
    add_entry(app, amount="999.00", bank_id="ACT/2")
    page = client.get("/bank/").get_data(as_text=True)
    assert page.count("May already be entered") == 1 and "/transactions/1" in page


def test_a_close_date_is_needed_to_point_it_out(client, app):
    add_transaction(client, direction="in", amount="3,500.00", transaction_date="2026-10-20")
    add_entry(app)
    assert "May already be entered" not in client.get("/bank/").get_data(as_text=True)


def test_two_equal_entries_dont_both_claim_one_transaction(client, app):
    add_transaction(client, direction="in", amount="3,500.00", transaction_date="2026-11-03")
    add_entry(app)
    add_entry(app, bank_id="ACT/2")
    assert client.get("/bank/").get_data(as_text=True).count("May already be entered") == 1


def test_money_out_with_the_same_number_isnt_the_same_money(client, app):
    add_transaction(client, direction="out", amount="3,500.00", transaction_date="2026-11-03")
    add_entry(app)
    assert "May already be entered" not in client.get("/bank/").get_data(as_text=True)


def test_one_already_from_the_bank_isnt_pointed_out(client, app):
    enter(client, add_entry(app))
    add_entry(app, bank_id="ACT/2")
    assert "May already be entered" not in client.get("/bank/").get_data(as_text=True)


def deposits_box(client) -> str:
    """The box in Home's top row that opens From the bank."""
    return client.get("/").get_data(as_text=True).split('<a class="tile" href="/bank/">')[1].split("</a>")[0]


def test_homes_box_counts_the_deposits_from_the_latest_fetch(connected, monkeypatch):
    client = landlord_of(connected)
    box = deposits_box(client)
    assert "New bank deposits" in box and "From latest fetch" in box and '<span class="tile-value">0</span>' in box
    today = date.today()
    # (One the bank posted days after its date still counts. Money going out doesn't.)
    answer = answer_with(
        money_in("today", day=today.isoformat()),
        money_in("posted-late", day=(today - timedelta(days=5)).isoformat()),
        money_in("paid-out", amount=25.0, day=today.isoformat()),
    )
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(answer))
    client.post("/settings/bank/check")
    assert '<span class="tile-value">2</span>' in deposits_box(client)
    # Once one is in her books, it isn't new.
    with connected.app_context():
        db_session.scalar(select(BankEntry).where(BankEntry.bank_id == "today")).status = "Entered"
        db_session.commit()
    assert '<span class="tile-value">1</span>' in deposits_box(client)
    # A later fetch that brings nothing has nothing to count.
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(answer_with(cursor="bookmark-2")))
    client.post("/settings/bank/check")
    assert '<span class="tile-value">0</span>' in deposits_box(client)


def test_new_deposits_dont_need_attention(client, app):
    add_entry(app, day=date.today())
    home = client.get("/").get_data(as_text=True)
    assert "Needs attention</h2>" in home and "Nothing needs attention right now." in home and "alert-pill" not in home
    assert "new deposit" not in client.get("/reminders/").get_data(as_text=True)


def test_transactions_has_a_button_with_how_many_are_waiting(client, app):
    assert "From the bank" not in client.get("/transactions/").get_data(as_text=True)
    add_entry(app)
    add_entry(app, amount="-20.00", bank_id="ACT/2")
    page = client.get("/transactions/").get_data(as_text=True)
    assert 'class="button button-secondary' in page and "<span>From the bank</span>" in page
    assert '<b class="button-count">2</b>' in page


# ---------- More than one login ----------


SECOND_TOKEN = "access-second-token"


def with_two_logins(app):
    bank_link._write_link(
        app.config["DATA_PATHS"],
        {
            "connections": [
                {"access_token": ACCESS_TOKEN, "bank": "Bank of America", "history_ready": True},
                {"item_id": "item-2", "access_token": SECOND_TOKEN, "bank": "Bank of America", "history_ready": True},
            ]
        },
    )


SECOND_ACCOUNT = {"account_id": "ACC2", "name": "Savings", "official_name": None, "mask": "5678", "subtype": "savings"}


def answer_from(token, *items, cursor="bookmark-1"):
    """An answer for one login. The second login has its own account."""
    answer = answer_with(*items, cursor=f"{token}-{cursor}")
    if token == SECOND_TOKEN:
        answer["accounts"] = [SECOND_ACCOUNT]
    return answer


def test_todays_file_keeps_its_key_and_carries_on_from_its_bookmark(keyed):
    # (Just what the file held when there could only be one connection, with no item_id.)
    bank_link._write_link(
        keyed.config["DATA_PATHS"],
        {"access_token": ACCESS_TOKEN, "bank": "Bank of America", "history_ready": True, "consent_expires": "2027-11-03"},
    )
    with keyed.app_context():
        db_session.add(BankCheck(asked_at=NOW - timedelta(days=1), cursor="her-old-bookmark"))
        db_session.commit()
    assert bank_link.is_connected(keyed) and bank_link.bank_name(keyed) == "Bank of America"
    plaid = FakePlaid(answer_with(cursor="bookmark-2"))
    bank_link.check_if_due(keyed, NOW, plaid)
    assert plaid.asked == [(ACCESS_TOKEN, "her-old-bookmark")]
    # Saving wrote it the new way, with everything still there, and it goes on from there.
    link = saved_link(keyed)
    assert link["connections"][0]["access_token"] == ACCESS_TOKEN and link["connections"][0]["consent_expires"] == "2027-11-03"
    assert "access_token" not in link
    bank_link.check_now(keyed, NOW + timedelta(days=1), plaid)
    assert plaid.asked[-1] == (ACCESS_TOKEN, "bookmark-2")


def test_todays_file_with_an_attempt_left_on_plaids_page(keyed, monkeypatch):
    bank_link._write_link(keyed.config["DATA_PATHS"], {"access_token": ACCESS_TOKEN, "bank": "B", "pending_link_token": "link-1"})
    left = {"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "exit": {"status": "requires_credentials"}, "results": None}]}
    line = FakeLine(monkeypatch, link__token__get=left)
    assert bank_link.finish_connecting(keyed) == ""
    assert "/item/public_token/exchange" not in line.paths() and bank_link.is_connected(keyed)
    assert saved_connection(keyed)["access_token"] == ACCESS_TOKEN


def test_the_add_account_page_asks_which_login(connected):
    bank_link.check_now(connected, datetime.now(), FakePlaid(answer_with()))
    page = landlord_of(connected).get("/bank/accounts/add").get_data(as_text=True)
    assert "Which Bank of America login" in page and "The same login</h2>" in page and "<p>Adv Plus Banking ···1234</p>" in page
    assert 'name="add_account" value="1"' in page and "stop being shared" in page and "same login" in page
    assert 'name="new_login" value="1"' in page and "uses one for good" in page
    assert ACCESS_TOKEN not in page


def test_the_add_account_page_needs_a_connection(keyed):
    response = landlord_of(keyed).get("/bank/accounts/add", follow_redirects=False)
    assert response.status_code == 302 and response.headers["Location"].endswith("/bank/")


def test_a_different_login_is_a_new_connection_beside_the_first(connected, monkeypatch):
    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get=finished_session(added_item()),
        item__public_token__exchange={"access_token": SECOND_TOKEN, "item_id": "item-2"},
        transactions__sync=answer_with(),
        item__get={"item": {}},
    )
    client = landlord_of(connected)
    response = client.post("/bank/connect", data={"new_login": "1"}, follow_redirects=False)
    assert response.headers["Location"] == LINK_CREATED["hosted_link_url"]
    fields = line.fields("/link/token/create")
    assert "access_token" not in fields and "update" not in fields
    assert fields["products"] == ["transactions"] and fields["transactions"]["days_requested"] == 730
    assert fields["hosted_link"]["completion_redirect_uri"].endswith("/bank/connected?adding=1")

    response = client.get("/bank/connected?adding=1", follow_redirects=False)
    assert response.headers["Location"].endswith("/bank/accounts")
    assert "The other login is connected." in client.get("/bank/accounts").get_data(as_text=True)
    first, second = saved_link(connected)["connections"]
    assert first["access_token"] == ACCESS_TOKEN and "item_id" not in first
    assert second["access_token"] == SECOND_TOKEN and second["item_id"] == "item-2" and second["bank"] == "Bank of America"
    assert bank_link.bank_name(connected) == "Bank of America"
    # Both were checked, each with its own key.
    tokens = [fields["access_token"] for path, fields, _request in line.sent if path == "/transactions/sync"]
    assert tokens == [ACCESS_TOKEN, SECOND_TOKEN]


def test_each_login_keeps_its_own_bookmark(keyed):
    with_two_logins(keyed)
    asked = []

    def ask(token, bookmark):
        asked.append((token, bookmark))
        return answer_from(token, cursor=f"bookmark-{len(asked)}")

    bank_link.check_if_due(keyed, NOW, ask)
    assert asked == [(ACCESS_TOKEN, ""), (SECOND_TOKEN, "")]
    bank_link.check_if_due(keyed, NOW + timedelta(hours=1), ask)
    assert len(asked) == 2
    bank_link.check_if_due(keyed, NOW + timedelta(days=1), ask)
    assert asked[2:] == [(ACCESS_TOKEN, f"{ACCESS_TOKEN}-bookmark-1"), (SECOND_TOKEN, f"{SECOND_TOKEN}-bookmark-2")]
    with keyed.app_context():
        assert [one.item_id for one in db_session.query(BankCheck).order_by(BankCheck.id)] == ["", "item-2", "", "item-2"]


def test_entries_from_both_logins_come_in(keyed):
    with_two_logins(keyed)

    def ask(token, _bookmark):
        if token == SECOND_TOKEN:
            return answer_from(token, money_in("second", account_id="ACC2"))
        return answer_from(token, money_in("first"))

    bank_link.check_now(keyed, NOW, ask)
    assert [row[0] for row in entries(keyed)] == ["first", "second"]
    assert saved_connection(keyed, 1)["accounts"] == [{"name": "Savings ···5678", "kind": "Savings"}]


def test_an_account_both_logins_can_see_only_comes_in_once(keyed):
    with_two_logins(keyed)

    def ask(token, _bookmark):
        if token == SECOND_TOKEN:
            answer = answer_from(token, money_in("joint-again", account_id="JOINT2"), money_in("his-own", account_id="ACC2"))
            joint = {"account_id": "JOINT2", "name": "Checking", "official_name": "Adv Plus Banking", "mask": "1234", "subtype": "checking"}
            answer["accounts"] = [joint, SECOND_ACCOUNT]
            return answer
        return answer_from(token, money_in("joint"))

    bank_link.check_now(keyed, NOW, ask)
    assert [row[0] for row in entries(keyed)] == ["joint", "his-own"]
    assert [one["name"] for one in saved_connection(keyed)["accounts"]] == ["Adv Plus Banking ···1234"]
    assert [one["name"] for one in saved_connection(keyed, 1)["accounts"]] == ["Savings ···5678"]
    # Not counted as new, and the same again on the next check.
    with keyed.app_context():
        assert [one.new_entries for one in db_session.query(BankCheck).order_by(BankCheck.id)] == [1, 1]


def test_listing_a_login_for_the_first_time_leaves_out_a_shared_account(keyed, monkeypatch):
    with_two_logins(keyed)
    link = saved_link(keyed)
    link["connections"][0]["accounts"] = [{"name": "Adv Plus Banking ···1234", "kind": "Checking"}]
    bank_link._write_link(keyed.config["DATA_PATHS"], link)
    joint = {"account_id": "J", "name": "Checking", "official_name": "Adv Plus Banking", "mask": "1234", "subtype": "checking"}
    FakeLine(monkeypatch, accounts__get={"accounts": [joint, SECOND_ACCOUNT]})
    landlord_of(keyed).get("/bank/accounts")
    assert [one["name"] for one in saved_connection(keyed, 1)["accounts"]] == ["Savings ···5678"]


def test_the_daily_limit_is_for_each_login(keyed):
    with_two_logins(keyed)
    with keyed.app_context():
        for minute in range(bank_link.MOST_CHECKS_A_DAY):
            db_session.add(BankCheck(asked_at=NOW - timedelta(minutes=minute + 1)))
        db_session.commit()
    plaid = FakePlaid(answer_from(SECOND_TOKEN))
    bank_link.check_now(keyed, NOW, plaid)
    assert [token for token, _bookmark in plaid.asked] == [SECOND_TOKEN]


def test_fetch_now_adds_up_both_logins(keyed, monkeypatch):
    with_two_logins(keyed)

    def ask(token, _bookmark):
        if token == SECOND_TOKEN:
            return answer_from(token, money_in("second", account_id="ACC2"))
        return answer_from(token, money_in("first"))

    monkeypatch.setattr(bank_link, "ask_plaid", ask)
    page = landlord_of(keyed).post("/settings/bank/check", follow_redirects=True).get_data(as_text=True)
    assert "Fetched from the bank: 2 new entries." in page


def test_the_accounts_page_groups_them_by_login(keyed):
    with_two_logins(keyed)
    bank_link.check_now(keyed, NOW, lambda token, _bookmark: answer_from(token))
    page = landlord_of(keyed).get("/bank/accounts").get_data(as_text=True)
    assert "<strong>2</strong> accounts connected at Bank of America" in page
    assert ">Login 1</h2>" in page and ">Login 2</h2>" in page
    assert page.index("Adv Plus Banking ···1234") < page.index("Savings ···5678")
    assert ACCESS_TOKEN not in page and SECOND_TOKEN not in page


def test_one_login_has_no_login_headings(connected):
    bank_link.check_now(connected, NOW, FakePlaid(answer_with()))
    assert "Login 1" not in landlord_of(connected).get("/bank/accounts").get_data(as_text=True)


def test_a_problem_with_one_login_offers_to_repair_that_one(keyed, monkeypatch):
    with_two_logins(keyed)

    def ask(token, _bookmark):
        if token == SECOND_TOKEN:
            raise bank_link.BankProblem("The bank needs you to sign in again.")
        return answer_from(token, money_in("first"))

    bank_link.check_now(keyed, datetime.now(), ask)
    assert [row[0] for row in entries(keyed)] == ["first"]
    client = landlord_of(keyed)
    assert "The bank connection needs attention" in client.get("/").get_data(as_text=True)
    page = client.get("/bank/").get_data(as_text=True)
    assert "(Login 2) needs attention" in page and 'name="connection" value="item-2"' in page
    assert page.count("Reconnect the bank") == 1

    line = FakeLine(
        monkeypatch,
        link__token__create=LINK_CREATED,
        link__token__get={"link_sessions": [{"finished_at": "2026-11-03T10:00:00Z", "results": None}]},
    )
    client.post("/bank/connect", data={"connection": "item-2"}, follow_redirects=False)
    assert line.fields("/link/token/create")["access_token"] == SECOND_TOKEN
    assert bank_link.finish_connecting(keyed) == "repaired"
    assert saved_connection(keyed, 1)["access_token"] == SECOND_TOKEN and "accounts" not in saved_connection(keyed, 1)
    assert "accounts" in saved_connection(keyed)


def test_adding_an_account_to_the_second_login_uses_its_connection(keyed, monkeypatch):
    with_two_logins(keyed)
    line = FakeLine(monkeypatch, link__token__create=LINK_CREATED)
    landlord_of(keyed).post("/bank/connect", data={"add_account": "1", "connection": "item-2"}, follow_redirects=False)
    fields = line.fields("/link/token/create")
    assert fields["access_token"] == SECOND_TOKEN and fields["update"] == {"account_selection_enabled": True}


def test_a_login_that_isnt_connected_is_refused(keyed, monkeypatch):
    with_two_logins(keyed)
    line = FakeLine(monkeypatch)
    page = landlord_of(keyed).post("/bank/connect", data={"connection": "nope"}, follow_redirects=True).get_data(as_text=True)
    assert "isn&#39;t connected any more" in page and line.sent == []


def test_it_wont_go_past_the_free_plans_connections(keyed, monkeypatch):
    bank_link._write_link(
        keyed.config["DATA_PATHS"],
        {"connections": [{"item_id": f"item-{n}", "access_token": f"token-{n}", "bank": "B"} for n in range(bank_link.MOST_CONNECTIONS)]},
    )
    line = FakeLine(monkeypatch, accounts__get={"accounts": []})
    client = landlord_of(keyed)
    assert "Connect another login" not in client.get("/bank/accounts/add").get_data(as_text=True)
    line.sent.clear()
    page = client.post("/bank/connect", data={"new_login": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "all of them have been made" in page and "/link/token/create" not in line.paths()


# ---------- On the server ----------


def test_a_connection_problem_shows_on_home_and_the_page(connected):
    client = landlord_of(connected)
    problem = bank_link.BankProblem("The bank needs you to sign in again.")
    bank_link.check_now(connected, datetime.now(), FakePlaid(problem=problem))
    assert "The bank connection needs attention" in client.get("/").get_data(as_text=True)
    page = client.get("/bank/").get_data(as_text=True)
    assert "sign in again" in page and "Reconnect the bank" in page and "Connect the bank</button>" not in page
    # A check that works clears it.
    bank_link.check_now(connected, datetime.now(), FakePlaid())
    assert "The bank connection needs attention" not in client.get("/").get_data(as_text=True)
    assert "Reconnect the bank" not in client.get("/bank/").get_data(as_text=True)


def test_the_yearly_renewal_is_reminded_a_month_ahead(connected):
    client = landlord_of(connected)
    far_off = (date.today() + timedelta(days=31)).isoformat()
    bank_link.check_now(connected, datetime.now(), FakePlaid(answer_with(consent_expires=far_off)))
    assert "needs renewing" not in client.get("/").get_data(as_text=True)
    soon = (date.today() + timedelta(days=30)).isoformat()
    bank_link.check_now(connected, datetime.now(), FakePlaid(answer_with(consent_expires=soon, cursor="bookmark-2")))
    assert "The bank connection needs renewing" in client.get("/").get_data(as_text=True)
    assert "Reconnect the bank" in client.get("/bank/").get_data(as_text=True)


def test_from_the_bank_has_an_accounts_button_once_connected(keyed):
    assert "/bank/accounts" not in landlord_of(keyed).get("/bank/").get_data(as_text=True)
    bank_link._write_link(keyed.config["DATA_PATHS"], {"access_token": ACCESS_TOKEN, "bank": "Bank of America"})
    assert 'href="/bank/accounts"' in landlord_of(keyed).get("/bank/").get_data(as_text=True)


def test_the_accounts_page_needs_a_connection(keyed):
    response = landlord_of(keyed).get("/bank/accounts", follow_redirects=False)
    assert response.status_code == 302 and response.headers["Location"].endswith("/bank/")


def test_the_accounts_page_lists_them_with_add_account_above(connected):
    bank_link.check_now(connected, datetime.now(), FakePlaid(answer_with()))
    page = landlord_of(connected).get("/bank/accounts").get_data(as_text=True)
    assert "<strong>1</strong> account connected at Bank of America" in page and "Adv Plus Banking ···1234" in page
    assert page.index("Add account</span>") < page.index("Adv Plus Banking ···1234")
    assert 'href="/bank/accounts/add"' in page
    assert ACCESS_TOKEN not in page


def test_nothing_on_the_accounts_page_disconnects(connected):
    bank_link.check_now(connected, datetime.now(), FakePlaid(answer_with()))
    assert "isconnect" not in landlord_of(connected).get("/bank/accounts").get_data(as_text=True)


def test_a_connection_made_before_the_list_asks_plaid_once(connected, monkeypatch):
    plaid_accounts = [{"account_id": "A", "name": "Checking", "official_name": None, "mask": "9", "subtype": "checking", "type": "depository"}]
    line = FakeLine(monkeypatch, accounts__get={"accounts": plaid_accounts})
    client = landlord_of(connected)
    page = client.get("/bank/accounts").get_data(as_text=True)
    assert "Checking ···9" in page and ">Bank of America · Checking<" in page.replace("\n", "").replace("  ", "")
    client.get("/bank/accounts")
    assert line.paths() == ["/accounts/get"] and saved_connection(connected)["access_token"] == ACCESS_TOKEN


def test_each_fetch_keeps_the_list_of_accounts_up_to_date(connected):
    bank_link.check_now(connected, NOW, FakePlaid(answer_with()))
    assert [one["name"] for one in saved_connection(connected)["accounts"]] == ["Adv Plus Banking ···1234"]
    # (No accounts in an answer, such as while Plaid is still gathering, leaves the list as it was.)
    bank_link.check_now(connected, NOW + timedelta(days=1), FakePlaid({"added": [], "next_cursor": "x"}))
    assert [one["name"] for one in saved_connection(connected)["accounts"]] == ["Adv Plus Banking ···1234"]


def test_when_plaid_cant_list_the_accounts_the_page_says_so(connected, monkeypatch):
    FakeLine(monkeypatch, accounts__get=plaid_error("ITEM_LOGIN_REQUIRED"))
    page = landlord_of(connected).get("/bank/accounts").get_data(as_text=True)
    assert "sign in again" in page and "No accounts to show" in page and "Add account" in page
    assert "accounts" not in saved_connection(connected)


def test_the_page_offers_connecting_only_when_it_can(server, keyed, monkeypatch):
    page = landlord_of(keyed).get("/bank/").get_data(as_text=True)
    assert "Connect the bank</button>" in page and "never sees your password" in page


def test_without_keys_the_page_offers_nothing(server):
    page = landlord_of(server).get("/bank/").get_data(as_text=True)
    assert "The bank isn't connected." in page and "Connect the bank" not in page


def test_settings_says_where_things_stand(server, monkeypatch):
    client = landlord_of(server)
    page = client.get("/settings/").get_data(as_text=True)
    assert bank_link.CLIENT_ID_VARIABLE in page and bank_link.SECRET_VARIABLE in page
    monkeypatch.setenv(bank_link.CLIENT_ID_VARIABLE, "id")
    monkeypatch.setenv(bank_link.SECRET_VARIABLE, PLAID_SECRET)
    assert "Ready to connect" in client.get("/settings/").get_data(as_text=True)
    bank_link._write_link(server.config["DATA_PATHS"], {"access_token": ACCESS_TOKEN, "bank": "Bank of America"})
    page = client.get("/settings/").get_data(as_text=True)
    assert "Connected to Bank of America through Plaid" in page and "Fetch now</button>" in page
    assert ACCESS_TOKEN not in page and PLAID_SECRET not in page


def test_a_computer_doesnt_show_the_bank_setting(client):
    assert 'id="bank"' not in client.get("/settings/").get_data(as_text=True)


def test_a_preview_with_the_made_up_bank_shows_the_bank_setting(client, monkeypatch):
    monkeypatch.setenv(bank_link.CLIENT_ID_VARIABLE, "id")
    monkeypatch.setenv(bank_link.SECRET_VARIABLE, PLAID_SECRET)
    monkeypatch.setenv(bank_link.ENVIRONMENT_VARIABLE, "sandbox")
    assert 'id="bank"' in client.get("/settings/").get_data(as_text=True)


def test_fetch_now_needs_a_connection(keyed):
    assert landlord_of(keyed).post("/settings/bank/check").status_code == 404


def test_fetch_now_says_what_it_found(connected, monkeypatch):
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(answer_with(money_in())))
    response = landlord_of(connected).post("/settings/bank/check", follow_redirects=True)
    assert response.request.path == "/settings/" and "Fetched from the bank: 1 new entry." in response.get_data(as_text=True)


def test_fetch_now_says_if_something_went_wrong(connected, monkeypatch):
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(problem=bank_link.BankProblem("Plaid couldn't be reached.")))
    page = landlord_of(connected).post("/settings/bank/check", follow_redirects=True).get_data(as_text=True)
    assert "Plaid couldn&#39;t be reached." in page and "Fetched from the bank" not in page


def test_fetch_now_says_if_its_been_asked_too_often(connected, monkeypatch):
    plaid = FakePlaid()
    monkeypatch.setattr(bank_link, "ask_plaid", plaid)
    client = landlord_of(connected)
    for _time in range(bank_link.MOST_CHECKS_A_DAY):
        client.post("/settings/bank/check")
    page = client.post("/settings/bank/check", follow_redirects=True).get_data(as_text=True)
    assert "not fetched" in page and len(plaid.asked) == bank_link.MOST_CHECKS_A_DAY


def test_nobody_gets_in_without_signing_in(connected, monkeypatch):
    plaid = FakePlaid()
    monkeypatch.setattr(bank_link, "ask_plaid", plaid)
    # (A browser that isn't signed in ends up on the sign-in page.)
    page = connected.test_client().get("/bank/").get_data(as_text=True)
    assert "From the bank" not in page and "Sign in" in page
    connected.test_client().post("/settings/bank/check")
    connected.test_client().post("/bank/connect")
    assert plaid.asked == []


# ---------- In a browser that's signed in ----------
# (The tests above send the server's key, as the installed app does. She signs in with a name and password.)


def signed_in(app):
    """A browser signed in with a name and password."""
    add_person(landlord_of(app))
    return signed_in_browser(app)


def test_fetch_now_works_in_a_browser_that_signed_in(connected, monkeypatch):
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(answer_with(money_in())))
    response = signed_in(connected).post("/settings/bank/check", base_url=ADDRESS, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/settings/" and "Fetched from the bank: 1 new entry." in response.get_data(as_text=True)


def test_coming_back_from_plaid_works_in_a_browser_that_signed_in(keyed, monkeypatch):
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid(answer_with(money_in())))
    FakeLine(
        monkeypatch,
        link__token__get={"link_sessions": [{"results": {"item_add_results": [{"public_token": "public", "institution": {"name": "Bank of America"}}]}}]},
        item__public_token__exchange={"access_token": ACCESS_TOKEN, "item_id": "item-1"},
    )
    bank_link._write_link(keyed.config["DATA_PATHS"], {"connections": [], "pending_link_token": "link-token"})
    response = signed_in(keyed).get("/bank/connected", base_url=ADDRESS, follow_redirects=True)
    assert response.status_code == 200 and "Bank of America is connected." in response.get_data(as_text=True)


def test_a_request_still_closes_its_database_session_when_its_over(connected, monkeypatch):
    monkeypatch.setattr(bank_link, "ask_plaid", FakePlaid())
    signed_in(connected).post("/settings/bank/check", base_url=ADDRESS)
    assert not db_session.registry.has()


def test_the_list_shows_one_account_or_money_in_or_out(client, app):
    add_entry(app)  # +$3,500.00 into Checking
    add_entry(app, amount="-186.96", description="CHECKCARD 1102 HOME DEPOT", bank_id="ACT/2")
    with app.app_context():
        db_session.add(BankEntry(bank_id="ACT/3", account_name="Savings", day=date(2026, 11, 2), amount_cents=1234, description="INTEREST"))
        db_session.commit()

    page = client.get("/bank/").get_data(as_text=True)
    # The menus show the result as soon as she picks (filters.js hides the Show button).
    assert "data-auto-filter" in page and "data-show-button" in page and "filters.js" in page
    assert '<option value="Savings">Savings</option>' in page and "+$12.34" in page and "−$186.96" in page

    page = client.get("/bank/?direction=out").get_data(as_text=True)
    assert "−$186.96" in page and "+$3,500.00" not in page and "+$12.34" not in page
    assert "Show all</a>" in page and '<option value="out" selected>' in page

    page = client.get("/bank/?account=Savings&direction=in").get_data(as_text=True)
    assert "+$12.34" in page and "+$3,500.00" not in page and "−$186.96" not in page

    page = client.get("/bank/?account=Savings&direction=out").get_data(as_text=True)
    assert "Nothing from the bank matches." in page

    # An account that isn't there (or a made-up choice) shows everything.
    page = client.get("/bank/?account=Nope&direction=sideways").get_data(as_text=True)
    assert "+$12.34" in page and "−$186.96" in page and "Show all</a>" not in page



# ---------- Connecting a login again (Larry, 2026-11-05) ----------


def test_a_login_connected_again_only_adds_its_older_money(keyed):
    with keyed.app_context():
        # What the first connection brought in: two identical $50 payments, and a deposit.
        old = answer_with(
            money_in("OLD-1", amount=50.0, day="2026-10-01", description="ZELLE TO BOB"),
            money_in("OLD-2", amount=50.0, day="2026-10-01", description="ZELLE TO BOB"),
            money_in("OLD-3", day="2026-10-02"),
        )
        assert bank_link.save_entries(old, date(2026, 5, 1), connection_key="") == 3
        db_session.commit()
        # The same login, connected again: the same money with new ids, plus older months, plus a third $50.
        again = answer_with(
            money_in("NEW-1", amount=50.0, day="2026-10-01", description="ZELLE TO BOB"),
            money_in("NEW-2", amount=50.0, day="2026-10-01", description="ZELLE TO BOB"),
            money_in("NEW-3", amount=50.0, day="2026-10-01", description="ZELLE TO BOB"),
            money_in("NEW-4", day="2026-10-02"),
            money_in("NEW-5", day="2025-12-02", description="Zelle payment from SAM RIVERA, December"),
        )
        assert bank_link.save_entries(again, date(2025, 10, 1), connection_key="item-new") == 2
        db_session.commit()
        saved = {entry.bank_id: entry.item_id for entry in db_session.query(BankEntry)}
    assert saved == {"OLD-1": "", "OLD-2": "", "OLD-3": "", "NEW-3": "item-new", "NEW-5": "item-new"}


def test_disconnecting_by_hand_keeps_the_entries_and_counts_what_was_made(keyed, monkeypatch, capsys):
    from property_manager import bank_by_hand

    paths = keyed.config["DATA_PATHS"]
    bank_link._write_link(paths, {"connections": [
        {"access_token": "token-first", "bank": "Bank of America", "accounts": [{"name": "Checking ···1234"}]},
        {"item_id": "item-2", "access_token": "token-2", "bank": "Bank of America"},
    ]})
    add_entry(keyed)
    line = FakeLine(monkeypatch, item__remove={})
    monkeypatch.setenv("PROPERTY_MANAGER_DATA", str(paths.root))

    assert bank_by_hand.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "Checking ···1234" in listed and "item-2" in listed and "2 connected; 2 of 10 ever made." in listed
    assert "token" not in listed  # never an access token

    assert bank_by_hand.main(["disconnect", "first"]) == 0
    assert line.fields("/item/remove") == {"access_token": "token-first"}
    link = bank_link._read_link(paths)
    assert [one["item_id"] for one in link["connections"]] == ["item-2"] and link["made"] == 2
    assert len(entries(keyed)) == 1  # From the bank keeps what it had
    assert bank_by_hand.main(["disconnect", "nope"]) == 1


def test_connections_ever_made_count_after_some_are_removed(keyed, monkeypatch):
    bank_link._write_link(keyed.config["DATA_PATHS"], {"made": bank_link.MOST_CONNECTIONS, "connections": []})
    line = FakeLine(monkeypatch)
    page = landlord_of(keyed).post("/bank/connect", follow_redirects=True).get_data(as_text=True)
    assert "all of them have been made" in page and "/link/token/create" not in line.paths()


def test_a_new_connections_older_deposits_dont_fill_the_box_on_home(keyed):
    with keyed.app_context():
        db_session.add(BankCheck(asked_at=NOW, item_id="item-new"))
        db_session.flush()
        db_session.add_all([
            BankEntry(bank_id="recent", day=NOW.date() - timedelta(days=3), amount_cents=100, created_at=NOW),
            BankEntry(bank_id="history", day=NOW.date() - timedelta(days=200), amount_cents=100, created_at=NOW),
        ])
        db_session.commit()
        assert bank_link.new_deposits_from_latest_fetch() == 1


def test_with_every_connection_ended_the_page_still_offers_to_connect(keyed, monkeypatch):
    add_entry(keyed)
    page = landlord_of(keyed).get("/bank/").get_data(as_text=True)
    assert "Connect the bank</button>" in page and "+$3,500.00" in page  # the entries stay listed
    bank_link._write_link(keyed.config["DATA_PATHS"], {"connections": [{"item_id": "item-1", "access_token": "t", "bank": "B"}]})
    FakeLine(monkeypatch, item__get={"item": {}}, link__token__get={"link_sessions": []})
    assert "Connect the bank</button>" not in landlord_of(keyed).get("/bank/").get_data(as_text=True)


def test_an_overridden_entry_is_checked_and_can_still_be_entered(keyed):
    entry = add_entry(keyed)
    with keyed.app_context():
        db_session.get(BankEntry, entry).status = "Overridden"
        db_session.commit()
        assert bank_link.waiting_entry(entry) is not None  # it can be entered
        assert bank_link.new_deposits_from_latest_fetch() == 0 and bank_link.waiting_count() == 0  # but it isn't waiting
    page = landlord_of(keyed).get("/bank/").get_data(as_text=True)
    assert 'aria-label="Checked"' in page and f"/transactions/new?bank_entry={entry}" in page and "Overridden</span>" not in page
    # Entering it puts it in Transactions.
    response = landlord_of(keyed).post("/transactions/new", data={"bank_entry": str(entry), "transaction_date": "2026-11-03", "direction": "in",
                                                                  "amount": "3,500.00", "category": "Rental Income"})
    assert "in Transactions now, marked BOFA" in response.get_data(as_text=True)
    with keyed.app_context():
        assert db_session.get(BankEntry, entry).status == "Entered"
    # The bank reporting a change still leaves an entered one as it is.
    with keyed.app_context():
        bank_link.save_entries(answer_with(money_in("ACT/1", amount=-1.0)), date(2026, 1, 1))
        db_session.commit()
        assert db_session.get(BankEntry, entry).amount_cents == 350000


def test_the_status_menu_shows_checked_linked_entered_or_not_entered(keyed):
    client = landlord_of(keyed)
    transaction = typed_in(client, direction="in", amount="1", category="Rental Income")
    statuses = {"ACT/1": "New", "ACT/2": "Overridden", "ACT/3": "Entered", "ACT/4": "Linked"}
    for number, (bank_id, status) in enumerate(statuses.items(), 1):
        entry = add_entry(keyed, amount=f"{number}00.00", bank_id=bank_id)
        with keyed.app_context():
            db_session.get(BankEntry, entry).status = status
            if status in ("Entered", "Linked"):
                db_session.get(BankEntry, entry).transaction_id = transaction
            db_session.commit()
    page = client.get("/bank/").get_data(as_text=True)
    assert '<option value="checked">Checked</option>' in page and '<option value="waiting">Not entered</option>' in page
    for value, amount in (("waiting", "+$100.00"), ("checked", "+$200.00"), ("entered", "+$300.00"), ("linked", "+$400.00")):
        shown = client.get(f"/bank/?status={value}").get_data(as_text=True)
        assert amount in shown and all(other not in shown for other in ("+$100.00", "+$200.00", "+$300.00", "+$400.00") if other != amount), value


def typed_in(client, **fields):
    """A transaction she typed in herself, and its id."""
    add_transaction(client, **fields)
    page = client.get("/transactions/").get_data(as_text=True)
    return max(int(number) for number in __import__("re").findall(r'/transactions/(\d+)"', page))


def test_an_entry_linked_with_ai_shows_its_transaction_and_can_be_unlinked(keyed):
    client = landlord_of(keyed)
    entry = add_entry(keyed)
    transaction = typed_in(client, direction="in", amount="3,500", category="Rental Income")
    with keyed.app_context():
        bank_link.link(db_session.get(BankEntry, entry), db_session.get(Transaction, transaction))
        db_session.commit()
    page = client.get("/bank/?direction=in").get_data(as_text=True)
    assert f'<a class="tag tag-active tag-small" href="/transactions/{transaction}">' in page and "Linked with AI</a>" in page
    assert f'action="/bank/{entry}/unlink"' in page and "Unlink</button>" in page and "be linked again" in page
    assert "Enter into Transactions</a>" not in page
    # It's her own transaction, so it isn't marked BOFA, and it can still be archived.
    listing = client.get("/transactions/").get_data(as_text=True)
    assert "tag-bofa" not in listing

    response = client.post(f"/bank/{entry}/unlink", data={"back": "/bank/?direction=in"}, follow_redirects=False)
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/bank/?direction=in#entry-{entry}")
    with keyed.app_context():
        unlinked = db_session.get(BankEntry, entry)
        assert (unlinked.status, unlinked.transaction_id, unlinked.never_link) == ("New", None, True)
        with pytest.raises(ValueError):
            bank_link.link(unlinked, db_session.get(Transaction, transaction))  # never linked again


def test_unlinking_an_overridden_entry_makes_it_overridden_again(keyed):
    client = landlord_of(keyed)
    entry = add_entry(keyed)
    transaction = typed_in(client, direction="in", amount="3,500", category="Rental Income")
    with keyed.app_context():
        db_session.get(BankEntry, entry).status = "Overridden"
        bank_link.link(db_session.get(BankEntry, entry), db_session.get(Transaction, transaction))
        db_session.commit()
    assert "It will be Overridden again" in client.get("/bank/").get_data(as_text=True)
    assert "back to Overridden" in client.post(f"/bank/{entry}/unlink", follow_redirects=True).get_data(as_text=True)
    with keyed.app_context():
        assert db_session.get(BankEntry, entry).status == "Overridden"


def test_archiving_a_linked_transaction_unlinks_its_entries_for_good(keyed):
    client = landlord_of(keyed)
    halves = [add_entry(keyed, amount="1750.00", bank_id="ACT/1"), add_entry(keyed, amount="1750.00", day=date(2026, 11, 16), bank_id="ACT/2")]
    transaction = typed_in(client, direction="in", amount="3,500", category="Rental Income")
    with keyed.app_context():
        for entry in halves:
            bank_link.link(db_session.get(BankEntry, entry), db_session.get(Transaction, transaction))
        db_session.commit()
    assert "was moved to the Archive" in client.post(f"/transactions/{transaction}/archive").get_data(as_text=True)
    client.post(f"/archive/restore/transaction/{transaction}")
    with keyed.app_context():
        assert db_session.get(Transaction, transaction).archived_at is None  # restored
        states = [(e.status, e.transaction_id, e.never_link) for e in (db_session.get(BankEntry, entry) for entry in halves)]
    assert states == [("New", None, True), ("New", None, True)]  # but not linked again


def test_a_transaction_linked_with_ai_is_tagged_in_the_list(keyed):
    client = landlord_of(keyed)
    entry = add_entry(keyed)
    transaction = typed_in(client, direction="in", amount="3,500", category="Rental Income")
    assert "AI linked</span>" not in client.get("/transactions/").get_data(as_text=True)
    with keyed.app_context():
        bank_link.link(db_session.get(BankEntry, entry), db_session.get(Transaction, transaction))
        db_session.commit()
    listing = client.get("/transactions/").get_data(as_text=True)
    assert '<span class="tag tag-ai">AI linked</span>' in listing and "tag-bofa" not in listing
    client.post(f"/bank/{entry}/unlink")
    assert "AI linked</span>" not in client.get("/transactions/").get_data(as_text=True)
