"""Group relatives by recorded family and order their recorded dates."""

from __future__ import annotations

import re
from functools import cache
from typing import TYPE_CHECKING, Final

from death_age import read_age_at_death
from store import (
    DATE_CODE,
    NAME,
    FamilyDetails,
    FamilyPartnership,
    FamilyPerson,
    PersonID,
    Profile,
    calendar_label,
    display_date,
)

if TYPE_CHECKING:
    import sqlite3

CHILD_FAMILIES: Final = """
    WITH families AS (
        SELECT FamilyID,
               nullif(CASE WHEN FatherID = :person_id THEN MotherID
                           ELSE FatherID END, :person_id) AS partner_id
        FROM FamilyTable
        WHERE FamilyID > 0 AND (FatherID = :person_id OR MotherID = :person_id)
    ), children AS (
        SELECT FamilyID, ChildID AS child_id
        FROM ChildTable JOIN families USING (FamilyID)
        UNION
        SELECT FamilyID, PersonID AS child_id
        FROM PersonTable JOIN families ON FamilyID = ParentID
    )
    SELECT FamilyID AS family_id, partner_id, child_id
    FROM children JOIN families USING (FamilyID)
    WHERE child_id > 0 AND child_id <> :person_id
      AND child_id <> coalesce(partner_id, 0)
    ORDER BY FamilyID, child_id
"""
DATES: Final = """
    SELECT Date FROM EventTable
    WHERE OwnerType = ? AND OwnerID = ? AND EventType = ?
      AND coalesce(IsPrivate, 0) = 0
    ORDER BY IsPrimary DESC, nullif(nullif(Date, '.'), '') IS NULL,
             PlaceID = 0 AND SiteID = 0, EventID
"""
PERSON: Final = f"SELECT * FROM ({NAME}) WHERE id = ?"  # noqa: S608
TEXT_DATE: Final = re.compile(r"T?(\d{4})(?:-(\d{2})-(\d{2}))?")


def date_order(raw: str | None, year: int | None = None) -> tuple[int, int, int]:
    """Order partial and qualified dates by their recorded start components."""
    match = DATE_CODE.fullmatch(raw or "")
    if match and calendar_label(match[2]):
        digits = match[2]
        return int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    text_date = TEXT_DATE.fullmatch(raw or "")
    if text_date:
        digits = text_date[1] + (text_date[2] or "00") + (text_date[3] or "00")
        if calendar_label(digits):
            return int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    return (year, 0, 0) if year and year > 0 else (10000, 0, 0)


def read_family_details(
    db: sqlite3.Connection, record: Profile, preferred_family_id: int | None
) -> FamilyDetails:
    """Project complete family groups within the caller's read transaction."""

    @cache
    def event_date(owner_type: int, owner_id: int, event_type: int) -> str | None:
        return next(
            (
                row["Date"]
                for row in db.execute(DATES, (owner_type, owner_id, event_type))
                if display_date(row["Date"])
            ),
            None,
        )

    @cache
    def person_row(person_id: PersonID) -> sqlite3.Row | None:
        return db.execute(PERSON, (person_id,)).fetchone()

    @cache
    def person(person_id: PersonID) -> FamilyPerson | None:
        row = person_row(person_id)
        if row is None:
            return None
        return FamilyPerson.model_validate(
            dict(row)
            | {
                "role": "Parent",
                "birth_date": display_date(event_date(0, person_id, 1))
                or (str(row["birth_year"]) if row["birth_year"] else None),
                "death_date": display_date(event_date(0, person_id, 2))
                or (str(row["death_year"]) if row["death_year"] else None),
                "age_at_death": read_age_at_death(
                    db,
                    person_id,
                    birth_year=row["birth_year"],
                    death_year=row["death_year"],
                    living=row["living"],
                ),
            }
        )

    def birth_order(member: FamilyPerson) -> tuple[int, int, int, int]:
        row = person_row(member.id)
        year = row["birth_year"] if row is not None else None
        return (*date_order(event_date(0, member.id, 1), year), member.id)

    parent_rows = db.execute(
        """
        SELECT FamilyID, FatherID, MotherID FROM FamilyTable
        WHERE FamilyID = :preferred OR FamilyID IN (
            SELECT FamilyID FROM ChildTable WHERE ChildID = :person_id
        ) ORDER BY FamilyID <> coalesce(:preferred, 0), FamilyID
    """,
        {"preferred": preferred_family_id, "person_id": record.id},
    )
    parents: dict[PersonID, FamilyPerson] = {}
    for row in parent_rows:
        for column, role in (("FatherID", "Father"), ("MotherID", "Mother")):
            parent_id = PersonID(row[column] or 0)
            if parent_id and parent_id != record.id and (member := person(parent_id)):
                parents.setdefault(parent_id, member.model_copy(update={"role": role}))
    for relative in record.relatives:
        if relative.role == "Parent" and (member := person(relative.id)):
            parents.setdefault(relative.id, member)

    children: dict[int, list[FamilyPerson]] = {}
    for row in db.execute(CHILD_FAMILIES, {"person_id": record.id}):
        if member := person(PersonID(row["child_id"])):
            children.setdefault(row["family_id"], []).append(
                member.model_copy(update={"role": "Child"})
            )
    partnerships: list[FamilyPartnership] = []
    for row in db.execute(
        """
        SELECT FamilyID, CASE WHEN FatherID = :person_id THEN MotherID
                             ELSE FatherID END AS partner_id
        FROM FamilyTable
        WHERE FamilyID > 0 AND (FatherID = :person_id OR MotherID = :person_id)
        ORDER BY FamilyID
    """,
        {"person_id": record.id},
    ):
        partner_id = PersonID(row["partner_id"] or 0)
        partner = person(partner_id) if partner_id and partner_id != record.id else None
        marriage_date = display_date(event_date(1, row["FamilyID"], 300))
        family_children = children.get(row["FamilyID"], [])
        if partner is None and not family_children and not marriage_date:
            continue
        partnerships.append(
            FamilyPartnership(
                family_id=row["FamilyID"],
                partner=partner.model_copy(update={"role": "Partner"})
                if partner
                else None,
                marriage_date=marriage_date,
                children=tuple(sorted(family_children, key=birth_order)),
            )
        )
    partnerships.sort(
        key=lambda family: (
            *date_order(event_date(1, family.family_id, 300)),
            family.family_id,
        )
    )
    siblings = tuple(
        member.model_copy(update={"role": relative.role})
        for relative in record.relatives
        if relative.role in ("Sibling", "Half-sibling")
        and (member := person(relative.id)) is not None
    )
    return FamilyDetails(
        parents=tuple(parents.values()),
        partnerships=tuple(partnerships),
        siblings=siblings,
    )
