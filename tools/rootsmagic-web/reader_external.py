"""The read-only SQLite projection used by an External Reader."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class ExternalTree:
    """A tree that may be read only through its public projection."""

    file: Path


type DatabasePath = Path | ExternalTree

PUBLIC_VIEWS: Final = """
CREATE TEMP VIEW reader_public_people AS
    SELECT p.PersonID FROM main.PersonTable p
    WHERE p.Living = 0 AND p.IsPrivate = 0 AND EXISTS (
        SELECT 1 FROM main.NameTable n WHERE n.OwnerID = p.PersonID AND n.IsPrivate = 0
          AND join_name(n.Given, n.Surname, n.Prefix, n.Suffix) <> ''
    );
CREATE TEMP VIEW FamilyTable AS
    SELECT FamilyID,
           CASE WHEN FatherID IN reader_public_people THEN FatherID ELSE 0 END AS FatherID,
           CASE WHEN MotherID IN reader_public_people THEN MotherID ELSE 0 END AS MotherID
    FROM main.FamilyTable WHERE IsPrivate = 0
      AND (FatherID IN reader_public_people OR MotherID IN reader_public_people);
CREATE TEMP VIEW PersonTable AS
    SELECT p.PersonID, p.UniqueID, p.Living, NULL AS Note,
           CASE WHEN p.ParentID IN (SELECT FamilyID FROM temp.FamilyTable)
             AND NOT EXISTS (SELECT 1 FROM main.ChildTable c
                             WHERE c.ChildID = p.PersonID AND c.FamilyID = p.ParentID AND c.IsPrivate IS NOT 0)
           THEN p.ParentID ELSE 0 END AS ParentID
    FROM main.PersonTable p WHERE p.PersonID IN reader_public_people;
CREATE TEMP VIEW ChildTable AS
    SELECT c.RecID, c.ChildID, c.FamilyID FROM main.ChildTable c
    WHERE c.IsPrivate = 0 AND c.ChildID IN reader_public_people
      AND c.FamilyID IN (SELECT FamilyID FROM temp.FamilyTable)
      AND NOT EXISTS (SELECT 1 FROM main.ChildTable h
                      WHERE h.ChildID = c.ChildID AND h.FamilyID = c.FamilyID AND h.IsPrivate IS NOT 0);
CREATE TEMP VIEW PlaceTable AS
    SELECT PlaceID, Name FROM main.PlaceTable WHERE PlaceType = 0;
CREATE TEMP VIEW EventTable AS
    SELECT e.EventID, e.EventType, e.OwnerType, e.OwnerID, e.IsPrimary, e.IsPrivate,
           reader_public_date(e.Date) AS Date, e.SortDate,
           CASE WHEN e.PlaceID IN (SELECT PlaceID FROM temp.PlaceTable) THEN e.PlaceID ELSE 0 END AS PlaceID,
           0 AS SiteID, NULL AS Details, NULL AS Note
    FROM main.EventTable e WHERE e.IsPrivate = 0 AND (
        (e.OwnerType = 0 AND e.OwnerID IN reader_public_people AND e.EventType IN (1, 2, 3, 4, 7))
        OR (e.OwnerType = 1 AND e.EventType = 300 AND e.OwnerID IN (
            SELECT FamilyID FROM temp.FamilyTable WHERE FatherID > 0 AND MotherID > 0 AND FatherID <> MotherID
        ))
    );
CREATE TEMP VIEW NameTable AS
    SELECT n.NameID, n.OwnerID, n.Prefix, n.Given, n.Surname, n.Suffix, NULL AS Nickname, n.IsPrimary,
           (SELECT reader_public_year(e.Date) FROM temp.EventTable e
            WHERE e.OwnerType = 0 AND e.OwnerID = n.OwnerID AND e.EventType = 1 AND e.Date IS NOT NULL
            ORDER BY e.IsPrimary DESC, e.EventID LIMIT 1) AS BirthYear,
           (SELECT reader_public_year(e.Date) FROM temp.EventTable e
            WHERE e.OwnerType = 0 AND e.OwnerID = n.OwnerID AND e.EventType = 2 AND e.Date IS NOT NULL
            ORDER BY e.IsPrimary DESC, e.EventID LIMIT 1) AS DeathYear
    FROM main.NameTable n WHERE n.IsPrivate = 0 AND n.OwnerID IN reader_public_people
      AND join_name(n.Given, n.Surname, n.Prefix, n.Suffix) <> '';
CREATE TEMP VIEW FactTypeTable AS
    SELECT 1 AS FactTypeID, 'Birth' AS Name UNION ALL SELECT 2, 'Death'
    UNION ALL SELECT 3, 'Christening' UNION ALL SELECT 4, 'Burial'
    UNION ALL SELECT 7, 'Baptism' UNION ALL SELECT 300, 'Marriage';
CREATE TEMP VIEW CitationLinkTable AS
    SELECT NULL AS CitationID, NULL AS OwnerType, NULL AS OwnerID, NULL AS SortOrder, NULL AS LinkID WHERE 0;
CREATE TEMP VIEW CitationTable AS
    SELECT NULL AS CitationID, NULL AS SourceID, NULL AS CitationName, NULL AS RefNumber, NULL AS Footnote WHERE 0;
CREATE TEMP VIEW SourceTable AS SELECT NULL AS SourceID, NULL AS Name WHERE 0;
"""

PROJECTION_READERS: Final = frozenset(
    {
        "reader_public_people",
        "PersonTable",
        "NameTable",
        "FamilyTable",
        "ChildTable",
        "EventTable",
        "PlaceTable",
    }
)


def authorize_external(
    operation: int,
    table: str | None,
    column: str | None,
    database: str | None,
    view: str | None,
) -> int:
    """Allow underlying tree reads only from the defined projection views."""
    # SQLite can report a view's columnless cursor as a separate table read.
    projected_cursor = column == "" and table in PROJECTION_READERS
    if (
        operation == sqlite3.SQLITE_READ
        and database == "main"
        and view not in PROJECTION_READERS
        and not projected_cursor
        and (table, column) != ("json_each", "value")
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
