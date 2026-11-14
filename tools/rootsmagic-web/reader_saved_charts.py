"""Private named charts owned by one reader and tied to a RootsMagic identity."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import HTTPException
from pydantic import StringConstraints

from reader_chart_links import ChartLinkDraft, ChartView, public_chart_view
from reader_tree_state import initialize, initialized
from store import PersonID, Record, connect, person_identity

if TYPE_CHECKING:
    import sqlite3

    from reader_tree_state import TreeState
    from tree_catalog import Tree

ChartName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
PAGE: Final = "/pedigree/saved-charts"
CREATE_SAVED: Final = """
    CREATE TABLE IF NOT EXISTS saved_charts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner INTEGER NOT NULL,
        tree_slug TEXT NOT NULL,
        person_id INTEGER NOT NULL,
        identity TEXT NOT NULL,
        name TEXT NOT NULL,
        view TEXT NOT NULL
    )
"""


class SavedChartDraft(ChartLinkDraft):
    name: ChartName


class SavedChartName(Record):
    name: ChartName


class SavedChart(Record):
    id: int
    tree_slug: str
    person_id: PersonID
    identity: str
    name: str
    view: ChartView

    @property
    def path(self) -> str:
        return f"{PAGE}/{self.id}"

    def reopen(self, tree: Tree) -> ChartView:
        with connect(tree.database) as db:
            identity = person_identity(db, self.person_id)
        if identity is None or identity.casefold() != self.identity.casefold():
            raise HTTPException(
                status_code=410,
                detail="The starting record is unavailable or its RootsMagic ID has changed.",
            )
        return public_chart_view(tree, self.person_id, self.view)


def has_saved_charts(db: sqlite3.Connection) -> bool:
    return (
        initialized(db)
        and db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name = 'saved_charts' AND type = 'table'"
        ).fetchone()
        is not None
    )


def from_row(row: sqlite3.Row) -> SavedChart:
    return SavedChart.model_validate(
        dict(row) | {"view": ChartView.model_validate_json(row["view"])}
    )


@dataclass(frozen=True, slots=True)
class SavedCharts:
    state: TreeState

    def save(self, tree: Tree, owner: int, draft: SavedChartDraft) -> SavedChart:
        with connect(tree.database) as db:
            identity = person_identity(db, draft.person_id)
        if identity is None:
            raise HTTPException(
                status_code=409,
                detail="The starting record has no stable RootsMagic identity.",
            )
        view = (
            public_chart_view(tree, draft.person_id, draft.view)
            if tree.external
            else draft.view
        )
        with self.state.connect(write=True) as db:
            initialize(db)
            db.execute(CREATE_SAVED)
            row = db.execute(
                """INSERT INTO saved_charts (owner, tree_slug, person_id, identity, name, view)
                   VALUES (?, ?, ?, ?, ?, ?) RETURNING *""",
                (
                    owner,
                    tree.slug,
                    draft.person_id,
                    identity,
                    draft.name,
                    view.model_dump_json(by_alias=True),
                ),
            ).fetchone()
        return from_row(row)

    def list(self, owner: int, trees: tuple[str, ...]) -> tuple[SavedChart, ...]:
        if not trees:
            return ()
        placeholders = ", ".join("?" for _ in trees)
        with suppress(FileNotFoundError), self.state.connect(write=False) as db:
            if has_saved_charts(db):
                rows = db.execute(
                    f"SELECT * FROM saved_charts WHERE owner = ? AND tree_slug IN ({placeholders}) ORDER BY name COLLATE NOCASE, id",  # noqa: S608
                    (owner, *trees),
                )
                return tuple(from_row(row) for row in rows)
        return ()

    def get(self, owner: int, chart_id: int) -> SavedChart | None:
        with suppress(FileNotFoundError), self.state.connect(write=False) as db:
            if has_saved_charts(db):
                row = db.execute(
                    "SELECT * FROM saved_charts WHERE owner = ? AND id = ?",
                    (owner, chart_id),
                ).fetchone()
                return from_row(row) if row is not None else None
        return None

    def update(self, owner: int, chart: SavedChart, name: str | None) -> None:
        params = (owner, chart.id, chart.tree_slug)
        with self.state.connect(write=True) as db:
            if name is None:
                db.execute(
                    "DELETE FROM saved_charts WHERE owner = ? AND id = ? AND tree_slug = ?",
                    params,
                )
            else:
                db.execute(
                    "UPDATE saved_charts SET name = ? WHERE owner = ? AND id = ? AND tree_slug = ?",
                    (name, *params),
                )
