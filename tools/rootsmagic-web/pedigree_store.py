"""Read the people and facts shown on the interactive pedigree."""

from __future__ import annotations

import subprocess
import sys
from functools import cache
from html.parser import HTMLParser
from itertools import groupby
from typing import TYPE_CHECKING, Final, Literal, NewType

from pydantic import Field

from death_age import read_age_at_death
from family_details import CHILD_FAMILIES, read_family_details
from store import (
    NAME,
    Person,
    PersonID,
    Profile,
    Record,
    Relative,
    connect,
    display_date,
    read_profile,
)

if TYPE_CHECKING:
    import sqlite3
    from reader_external import DatabasePath

FamilyID = NewType("FamilyID", int)
SiblingRelationship = Literal["Sibling", "Half-sibling"]

CARD_PERSON: Final = f"""
    SELECT person.*, CASE p.Sex WHEN 0 THEN 'male' WHEN 1 THEN 'female' END AS sex
    FROM ({NAME}) person JOIN PersonTable p ON p.PersonID = person.id
    WHERE person.id = ?
"""  # noqa: S608
PARENT_FAMILY: Final = """
    SELECT f.FamilyID AS id, nullif(f.FatherID, 0) AS father,
           nullif(f.MotherID, 0) AS mother
    FROM PersonTable p
    JOIN FamilyTable f ON f.FamilyID = coalesce(
        (SELECT FamilyID FROM FamilyTable WHERE FamilyID = nullif(p.ParentID, 0)),
        (SELECT c.FamilyID FROM ChildTable c JOIN FamilyTable USING (FamilyID)
         WHERE c.ChildID = p.PersonID ORDER BY c.RecID LIMIT 1)
    )
    WHERE p.PersonID = ?
"""
SIBLING_FAMILIES: Final = """
    WITH families AS (
        SELECT FamilyID, FatherID, MotherID
        FROM FamilyTable
        WHERE FamilyID = :family_id
           OR FatherID IN (:father_id, :mother_id)
           OR MotherID IN (:father_id, :mother_id)
    ), children AS (
        SELECT ChildID AS person_id, FamilyID
        FROM ChildTable JOIN families USING (FamilyID)
        UNION
        SELECT PersonID AS person_id, ParentID AS FamilyID
        FROM PersonTable JOIN families ON FamilyID = ParentID
    )
    SELECT person_id, FamilyID AS id,
           nullif(FatherID, 0) AS father, nullif(MotherID, 0) AS mother
    FROM children JOIN families USING (FamilyID)
    WHERE person_id <> :person_id
    ORDER BY person_id, id
"""
PARTNER_FAMILIES: Final = """
    WITH partners AS (
        SELECT FamilyID AS family_id, MotherID AS id
        FROM FamilyTable WHERE FatherID = :person_id
        UNION
        SELECT FamilyID AS family_id, FatherID AS id
        FROM FamilyTable WHERE MotherID = :person_id
    )
    SELECT family_id, id FROM partners
    WHERE id > 0 AND id <> :person_id ORDER BY id, family_id
"""
FACTS: Final = """
    SELECT e.EventType AS type, e.Date AS date, p.Name AS place,
           site.Name AS place_detail
    FROM EventTable e
    LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID
    LEFT JOIN PlaceTable site ON site.PlaceID = e.SiteID
    WHERE e.OwnerType = ? AND e.OwnerID = ?
      AND e.EventType IN (1, 2, 3, 4, 7, 300) AND coalesce(e.IsPrivate, 0) = 0
    ORDER BY e.IsPrimary DESC, nullif(nullif(e.Date, '.'), '') IS NULL,
             e.PlaceID = 0 AND e.SiteID = 0, e.EventID
"""


class Fact(Record):
    """A fact selected for the pedigree card."""

    label: str
    date: str | None
    place: str | None


class PersonCard(Person):
    """A person and the vital details that fit on the chart."""

    birth: Fact | None
    death: Fact | None
    age_at_death: str | None = None
    sex: Literal["male", "female"] | None = None


class PedigreeNode(Record):
    """A chart position and the parents connected to it."""

    person: PersonCard
    parents: tuple[PedigreeNode, ...]
    marriage: Fact | None
    has_more_parents: bool = False


class Sibling(Record):
    """One sibling of the selected person."""

    person: PersonCard
    relationship: SiblingRelationship
    shares_parents: bool = False


class ChildFamily(Record):
    """Children linked to one recorded family of the starting person."""

    id: FamilyID
    partner: PersonCard | None
    children: tuple[PersonCard, ...]
    marriage: Fact | None = None


class Pedigree(Record):
    """The selected person's ancestry and immediate relatives."""

    root: PedigreeNode
    siblings: tuple[Sibling, ...]
    children: tuple[PersonCard, ...]
    partners: tuple[PersonCard, ...] = ()
    partner_marriages: dict[PersonID, tuple[Fact, ...]] = Field(default_factory=dict)
    child_families: tuple[ChildFamily, ...] = ()


class ParentFamily(Record):
    """A RootsMagic family with recorded parent IDs."""

    id: FamilyID
    father: PersonID | None
    mother: PersonID | None

    @property
    def parents(self) -> tuple[PersonID, ...]:
        """Return the recorded parents in chart order."""
        return tuple(
            dict.fromkeys(parent for parent in (self.father, self.mother) if parent)
        )


class SiblingFamily(ParentFamily):
    """A person linked to a family that may be shared with the root."""

    person_id: PersonID


class StoredFact(Record):
    """Fields read from one RootsMagic event row."""

    type: int
    date: str | None
    place: str | None
    place_detail: str | None


class CardFacts(Record):
    """Preferred chart facts for one owner."""

    birth: Fact | None
    death: Fact | None
    marriage: Fact | None


def facts(
    db: sqlite3.Connection,
    owner_type: Literal[0, 1],
    owner_id: PersonID | FamilyID,
) -> CardFacts:
    """Select the best displayable event of each supported type."""
    labels = {
        1: "Born",
        2: "Died",
        3: "Christened",
        4: "Buried",
        7: "Baptized",
        300: "Married",
    }
    result: dict[int, Fact] = {}
    for row in db.execute(FACTS, (owner_type, owner_id)):
        event = StoredFact.model_validate(dict(row))
        if event.type in result:
            continue
        date = display_date(event.date)
        place = ", ".join(
            dict.fromkeys(
                part.strip()
                for part in (event.place_detail, event.place)
                if part and part.strip()
            )
        )
        if date or place:
            result[event.type] = Fact(
                label=labels[event.type], date=date, place=place or None
            )
    return CardFacts(
        birth=result.get(1) or result.get(7) or result.get(3),
        death=result.get(2) or result.get(4),
        marriage=result.get(300),
    )


def sibling_relationships(
    db: sqlite3.Connection,
    person_id: PersonID,
    family: ParentFamily,
) -> tuple[tuple[PersonID, SiblingRelationship], ...]:
    """Find siblings across parent families and label proven half-siblings."""
    rows = db.execute(
        SIBLING_FAMILIES,
        {
            "person_id": person_id,
            "family_id": family.id,
            "father_id": family.father,
            "mother_id": family.mother,
        },
    )
    relatives: dict[PersonID, SiblingRelationship] = {}
    parents = set(family.parents)
    for row in rows:
        candidate = SiblingFamily.model_validate(dict(row))
        other_parents = set(candidate.parents)
        if (
            candidate.id != family.id
            and len(parents) == len(other_parents) == 2
            and len(parents & other_parents) == 1
        ):
            relatives.setdefault(candidate.person_id, "Half-sibling")
        else:
            relatives[candidate.person_id] = "Sibling"
    return tuple(relatives.items())


class NoteText(HTMLParser):
    """Read text and paragraph breaks from UnRTF's Unicode HTML output."""

    def __init__(self) -> None:
        super().__init__()
        self.in_body = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.in_body = True
        if self.in_body and tag in {"br", "li", "p", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "body":
            self.in_body = False

    def handle_data(self, data: str) -> None:
        if self.in_body:
            self.parts.append(data.strip("\r\n"))


def plain_note(note: str | None) -> str | None:
    """Convert legacy RTF notes for display, preserving the source on failure."""
    if not note or not note.lstrip().startswith(r"{\rtf"):
        return note
    macos = sys.platform == "darwin"
    command = (
        ("/usr/bin/textutil", "-convert", "txt", "-stdin", "-stdout")
        if macos
        else ("unrtf", "--html", "--quiet", "--nopict")
    )
    try:
        result = subprocess.run(
            command,
            input=note,
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return note
    if macos:
        return result.stdout.strip()
    parser = NoteText()
    parser.feed(result.stdout)
    parser.close()
    return "".join(parser.parts).strip() if parser.parts else note


def pedigree_details(path: DatabasePath, person_id: PersonID) -> Profile | None:
    """Read the complete person projection and the chart's sibling relationships."""
    with connect(path) as db:
        return read_pedigree_details(db, person_id)


def read_pedigree_details(
    db: sqlite3.Connection, person_id: PersonID
) -> Profile | None:
    """Project the full record inside the caller's current database snapshot."""
    record = read_profile(db, person_id)
    if record is None:
        return None
    record = record.model_copy(update={"note": plain_note(record.note)})
    row = db.execute(PARENT_FAMILY, (person_id,)).fetchone()
    family = ParentFamily.model_validate(dict(row)) if row else None
    sibling_links = sibling_relationships(db, person_id, family) if family else ()
    siblings = tuple(
        Relative.model_validate(dict(person) | {"role": relationship})
        for sibling_id, relationship in sibling_links
        if (person := db.execute(CARD_PERSON, (sibling_id,)).fetchone()) is not None
    )
    record = record.model_copy(update={"relatives": record.relatives + siblings})
    person_row = db.execute(CARD_PERSON, (person_id,)).fetchone()
    return record.model_copy(
        update={
            "family": read_family_details(db, record, family.id if family else None),
            "age_at_death": read_age_at_death(
                db,
                person_id,
                birth_year=person_row["birth_year"],
                death_year=person_row["death_year"],
                living=record.living,
            ),
        }
    )


def read_card(db: sqlite3.Connection, record_id: PersonID) -> PersonCard | None:
    """Read the chart card from the current authorized view."""
    row = db.execute(CARD_PERSON, (record_id,)).fetchone()
    if row is None:
        return None
    person = Person.model_validate(dict(row))
    events = facts(db, 0, record_id)
    return PersonCard(
        id=person.id,
        name=person.name if person.name.strip(" ?_") else "Name unknown",
        living=person.living,
        sex=row["sex"],
        birth=events.birth,
        death=None if person.living else events.death,
        age_at_death=read_age_at_death(
            db,
            record_id,
            birth_year=row["birth_year"],
            death_year=row["death_year"],
            living=person.living,
        ),
    )


def read_parent_family(
    db: sqlite3.Connection, record_id: PersonID
) -> ParentFamily | None:
    """Read the preferred parental family, falling back to the first link."""
    row = db.execute(PARENT_FAMILY, (record_id,)).fetchone()
    return ParentFamily.model_validate(dict(row)) if row else None


def pedigree(
    path: DatabasePath, person_id: PersonID, generations: int
) -> Pedigree | None:
    """Read the entire chart from one current, read-only database snapshot."""
    with connect(path) as db:
        return read_pedigree(db, person_id, generations)


def read_pedigree(
    db: sqlite3.Connection, person_id: PersonID, generations: int
) -> Pedigree | None:
    """Project the chart inside the caller's current database snapshot."""

    @cache
    def card(record_id: PersonID) -> PersonCard | None:
        return read_card(db, record_id)

    @cache
    def parent_family(record_id: PersonID) -> ParentFamily | None:
        return read_parent_family(db, record_id)

    def branch(
        person: PersonCard,
        remaining: int,
        path_ids: frozenset[PersonID],
    ) -> PedigreeNode:
        """Build a branch, cutting only cycles on the current ancestry path."""
        family = parent_family(person.id)
        if family is None:
            return PedigreeNode(person=person, parents=(), marriage=None)
        lineage = path_ids | {person.id}
        if remaining == 1:
            return PedigreeNode(
                person=person,
                parents=(),
                marriage=None,
                has_more_parents=any(
                    parent_id not in lineage and card(parent_id) is not None
                    for parent_id in family.parents
                ),
            )
        parents = tuple(
            branch(parent, remaining - 1, lineage)
            for parent_id in family.parents
            if parent_id not in lineage and (parent := card(parent_id)) is not None
        )
        marriage = facts(db, 1, family.id).marriage if len(parents) == 2 else None
        return PedigreeNode(person=person, parents=parents, marriage=marriage)

    person = card(person_id)
    if person is None:
        return None
    family = parent_family(person_id)
    siblings = (
        tuple(
            Sibling(
                person=sibling,
                relationship=relationship,
                shares_parents=bool(
                    other_family
                    and (
                        other_family.id == family.id
                        or (
                            len(family.parents) == 2
                            and set(other_family.parents) == set(family.parents)
                        )
                    )
                ),
            )
            for sibling_id, relationship in sibling_relationships(db, person_id, family)
            if (sibling := card(sibling_id)) is not None
            for other_family in (parent_family(sibling_id),)
        )
        if family is not None
        else ()
    )
    child_families: list[ChildFamily] = []
    rows = db.execute(CHILD_FAMILIES, {"person_id": person_id})
    for family_id, linked_rows in groupby(rows, key=lambda row: row["family_id"]):
        links = tuple(linked_rows)
        children = tuple(
            child
            for row in links
            if (child := card(PersonID(row["child_id"]))) is not None
        )
        if children:
            partner_id = links[0]["partner_id"] or 0
            partner = card(PersonID(partner_id)) if partner_id > 0 else None
            child_families.append(
                ChildFamily(
                    id=FamilyID(family_id),
                    partner=partner,
                    children=children,
                    marriage=facts(db, 1, FamilyID(family_id)).marriage
                    if partner
                    else None,
                )
            )
    children_by_id = {
        child.id: child for group in child_families for child in group.children
    }
    partners_by_id: dict[PersonID, PersonCard] = {}
    partner_marriages: dict[PersonID, tuple[Fact, ...]] = {}
    for row in db.execute(PARTNER_FAMILIES, {"person_id": person_id}):
        partner = card(PersonID(row["id"]))
        if partner is None:
            continue
        partners_by_id[partner.id] = partner
        marriage = facts(db, 1, FamilyID(row["family_id"])).marriage
        marriages = partner_marriages.get(partner.id, ())
        if marriage is not None and marriage not in marriages:
            partner_marriages[partner.id] = (*marriages, marriage)
    return Pedigree(
        root=branch(person, generations, frozenset()),
        siblings=siblings,
        children=tuple(children_by_id[key] for key in sorted(children_by_id)),
        partners=tuple(partners_by_id.values()),
        partner_marriages=partner_marriages,
        child_families=tuple(child_families),
    )
