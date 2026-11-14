"""Private SQLite state for comments and research follow-ups."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Iterator

APPLICATION_ID: Final = 0x524D434D
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
    """The configured database is not safe to use for reader state."""


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


def initialized(db: sqlite3.Connection, *, attached: bool = False) -> bool:
    """Accept our database or an untouched file left by an earlier startup."""
    schema = "tree_state" if attached else "main"
    app_id: int = db.execute(f"PRAGMA {schema}.application_id").fetchone()[0]
    if app_id == APPLICATION_ID:
        return True
    if app_id or db.execute(f"SELECT 1 FROM {schema}.sqlite_schema LIMIT 1").fetchone():  # noqa: S608
        message = "Choose a separate database for reader comments."
        raise CommentStorageError(message)
    return False


def initialize(db: sqlite3.Connection) -> None:
    """Initialize a private store using the existing comments database format."""
    if initialized(db):
        return
    db.execute(CREATE_COMMENTS)
    db.execute("CREATE INDEX comments_person ON comments (tree_slug, person_id, id)")
    db.execute(f"PRAGMA application_id = {APPLICATION_ID}")  # noqa: S608


def erase_reader_data(db: sqlite3.Connection, owner: int) -> None:
    """Erase data in the attached store as part of the account deletion transaction."""
    if not initialized(db, attached=True):
        db.execute(CREATE_COMMENTS.replace("TABLE comments", "TABLE tree_state.comments"))
        db.execute(
            "CREATE INDEX tree_state.comments_person ON comments (tree_slug, person_id, id)"
        )
        db.execute(f"PRAGMA tree_state.application_id = {APPLICATION_ID}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS tree_state.deleted_readers (owner INTEGER PRIMARY KEY)"
    )
    db.execute("INSERT OR IGNORE INTO tree_state.deleted_readers VALUES (?)", (owner,))
    db.execute("PRAGMA tree_state.secure_delete = ON")
    for table in ("follow_ups", "saved_charts", "chart_links", "comments"):
        columns = {
            column["name"]
            for column in db.execute(f"PRAGMA tree_state.table_info({table})")
        }
        if table == "comments" and "author_id" in columns:
            db.execute(
                """UPDATE tree_state.comments SET author = 'Former member', author_id = NULL
                   WHERE author_id = ?""",
                (owner,),
            )
        elif "owner" in columns:
            db.execute(f"DELETE FROM tree_state.{table} WHERE owner = ?", (owner,))  # noqa: S608


def require_active_reader(db: sqlite3.Connection, owner: int) -> None:
    """Reject writes already in flight when the account was removed."""
    if (
        owner
        and db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name = 'deleted_readers'"
        ).fetchone()
        and db.execute(
            "SELECT 1 FROM deleted_readers WHERE owner = ?", (owner,)
        ).fetchone()
    ):
        raise HTTPException(
            status_code=401, detail="This reader account has been deleted."
        )


@dataclass(frozen=True, slots=True)
class TreeState:
    """Open a private reader database in one read or write transaction."""

    path: Path

    def _uri(self, *, write: bool) -> str:
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
            return f"{self.path.absolute().as_uri()}?mode={access}"
        except FileNotFoundError:
            if not write:
                raise
            raise CommentStorageError("Comments are temporarily unavailable.") from None
        except OSError as error:
            raise CommentStorageError("Comments are temporarily unavailable.") from error

    def attach(self, db: sqlite3.Connection) -> None:
        """Attach state before an atomic transaction across both private stores."""
        db.execute("ATTACH DATABASE ? AS tree_state", (self._uri(write=True),))
        for schema in ("main", "tree_state"):
            if db.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0] != "delete":
                raise CommentStorageError(
                    "Account deletion requires rollback journals on both reader databases."
                )

    @contextmanager
    def connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        uri = self._uri(write=write)
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db, db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
        except sqlite3.Error as error:
            message = "Comments are temporarily unavailable."
            raise CommentStorageError(message) from error
