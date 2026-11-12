"""Keep oversized charts and repeated exports from tying up the reader."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from math import ceil
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException

from reader_settings import site_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pedigree_store import Fact, Pedigree, PedigreeNode, PersonCard
    from reader_accounts import AccountID

PDF_BODY_LIMIT: Final = 4 * 1024 * 1024
PDF_CARD_LIMIT: Final = 4096
PDF_DEPTH_LIMIT: Final = 32
PDF_TEXT_LIMIT: Final = 4096
PDF_WINDOW: Final = 15
PDF_SIMULTANEOUS: Final = 2
PDF_LOCK: Final = Lock()
PDF_RECENT: Final[dict[AccountID, deque[float]]] = {}
PDF_RUNNING: Final[set[AccountID | None]] = set()


def check_text(*values: str | None) -> None:
    if any(value is not None and len(value) > PDF_TEXT_LIMIT for value in values):
        raise HTTPException(
            status_code=422, detail="A pedigree card has too much text to print."
        )


def check_fact(fact: Fact | None) -> None:
    if fact is not None:
        check_text(fact.label, fact.date, fact.place)


def check_card(person: PersonCard) -> None:
    check_text(person.name, person.age_at_death)
    check_fact(person.birth)
    check_fact(person.death)


def validate_print(chart: Pedigree, partner_roots: tuple[PedigreeNode, ...]) -> None:
    """Bound the chart traversal and text handled by the PDF layout."""
    relatives = (
        *chart.children,
        *chart.partners,
        *(sibling.person for sibling in chart.siblings),
    )
    count = len(relatives)
    for family in chart.child_families:
        count += len(family.children) + int(family.partner is not None)
        if count > PDF_CARD_LIMIT:
            raise HTTPException(
                status_code=422, detail="Collapse branches before printing this chart."
            )
        for child in family.children:
            check_card(child)
        if family.partner is not None:
            check_card(family.partner)
    for relative in relatives:
        check_card(relative)
    pending = [(node, 1) for node in (chart.root, *partner_roots)]
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > PDF_CARD_LIMIT or depth > PDF_DEPTH_LIMIT or len(node.parents) > 2:
            raise HTTPException(
                status_code=422, detail="Collapse branches before printing this chart."
            )
        check_card(node.person)
        check_fact(node.marriage)
        pending.extend((parent, depth + 1) for parent in node.parents)
    for facts in chart.partner_marriages.values():
        count += len(facts)
        if count > PDF_CARD_LIMIT:
            raise HTTPException(
                status_code=422, detail="Collapse branches before printing this chart."
            )
        for fact in facts:
            check_fact(fact)


def busy(seconds: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="PDF export is busy. Try again shortly.",
        headers={"Retry-After": str(seconds)},
    )


@contextmanager
def print_slot(account: AccountID | None) -> Iterator[None]:
    """Allow one export per account at a time and two across the reader."""
    per_account = site_settings().security.pdf_exports if account is not None else 0
    with PDF_LOCK:
        now = monotonic()
        for reader, recent in tuple(PDF_RECENT.items()):
            while recent and recent[0] <= now - PDF_WINDOW:
                recent.popleft()
            if not recent:
                del PDF_RECENT[reader]
        if account in PDF_RUNNING or len(PDF_RUNNING) >= PDF_SIMULTANEOUS:
            raise busy(2)
        if account is not None:
            recent = PDF_RECENT.setdefault(account, deque())
            if len(recent) >= per_account:
                raise busy(max(1, ceil(PDF_WINDOW - (now - recent[0]))))
            recent.append(now)
        PDF_RUNNING.add(account)
    try:
        yield
    finally:
        with PDF_LOCK:
            PDF_RUNNING.remove(account)
