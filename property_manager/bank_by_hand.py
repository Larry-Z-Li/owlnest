"""The bank connections, by hand on the server: list them, or end one for good.

No page does this, on purpose: Larry decides, and it's done by hand in Render's Shell (or over SSH):

    python -m property_manager.bank_by_hand list
    python -m property_manager.bank_by_hand disconnect KEY

"list" shows each connection's key, bank and accounts, never its access token. "disconnect"
ends one at Plaid and forgets it here. Its entries on "From the bank" and its checks stay.
On Plaid's free plan this doesn't give the connection back (see bank_link.py).
"""

import sys

from . import bank_link
from .config import DataPaths, default_data_dir


def main(arguments: list[str]) -> int:
    paths = DataPaths(default_data_dir())
    link = bank_link._read_link(paths)
    if arguments == ["list"]:
        for connection in link["connections"]:
            accounts = ", ".join(account["name"] for account in connection.get("accounts") or []) or "(no accounts saved yet)"
            print(f"{bank_link._key(connection) or '(first connection, empty key)'}: {connection.get('bank') or 'a bank'}: {accounts}")
        print(f"{len(link['connections'])} connected; {bank_link._made(link)} of {bank_link.MOST_CONNECTIONS} ever made.")
        return 0
    if len(arguments) == 2 and arguments[0] == "disconnect":
        key = "" if arguments[1] == "first" else arguments[1]
        try:
            print(bank_link.disconnect(paths, key))
        except bank_link.BankProblem as problem:
            print(f"Not disconnected: {problem}")
            return 1
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
