"""Build the facts-only data and document for a standalone pedigree."""

from __future__ import annotations

from base64 import b64encode
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from pedigree_evidence import event_sources
from pedigree_store import (
    PARTNER_FAMILIES,
    FamilyID,
    ParentFamily,
    PersonCard,
    SiblingRelationship,
    read_cards,
    sibling_relationships,
)
from store import PersonID, Record, connect, display_date

if TYPE_CHECKING:
    import sqlite3

    from jinja2 import Environment

    from tree_catalog import Tree

PARENT_LINKS: Final = """
    WITH selected AS (SELECT value AS id FROM json_each(?)),
    links AS (
        SELECT p.PersonID AS person_id, p.ParentID AS family_id
        FROM selected s JOIN PersonTable p ON p.PersonID = s.id
        UNION
        SELECT c.ChildID AS person_id, c.FamilyID AS family_id
        FROM selected s JOIN ChildTable c ON c.ChildID = s.id
    )
    SELECT l.person_id, f.FamilyID AS id, nullif(f.FatherID, 0) AS father,
           nullif(f.MotherID, 0) AS mother
    FROM links l JOIN FamilyTable f ON f.FamilyID = l.family_id
    WHERE f.FamilyID > 0
    ORDER BY l.person_id, f.FamilyID
"""
EXPORT_FACTS: Final = """
    SELECT e.EventID AS id, e.OwnerType AS owner_type, e.OwnerID AS owner_id,
           coalesce(t.Name, 'Event ' || e.EventType) AS label, e.Date AS date,
           p.Name AS place, site.Name AS site,
           EXISTS(SELECT 1 FROM CitationLinkTable c WHERE c.OwnerType = 2
                  AND c.OwnerID = e.EventID) AS cited
    FROM EventTable e
    LEFT JOIN FactTypeTable t ON t.FactTypeID = e.EventType
    LEFT JOIN PlaceTable p ON p.PlaceID = e.PlaceID
    LEFT JOIN PlaceTable site ON site.PlaceID = e.SiteID
    WHERE (e.OwnerType = 0 AND e.OwnerID IN (SELECT value FROM json_each(?)))
       OR (e.OwnerType = 1 AND e.EventType = 300 AND e.OwnerID IN (SELECT value FROM json_each(?)))
    ORDER BY e.SortDate, e.EventID
"""


class ExportFact(Record):
    label: str
    date: str | None
    place: str | None
    links: tuple[str, ...]


class ExportPerson(Record):
    name: str
    sex: Literal["male", "female"] | None
    birth: str | None
    death: str | None
    facts: tuple[ExportFact, ...]


class ExportFamilyMember(Record):
    id: str
    role: Literal["Father", "Mother", "Partner"]


class ExportFamily(Record):
    members: tuple[ExportFamilyMember, ...]
    facts: tuple[ExportFact, ...]


class ExportRelative(Record):
    id: str
    role: SiblingRelationship | Literal["Partner"]


class ExportTree(Record):
    root: str
    people: dict[str, ExportPerson]
    families: dict[str, ExportFamily]
    ancestry: dict[str, tuple[str, ...]]
    relatives: tuple[ExportRelative, ...]


@dataclass
class Ancestry:
    cards: dict[PersonID, PersonCard]
    links: dict[PersonID, list[ParentFamily]]


def read_ancestry(db: sqlite3.Connection, root: PersonID) -> Ancestry:
    """Visit each visible person once, recording every parent-family link."""
    result = Ancestry({}, {})
    seen: set[PersonID] = set()
    pending = {root}
    while pending:
        cards = read_cards(db, pending)
        result.cards.update(cards)
        seen.update(pending)
        pending.clear()
        if not cards:
            break
        for row in db.execute(PARENT_LINKS, (dumps(tuple(cards)),)):
            family = ParentFamily.model_validate(dict(row))
            result.links.setdefault(PersonID(row["person_id"]), []).append(family)
            pending.update(family.parents)
        pending.difference_update(seen)
    return result


def registers_links(db: sqlite3.Connection, event_id: int) -> tuple[str, ...]:
    links = {}
    for source in event_sources(db, event_id):
        url = urlsplit(source.url)
        if url.scheme == "https" and url.hostname == "registers.penic.net" and url.port in (None, 443):
            links[source.url] = None
    return tuple(links)


def card_fact(card: PersonCard, *, death: bool = False) -> str | None:
    fact = card.death if death else card.birth
    age = card.age_at_death if death else None
    text = " ".join(filter(None, (fact.label, fact.date))) if fact else ""
    if age:
        text = f"{text} (age {age})" if text else f"Age at death {age}"
    return " · ".join(filter(None, (text, fact.place if fact else None))) or None


def read_export(db: sqlite3.Connection, root: PersonID, *, external: bool) -> ExportTree | None:
    ancestry = read_ancestry(db, root)
    if root not in ancestry.cards:
        return None
    siblings: dict[PersonID, SiblingRelationship] = {}
    for family in ancestry.links.get(root, ()):
        for sibling, relationship in sibling_relationships(db, root, family):
            if siblings.get(sibling) != "Sibling":
                siblings[sibling] = relationship
    partners: dict[FamilyID, PersonID] = {
        FamilyID(row["family_id"]): PersonID(row["id"])
        for row in db.execute(PARTNER_FAMILIES, {"person_id": root})
    }
    relatives = set(siblings) | set(partners.values())
    direct = frozenset(ancestry.cards)
    ancestry.cards.update(read_cards(db, relatives - direct))
    families: dict[FamilyID, tuple[ExportFamilyMember, ...]] = {}
    links: dict[str, tuple[str, ...]] = {}
    for child, records in ancestry.links.items():
        family_ids = []
        for family in records:
            parents = tuple(
                ExportFamilyMember(id=str(person), role=role)
                for person, role in ((family.father, "Father"), (family.mother, "Mother"))
                if person in direct and (role == "Father" or person != family.father)
            )
            if parents:
                families[family.id] = parents
                family_ids.append(str(family.id))
        if family_ids:
            links[str(child)] = tuple(family_ids)
    for family, partner in partners.items():
        if partner in ancestry.cards:
            # The partner card and starting person share this union's facts.
            families.setdefault(family, (
                ExportFamilyMember(id=str(root), role="Partner"),
                ExportFamilyMember(id=str(partner), role="Partner"),
            ))
    facts: dict[tuple[int, int], list[ExportFact]] = defaultdict(list)
    for row in db.execute(EXPORT_FACTS, (dumps(tuple(ancestry.cards)), dumps(tuple(families)))):
        place = ", ".join(dict.fromkeys(
            value.strip() for value in (row["site"], row["place"]) if value and value.strip()
        ))
        facts[(row["owner_type"], row["owner_id"])].append(ExportFact(
            label=row["label"],
            date=display_date(row["date"]),
            place=place or None,
            links=registers_links(db, row["id"]) if row["cited"] and not external else (),
        ))
    return ExportTree(
        root=str(root),
        people={
            str(person): ExportPerson(
                name=card.name, sex=card.sex, birth=card_fact(card),
                death=card_fact(card, death=True), facts=tuple(facts[0, person]),
            )
            for person, card in ancestry.cards.items()
        },
        families={
            str(family): ExportFamily(members=members, facts=tuple(facts[1, family]))
            for family, members in families.items()
        },
        ancestry=links,
        relatives=(
            *(ExportRelative(id=str(person), role=role) for person, role in siblings.items()
              if person in ancestry.cards),
            *(ExportRelative(id=str(person), role="Partner") for person in dict.fromkeys(partners.values())
              if person in ancestry.cards),
        ),
    )


def render_html_export(templates: Environment, tree: Tree, person_id: PersonID) -> str | None:
    """Read one authorized snapshot, then render a portable HTML document."""
    with connect(tree.database) as db:
        export = read_export(db, person_id, external=tree.external)
    if export is None:
        return None
    assert templates.loader is not None
    script, _, _ = templates.loader.get_source(templates, "pedigree-html.js")
    style, _, _ = templates.loader.get_source(templates, "pedigree-html.css")
    signature = b64encode(sha256(script.encode()).digest()).decode("ascii")
    return templates.get_template("pedigree-html.html").render(
        tree=tree.name, person=export.people[export.root].name,
        export=export.model_dump(mode="json"), script=script, style=style, signature=signature,
    )
