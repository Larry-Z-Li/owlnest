"""What a transaction is for, and what type a maintenance request is: one list.

It's the client's list, plus the labels from her own spreadsheets (such as "Prof Fee"),
added at the end of each group so she can keep using them. Where the two lists meant the
same thing, her name stayed: "Rental Income", "Repair & Maintenance", "HOA", "Cleaning" and "Property Tax".
The groups decide where each amount goes in the cash-flow report. Every name is
different, so a transaction stores just the name.
"""

INCOME = "Income"
OPERATING = "Operating expenses"
MORTGAGE_AND_LOAN = "Mortgage & loan expenses"
TRANSFERS = "Transfers"

# The purpose that means rent came in (Home adds these up for the month).
RENT = "Rental Income"
# What a tenant pays for paying late. With RENT, these count as payments of a lease's rent (see rent_ledger.py).
LATE_FEES = "Late Fees"

GROUPS = [
    (
        INCOME,
        [
            "Rental Income",
            "Tenant Fees",
            "Late Fees",
            "Tenant Pass-Throughs",
            "Insurance Proceeds",
            "Amenities & Upsells",
            "Interest & Cash Back",
            "Other Income",
            # From her spreadsheets:
            "Insurance Roof Claim",
        ],
    ),
    (
        OPERATING,
        [
            "Office & Admin",
            "Marketing & Leasing",
            "Travel & Meals",
            "HOA",
            "Legal & Professional",
            "Insurance",
            "Management Fees",
            "Repair & Maintenance",
            "Cleaning",
            "Property Tax",
            "Utilities",
            # Capital expenses until Larry put them here (2026-11-05: "put all capital expenses under
            # operational. no capital"), with "Flooring & Remodeling" split in two:
            "Structure & Systems",
            "Appliances & Furnishings",
            "Flooring",
            "Remodeling",
            "Landscaping",
            # From her spreadsheets ("Insurance" and "Utilities" are above already):
            "Gardening",
            "Swimming Pool",
            "Commission",
            "Contract Labor",
            "Prof Fee",
            "Accounting",
            "Supplies",
            "License",
        ],
    ),
    (MORTGAGE_AND_LOAN, ["Loan Payments", "Interest", "Principal", "PMI"]),
    (
        TRANSFERS,
        [
            "Security Deposits",
            "Escrows",
            "Owner Contributions & Distributions",
            "Credit Card Payments",
            "Down Payments",
            "Taxes to Remit",
            "Other Transfers",
        ],
    ),
]

ALL = [name for _group, names in GROUPS for name in names]

# A new maintenance request's type, unless she picks another.
DEFAULT_REQUEST_TYPE = "Repair & Maintenance"

_GROUP_OF = {name: group for group, names in GROUPS for name in names}


def group_of(category: str) -> str:
    """The group a category is in, such as "Operating expenses" for "Insurance"."""
    return _GROUP_OF.get(category, TRANSFERS)


# Purposes that are work on a home, for the wrench icon in the Transactions list.
_UPKEEP = {
    "Repair & Maintenance",
    "Structure & Systems",
    "Appliances & Furnishings",
    "Flooring",
    "Remodeling",
    "Landscaping",
    "Contract Labor",
    "Supplies",
    "Cleaning",
    "Gardening",
    "Swimming Pool",
}


def look_of(category: str) -> tuple[str, str]:
    """How a purpose looks in the Transactions list: its icon (templates/_icons.html) and its chip's color."""
    group = group_of(category)
    if group == INCOME:
        return "dollar", "chip-green"
    if category == "Insurance":
        return "shield", "chip-teal"
    if category in _UPKEEP:
        return "wrench", "chip-indigo"
    return "receipt", "chip-sky"


def choices() -> list[tuple[str, list[tuple[str, str]]]]:
    """The categories for a drop-down list, under their group headings."""
    return [(group, [(name, name) for name in names]) for group, names in GROUPS]
