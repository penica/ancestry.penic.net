"""Read-only projections of the RootsMagic tree."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import date
from typing import TYPE_CHECKING, Final, Literal, NewType

from pydantic import BaseModel, ConfigDict

from reader_external import PUBLIC_VIEWS, ExternalTree, authorize_external

if TYPE_CHECKING:
    from collections.abc import Iterator

    from reader_external import DatabasePath

PersonID = NewType("PersonID", int)
NAME: Final = """
    SELECT p.PersonID AS id,
           coalesce(nullif(join_name(n.Prefix, n.Given, n.Surname, n.Suffix),
                                  ''), 'Unnamed person') AS name,
           p.Living = 1 AS living,
           nullif(n.BirthYear, 0) AS birth_year,
           nullif(n.DeathYear, 0) AS death_year
    FROM PersonTable p
    LEFT JOIN NameTable n ON n.NameID = (
        SELECT NameID FROM NameTable WHERE OwnerID = p.PersonID
        ORDER BY IsPrimary DESC, NameID LIMIT 1
    )
"""
# Only the static NAME query is interpolated; input is bound by SQLite.
RELATIVES: Final = f"""
    WITH people AS ({NAME}), families AS (
        SELECT FamilyID, FatherID, MotherID FROM FamilyTable
    ), links AS (
        SELECT 'Parent' AS role, f.FatherID AS id
        FROM ChildTable c JOIN families f USING (FamilyID) WHERE c.ChildID = :id
        UNION
        SELECT 'Parent', f.MotherID
        FROM ChildTable c JOIN families f USING (FamilyID) WHERE c.ChildID = :id
        UNION
        SELECT 'Partner', MotherID FROM families WHERE FatherID = :id
        UNION
        SELECT 'Partner', FatherID FROM families WHERE MotherID = :id
        UNION
        SELECT 'Child', c.ChildID FROM ChildTable c JOIN families f USING (FamilyID)
        WHERE f.FatherID = :id OR f.MotherID = :id
    )
    SELECT people.*, links.role
    FROM people JOIN links USING (id)
    WHERE id != :id
    ORDER BY fold(name), id
"""  # noqa: S608
EVENTS: Final = """
    SELECT e.EventID AS id, coalesce(t.Name, 'Unlabelled fact') AS kind,
           e.OwnerType AS owner_type, e.OwnerID AS owner_id,
           nullif(e.Date, '.') AS date_raw, p.Name AS place,
           site.Name AS place_detail,
           nullif(e.Details, '') AS details, nullif(e.Note, '') AS note
    FROM EventTable e
    LEFT JOIN FactTypeTable t ON t.FactTypeID = e.EventType
    LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID
    LEFT JOIN PlaceTable site ON site.PlaceID = e.SiteID
    WHERE (e.OwnerType = 0 AND e.OwnerID = :id)
       OR (e.OwnerType = 1 AND e.OwnerID IN (
           SELECT FamilyID FROM FamilyTable WHERE FatherID = :id OR MotherID = :id
       ))
    ORDER BY e.SortDate, e.EventID
"""
CITATIONS: Final = """
    SELECT c.CitationID AS id, s.Name AS source, c.CitationName AS name,
           nullif(c.RefNumber, '') AS reference, nullif(c.Footnote, '') AS footnote
    FROM CitationLinkTable l
    JOIN CitationTable c USING (CitationID)
    LEFT JOIN SourceTable s USING (SourceID)
    WHERE l.OwnerType = 2 AND l.OwnerID = ?
    ORDER BY l.SortOrder, l.LinkID
"""

# Only the static NAME query is interpolated; input is bound by SQLite.
DIRECTORY_FILTER: Final = f"""
    WITH people AS ({NAME})
    SELECT * FROM people
    WHERE json_array_length(:terms) = 0 OR CAST(id AS TEXT) = :query OR EXISTS (
        SELECT 1 FROM NameTable n
        WHERE n.OwnerID = people.id AND NOT EXISTS (
            SELECT 1 FROM json_each(:terms) t
            WHERE instr(fold(join_name(n.Given, n.Surname, n.Prefix, n.Suffix,
                                       n.Nickname)), t.value) = 0
        )
    )
"""  # noqa: S608
# Only the static NAME query is interpolated; input is bound by SQLite.
PERSON: Final = f"""
    WITH people AS ({NAME}) SELECT people.*, p.Note AS note
    FROM people JOIN PersonTable p ON p.PersonID = id WHERE id = :id
"""  # noqa: S608
DATE_CODE: Final = re.compile(r"D([.BAR-])\+(\d{8})\.([.A])\+(\d{8})\.\.")
PUBLIC_SIMPLE_DATE: Final = re.compile(r"T?([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2}))?)?")
MONTHS: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class Record(BaseModel):
    """Base for immutable API records."""

    model_config = ConfigDict(frozen=True)


class Person(Record):
    """Directory entry for one RootsMagic person."""

    id: PersonID
    name: str
    living: bool | None


class Relative(Person):
    """A person linked through a recorded family."""

    role: Literal["Parent", "Partner", "Child", "Sibling", "Half-sibling"]


class FamilyPerson(Person):
    """A family member with recorded vital dates for the details views."""

    role: Literal[
        "Father", "Mother", "Parent", "Partner", "Child", "Sibling", "Half-sibling"
    ]
    birth_date: str | None = None
    death_date: str | None = None
    age_at_death: str | None = None


class FamilyPartnership(Record):
    """One recorded union, retaining its own children and marriage date."""

    family_id: int
    partner: FamilyPerson | None
    marriage_date: str | None
    children: tuple[FamilyPerson, ...]


class FamilyDetails(Record):
    """Ordered family groups shared by the panel and full record."""

    parents: tuple[FamilyPerson, ...]
    partnerships: tuple[FamilyPartnership, ...]
    siblings: tuple[FamilyPerson, ...]


class Citation(Record):
    """Citation and source information for one event."""

    id: int
    source: str | None
    name: str | None
    reference: str | None
    footnote: str | None


class Event(Record):
    """A personal or family event and its evidence."""

    id: int
    kind: str
    owner_type: Literal[0, 1]
    owner_id: int
    date_raw: str | None
    date_display: str | None
    place: str | None
    place_detail: str | None = None
    details: str | None
    note: str | None
    citations: tuple[Citation, ...]


class PersonName(Record):
    """A name row, including its ID for evidence lookup."""

    id: int
    name: str
    primary: bool


class Profile(Person):
    """A person with their family and event records."""

    note: str | None
    relatives: tuple[Relative, ...]
    events: tuple[Event, ...]
    names: tuple[PersonName, ...] = ()
    family: FamilyDetails | None = None
    age_at_death: str | None = None


class Directory(Record):
    """One page of people matching the search."""

    people: tuple[Person, ...]
    total: int
    limit: int
    offset: int


class PedigreeSearchPerson(Person):
    """Search identity with years from the displayed primary name record."""

    birth_year: int | None = None
    death_year: int | None = None


class PedigreeDirectory(Directory):
    """Pedigree search results with optional recorded years."""

    people: tuple[PedigreeSearchPerson, ...]


def fold(value: str) -> str:
    """Ignore case and accents for name search."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )


def display_date(raw: str | None) -> str | None:
    """Decode simple dates; retain unfamiliar encodings verbatim."""
    if not raw or raw == ".":
        return None
    if raw.startswith("T"):
        return raw[1:]
    parts = DATE_CODE.fullmatch(raw)
    if parts is None:
        return raw
    marker, start, qualifier, end = parts.groups()
    first = calendar_label(start)
    if first is None:
        return raw
    if qualifier == "A":
        return f"About {first}" if marker == "." and end == "00000000" else raw
    if marker in {"-", "R"}:
        last = calendar_label(end)
        return f"{first} to {last}" if last else raw
    return (
        {".": "", "B": "Before ", "A": "After "}[marker] + first
        if end == "00000000"
        else raw
    )


def calendar_label(digits: str) -> str | None:
    """Format a RootsMagic date component at its recorded precision."""
    year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:])
    if not year:
        return None
    if month == day == 0:
        return str(year)
    if 1 <= month <= len(MONTHS):
        month_name = MONTHS[month - 1]
        if not day:
            return f"{month_name} {year}"
        try:
            _ = date(year, month, day)
        except ValueError:
            return None
        return f"{day} {month_name} {year}"
    return None


def join_name(*parts: str | None) -> str:
    """Join populated RootsMagic name fields."""
    return " ".join(part.strip() for part in parts if part and part.strip())


def public_date(value: str | None) -> str | None:
    """Expose structured dates and simple calendar dates without date-field notes."""
    if not isinstance(value, str):
        return None
    if DATE_CODE.fullmatch(value):
        return value if display_date(value) != value else None
    if match := PUBLIC_SIMPLE_DATE.fullmatch(value):
        digits = match[1] + (match[2] or "00") + (match[3] or "00")
        if calendar_label(digits):
            return value
    return None


def public_year(value: str | None) -> int | None:
    """Get the year from a date already screened by the public event view."""
    if not value:
        return None
    return int(value[3:7] if value.startswith("D") else value.removeprefix("T")[:4])


def compare_names(left: str, right: str) -> int:
    left, right = fold(left), fold(right)
    return (left > right) - (left < right)


@contextmanager
def connect(path: DatabasePath) -> Iterator[sqlite3.Connection]:
    """Read one consistent view without opening the source for writing."""
    file = path.file if isinstance(path, ExternalTree) else path
    with closing(sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.create_function("fold", 1, fold, deterministic=True)
        db.create_function("join_name", -1, join_name, deterministic=True)
        if isinstance(path, ExternalTree):
            if db.execute(
                "SELECT 1 FROM main.sqlite_schema WHERE lower(name) = 'json_each'"
            ).fetchone():
                raise sqlite3.DatabaseError(
                    "A tree table conflicts with the search projection."
                )
            db.create_collation("RMNOCASE", compare_names)
            db.create_function("reader_public_date", 1, public_date, deterministic=True)
            db.create_function("reader_public_year", 1, public_year, deterministic=True)
            db.executescript(PUBLIC_VIEWS)
            db.set_authorizer(authorize_external)
        _ = db.execute("PRAGMA query_only = ON")
        _ = db.execute("BEGIN")
        yield db


def search(path: DatabasePath, query: str, limit: int, offset: int) -> Directory:
    """Find people by ID, primary name or alternate name."""
    rows, total = search_rows(path, query, limit, offset)
    return Directory(
        people=tuple(Person.model_validate(dict(row)) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def count_people(db: sqlite3.Connection) -> int:
    """Count records in the current reader's view of the tree."""
    return int(db.execute("SELECT count(*) FROM PersonTable").fetchone()[0])


def pedigree_search(
    path: DatabasePath,
    query: str,
    limit: int,
    offset: int,
) -> PedigreeDirectory:
    """Find pedigree people with primary-name years, including alias matches."""
    rows, total = search_rows(path, query, limit, offset)
    return PedigreeDirectory(
        people=tuple(PedigreeSearchPerson.model_validate(dict(row)) for row in rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def search_rows(
    path: DatabasePath,
    query: str,
    limit: int,
    offset: int,
) -> tuple[tuple[sqlite3.Row, ...], int]:
    """Read a page and its count from one database snapshot."""
    term = re.sub(r"^#(?=[0-9]+$)", "", query.strip())
    params = {
        "terms": json.dumps(fold(term).split()),
        "query": term,
        "limit": limit,
        "offset": offset,
    }
    with connect(path) as db:
        count_sql = f"SELECT count(*) FROM ({DIRECTORY_FILTER})"  # noqa: S608
        total_row = next(iter(db.execute(count_sql, params)))
        rows = tuple(
            db.execute(
                f"{DIRECTORY_FILTER} ORDER BY fold(name), id LIMIT :limit OFFSET :offset",
                params,
            )
        )
        return rows, int(total_row[0])


def person_names(
    path: DatabasePath, people: tuple[PersonID, ...]
) -> dict[PersonID, str]:
    """Find primary names for the current page of reader state."""
    if not people:
        return {}
    placeholders = ", ".join("?" for _ in people)
    with connect(path) as db:
        rows = db.execute(
            f"SELECT id, name FROM ({NAME}) WHERE id IN ({placeholders})",
            people,  # noqa: S608
        )
        return {PersonID(row["id"]): row["name"] for row in rows}


def profile(path: DatabasePath, person_id: PersonID) -> Profile | None:
    """Read one person, immediate relatives and personal or family events."""
    with connect(path) as db:
        return read_profile(db, person_id)


def read_profile(db: sqlite3.Connection, person_id: PersonID) -> Profile | None:
    """Project a person from an already-open read-only snapshot."""
    params = {"id": person_id}
    row = db.execute(PERSON, params).fetchone()
    if row is None:
        return None
    relatives = db.execute(RELATIVES, params)
    names = db.execute(
        """
        SELECT NameID AS id, join_name(Prefix, Given, Surname, Suffix) AS name,
               coalesce(IsPrimary, 0) = 1 AS "primary"
        FROM NameTable WHERE OwnerID = :id ORDER BY IsPrimary DESC, NameID
        """,
        params,
    )
    events: list[Event] = []
    for event in db.execute(EVENTS, params):
        citations = tuple(
            Citation.model_validate(dict(citation))
            for citation in db.execute(CITATIONS, (event["id"],))
        )
        events.append(
            Event.model_validate(
                dict(event)
                | {
                    "date_display": display_date(event["date_raw"]),
                    "citations": citations,
                }
            )
        )
    return Profile.model_validate(
        dict(row)
        | {
            "relatives": tuple(dict(relative) for relative in relatives),
            "events": events,
            "names": tuple(dict(name) for name in names),
        }
    )
