"""Saved pedigree views whose links still require access to the selected tree."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from functools import cache
from hashlib import sha256
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from pedigree_store import PARTNER_FAMILIES, pedigree_details, read_parent_family
from reader_tree_state import initialize, initialized
from store import PersonID, Record, connect

if TYPE_CHECKING:
    from reader_tree_state import TreeState
    from tree_catalog import Tree

TOKEN_PATTERN: Final = r"^[A-Za-z0-9_-]{24}$"
BRANCH_PATH: Final = r"^(partner/)?[1-9][0-9]{0,15}(/[1-9][0-9]{0,15})*$"
BranchPath = Annotated[str, StringConstraints(max_length=1024, pattern=BRANCH_PATH)]
PartnerPath = Annotated[str, StringConstraints(pattern=r"^partner/[1-9][0-9]{0,15}$")]
BranchPaths = Annotated[tuple[BranchPath, ...], Field(max_length=4096)]


class ChartView(Record):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel)

    depth: Literal["3", "5", "8", "11"]
    children: bool
    siblings: bool
    gaps: bool
    scale: Annotated[float, Field(gt=0, le=2)]
    scroll_left: Annotated[float, Field(ge=0, le=2**31 - 1)]
    scroll_top: Annotated[float, Field(ge=0, le=2**31 - 1)]
    branches: Annotated[
        tuple[tuple[BranchPath, BranchPaths], ...], Field(max_length=4096)
    ]
    collapsed_partners: Annotated[tuple[PartnerPath, ...], Field(max_length=4096)]


class ChartLinkDraft(Record):
    model_config = ConfigDict(extra="forbid")

    person_id: Annotated[PersonID, Field(ge=1, le=2**53 - 1)]
    view: ChartView


class ChartLink(Record):
    tree_slug: str
    person_id: PersonID
    revision: str
    view: ChartView
    public_revision: str | None = None
    source_external: bool = False


class ChartLinkURL(Record):
    path: str


def record_revision(tree: Tree, person: PersonID) -> str | None:
    """Compare the same full person details exposed by the pedigree."""
    profile = pedigree_details(tree.database, person)
    return sha256(profile.model_dump_json().encode()).hexdigest() if profile else None


def public_record_revision(tree: Tree, person: PersonID) -> str | None:
    """Older RootsMagic files can still be shared among family members."""
    try:
        return record_revision(replace(tree, external=True), person)
    except sqlite3.DatabaseError:
        return None


def public_chart_view(
    tree: Tree, starting_person: PersonID, view: ChartView
) -> ChartView:
    """Remove stored branch paths that are absent from the authorized chart."""
    with connect(tree.database) as db:
        partners = {
            PersonID(row["id"])
            for row in db.execute(PARTNER_FAMILIES, {"person_id": starting_person})
        }

        @cache
        def public_parents(person: PersonID) -> tuple[PersonID, ...]:
            family = read_parent_family(db, person)
            return family.parents if family else ()

        @cache
        def permitted(path: str) -> bool:
            is_partner = path.startswith("partner/")
            people = tuple(
                PersonID(int(part)) for part in path.removeprefix("partner/").split("/")
            )
            root = people[0]
            if root not in partners if is_partner else root != starting_person:
                return False
            return len(set(people)) == len(people) and all(
                parent in public_parents(child)
                for child, parent in zip(people, people[1:])
            )

        return view.model_copy(
            update={
                "gaps": False,
                "branches": tuple(
                    (key, tuple(path for path in paths if permitted(path)))
                    for key, paths in view.branches
                    if permitted(key)
                ),
                "collapsed_partners": tuple(
                    key for key in view.collapsed_partners if permitted(key)
                ),
            }
        )


def read_shared_chart(tree: Tree, link: ChartLink) -> tuple[ChartLink, bool]:
    """Compare revisions from the recipient's authorized view of the starting record."""
    revision = record_revision(tree, link.person_id)
    if revision is None:
        raise HTTPException(
            status_code=410,
            detail="The starting record for this shared chart is no longer available.",
        )
    if tree.external:
        view = public_chart_view(tree, link.person_id, link.view)
        changed = link.public_revision is not None and revision != link.public_revision
        return link.model_copy(update={"view": view}), changed
    if link.source_external:
        revision = public_record_revision(tree, link.person_id)
    return link, revision != link.revision


@dataclass(frozen=True, slots=True)
class ChartLinks:
    state: TreeState

    def create(self, tree: Tree, draft: ChartLinkDraft) -> ChartLinkURL:
        revision = record_revision(tree, draft.person_id)
        if revision is None:
            raise HTTPException(
                status_code=404, detail="The starting record is unavailable."
            )
        if tree.external:
            view = public_chart_view(tree, draft.person_id, draft.view)
            public_revision = revision
        else:
            view = draft.view
            public_revision = public_record_revision(tree, draft.person_id)
        token = token_urlsafe(18)
        with self.state.connect(write=True) as db:
            initialize(db)
            db.execute("""CREATE TABLE IF NOT EXISTS chart_links (
                token TEXT PRIMARY KEY, tree_slug TEXT NOT NULL, person_id INTEGER NOT NULL,
                revision TEXT NOT NULL, view TEXT NOT NULL,
                public_revision TEXT, source_external INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(chart_links)")
            }
            if "public_revision" not in columns:
                db.execute("ALTER TABLE chart_links ADD COLUMN public_revision TEXT")
                db.execute(
                    "ALTER TABLE chart_links ADD COLUMN source_external INTEGER NOT NULL DEFAULT 0"
                )
            db.execute(
                """INSERT INTO chart_links (token, tree_slug, person_id, revision, view, public_revision, source_external)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    token,
                    tree.slug,
                    draft.person_id,
                    revision,
                    view.model_dump_json(by_alias=True),
                    public_revision,
                    tree.external,
                ),
            )
        return ChartLinkURL(path=f"/pedigree/share/{token}")

    def get(self, token: str) -> ChartLink | None:
        try:
            with self.state.connect(write=False) as db:
                if (
                    not initialized(db)
                    or not db.execute(
                        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'chart_links'"
                    ).fetchone()
                ):
                    return None
                row = db.execute(
                    "SELECT * FROM chart_links WHERE token = ?",
                    (token,),
                ).fetchone()
        except FileNotFoundError:
            return None
        if row is None:
            return None
        return ChartLink.model_validate(
            dict(row) | {"view": ChartView.model_validate_json(row["view"])}
        )
