"""Persistent research follow-ups scoped to a reader, tree, and person."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StringConstraints

from pedigree_research import Missing, research_gaps
from reader_tree_state import initialize, initialized
from store import PersonID, Record

if TYPE_CHECKING:
    import sqlite3

    from reader_tree_state import TreeState
    from tree_catalog import Tree

FollowUpStatus = Literal["all", "open", "complete"]
GAP_LABELS: Final[dict[Missing, str]] = {
    "father": "Father not recorded",
    "mother": "Mother not recorded",
    "birth_date": "Birth date missing",
    "birth_place": "Birth place missing",
    "death_date": "Death date missing",
    "death_place": "Death place missing",
}
CREATE_FOLLOW_UPS: Final = """
    CREATE TABLE IF NOT EXISTS follow_ups (
        id INTEGER PRIMARY KEY,
        owner INTEGER NOT NULL,
        tree_slug TEXT NOT NULL,
        person_id INTEGER NOT NULL,
        gap TEXT NOT NULL,
        label TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE (owner, tree_slug, person_id, gap)
    )
"""


class FollowUpDraft(Record):
    model_config = ConfigDict(extra="forbid")

    person_id: Annotated[PersonID, Field(ge=1, le=2**63 - 1)]
    gap: Annotated[str, StringConstraints(min_length=1, max_length=25)]


class FollowUp(Record):
    id: int
    tree_slug: str
    person_id: PersonID
    gap: str
    label: str
    completed_at: str | None


def gap_label(tree: Tree, draft: FollowUpDraft) -> str:
    """Store the description from the current RootsMagic record."""
    result = research_gaps(tree.path, (draft.person_id,))
    if not result.people:
        raise HTTPException(status_code=404, detail="Person not found.")
    person = result.people[0]
    for gap in person.missing:
        if gap == draft.gap:
            return GAP_LABELS[gap]
    for event in person.uncited_events:
        if f"event:{event.id}" == draft.gap:
            return " · ".join(
                filter(None, (f"Find a citation for {event.kind}", event.date))
            )
    raise HTTPException(
        status_code=409, detail="This research gap has changed. Refresh the worklist."
    )


def has_follow_ups(db: sqlite3.Connection) -> bool:
    return (
        initialized(db)
        and db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'follow_ups'"
        ).fetchone()
        is not None
    )


@dataclass(frozen=True, slots=True)
class FollowUps:
    state: TreeState

    def save(self, tree: Tree, owner: int, draft: FollowUpDraft) -> FollowUp:
        label = gap_label(tree, draft)
        with self.state.connect(write=True) as db:
            initialize(db)
            db.execute(CREATE_FOLLOW_UPS)
            row = db.execute(
                """INSERT INTO follow_ups (owner, tree_slug, person_id, gap, label)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (owner, tree_slug, person_id, gap) DO UPDATE SET label = follow_ups.label
                   RETURNING *""",
                (owner, tree.slug, draft.person_id, draft.gap, label),
            ).fetchone()
        return FollowUp.model_validate(dict(row))

    def statuses(
        self, tree: Tree, owner: int, people: tuple[PersonID, ...]
    ) -> dict[PersonID, dict[str, bool]]:
        if not people:
            return {}
        placeholders = ", ".join("?" for _ in people)
        statuses: dict[PersonID, dict[str, bool]] = {}
        try:
            with self.state.connect(write=False) as db:
                if not has_follow_ups(db):
                    return statuses
                for row in db.execute(
                    f"""SELECT person_id, gap, completed_at FROM follow_ups WHERE owner = ?
                         AND tree_slug = ? AND person_id IN ({placeholders})""",  # noqa: S608
                    (owner, tree.slug, *people),
                ):
                    statuses.setdefault(PersonID(row["person_id"]), {})[row["gap"]] = (
                        row["completed_at"] is not None
                    )
        except FileNotFoundError:
            pass
        return statuses

    def page(
        self,
        owner: int,
        trees: tuple[str, ...],
        person: PersonID | None,
        status: FollowUpStatus,
        limit: int,
        offset: int,
    ) -> tuple[FollowUp, ...]:
        if not trees:
            return ()
        placeholders = ", ".join("?" for _ in trees)
        condition = {
            "all": "",
            "open": "AND completed_at IS NULL",
            "complete": "AND completed_at IS NOT NULL",
        }[status]
        try:
            with self.state.connect(write=False) as db:
                if not has_follow_ups(db):
                    return ()
                rows = db.execute(
                    f"""SELECT * FROM follow_ups WHERE owner = ? AND tree_slug IN ({placeholders})
                         AND (? IS NULL OR person_id = ?) {condition}
                         ORDER BY tree_slug, person_id, id LIMIT ? OFFSET ?""",  # noqa: S608
                    (owner, *trees, person, person, limit, offset),
                )
                return tuple(FollowUp.model_validate(dict(row)) for row in rows)
        except FileNotFoundError:
            return ()

    def set_complete(self, owner: int, tree: Tree, item: int, complete: bool) -> None:
        stamp = datetime.now(UTC).isoformat(timespec="seconds") if complete else None
        try:
            with self.state.connect(write=False) as db:
                if not has_follow_ups(db):
                    raise HTTPException(status_code=404, detail="Follow-up not found.")
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail="Follow-up not found."
            ) from error
        with self.state.connect(write=True) as db:
            changed = db.execute(
                """UPDATE follow_ups SET completed_at = CASE WHEN ? IS NULL THEN NULL ELSE coalesce(completed_at, ?) END
                   WHERE id = ? AND owner = ? AND tree_slug = ?""",
                (stamp, stamp, item, owner, tree.slug),
            ).rowcount
            if not changed:
                raise HTTPException(status_code=404, detail="Follow-up not found.")
