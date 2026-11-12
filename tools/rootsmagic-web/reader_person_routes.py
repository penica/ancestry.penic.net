"""Select a personal shortcut in a tree the account can open."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from reader_access import SESSION_COOKIE, read_form, require_session, verify_csrf
from reader_accounts import AccountsError
from store import NAME, PedigreeSearchPerson, connect, pedigree_search
from tree_catalog import MAX_PERSON_ID, MAX_TREE_SLUG_LENGTH, Tree

if TYPE_CHECKING:
    from jinja2 import Environment

    from reader_access import ReaderAccess

SEARCH_LIMIT: Final = 20
EXACT_PERSON: Final = f"SELECT * FROM ({NAME}) WHERE id = ?"  # noqa: S608
TreeSlug = Annotated[str, Query(max_length=MAX_TREE_SLUG_LENGTH)]


def read_tree_person(tree: Tree, person_id: int) -> PedigreeSearchPerson | None:
    """Read the exact record from the selected tree without opening it for writing."""
    with connect(tree.database) as db:
        row = db.execute(EXACT_PERSON, (person_id,)).fetchone()
    return PedigreeSearchPerson.model_validate(dict(row)) if row else None


def parse_person_id(value: str) -> int | None:
    """Read a positive SQLite record ID within its supported range."""
    if not value.isascii() or not value.isdecimal() or len(value) > 19:
        return None
    number = int(value)
    return number if 0 < number <= MAX_PERSON_ID else None


def search_people(
    tree: Tree, query: str
) -> tuple[tuple[PedigreeSearchPerson, ...], bool]:
    """Search by name, or use exact ID lookup for an entirely numeric query."""
    number = query.removeprefix("#")
    if number.isascii() and number.isdecimal():
        person_id = parse_person_id(number)
        person = read_tree_person(tree, person_id) if person_id is not None else None
        return ((person,) if person else ()), False
    results = pedigree_search(tree.database, query, SEARCH_LIMIT, 0)
    return results.people, results.total > len(results.people)


def choose_tree(trees: tuple[Tree, ...], slug: str) -> Tree | None:
    """Resolve a slug within the account's visible tree catalog."""
    if not slug:
        return next(iter(trees), None)
    tree = next((candidate for candidate in trees if candidate.slug == slug), None)
    if tree is None:
        raise HTTPException(status_code=404, detail="That tree is unavailable.")
    return tree


def person_router(access: ReaderAccess, templates: Environment) -> APIRouter:
    """Register the hosted account's personal record selection page."""
    router = APIRouter(include_in_schema=False)

    @router.get("/account/person")
    def my_person(
        request: Request,
        tree: TreeSlug = "",
        q: Annotated[str, Query(max_length=160)] = "",
        updated: Annotated[str, Query(max_length=16)] = "",
    ) -> HTMLResponse:
        access.store()
        session = require_session(request)
        trees = access.family_trees(request)
        selected = choose_tree(trees, tree)
        linked_id = session.account.person_for(selected.slug) if selected else None
        linked = (
            read_tree_person(selected, linked_id)
            if selected is not None and linked_id is not None
            else None
        )
        query = q.strip()
        matches, more = (
            search_people(selected, query) if selected and query else ((), False)
        )
        notice = ""
        if updated == "saved" and linked is not None:
            notice = "Your person was saved for this tree."
        elif updated == "cleared" and linked_id is None:
            notice = "Your person was cleared for this tree."
        return HTMLResponse(
            templates.get_template("reader-my-person.html").render(
                current_user=session.account,
                csrf_token=session.csrf_token,
                profile_page=True,
                trees=trees,
                tree=selected,
                linked_id=linked_id,
                linked=linked,
                q=query,
                matches=matches,
                more=more,
                notice=notice,
            )
        )

    @router.post("/account/person")
    async def set_my_person(request: Request, tree: TreeSlug = "") -> RedirectResponse:
        store = access.store()
        session = require_session(request)
        form = await read_form(request)
        verify_csrf(session.csrf_token, form.get("csrf_token", ""))
        selected = choose_tree(access.family_trees(request), tree)
        if selected is None:
            raise HTTPException(status_code=404, detail="That tree is unavailable.")
        value = form.get("person_id")
        person_id = None
        if value != "":
            person_id = parse_person_id(value) if value is not None else None
            if person_id is None:
                raise HTTPException(status_code=400, detail="Choose a valid person.")
            if await run_in_threadpool(read_tree_person, selected, person_id) is None:
                raise HTTPException(
                    status_code=404, detail="That person is unavailable."
                )
        try:
            await run_in_threadpool(
                store.set_person_link,
                request.cookies.get(SESSION_COOKIE),
                selected.slug,
                person_id,
            )
        except AccountsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        outcome = "saved" if person_id is not None else "cleared"
        return RedirectResponse(
            selected.url(f"/account/person?updated={outcome}"), status_code=303
        )

    return router
