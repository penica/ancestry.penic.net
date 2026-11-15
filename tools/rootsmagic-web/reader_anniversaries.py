"""Read calendar anniversaries from the current authorized tree snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

from death_age import parse_age_date
from store import DATE_CODE, MONTHS, NAME, PersonID, connect, fold

if TYPE_CHECKING:
    from reader_external import DatabasePath

ANNIVERSARY_EVENTS: Final = f"""
    WITH people AS ({NAME})
    SELECT people.*, e.EventType AS kind, e.Date AS recorded_date,
           place.Name AS place, site.Name AS site
    FROM EventTable e JOIN people ON people.id = e.OwnerID
    LEFT JOIN PlaceTable place ON place.PlaceID = e.PlaceID
    LEFT JOIN PlaceTable site ON site.PlaceID = e.SiteID
    WHERE e.OwnerType = 0 AND e.EventType IN (1, 3, 7)
      AND coalesce(e.IsPrivate, 0) = 0
    ORDER BY people.id, e.EventType = 1 DESC, e.IsPrimary DESC,
             e.EventType = 7 DESC, e.EventID
"""  # noqa: S608
type LifeStatus = Literal["Living", "Deceased", "Status unknown"]


@dataclass(frozen=True, slots=True)
class Anniversary:
    person_id: PersonID
    name: str
    status: LifeStatus
    fact: str
    date_label: str
    place: str
    death_year: int | None
    age: int | None


@dataclass(frozen=True, slots=True)
class Anniversaries:
    births: tuple[Anniversary, ...]
    baptisms: tuple[Anniversary, ...]


def calendar_day(raw: str | None) -> tuple[int | None, int, int] | None:
    """Return a recorded day, including an exact day with an unknown year."""
    if not raw:
        return None
    value = raw.strip()
    if match := DATE_CODE.fullmatch(value):
        marker, digits, qualifier, end = match.groups()
        if marker != "." or qualifier != "." or end != "00000000":
            return None
        year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:])
        try:
            date(year or 2000, month, day)
        except ValueError:
            return None
        return year or None, month, day
    parsed = parse_age_date(value)
    if parsed and parsed.month and parsed.day and not parsed.approximate:
        return parsed.year, parsed.month, parsed.day
    return None


def read_anniversaries(path: DatabasePath, selected: date) -> Anniversaries:
    """Use one exact date per event group, with a known birth day taking precedence."""
    known_birth: set[PersonID] = set()
    known_baptism: set[PersonID] = set()
    births: list[Anniversary] = []
    baptisms: list[Anniversary] = []
    with connect(path) as db:
        for row in db.execute(ANNIVERSARY_EVENTS):
            person_id = PersonID(row["id"])
            birth = row["kind"] == 1
            if person_id in known_birth or (not birth and person_id in known_baptism):
                continue
            recorded = calendar_day(row["recorded_date"])
            if recorded is None:
                continue
            year, month, day = recorded
            seen = known_birth if birth else known_baptism
            seen.add(person_id)
            if (month, day) != (selected.month, selected.day):
                continue
            status: LifeStatus = "Status unknown"
            if row["living"] is not None:
                status = "Living" if row["living"] else "Deceased"
            age = None
            if (
                birth
                and status == "Living"
                and year is not None
                and year <= selected.year
            ):
                age = selected.year - year
            fact = "Born" if birth else "Baptized" if row["kind"] == 7 else "Christened"
            label = f"{day} {MONTHS[month - 1]}"
            if year is not None:
                label += f" {year}"
            place = ", ".join(
                dict.fromkeys(
                    part.strip()
                    for part in (row["site"], row["place"])
                    if part and part.strip()
                )
            )
            group = births if birth else baptisms
            group.append(
                Anniversary(
                    person_id,
                    row["name"],
                    status,
                    fact,
                    label,
                    place,
                    row["death_year"] if birth and status == "Deceased" else None,
                    age,
                )
            )

    def by_name(entry: Anniversary) -> tuple[str, PersonID]:
        return fold(entry.name), entry.person_id

    return Anniversaries(
        tuple(sorted(births, key=by_name)), tuple(sorted(baptisms, key=by_name))
    )
