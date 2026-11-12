"""Saved pedigree views whose links still require access to the selected tree."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import HTTPException
from pydantic import ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from pedigree_store import pedigree_details
from reader_tree_state import initialize, initialized
from store import PersonID, Record

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


class ChartLinkURL(Record):
    path: str


def record_revision(tree: Tree, person: PersonID) -> str | None:
    """Compare the same full person details exposed by the pedigree."""
    profile = pedigree_details(tree.path, person)
    return sha256(profile.model_dump_json().encode()).hexdigest() if profile else None


@dataclass(frozen=True, slots=True)
class ChartLinks:
    state: TreeState

    def create(self, tree: Tree, draft: ChartLinkDraft) -> ChartLinkURL:
        revision = record_revision(tree, draft.person_id)
        if revision is None:
            raise HTTPException(
                status_code=404, detail="The starting record is unavailable."
            )
        token = token_urlsafe(18)
        with self.state.connect(write=True) as db:
            initialize(db)
            db.execute("""CREATE TABLE IF NOT EXISTS chart_links (
                token TEXT PRIMARY KEY, tree_slug TEXT NOT NULL, person_id INTEGER NOT NULL,
                revision TEXT NOT NULL, view TEXT NOT NULL
            )""")
            db.execute(
                "INSERT INTO chart_links VALUES (?, ?, ?, ?, ?)",
                (
                    token,
                    tree.slug,
                    draft.person_id,
                    revision,
                    draft.view.model_dump_json(by_alias=True),
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
                    "SELECT tree_slug, person_id, revision, view FROM chart_links WHERE token = ?",
                    (token,),
                ).fetchone()
        except FileNotFoundError:
            return None
        if row is None:
            return None
        return ChartLink.model_validate(
            dict(row) | {"view": ChartView.model_validate_json(row["view"])}
        )
