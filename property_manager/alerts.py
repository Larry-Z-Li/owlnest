"""Alerts: everything that needs her attention, for the Home page and the count in the top bar.

Each alert is "urgent" (due or overdue: shown in red, first) or "soon" (coming
up: shown in yellow). They come from:

* Leases: ending within 60 days (urgent within 14), until she dismisses it or
  extends the lease; past their end date but still current.
* Insurances: ending within 60 days (urgent within 14), until she dismisses it
  or changes the end date; already ended.
* Rent (urgent): from its due date until it and any late fee are paid, or she
  waives them on the lease's page (see rent_ledger.py).
* Reminders she added (property tax and so on): from "remind me N days before"
  until done; urgent once the due date arrives.
* Maintenance requests sent from the tenant portal that she hasn't opened yet.
* The bank: a connection that needs attention (urgent), and its yearly renewal, from
  30 days ahead. (New deposits aren't here: they have their own box on Home.)
* Backups: none has worked for 7 days, or the last one failed. On the server
  also: its disk filling up, and no computer saving copies.
* On the server: a name locked after too many wrong passwords (see accounts.py).

An alert goes away by itself once it's dealt with, except reminders, which she
marks done, and "ending soon" alerts, which she can dismiss (they come back if
the end date changes).
"""

import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from flask import current_app, url_for
from sqlalchemy import func, select

from . import accounts, backup, bank_link, records, rent_ledger
from .database import db_session
from .models import BackupCopy, Insurance, Lease, MaintenanceRequest, Reminder
from .money import format_money

# Leases and insurances remind her this many days before they end (urgently in the last 14).
ENDING_SOON_DAYS = 60
ENDING_URGENT_DAYS = 14
# On the server: warn when its disk is this full (and urgently when it's even fuller).
DISK_WARNING_PERCENT = 85
DISK_URGENT_PERCENT = 95
# On the server: warn when no computer has saved a copy of the newest backup for this long.
COPIES_OVERDUE_AFTER = timedelta(days=7)


@dataclass
class Alert:
    urgent: bool
    text: str
    link: str  # where clicking the text goes
    link_text: str = ""  # the part of the text that is the link (the start, if empty)
    # What it's about, for its icon on Home: "request", "sign-in", "lease", "insurance", "reminder", "backup" or "disk".
    kind: str = ""
    # The property it's about, if any (its page shows it too).
    property_id: int | None = None
    # An optional button: (label, the address it posts to).
    button: tuple[str, str] | None = None
    sort_date: date = field(default_factory=date.today)
    # How often it repeats, if it does (her own reminders).
    repeats: str = ""
    # Shown in yellow: it has come due, but isn't urgent (red) yet. Rent, until its late fee is added.
    yellow: bool = False

    @property
    def rest(self) -> str:
        """The text after the link (every alert's text starts with its link text)."""
        return self.text[len(self.link_text):]

    @property
    def second_line(self) -> str:
        """The rest of the text as its own sentence, for a page that puts it under the link."""
        words = self.rest.lstrip(" .:,")
        return words[:1].upper() + words[1:]


def gather(today: date, paths, last_backup_result=None) -> list[Alert]:
    """Every alert, urgent ones first, then by date."""
    alerts = (
        _tenant_requests()
        + _rent(today)
        + _bank(today)
        + _leases(today)
        + _insurances(today)
        + _reminders(today)
        + _backups(today, paths, last_backup_result)
        + _server_disk(today, paths)
        + _copies_on_computers(today)
        + _locked_names()
    )
    return sorted(alerts, key=lambda alert: (not alert.urgent, alert.sort_date))


def count(today: date, paths, last_backup_result=None) -> tuple[int, bool]:
    """(how many alerts, whether any is urgent), for the top bar."""
    alerts = gather(today, paths, last_backup_result)
    return len(alerts), any(alert.urgent for alert in alerts)


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _ends(day: date, today: date) -> str:
    days_left = (day - today).days
    return f"ends on {_nice(day)}" + (" (today)." if days_left == 0 else f", in {_days(days_left)}.")


# ---------- Maintenance requests from tenants ----------


def _tenant_requests() -> list[Alert]:
    query = select(MaintenanceRequest).where(
        MaintenanceRequest.unread.is_(True), MaintenanceRequest.archived_at.is_(None)
    )
    alerts = []
    for maintenance_request in db_session.scalars(query.order_by(MaintenanceRequest.id)):
        lease = maintenance_request.lease
        who = f"{lease.tenant_names} ({maintenance_request.property.nickname})" if lease else maintenance_request.property.nickname
        alerts.append(
            Alert(
                urgent=False,
                kind="request",
                property_id=maintenance_request.property_id,
                text=f"New request from {who}: “{maintenance_request.title}”.",
                link=url_for("maintenance.edit", request_id=maintenance_request.id),
                link_text=f"New request from {who}",
                sort_date=maintenance_request.reported_on,
            )
        )
    return alerts


# ---------- Signing in ----------


def _locked_names() -> list[Alert]:
    if not current_app.config["ON_SERVER"]:
        return []  # (nobody signs in on one computer)
    return [
        Alert(
            urgent=True,
            kind="sign-in",
            text=(
                f"{account.name}'s sign-in is locked: a wrong password was typed {accounts.WRONG_TRIES_ALLOWED} "
                "times in a row. Devices already signed in still work. Unlock it in Settings."
            ),
            link=url_for("settings.index", _anchor="people"),
            link_text=f"{account.name}'s sign-in is locked",
            sort_date=account.locked_at.date(),
        )
        for account in accounts.locked()
    ]


# ---------- Leases ----------


def _leases(today: date) -> list[Alert]:
    alerts = []
    query = select(Lease).where(Lease.status == "Active", Lease.archived_at.is_(None), Lease.end_date.is_not(None))
    for lease in db_session.scalars(query):
        if lease.property.archived_at is not None:
            continue
        name = f"The lease for {records.lease_name(lease)}"
        link = url_for("leases.detail", lease_id=lease.id)
        if lease.end_date < today:
            alerts.append(
                Alert(
                    urgent=True,
                    kind="lease",
                    property_id=lease.property_id,
                    text=f"{name} ended on {_nice(lease.end_date)} but is still current. "
                    "End it, or extend it if they're staying.",
                    link=link,
                    link_text=name,
                    sort_date=lease.end_date,
                )
            )
        elif (lease.end_date - today).days <= ENDING_SOON_DAYS:
            alerts.append(
                Alert(
                    urgent=(lease.end_date - today).days <= ENDING_URGENT_DAYS,
                    kind="lease",
                    property_id=lease.property_id,
                    text=f"{name} {_ends(lease.end_date, today)}",
                    link=link,
                    link_text=name,
                    sort_date=lease.end_date,
                )
            )
    return alerts


# ---------- Rent ----------


def _rent(today: date) -> list[Alert]:
    alerts = []
    for lease in db_session.scalars(select(Lease).where(Lease.archived_at.is_(None))):
        if lease.property.archived_at is not None:
            continue
        for month in rent_ledger.owing(lease, today):
            owed = format_money(month.owed_cents)
            if month.rent_owed_cents:
                name = f"{month.month:%B} rent for {lease.property.nickname}"
                late = f", {_days(month.days_late)} ago" if month.days_late else " (today)"
                text = f"{name} isn't fully paid: {owed} still owed. It was due on {_nice(month.due_on)}{late}."
            else:
                name = f"The {month.month:%B} late fee for {lease.property.nickname}"
                text = f"{name} isn't paid: {owed} still owed."
            alerts.append(
                Alert(
                    urgent=month.past_grace,
                    yellow=not month.past_grace,
                    kind="rent",
                    property_id=lease.property_id,
                    text=text,
                    link=url_for("leases.detail", lease_id=lease.id, _anchor="rent"),
                    link_text=name,
                    sort_date=month.due_on,
                )
            )
    return alerts


# ---------- The bank ----------


def _bank(today: date) -> list[Alert]:
    alerts = []
    link = url_for("bank.index")
    problem = bank_link.last_problem(current_app)
    if problem:
        name = "The bank connection needs attention"
        alerts.append(Alert(urgent=True, kind="bank", text=f"{name}: {problem}", link=link, link_text=name, sort_date=today))
    renew_by = bank_link.renew_by(current_app)
    if renew_by and not problem:
        name = "The bank connection needs renewing"
        text = f"{name} by {_nice(renew_by)}. Click “Reconnect the bank” on the From the bank page."
        alerts.append(Alert(urgent=False, kind="bank", text=text, link=link, link_text=name, sort_date=renew_by))
    return alerts


# ---------- Insurances ----------


def _insurances(today: date) -> list[Alert]:
    alerts = []
    for insurance in db_session.scalars(select(Insurance).where(Insurance.archived_at.is_(None))):
        if insurance.property.archived_at is not None:
            continue
        days_left = (insurance.end_date - today).days
        if days_left > ENDING_SOON_DAYS:
            continue
        name = f"The {insurance.kind.lower()} insurance for {insurance.property.nickname}"
        if insurance.company:
            name += f" ({insurance.company})"
        if days_left < 0:
            text = (
                f"{name} ended on {_nice(insurance.end_date)}. Change its dates for the renewed policy, "
                "or add the new policy and archive this one."
            )
        else:
            text = f"{name} {_ends(insurance.end_date, today)}"
        alerts.append(
            Alert(
                urgent=days_left <= ENDING_URGENT_DAYS,
                kind="insurance",
                property_id=insurance.property_id,
                text=text,
                link=url_for("insurances.edit", insurance_id=insurance.id),
                link_text=name,
                sort_date=insurance.end_date,
            )
        )
    return alerts


# ---------- Reminders ----------


def due_reminders(today: date) -> list[Reminder]:
    """Reminders that are due soon (within their warning time) or overdue."""
    query = select(Reminder).where(Reminder.archived_at.is_(None), Reminder.finished.is_(False))
    return [
        reminder
        for reminder in db_session.scalars(query)
        if (reminder.property is None or reminder.property.archived_at is None)
        and today >= reminder.due_date - timedelta(days=reminder.remind_days_before)
    ]


def reminder_label(reminder: Reminder) -> str:
    """ "Property tax due: second installment (Elm St)" """
    label = reminder.kind + (f": {reminder.title}" if reminder.title else "")
    return label + (f" ({reminder.property.nickname})" if reminder.property else "")


def _reminders(today: date) -> list[Alert]:
    return [_reminder_alert(reminder, today) for reminder in due_reminders(today)]


def not_close_yet(today: date) -> list[Alert]:
    """Her own reminders that are still too far off to need attention, soonest first (for the Reminders page)."""
    close = {reminder.id for reminder in due_reminders(today)}
    query = select(Reminder).where(Reminder.archived_at.is_(None), Reminder.finished.is_(False)).order_by(Reminder.due_date)
    return [
        _reminder_alert(reminder, today)
        for reminder in db_session.scalars(query)
        if reminder.id not in close and (reminder.property is None or reminder.property.archived_at is None)
    ]


def _reminder_alert(reminder: Reminder, today: date) -> Alert:
    days = (reminder.due_date - today).days
    if days < 0:
        when = f"was due on {_nice(reminder.due_date)}, {_days(-days)} ago."
    elif days == 0:
        when = "is due today."
    else:
        when = f"is due on {_nice(reminder.due_date)}, in {_days(days)}."
    return Alert(
        urgent=days <= 0,
        kind="reminder",
        property_id=reminder.property_id,
        text=f"{reminder_label(reminder)} {when}",
        link=url_for("reminders.edit", reminder_id=reminder.id),
        link_text=reminder_label(reminder),
        button=("Done", url_for("reminders.done", reminder_id=reminder.id)),
        sort_date=reminder.due_date,
        repeats="" if reminder.repeat == "Once" else reminder.repeat,
    )


# ---------- Backups ----------


def _backups(today: date, paths, last_result) -> list[Alert]:
    settings_link = url_for("settings.index") + "#backups"
    if last_result is not None and not last_result.succeeded:
        return [
            Alert(
                urgent=True,
                kind="backup",
                text=f"The last backup didn't work: {last_result.error}",
                link=settings_link,
                link_text="The last backup didn't work",
                sort_date=today,
            )
        ]
    last = backup.last_successful_backup(paths.backups)
    if backup.is_overdue(last):
        return [
            Alert(
                urgent=True,
                kind="backup",
                text="No backup has worked in the last 7 days. Go to Settings and click “Back up now”.",
                link=settings_link,
                link_text="No backup has worked in the last 7 days",
                sort_date=today,
            )
        ]
    return []


def _server_disk(today: date, paths) -> list[Alert]:
    """On the server: warn before its disk fills up, because then nothing new can be saved.

    (Not on her own computer, where a fairly full disk is normal and she'd hear about it anyway.)
    """
    if not current_app.config.get("ON_SERVER"):
        return []
    usage = shutil.disk_usage(paths.root)
    percent = round(usage.used * 100 / usage.total)
    if percent < DISK_WARNING_PERCENT:
        return []
    return [
        Alert(
            urgent=percent >= DISK_URGENT_PERCENT,
            kind="disk",
            text=(
                f"The server's disk is {percent}% full. When it's full, nothing new can be saved. "
                "Ask whoever looks after the app to make the disk bigger on Render."
            ),
            link=url_for("settings.index") + "#backups",
            link_text=f"The server's disk is {percent}% full",
            sort_date=today,
        )
    ]


def _copies_on_computers(today: date) -> list[Alert]:
    """On the server: warn when the computers stop saving copies of the newest backup.

    Those copies are the only ones off the server (see relay.py). Each computer
    reports to the server after saving one; Settings lists when each last did.
    """
    if not current_app.config.get("ON_SERVER"):
        return []
    newest = db_session.scalar(select(func.max(BackupCopy.last_copied_at)))
    if newest is not None and datetime.now() - newest < COPIES_OVERDUE_AFTER:
        return []
    # (Only about the backup copies: the records themselves are shared by every computer either way.)
    if newest is None:
        start = "No computer has saved a backup copy yet"
        text = start + ". Each computer with the app saves one a day while the app is open, as a safety copy."
    else:
        start = f"No computer has saved a backup copy since {_nice(newest.date())}"
        text = start + ". Open the app on your computer, and if this stays, ask whoever looks after the app."
    return [
        Alert(
            urgent=newest is not None,
            kind="backup",
            text=text,
            link=url_for("settings.index") + "#backups",
            link_text=start,
            sort_date=today,
        )
    ]


def _nice(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"
