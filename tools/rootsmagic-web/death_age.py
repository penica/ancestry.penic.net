"""Calculate a displayable age using recorded RootsMagic vital dates."""

from __future__ import annotations

import re
from calendar import month_abbr, month_name, monthrange
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import sqlite3

DATE_CODE: Final = re.compile(r"D([.BAR-])\+(\d{8})\.([.A])\+(\d{8})\.\.")
ISO_DATE: Final = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")
TEXT_DATE: Final = re.compile(r"(?:(\d{1,2})\.?\s+)?([^\W\d_]+)\.?\s+(\d{4})")
APPROXIMATE: Final = re.compile(r"^(?:about|abt\.?|circa|ca\.?|c\.?)\s+", re.I)
SLOVENIAN_MONTHS: Final = (
    ("januar", "januarja"),
    ("februar", "februarja"),
    ("marec", "marca"),
    ("april", "aprila"),
    ("maj", "maja"),
    ("junij", "junija"),
    ("julij", "julija"),
    ("avgust", "avgusta"),
    ("september", "septembra"),
    ("oktober", "oktobra"),
    ("november", "novembra"),
    ("december", "decembra"),
)
VITAL_DATES: Final = """
    SELECT EventType, Date, IsPrivate FROM EventTable
    WHERE OwnerType = 0 AND OwnerID = ? AND EventType IN (1, 2, 3, 4, 7)
    ORDER BY IsPrimary DESC, PlaceID = 0 AND SiteID = 0, EventID
"""


@dataclass(frozen=True, slots=True)
class AgeDate:
    """The components known for a date, with zero for omitted month or day."""

    year: int
    month: int = 0
    day: int = 0
    approximate: bool = False

    @property
    def earliest(self) -> date:
        """First calendar date matching the recorded components."""
        return date(self.year, self.month or 1, self.day or 1)

    @property
    def latest(self) -> date:
        """Last calendar date matching the recorded components."""
        month = self.month or 12
        return date(self.year, month, self.day or monthrange(self.year, month)[1])


def calendar_date(
    year: int, month: int = 0, day: int = 0, *, approximate: bool = False
) -> AgeDate | None:
    """Parse the components while allowing the month and day to be omitted."""
    if day and not month:
        return None
    try:
        _ = date(year, month or 1, day or 1)
    except ValueError:
        return None
    return AgeDate(year, month, day, approximate)


def structured_date(digits: str, *, approximate: bool = False) -> AgeDate | None:
    """Parse the YYYYMMDD field of a RootsMagic date."""
    return calendar_date(
        int(digits[:4]), int(digits[4:6]), int(digits[6:]), approximate=approximate
    )


def parse_age_date(raw: str) -> AgeDate | None:
    """Use a dated value or closed range and leave open-ended dates unresolved."""
    if match := DATE_CODE.fullmatch(raw):
        marker, start, qualifier, end = match.groups()
        first = structured_date(start, approximate=qualifier == "A")
        if marker == "." and end == "00000000":
            return first
        if marker in {"-", "R"} and first and (last := structured_date(end)):
            if first.earliest <= last.latest:
                return AgeDate((first.year + last.year) // 2, approximate=True)
        return None

    value = raw.removeprefix("T").strip()
    value, approximate = APPROXIMATE.subn("", value, count=1)
    if match := ISO_DATE.fullmatch(value):
        year, month, day = match.groups()
        return calendar_date(
            int(year), int(month or 0), int(day or 0), approximate=bool(approximate)
        )
    if match := TEXT_DATE.fullmatch(value):
        day, month_name_text, year = match.groups()
        for month, aliases in enumerate(SLOVENIAN_MONTHS, start=1):
            if month_name_text.casefold() in (
                *aliases,
                month_name[month].casefold(),
                month_abbr[month].casefold(),
            ):
                return calendar_date(
                    int(year), month, int(day or 0), approximate=bool(approximate)
                )
    return None


def preferred_date(*dates: AgeDate | None) -> AgeDate | None:
    """Use the first recorded date in priority order, with about dates last."""
    return min(
        (value for value in dates if value is not None),
        key=lambda value: value.approximate,
        default=None,
    )


def age_label(start: AgeDate, end: AgeDate, *, substitute: bool) -> str | None:
    """Use completed years for full dates and mark estimates for partial dates."""
    if end.latest < start.earliest:
        return None
    approximate = substitute or start.approximate or end.approximate
    years = end.year - start.year
    if start.day and end.day:
        years -= (end.month, end.day) < (start.month, start.day)
        if not years:
            months = (end.year - start.year) * 12 + end.month - start.month
            months -= end.day < start.day
            days = (end.earliest - start.earliest).days
            count, unit = (months, "month") if months else (days, "day")
        else:
            count, unit = years, "year"
    else:
        approximate = True
        if start.month and end.month:
            years -= end.month < start.month
        if not years:
            return "under 1 year"
        count, unit = years, "year"

    prefix = "about " if approximate else ""
    return f"{prefix}{count} {unit}{'s' if count != 1 else ''}"


def read_age_at_death(
    db: sqlite3.Connection,
    person_id: int,
    *,
    birth_year: int | None = None,
    death_year: int | None = None,
    living: bool | None = None,
) -> str | None:
    """Read preferred vital dates from the caller's snapshot and format the age."""
    if living:
        return None

    dates: dict[int, AgeDate] = {}
    recorded: set[int] = set()
    for kind, raw, private in db.execute(VITAL_DATES, (person_id,)):
        if not raw or raw.strip() in {"", "."}:
            continue
        recorded.add(kind)
        if not private and kind not in dates and (value := parse_age_date(raw.strip())):
            dates[kind] = value

    birth = dates.get(1)
    if birth is None and 1 not in recorded and birth_year:
        birth = calendar_date(birth_year)
    death = dates.get(2)
    if death is None and 2 not in recorded and death_year:
        death = calendar_date(death_year)
    start = preferred_date(birth, dates.get(7), dates.get(3))
    end = preferred_date(death, dates.get(4))
    if start is None or end is None:
        return None
    return age_label(start, end, substitute=start is not birth or end is not death)
