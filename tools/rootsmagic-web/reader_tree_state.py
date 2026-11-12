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


def initialized(db: sqlite3.Connection) -> bool:
    """Accept our database or an untouched file left by an earlier startup."""
    app_id: int = db.execute("PRAGMA application_id").fetchone()[0]
    if app_id == APPLICATION_ID:
        return True
    if app_id or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
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


@dataclass(frozen=True, slots=True)
class TreeState:
    """Open a private reader database in one read or write transaction."""

    path: Path

    @contextmanager
    def connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
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
