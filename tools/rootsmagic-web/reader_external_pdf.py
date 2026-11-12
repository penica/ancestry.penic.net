"""Build External Reader PDFs from authorized records and family edges."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from fastapi import HTTPException

from pedigree_store import (
    Pedigree,
    PedigreeNode,
    facts,
    read_card,
    read_parent_family,
    read_pedigree,
)
from store import PersonID, connect

if TYPE_CHECKING:
    from pedigree_store import ParentFamily, PersonCard
    from reader_external import ExternalTree


def external_print_chart(
    database: ExternalTree, requested: Pedigree, partner_roots: tuple[PedigreeNode, ...]
) -> tuple[Pedigree, tuple[PedigreeNode, ...]]:
    """Accept the chart outline and source every display field from public records."""
    with connect(database) as db:
        current = read_pedigree(db, requested.root.person.id, 1)
        if current is None:
            raise HTTPException(status_code=404, detail="Person not found")

        @cache
        def card(person: PersonID) -> PersonCard | None:
            return read_card(db, person)

        @cache
        def parent_family(person: PersonID) -> ParentFamily | None:
            return read_parent_family(db, person)

        def project(
            outline: PedigreeNode, person: PersonCard, visited: frozenset[PersonID]
        ) -> PedigreeNode:
            family = parent_family(person.id)
            if family is None or not outline.parents:
                return PedigreeNode(person=person, parents=(), marriage=None)
            lineage = visited | {person.id}
            requested_parents = {parent.person.id: parent for parent in outline.parents}
            parents = tuple(
                project(requested_parents[identifier], parent, lineage)
                for identifier in family.parents
                if identifier in requested_parents
                and identifier not in lineage
                and (parent := card(identifier)) is not None
            )
            return PedigreeNode(
                person=person,
                parents=parents,
                marriage=facts(db, 1, family.id).marriage
                if len(parents) == 2
                else None,
            )

        siblings = {sibling.person.id for sibling in requested.siblings}
        outlines = {partner.person.id: partner for partner in partner_roots}
        return (
            current.model_copy(
                update={
                    "root": project(requested.root, current.root.person, frozenset()),
                    "siblings": tuple(
                        sibling
                        for sibling in current.siblings
                        if sibling.person.id in siblings
                    ),
                }
            ),
            tuple(
                project(outlines[partner.id], partner, frozenset())
                for partner in current.partners
                if partner.id in outlines
            ),
        )
