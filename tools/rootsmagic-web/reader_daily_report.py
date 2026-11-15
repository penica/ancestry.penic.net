"""Prepare and deliver opted-in On this day reports on the hosted reader."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from reader_access import ReaderAccess
from reader_accounts import (
    AccountID,
    Accounts,
    AccountsError,
    Role,
    _read_account,
    _require_session_row,
    _required_token_hash,
)
from reader_anniversaries import Anniversary, read_anniversaries
from reader_email_content import EMAIL_TEMPLATES, EmailBody
from reader_mail import Mail, MailError
from reader_settings import DailyReportSettings, settings_path, site_settings
from store import MONTHS
from tree_catalog import Tree, available_trees

if TYPE_CHECKING:
    import sqlite3

LOG: Final = logging.getLogger(__name__)
SUBSCRIBERS: Final = """
    SELECT a.id, a.email, p.tree_slug,
           CASE WHEN a.administrator THEN 'administrator' ELSE m.role END AS role
    FROM reader_daily_report_preferences p
    JOIN reader_accounts a ON a.id = p.account_id
    JOIN reader_verified_email e ON e.account_id = a.id AND e.address = a.email
    LEFT JOIN reader_memberships m ON m.account_id = a.id AND m.tree_slug = p.tree_slug
    WHERE NOT a.disabled AND (a.administrator OR m.role IS NOT NULL)
"""


@dataclass(frozen=True, slots=True)
class Recipient:
    account: AccountID
    email: str
    tree_slug: str
    role: Role


@dataclass(frozen=True, slots=True)
class ReportEntry:
    name: str
    details: str
    url: str


def scheduled_day(schedule: DailyReportSettings, now: datetime) -> date | None:
    """Return the current report day only after the configured local send time."""
    if not schedule.enabled or schedule.delivery_time is None:
        return None
    local = now.astimezone(ZoneInfo(schedule.timezone))
    return local.date() if local.time() >= schedule.delivery_time else None


def read_subscription(accounts: Accounts, account: AccountID) -> str:
    with accounts._connect() as db:
        row = db.execute(
            "SELECT tree_slug FROM reader_daily_report_preferences WHERE account_id = ?",
            (account,),
        ).fetchone()
        return row["tree_slug"] if row else ""


def save_subscription(
    accounts: Accounts, session_token: str | None, tree_slug: str | None
) -> None:
    """An account may subscribe itself to one of its available trees."""
    token = _required_token_hash(session_token)
    with accounts._connect(write=True) as db:
        account = _read_account(db, _require_session_row(db, token))
        if tree_slug is None:
            db.execute(
                "DELETE FROM reader_daily_report_preferences WHERE account_id = ?",
                (account.id,),
            )
            return
        if not account.email:
            raise AccountsError("Save your email address before subscribing.")
        if account.role_for(tree_slug) is None or not any(
            tree.slug == tree_slug and tree.path.is_file() for tree in available_trees()
        ):
            raise AccountsError("Choose a tree you can open.")
        db.execute(
            """INSERT INTO reader_daily_report_preferences VALUES (?, ?)
               ON CONFLICT(account_id) DO UPDATE SET tree_slug = excluded.tree_slug""",
            (account.id, tree_slug),
        )


def _recipient(row: sqlite3.Row) -> Recipient:
    return Recipient(
        AccountID(row["id"]), row["email"], row["tree_slug"], Role(row["role"])
    )


def claim(accounts: Accounts, recipient: Recipient, day: date) -> bool:
    """Recheck access and reserve at most one send attempt per account and day."""
    with accounts._connect(write=True) as db:
        row = db.execute(SUBSCRIBERS + " AND a.id = ?", (recipient.account,)).fetchone()
        if row is None or _recipient(row) != recipient:
            return False
        result = db.execute(
            """INSERT INTO reader_daily_report_deliveries VALUES (?, ?, 'claimed')
               ON CONFLICT(report_day, account_id) DO NOTHING""",
            (recipient.account, day.isoformat()),
        )
        return result.rowcount == 1


def report_body(origin: str, tree: Tree, day: date) -> EmailBody:
    """Format the same authorized anniversary records used by the web page."""
    report = read_anniversaries(tree.database, day)
    title = f"On this day · {day.day} {MONTHS[day.month - 1]} {day.year}"

    def entry(person: Anniversary, *, baptism: bool = False) -> ReportEntry:
        place = f" · {person.place}" if person.place else ""
        extra = ""
        if baptism:
            extra = f" · {person.status}"
        elif person.age is not None:
            extra = f" · Turns {person.age}"
        elif person.death_year:
            extra = f" · Died {person.death_year}"
        details = f"{person.fact} {person.date_label}{place}{extra}"
        profile = tree.url(f"/pedigree?person={person.person_id}")
        return ReportEntry(
            f"{person.name} (#{person.person_id})", details, origin + profile
        )

    groups = (
        ("Deceased",) if tree.external else ("Living", "Deceased", "Status unknown")
    )
    births = [
        (status, entries)
        for status in groups
        if (
            entries := tuple(
                entry(person) for person in report.births if person.status == status
            )
        )
    ]
    baptisms = tuple(entry(person, baptism=True) for person in report.baptisms)
    overview_url = origin + tree.url(f"/on-this-day?date={day.isoformat()}")
    preferences_url = f"{origin}/account/daily-report"
    lines = [title, tree.name, "", "Born on this day"]
    if not births:
        lines.append("No matching births.")
    for status, people in births:
        lines.extend(("", status))
        for person in people:
            lines.extend((person.name, person.details, person.url, ""))
    lines.extend(("", "Baptized on this day"))
    for person in baptisms:
        lines.extend((person.name, person.details, person.url, ""))
    if not baptisms:
        lines.append("No matching baptisms or christenings.")
    lines.extend(
        (
            "",
            f"View this day in RootsMagic Reader: {overview_url}",
            f"Manage daily report emails: {preferences_url}",
        )
    )
    html = EMAIL_TEMPLATES.get_template("reader-mail-report.html").render(
        title=title,
        tree_name=tree.name,
        births=births,
        baptisms=baptisms,
        overview_url=overview_url,
        preferences_url=preferences_url,
    )
    return EmailBody("\n".join(lines), html)


def run_daily_report(access: ReaderAccess, now: datetime) -> int:
    """Send this day's report to each eligible recipient once."""
    accounts = access.store()
    settings = site_settings(settings_path(accounts.path))
    day = scheduled_day(settings.daily_report, now)
    if day is None:
        return 0
    mail = Mail(accounts)
    if not mail.configured():
        return 0
    with accounts._connect(write=True) as db:
        db.execute(
            "DELETE FROM reader_daily_report_deliveries WHERE report_day < ?",
            ((day - timedelta(days=31)).isoformat(),),
        )
        rows = db.execute(
            SUBSCRIBERS
            + """ AND NOT EXISTS (
                SELECT 1 FROM reader_daily_report_deliveries d
                WHERE d.account_id = a.id AND d.report_day = ?
            )""",
            (day.isoformat(),),
        )
        recipients = tuple(map(_recipient, rows))
    trees = {
        tree.slug: tree for tree in available_trees(settings) if tree.path.is_file()
    }
    reports: dict[Tree, EmailBody] = {}
    sent = 0
    subject = f"RootsMagic Reader · On this day · {day.day} {MONTHS[day.month - 1]} {day.year}"
    for recipient in recipients:
        original = trees.get(recipient.tree_slug)
        if original is None:
            continue
        tree = replace(original, external=recipient.role is Role.EXTERNAL_READER)
        if tree not in reports:
            reports[tree] = report_body(access.origin, tree, day)
        if not claim(accounts, recipient, day):
            continue
        outcome = "sent"
        try:
            mail.send_saved(recipient.email, subject, reports[tree])
        except MailError:
            outcome = "failed"
            LOG.warning(
                "Daily report delivery failed for account %s.", recipient.account
            )
        with accounts._connect(write=True) as db:
            db.execute(
                """UPDATE reader_daily_report_deliveries SET outcome = ?
                   WHERE account_id = ? AND report_day = ?""",
                (outcome, recipient.account, day.isoformat()),
            )
        sent += outcome == "sent"
    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = run_daily_report(
        ReaderAccess.from_environment(), datetime.now().astimezone()
    )
    if count:
        LOG.info("Daily reports accepted by SMTP: %s", count)
