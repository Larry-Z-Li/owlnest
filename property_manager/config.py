"""Fixed settings: where her records live, which address the app listens on, and how it reaches the server."""

import os
from dataclasses import dataclass
from pathlib import Path

# The folder with the app's code (the one that contains run.py).
CODE_FOLDER = Path(__file__).resolve().parent.parent

# The app's name, shown in the header, browser tabs, the Dock and the Desktop icon.
# Folder names such as PropertyManager/data stay in English and never change,
# so renaming the app never moves her records.
APP_NAME = "OwlNest"

# "127.0.0.1" means "this computer only", so no other device on the network can reach the app.
HOST = "127.0.0.1"

# The port is part of the app's address (http://127.0.0.1:8642/). Port 5000, Flask's usual
# choice, is avoided because newer Macs use it for AirPlay.
PORT = int(os.environ.get("PROPERTY_MANAGER_PORT", "8642"))

# The /health page returns this text, so the launcher can tell the app is already running.
HEALTH_TEXT = "property-manager-ok"

# ---------- The server ----------
# Her records live on one server (the "backend"), which every installed copy of the
# app talks to. See server.py (the server) and relay.py (the app on each computer).

# The server reads its secret key from this environment variable (set in Render's dashboard).
SERVER_KEY_VARIABLE = "PROPERTY_MANAGER_KEY"

# The app on each computer sends the key with every request, in this header.
# The server ignores any request without it, so only computers given the app's files get in.
KEY_HEADER = "X-Property-Manager-Key"

# This file, in the code folder, tells the app on a computer where the server is and
# holds the key. make_app_package.py writes it. It is never put in git.
# Developers can use another one with PROPERTY_MANAGER_CONNECTION, such as one for a test server.
CONNECTION_FILE_NAME = "connection.json"
CONNECTION_FILE = Path(os.environ.get("PROPERTY_MANAGER_CONNECTION", CODE_FOLDER / CONNECTION_FILE_NAME))


def public_address() -> str:
    """The server's address on the internet, such as https://property-manager-ab12.onrender.com.

    Tenants' portal links start with it. Render tells the server its address
    (RENDER_EXTERNAL_URL); PROPERTY_MANAGER_PUBLIC_URL can set another, such as
    a web address of her own. Empty when the app isn't on a server.
    """
    address = os.environ.get("PROPERTY_MANAGER_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    return address.strip().rstrip("/")


def default_data_dir() -> Path:
    """Return the folder that holds all of her records: PropertyManager/data in her home folder.

    Developers and the tests can point the app somewhere else by setting the
    PROPERTY_MANAGER_DATA environment variable, so they never touch her real records.
    """
    override = os.environ.get("PROPERTY_MANAGER_DATA")
    if override:
        return Path(override).expanduser()
    return Path.home() / "PropertyManager" / "data"


def default_second_backup_folder() -> Path:
    """Where every backup zip is also copied, unless she picks another folder in Settings.

    This is "Property Manager Backups" in her Documents folder, so a copy exists
    outside the data folder even if she never opens Settings.

    Developers and the tests can choose another folder with PROPERTY_MANAGER_SECOND_BACKUP.
    When PROPERTY_MANAGER_DATA points at a test data folder, the copies go next to
    it, so testing never writes into the real Documents folder.
    """
    override = os.environ.get("PROPERTY_MANAGER_SECOND_BACKUP")
    if override:
        return Path(override).expanduser()
    if os.environ.get("PROPERTY_MANAGER_DATA"):
        return default_data_dir().parent / "Property Manager Backups"

    documents = Path.home() / "Documents"
    # On Windows, OneDrive often moves the Documents folder inside OneDrive.
    onedrive_documents = Path.home() / "OneDrive" / "Documents"
    if os.name == "nt" and onedrive_documents.is_dir():
        documents = onedrive_documents
    return documents / "Property Manager Backups"


@dataclass(frozen=True)
class DataPaths:
    """The files and folders inside the data folder.

    The data folder is kept apart from the code folder, so updating the code never touches her data.
    """

    root: Path

    @property
    def database(self) -> Path:
        """app.db holds all records."""
        return self.root / "app.db"

    @property
    def receipts(self) -> Path:
        """Uploaded receipt files."""
        return self.root / "receipts"

    @property
    def invoices(self) -> Path:
        """Generated invoice PDFs."""
        return self.root / "invoices"

    @property
    def documents(self) -> Path:
        """Other papers she uploads, such as vendors' W-9 forms."""
        return self.root / "documents"

    @property
    def bank_link(self) -> Path:
        """The password-like address that reads her bank activity. Kept out of every backup (see bank_link.py)."""
        return self.root / "bank-link.json"

    @property
    def failed_logins(self) -> Path:
        """How many wrong logins there have been (see accounts.py). Not the database, so guessing never writes to it."""
        return self.root / "failed-logins.json"

    @property
    def backups(self) -> Path:
        """Automatic backup zips of everything above."""
        return self.root / "backups"

    def create_folders(self) -> None:
        """Create the data folder and its sub-folders if they don't exist yet. Never deletes anything."""
        for folder in (self.root, self.receipts, self.invoices, self.documents, self.backups):
            folder.mkdir(parents=True, exist_ok=True)
