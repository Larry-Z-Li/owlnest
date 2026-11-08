"""The app's screens. Each file in this folder is one screen (a Flask "blueprint")."""

# The menu row, as (page, label) pairs: the screens she uses every day.
# Add each new everyday screen here so it appears in the menu.
NAV_ITEMS = [
    ("home.index", "Home"),
    ("properties.index", "Properties"),
    ("transactions.index", "Transactions"),
    ("maintenance.index", "Maintenance"),
    ("reports.index", "Reports"),
]

# Which menu item is highlighted on pages that aren't in the menu themselves:
# {the page's blueprint: the menu item's blueprint}.
NAV_PARENTS = {
    "leases": "properties",
    "insurances": "properties",
    "files": "properties",
    "reminders": "home",
    "people": "settings",
    "bank": "transactions",
}

# Smaller links at the top right, for things she uses now and then.
UTILITY_ITEMS = [
    ("notes.index", "Notes"),
    ("archive.index", "Archive"),
    ("settings.index", "Settings"),
    ("help.index", "Help 帮助"),
]
