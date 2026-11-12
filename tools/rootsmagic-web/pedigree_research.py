"""Bounded recorded-family routes and research prompts for visible people."""
# noqa: SIZE_OK — the two research responses share this small read-only adapter.

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Final, Literal

from pedigree_store import CARD_PERSON, FACTS, PARENT_FAMILY, Fact, ParentFamily, facts
from store import NAME, Person, PersonID, Record, connect, display_date

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping
    from pathlib import Path

MAX_PATHS: Final = 20
MAX_DEPTH: Final = 64
MAX_EXPANSIONS: Final = 50_000
DEATH: Final = 2
Step = Literal["parent", "child", "partner"]
Missing = Literal[
    "father", "mother", "birth_date", "birth_place", "death_date", "death_place"
]
LINKS: Final = """
    WITH family_links AS (
        SELECT PersonID AS child, ParentID AS family FROM PersonTable WHERE ParentID > 0
        UNION
        SELECT ChildID, FamilyID FROM ChildTable
    )
    SELECT l.child, f.FatherID AS father, f.MotherID AS mother
    FROM family_links l
    JOIN PersonTable p ON p.PersonID = l.child
    JOIN FamilyTable f ON f.FamilyID = l.family
"""
UNCITED: Final = """
    SELECT e.EventID AS id, coalesce(t.Name, 'Unlabelled fact') AS kind,
           e.Date AS date
    FROM EventTable e LEFT JOIN FactTypeTable t ON t.FactTypeID = e.EventType
    WHERE ((e.OwnerType = 0 AND e.OwnerID = :id)
       OR (e.OwnerType = 1 AND e.OwnerID IN (
           SELECT FamilyID FROM FamilyTable WHERE FatherID = :id OR MotherID = :id
       ))) AND coalesce(e.IsPrivate, 0) = 0
      AND NOT EXISTS (SELECT 1 FROM CitationLinkTable l
          JOIN CitationTable c USING (CitationID)
          WHERE l.OwnerType = 2 AND l.OwnerID = e.EventID)
    ORDER BY e.SortDate, e.EventID
"""


class RelationshipPath(Record):
    """People in traversal order, with one direction per connecting edge."""

    people: tuple[Person, ...]
    steps: tuple[Step, ...]


class RelationshipPaths(Record):
    """Several ancestry routes, or shortest mixed-family routes only."""

    from_person: Person
    to_person: Person
    paths: tuple[RelationshipPath, ...]
    truncated: bool
    strategy: Literal["ancestry", "shortest", "same", "none"]


class UncitedEvent(Record):
    """An event without a valid, directly linked citation."""

    id: int
    kind: str
    date: str | None


class PersonGaps(Record):
    """Missing recorded data; alternative vital facts remain distinct."""

    person: Person
    missing: tuple[Missing, ...]
    uncited_events: tuple[UncitedEvent, ...]
    birth_fallback: Fact | None
    death_fallback: Fact | None


class ResearchGaps(Record):
    """Research prompts for existing requested people, in request order."""

    people: tuple[PersonGaps, ...]


@dataclass(frozen=True, slots=True)
class FamilyGraph:
    """One snapshot of recorded parent and partner relationships."""

    people: dict[PersonID, Person]
    parents: dict[PersonID, set[PersonID]]
    children: dict[PersonID, set[PersonID]]
    partners: dict[PersonID, set[PersonID]]


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Bounded route enumeration result."""

    paths: tuple[tuple[PersonID, ...], ...]
    truncated: bool


def _graph(db: sqlite3.Connection) -> FamilyGraph:
    """Resolve names and recorded family links without inventing people."""
    people = {
        person.id: person
        for row in db.execute(NAME)
        for person in (Person.model_validate(dict(row)),)
    }
    parents: dict[PersonID, set[PersonID]] = {person: set() for person in people}
    children: dict[PersonID, set[PersonID]] = {person: set() for person in people}
    partners: dict[PersonID, set[PersonID]] = {person: set() for person in people}
    for row in db.execute(LINKS):
        child = PersonID(row["child"])
        for value in (row["father"], row["mother"]):
            parent = PersonID(value or 0)
            if parent in people and parent != child:
                parents[child].add(parent)
                children[parent].add(child)
    for row in db.execute("SELECT FatherID, MotherID FROM FamilyTable"):
        left, right = PersonID(row[0] or 0), PersonID(row[1] or 0)
        if left in people and right in people and left != right:
            partners[left].add(right)
            partners[right].add(left)
    return FamilyGraph(people, parents, children, partners)


def _distances(
    reverse: Mapping[PersonID, set[PersonID]],
    target: PersonID,
) -> Mapping[PersonID, int]:
    """Measure minimum remaining distance without following family cycles."""
    distances = {target: 0}
    pending = deque([target])
    while pending:
        node = pending.popleft()
        for neighbor in reverse[node]:
            if neighbor not in distances:
                distances[neighbor] = distances[node] + 1
                pending.append(neighbor)
    return distances


def _routes(
    adjacency: Mapping[PersonID, set[PersonID]],
    reverse: Mapping[PersonID, set[PersonID]],
    start: PersonID,
    target: PersonID,
    *,
    shortest: bool,
) -> RouteResult:
    """Prune unreachable nodes, then enumerate simple paths with hard limits."""
    distances = _distances(reverse, target)
    if start not in distances:
        return RouteResult((), truncated=False)
    stack: list[tuple[PersonID, ...]] = [(start,)]
    result: list[tuple[PersonID, ...]] = []
    truncated = False
    expansions = 0
    while stack:
        route = stack.pop()
        node = route[-1]
        if node == target:
            if len(result) == MAX_PATHS:
                return RouteResult(tuple(result), truncated=True)
            result.append(route)
            continue
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return RouteResult(tuple(result), truncated=True)
        if len(route) - 1 + distances[node] > MAX_DEPTH:
            truncated = True
            continue
        for neighbor in sorted(adjacency[node], reverse=True):
            if neighbor in route or neighbor not in distances:
                continue
            if shortest and distances[neighbor] != distances[node] - 1:
                continue
            stack.append((*route, neighbor))
    return RouteResult(
        tuple(sorted(result, key=lambda route: (len(route), route))), truncated
    )


def relationship_paths(
    path: Path, from_id: PersonID, to_id: PersonID
) -> RelationshipPaths | None:
    """Find recorded ancestry routes first, otherwise shortest family routes."""
    with connect(path) as db:
        graph = _graph(db)
    if from_id not in graph.people or to_id not in graph.people:
        return None
    strategy: Literal["ancestry", "shortest", "same", "none"] = "same"
    result = RouteResult(((from_id,),), truncated=False)
    if from_id != to_id:
        strategy = "ancestry"
        result = _routes(graph.parents, graph.children, from_id, to_id, shortest=False)
        if not result.paths and not result.truncated:
            result = _routes(
                graph.children, graph.parents, from_id, to_id, shortest=False
            )
        if not result.paths and not result.truncated:
            neighbors = {
                person: graph.parents[person]
                | graph.children[person]
                | graph.partners[person]
                for person in graph.people
            }
            result = _routes(neighbors, neighbors, from_id, to_id, shortest=True)
            strategy = "shortest" if result.paths or result.truncated else "none"
    return RelationshipPaths(
        from_person=graph.people[from_id],
        to_person=graph.people[to_id],
        paths=tuple(
            RelationshipPath(
                people=tuple(graph.people[person] for person in route),
                steps=tuple(
                    "parent"
                    if right in graph.parents[left]
                    else "child"
                    if right in graph.children[left]
                    else "partner"
                    for left, right in pairwise(route)
                ),
            )
            for route in result.paths
        ),
        truncated=result.truncated,
        strategy=strategy,
    )


def _person_gaps(db: sqlite3.Connection, person: Person) -> PersonGaps:
    """Keep missing vital fields separate from substitutes and uncited events."""
    missing: list[Missing] = []
    family_row = db.execute(PARENT_FAMILY, (person.id,)).fetchone()
    family = ParentFamily.model_validate(dict(family_row)) if family_row else None
    parent_slots: tuple[tuple[Missing, PersonID | None], ...] = (
        ("father", family.father if family else None),
        ("mother", family.mother if family else None),
    )
    for role, parent_id in parent_slots:
        if not parent_id or not db.execute(CARD_PERSON, (parent_id,)).fetchone():
            missing.append(role)
    vital_rows = tuple(db.execute(FACTS, (0, person.id)))
    vital_fields: tuple[tuple[int, Missing, Missing], ...] = (
        (1, "birth_date", "birth_place"),
        (DEATH, "death_date", "death_place"),
    )
    for event_type, date_key, place_key in vital_fields:
        if event_type == DEATH and person.living is not False:
            continue
        events = tuple(row for row in vital_rows if row["type"] == event_type)
        if not any(display_date(row["date"]) for row in events):
            missing.append(date_key)
        if not any(
            (row["place"] or "").strip() or (row["place_detail"] or "").strip()
            for row in events
        ):
            missing.append(place_key)
    card = facts(db, 0, person.id)
    return PersonGaps(
        person=person,
        missing=tuple(missing),
        uncited_events=tuple(
            UncitedEvent(id=row["id"], kind=row["kind"], date=display_date(row["date"]))
            for row in db.execute(UNCITED, {"id": person.id})
        ),
        birth_fallback=card.birth
        if card.birth and card.birth.label != "Born"
        else None,
        death_fallback=card.death
        if card.death and card.death.label != "Died"
        else None,
    )


def research_gaps(path: Path, person_ids: tuple[PersonID, ...]) -> ResearchGaps:
    """Read gaps only for requested, existing people in a consistent snapshot."""
    with connect(path) as db:
        return ResearchGaps(
            people=tuple(
                _person_gaps(db, Person.model_validate(dict(row)))
                for person_id in dict.fromkeys(person_ids)
                if (row := db.execute(CARD_PERSON, (person_id,)).fetchone()) is not None
            )
        )
