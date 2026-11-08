"""Shared test setup. Every test gets its own empty, temporary data folder."""

import pytest
from flask.testing import FlaskClient

from property_manager import create_app
from property_manager.config import DataPaths


@pytest.fixture(autouse=True)
def _keep_tests_away_from_real_records(tmp_path, monkeypatch):
    """Point the app at a temporary folder, so tests can never touch her real records."""
    monkeypatch.setenv("PROPERTY_MANAGER_DATA", str(tmp_path / "data"))
    # And never use a real RentCast key, which could cost money (see home_values.py).
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    # Or a real bank connection (see bank_link.py).
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)


@pytest.fixture
def paths(tmp_path) -> DataPaths:
    return DataPaths(tmp_path / "data")


@pytest.fixture
def app(paths):
    app = create_app(paths.root)
    app.config["TESTING"] = True
    return app


class BrowserLikeClient(FlaskClient):
    """A test client that follows redirects like a browser does, so each "Saved."
    message is shown on the next page (and not left over for a later one)."""

    def open(self, *args, **kwargs):
        kwargs.setdefault("follow_redirects", True)
        return super().open(*args, **kwargs)


@pytest.fixture
def client(app):
    app.test_client_class = BrowserLikeClient
    return app.test_client()
