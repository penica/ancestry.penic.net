"""Plain-text person comments stored separately from RootsMagic data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated, Final, NewType

from fastapi import HTTPException
from pydantic import ConfigDict, StringConstraints, ValidationError

from reader_access import current_session, verify_csrf
from reader_tree_state import TreeState, initialize, initialized
from store import PersonID, Record, connect

if TYPE_CHECKING:
    import sqlite3

    from fastapi import Request

    from tree_catalog import Tree

CommentID = NewType("CommentID", int)
MAX_LENGTH: Final = 2000
JSON_LIMIT: Final = MAX_LENGTH * 12 + 128
LOCAL_TOKEN: Final = token_urlsafe(32)
LOOPBACK: Final = frozenset({"127.0.0.1", "::1", "localhost"})


class CommentDraft(Record):
    """The only client-supplied fields allowed when adding a comment."""

    model_config = ConfigDict(extra="forbid")
    body: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LENGTH),
    ]


class Comment(Record):
    """A stored person comment with its original author and creation time."""

    id: CommentID
    person_id: PersonID
    author: str
    body: str
    created_at: str
    edited_at: str | None = None
    can_edit: bool = False
    can_remove: bool = False


class CommentPage(Record):
    """One page of newest-first comments and an optional older-page cursor."""

    comments: tuple[Comment, ...]
    next_before: CommentID | None = None


@dataclass(frozen=True, slots=True)
class ReaderActor:
    """The stable account identity behind reader state changes."""

    id: int
    name: str
    administrator: bool

    def owns_comment(self, row: sqlite3.Row) -> bool:
        """Legacy hosted display names do not establish ownership."""
        author_id = dict(row).get("author_id")
        return author_id == self.id or (
            author_id is None and self.id == 0 and row["author"] == "Local reader"
        )


def comment_columns(db: sqlite3.Connection) -> set[str]:
    return {column["name"] for column in db.execute("PRAGMA table_info(comments)")}


def prepare_comments(db: sqlite3.Connection) -> None:
    """Upgrade existing comments under the same transaction as a write."""
    initialize(db)
    if "author_id" not in comment_columns(db):
        db.execute("ALTER TABLE comments ADD COLUMN author_id INTEGER")
        db.execute("ALTER TABLE comments ADD COLUMN edited_at TEXT")
        db.execute("ALTER TABLE comments ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")
        db.execute("CREATE INDEX comments_tree ON comments (tree_slug, id)")


def comment_record(row: sqlite3.Row, actor: ReaderActor) -> Comment:
    return Comment.model_validate(
        dict(row)
        | {"can_edit": actor.owns_comment(row), "can_remove": actor.administrator}
    )


@dataclass(frozen=True, slots=True)
class Comments:
    """Read and append comments by tree slug and RootsMagic person ID."""

    state: TreeState

    def page(
        self,
        tree: Tree,
        actor: ReaderActor,
        person_id: PersonID | None,
        limit: int,
        before: CommentID | None,
    ) -> CommentPage:
        """List one bounded page without creating an empty database on read."""
        if person_id is not None:
            require_person(tree, person_id)
        newest_id = before - 1 if before is not None else 2**63 - 1
        try:
            with self.state.connect(write=False) as db:
                if not initialized(db):
                    return CommentPage(comments=())
                active = "AND removed = 0" if "removed" in comment_columns(db) else ""
                person_filter = "AND person_id = ?" if person_id is not None else ""
                parameters: tuple[str | int, ...] = (tree.slug, newest_id)
                if person_id is not None:
                    parameters += (person_id,)
                rows = db.execute(
                    f"""SELECT * FROM comments WHERE tree_slug = ? AND id <= ?
                        {active} {person_filter} ORDER BY id DESC LIMIT ?""",  # noqa: S608
                    (*parameters, limit + 1),
                ).fetchall()
        except FileNotFoundError:
            return CommentPage(comments=())
        comments = tuple(comment_record(row, actor) for row in rows[:limit])
        return CommentPage(
            comments=comments,
            next_before=comments[-1].id if len(rows) > limit else None,
        )

    def add(
        self, tree: Tree, person_id: PersonID, author: ReaderActor, draft: CommentDraft
    ) -> Comment:
        """Append a comment and initialize a new store in the same transaction."""
        require_person(tree, person_id)
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self.state.connect(write=True) as db:
            prepare_comments(db)
            row = db.execute(
                """INSERT INTO comments (tree_slug, person_id, author, body, created_at, author_id)
                   VALUES (?, ?, ?, ?, ?, ?) RETURNING *""",
                (tree.slug, person_id, author.name, draft.body, created_at, author.id),
            ).fetchone()
        return comment_record(row, author)

    def edit(
        self,
        tree: Tree,
        person: PersonID,
        comment: CommentID,
        actor: ReaderActor,
        draft: CommentDraft,
    ) -> Comment:
        """Update a comment owned by the signed-in author."""
        with self.state.connect(write=True) as db:
            row = editable_comment(db, tree, person, comment)
            if not actor.owns_comment(row):
                raise HTTPException(
                    status_code=403, detail="Only the author can edit this comment."
                )
            edited = db.execute(
                "UPDATE comments SET body = ?, edited_at = ? WHERE id = ? RETURNING *",
                (draft.body, datetime.now(UTC).isoformat(timespec="seconds"), comment),
            ).fetchone()
        return comment_record(edited, actor)

    def remove(
        self, tree: Tree, person: PersonID, comment: CommentID, actor: ReaderActor
    ) -> None:
        """Remove comment text while reserving the ID for existing links and cursors."""
        if not actor.administrator:
            raise HTTPException(
                status_code=403, detail="Administrator access is required."
            )
        with self.state.connect(write=True) as db:
            editable_comment(db, tree, person, comment)
            db.execute(
                "UPDATE comments SET body = '', removed = 1 WHERE id = ?", (comment,)
            )


def editable_comment(
    db: sqlite3.Connection, tree: Tree, person: PersonID, comment: CommentID
) -> sqlite3.Row:
    prepare_comments(db)
    row = db.execute(
        """SELECT * FROM comments
           WHERE id = ? AND tree_slug = ? AND person_id = ? AND removed = 0""",
        (comment, tree.slug, person),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Comment not found.")
    return row


def require_person(tree: Tree, person_id: PersonID) -> None:
    """Verify the person exists in the chosen read-only RootsMagic snapshot."""
    with connect(tree.path) as db:
        if (
            db.execute(
                "SELECT 1 FROM PersonTable WHERE PersonID = ?", (person_id,)
            ).fetchone()
            is None
        ):
            raise HTTPException(status_code=404, detail="Person not found")


def comments_token(request: Request) -> str | None:
    """Expose the CSRF token to a hosted session or a loopback browser only."""
    if session := current_session(request):
        return session.csrf_token
    if (
        request.client is not None
        and request.client.host in LOOPBACK
        and request.url.hostname in LOOPBACK
    ):
        return LOCAL_TOKEN
    return None


def require_comments(request: Request) -> str:
    """Reject access to the local feature through any network-facing listener."""
    token = comments_token(request)
    if token is None:
        raise HTTPException(status_code=404, detail="Comments are unavailable here.")
    return token


def reader_actor(request: Request) -> ReaderActor:
    """Return the signed-in or local reader identity."""
    require_comments(request)
    if session := current_session(request):
        account = session.account
        return ReaderActor(
            account.id, account.display_name or account.username, account.administrator
        )
    return ReaderActor(0, "Local reader", True)


def mutation_actor(request: Request) -> ReaderActor:
    """Authorize a same-origin state change by the signed-in or local reader."""
    tokens = request.headers.getlist("X-CSRF-Token")
    return verify_reader_mutation(request, tokens[0] if len(tokens) == 1 else "")


def verify_reader_mutation(request: Request, token: str) -> ReaderActor:
    """Validate an explicit token from either a JSON request or a form."""
    verify_csrf(require_comments(request), token)
    actor = reader_actor(request)
    if actor.id:
        return actor
    origin = f"{request.url.scheme}://{request.url.netloc}"
    origins = request.headers.getlist("Origin")
    if origins != [origin] and not (
        origins in ([], ["null"])
        and request.headers.getlist("Sec-Fetch-Site") == ["same-origin"]
    ):
        raise HTTPException(
            status_code=403, detail="Open the local reader and try again."
        )
    return actor


async def read_comment(request: Request) -> CommentDraft:
    """Read a bounded JSON body before parsing it as a plain-text comment."""
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise HTTPException(status_code=415, detail="Send the comment as JSON.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > JSON_LIMIT:
            raise HTTPException(status_code=413, detail="The comment is too large.")
        data.extend(chunk)
    try:
        draft = CommentDraft.model_validate_json(data, strict=True)
    except ValidationError as error:
        message = f"Enter a comment of 1 to {MAX_LENGTH} characters."
        raise HTTPException(status_code=422, detail=message) from error
    if "\0" in draft.body:
        raise HTTPException(status_code=422, detail="Enter the comment as plain text.")
    return draft
