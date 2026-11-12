"""Plain-text person comments stored separately from RootsMagic data."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated, Final, NewType

from fastapi import HTTPException
from pydantic import ConfigDict, StringConstraints, ValidationError

from reader_access import current_session, verify_csrf
from store import Record, connect

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import Request

    from store import PersonID
    from tree_catalog import Tree

CommentID = NewType("CommentID", int)
APPLICATION_ID: Final = 0x524D434D
MAX_LENGTH: Final = 2000
JSON_LIMIT: Final = MAX_LENGTH * 12 + 128
LOCAL_TOKEN: Final = token_urlsafe(32)
LOOPBACK: Final = frozenset({"127.0.0.1", "::1", "localhost"})
CREATE_COMMENTS: Final = """
    CREATE TABLE comments (
        id INTEGER PRIMARY KEY,
        tree_slug TEXT NOT NULL,
        person_id INTEGER NOT NULL,
        author TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
"""


class CommentStorageError(RuntimeError):
    """The configured database is not safe to use as a comment store."""


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
    author: str
    body: str
    created_at: str


class CommentPage(Record):
    """One page of newest-first comments and an optional older-page cursor."""

    comments: tuple[Comment, ...]
    next_before: CommentID | None = None


def comments_path(accounts_path: Path | None) -> Path:
    """Keep hosted comments beside account state and local comments in app data."""
    if configured := os.environ.get("ROOTSMAGIC_COMMENTS_DB"):
        return Path(configured).expanduser().absolute()
    if accounts_path is not None:
        return accounts_path.with_name("comments.sqlite3").expanduser().absolute()
    if sys.platform == "darwin":
        data = Path.home() / "Library" / "Application Support" / "RootsMagic Reader"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        data = (Path(xdg_data) if xdg_data else Path.home() / ".local" / "share") / (
            "rootsmagic-reader"
        )
    return data / "comments.sqlite3"


def _initialized(db: sqlite3.Connection) -> bool:
    """Accept our database or an untouched file left by an earlier startup."""
    app_id: int = db.execute("PRAGMA application_id").fetchone()[0]
    if app_id == APPLICATION_ID:
        return True
    if app_id or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
        message = "Choose a separate database for reader comments."
        raise CommentStorageError(message)
    return False


@dataclass(frozen=True, slots=True)
class Comments:
    """Read and append comments by tree slug and RootsMagic person ID."""

    path: Path

    @contextmanager
    def _connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if self.path.suffix.lower() == ".rmtree":
            message = "Choose a separate database for reader comments."
            raise CommentStorageError(message)
        try:
            if write:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with suppress(FileExistsError):
                    os.close(
                        os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    )
            mode = self.path.lstat().st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                message = "The comments database must be a private regular file."
                raise CommentStorageError(message)
            access = "rw" if write else "ro"
            uri = f"{self.path.absolute().as_uri()}?mode={access}"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db, db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
        except FileNotFoundError:
            if not write:
                raise
            message = "Comments are temporarily unavailable."
            raise CommentStorageError(message) from None
        except (OSError, sqlite3.Error) as error:
            message = "Comments are temporarily unavailable."
            raise CommentStorageError(message) from error

    def page(
        self, tree: Tree, person_id: PersonID, limit: int, before: CommentID | None
    ) -> CommentPage:
        """List one bounded page without creating an empty database on read."""
        require_person(tree, person_id)
        newest_id = before - 1 if before is not None else 2**63 - 1
        try:
            with self._connect(write=False) as db:
                if not _initialized(db):
                    return CommentPage(comments=())
                rows = db.execute(
                    """SELECT id, author, body, created_at FROM comments
                       WHERE tree_slug = ? AND person_id = ? AND id <= ?
                       ORDER BY id DESC LIMIT ?""",
                    (tree.slug, person_id, newest_id, limit + 1),
                ).fetchall()
        except FileNotFoundError:
            return CommentPage(comments=())
        comments = tuple(Comment.model_validate(dict(row)) for row in rows[:limit])
        return CommentPage(
            comments=comments,
            next_before=comments[-1].id if len(rows) > limit else None,
        )

    def add(
        self, tree: Tree, person_id: PersonID, author: str, draft: CommentDraft
    ) -> Comment:
        """Append a comment and initialize a new store in the same transaction."""
        require_person(tree, person_id)
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect(write=True) as db:
            if not _initialized(db):
                db.execute(CREATE_COMMENTS)
                db.execute(
                    "CREATE INDEX comments_person ON comments (tree_slug, person_id, id)"
                )
                db.execute(f"PRAGMA application_id = {APPLICATION_ID}")  # noqa: S608
            row = db.execute(
                """INSERT INTO comments (tree_slug, person_id, author, body, created_at)
                   VALUES (?, ?, ?, ?, ?) RETURNING id, author, body, created_at""",
                (tree.slug, person_id, author, draft.body, created_at),
            ).fetchone()
        return Comment.model_validate(dict(row))


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


def comment_author(request: Request) -> str:
    """Authorize a write and attribute it to the signed-in or local reader."""
    expected = require_comments(request)
    tokens = request.headers.getlist("X-CSRF-Token")
    verify_csrf(expected, tokens[0] if len(tokens) == 1 else "")
    if session := current_session(request):
        return session.account.display_name or session.account.username
    origin = f"{request.url.scheme}://{request.url.netloc}"
    origins = request.headers.getlist("Origin")
    if origins != [origin] and not (
        origins in ([], ["null"])
        and request.headers.getlist("Sec-Fetch-Site") == ["same-origin"]
    ):
        raise HTTPException(
            status_code=403, detail="Open the local reader and try again."
        )
    return "Local reader"


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
