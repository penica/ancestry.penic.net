"""Read the people and facts shown on the interactive pedigree."""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from functools import cache, lru_cache
from html.parser import HTMLParser
from itertools import groupby
from json import dumps
from typing import TYPE_CHECKING, Final, Literal, NewType

from pydantic import Field

from death_age import VITAL_DATES, age_from_vital_dates, read_age_at_death
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
    from collections.abc import Iterable
    from reader_external import DatabasePath

FamilyID = NewType("FamilyID", int)
SiblingRelationship = Literal["Sibling", "Half-sibling"]
MAX_DESCENDANT_DEPTH: Final = 11
MAX_DESCENDANTS: Final = 1000

CARD_PERSON: Final = f"""
    SELECT person.*, CASE p.Sex WHEN 0 THEN 'male' WHEN 1 THEN 'female' END AS sex
    FROM ({NAME}) person JOIN PersonTable p ON p.PersonID = person.id
    WHERE person.id = ?
"""  # noqa: S608
PARENT_FAMILY: Final = """
    SELECT p.PersonID AS person_id, f.FamilyID AS id, nullif(f.FatherID, 0) AS father,
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
    SELECT e.OwnerID AS owner_id, e.EventType AS type, e.Date AS date, p.Name AS place,
           site.Name AS place_detail
    FROM EventTable e
    LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID
    LEFT JOIN PlaceTable site ON site.PlaceID = e.SiteID
    WHERE e.OwnerType = ? AND e.OwnerID = ?
      AND e.EventType IN (1, 2, 3, 4, 7, 300) AND coalesce(e.IsPrivate, 0) = 0
    ORDER BY e.IsPrimary DESC, nullif(nullif(e.Date, '.'), '') IS NULL,
             e.PlaceID = 0 AND e.SiteID = 0, e.EventID
"""
BATCH_IDS: Final = "IN (SELECT value FROM json_each(?))"
CARD_PEOPLE: Final = CARD_PERSON.replace("person.id = ?", f"person.id {BATCH_IDS}")
PARENT_FAMILIES: Final = PARENT_FAMILY.replace("p.PersonID = ?", f"p.PersonID {BATCH_IDS}")
OWNER_FACTS: Final = FACTS.replace("e.OwnerID = ?", f"e.OwnerID {BATCH_IDS}")
OWNER_DATES: Final = VITAL_DATES.replace("OwnerID = ?", f"OwnerID {BATCH_IDS}")


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


class Descendant(Record):
    person: PersonCard
    families: tuple[DescendantFamily, ...] = ()


class DescendantFamily(Record):
    id: FamilyID
    partner: PersonCard | None
    children: tuple[Descendant, ...]
    marriage: Fact | None = None


class DescendantLimitError(ValueError):
    """The requested chart contains too many descendant cards to render."""


class Pedigree(Record):
    """The selected person's ancestry and immediate relatives."""

    root: PedigreeNode
    siblings: tuple[Sibling, ...]
    children: tuple[PersonCard, ...]
    partners: tuple[PersonCard, ...] = ()
    partners_with_parents: tuple[PersonID, ...] = ()
    partner_marriages: dict[PersonID, tuple[Fact, ...]] = Field(default_factory=dict)
    child_families: tuple[ChildFamily, ...] = ()
    descendant_families: tuple[DescendantFamily, ...] = ()


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
    return select_facts(db.execute(FACTS, (owner_type, owner_id)))


def select_facts(rows: Iterable[sqlite3.Row]) -> CardFacts:
    labels = {
        1: "Born",
        2: "Died",
        3: "Christened",
        4: "Buried",
        7: "Baptized",
        300: "Married",
    }
    result: dict[int, Fact] = {}
    for row in rows:
        event = StoredFact.model_validate(
            {key: row[key] for key in ("type", "date", "place", "place_detail")}
        )
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


@lru_cache(maxsize=128)
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
    return project_card(
        row, facts(db, 0, record_id),
        read_age_at_death(
            db, record_id, birth_year=row["birth_year"], death_year=row["death_year"],
            living=row["living"],
        ),
    )


def project_card(row: sqlite3.Row, facts: CardFacts, age: str | None) -> PersonCard:
    person = Person.model_validate(dict(row))
    return PersonCard(
        id=person.id,
        name=person.name if person.name.strip(" ?_") else "Name unknown",
        living=person.living,
        sex=row["sex"],
        birth=facts.birth,
        death=None if person.living else facts.death,
        age_at_death=age,
    )


def read_cards(
    db: sqlite3.Connection, ids: Iterable[PersonID]
) -> dict[PersonID, PersonCard]:
    """Read person cards and their events in batches through the authorized view."""
    selected = dumps(tuple(ids))
    events: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in db.execute(OWNER_FACTS, (0, selected)):
        events[row["owner_id"]].append(row)
    dates: dict[int, list[tuple[int, str | None, int | None]]] = defaultdict(list)
    for row in db.execute(OWNER_DATES, (selected,)):
        dates[row["OwnerID"]].append((row["EventType"], row["Date"], row["IsPrivate"]))
    return {
        PersonID(row["id"]): project_card(
            row, select_facts(events[row["id"]]),
            None if row["living"] else age_from_vital_dates(
                dates[row["id"]], birth_year=row["birth_year"], death_year=row["death_year"]
            ),
        )
        for row in db.execute(CARD_PEOPLE, (selected,))
    }


def read_parent_family(
    db: sqlite3.Connection, record_id: PersonID
) -> ParentFamily | None:
    """Read the preferred parental family, falling back to the first link."""
    row = db.execute(PARENT_FAMILY, (record_id,)).fetchone()
    return ParentFamily.model_validate(dict(row)) if row else None


def pedigree(
    path: DatabasePath, person_id: PersonID, generations: int, descendant_depth: int = 1
) -> Pedigree | None:
    """Read the entire chart from one current, read-only database snapshot."""
    with connect(path) as db:
        return read_pedigree(db, person_id, generations, descendant_depth)


def read_pedigree(
    db: sqlite3.Connection,
    person_id: PersonID,
    generations: int,
    descendant_depth: int = 1,
) -> Pedigree | None:
    """Project the chart inside the caller's current database snapshot."""

    cards: dict[PersonID, PersonCard | None] = {}
    parent_families: dict[PersonID, ParentFamily | None] = {}
    marriages: dict[FamilyID, Fact | None] = {}
    root = read_card(db, person_id)
    if root is None:
        return None
    cards[person_id] = root

    def load_cards(ids: Iterable[PersonID]) -> None:
        missing = set(ids).difference(cards)
        if missing:
            found = read_cards(db, missing)
            cards.update((person, found.get(person)) for person in missing)

    def card(record_id: PersonID) -> PersonCard | None:
        load_cards((record_id,))
        return cards.get(record_id)

    def parent_family(record_id: PersonID) -> ParentFamily | None:
        if record_id not in parent_families:
            parent_families[record_id] = read_parent_family(db, record_id)
        return parent_families[record_id]

    def marriage_fact(family_id: FamilyID) -> Fact | None:
        if family_id not in marriages:
            marriages[family_id] = facts(db, 1, family_id).marriage
        return marriages[family_id]

    frontier = {person_id}
    for _ in range(generations):
        pending = frontier.difference(parent_families)
        if not pending:
            break
        rows = db.execute(PARENT_FAMILIES, (dumps(tuple(pending)),))
        found = {PersonID(row["person_id"]): ParentFamily.model_validate(dict(row)) for row in rows}
        parent_families.update((person, found.get(person)) for person in pending)
        frontier = {person for family in found.values() for person in family.parents}
    load_cards((*parent_families, *frontier))
    family_ids = {family.id for family in parent_families.values() if family is not None}
    event_groups: dict[FamilyID, list[sqlite3.Row]] = defaultdict(list)
    for row in db.execute(OWNER_FACTS, (1, dumps(tuple(family_ids)))):
        event_groups[FamilyID(row["owner_id"])].append(row)
    marriages.update((family_id, select_facts(event_groups[family_id]).marriage) for family_id in family_ids)

    @cache
    def child_families(record_id: PersonID) -> tuple[ChildFamily, ...]:
        families: list[ChildFamily] = []
        rows = tuple(db.execute(CHILD_FAMILIES, {"person_id": record_id}))
        load_cards(PersonID(person) for row in rows for person in (row["child_id"], row["partner_id"]) if person)
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
                families.append(
                    ChildFamily(
                        id=FamilyID(family_id),
                        partner=partner,
                        children=children,
                        marriage=marriage_fact(FamilyID(family_id))
                        if partner
                        else None,
                    )
                )
        return tuple(families)

    descendant_count = 0

    def descendants(
        record_id: PersonID, remaining: int, lineage: frozenset[PersonID]
    ) -> tuple[DescendantFamily, ...]:
        nonlocal descendant_count
        if remaining == 0:
            return ()
        lineage = lineage | {record_id}
        groups: list[DescendantFamily] = []
        for family in child_families(record_id):
            children: list[Descendant] = []
            for child in family.children:
                if child.id in lineage:
                    continue
                descendant_count += 1
                if descendant_count > MAX_DESCENDANTS:
                    raise DescendantLimitError
                children.append(
                    Descendant(
                        person=child,
                        families=descendants(child.id, remaining - 1, lineage),
                    )
                )
            if children:
                groups.append(
                    DescendantFamily(
                        id=family.id,
                        partner=family.partner,
                        marriage=family.marriage,
                        children=tuple(children),
                    )
                )
        return tuple(groups)

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
        marriage = marriage_fact(family.id) if len(parents) == 2 else None
        return PedigreeNode(person=person, parents=parents, marriage=marriage)

    family = parent_family(person_id)
    sibling_links = sibling_relationships(db, person_id, family) if family else ()
    load_cards(sibling_id for sibling_id, _ in sibling_links)
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
            for sibling_id, relationship in sibling_links
            if (sibling := card(sibling_id)) is not None
            for other_family in (parent_family(sibling_id),)
        )
        if family is not None
        else ()
    )
    immediate_families = child_families(person_id)
    children_by_id = {
        child.id: child for group in immediate_families for child in group.children
    }
    partners_by_id: dict[PersonID, PersonCard] = {}
    partner_marriages: dict[PersonID, tuple[Fact, ...]] = {}
    partner_rows = tuple(db.execute(PARTNER_FAMILIES, {"person_id": person_id}))
    load_cards(PersonID(row["id"]) for row in partner_rows)
    for row in partner_rows:
        partner = card(PersonID(row["id"]))
        if partner is None:
            continue
        partners_by_id[partner.id] = partner
        marriage = marriage_fact(FamilyID(row["family_id"]))
        recorded_marriages = partner_marriages.get(partner.id, ())
        if marriage is not None and marriage not in recorded_marriages:
            partner_marriages[partner.id] = (*recorded_marriages, marriage)
    return Pedigree(
        root=branch(root, generations, frozenset()),
        siblings=siblings,
        children=tuple(children_by_id[key] for key in sorted(children_by_id)),
        partners=tuple(partners_by_id.values()),
        partners_with_parents=tuple(
            partner.id
            for partner in partners_by_id.values()
            if branch(partner, 1, frozenset()).has_more_parents
        ),
        partner_marriages=partner_marriages,
        child_families=immediate_families,
        descendant_families=descendants(person_id, descendant_depth, frozenset())
        if descendant_depth > 1
        else (),
    )
