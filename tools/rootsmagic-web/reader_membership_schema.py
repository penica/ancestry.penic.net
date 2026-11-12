"""Tree-role constraints and migration for existing reader accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import sqlite3

MEMBERSHIPS: Final = """
CREATE TABLE IF NOT EXISTS reader_memberships (
    account_id INTEGER NOT NULL REFERENCES reader_accounts(id) ON DELETE CASCADE,
    tree_slug TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('reader', 'external_reader')),
    PRIMARY KEY (account_id, tree_slug)
)
"""

INVITATIONS: Final = """
CREATE TABLE IF NOT EXISTS reader_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash BLOB NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE, tree_slug TEXT,
    role TEXT NOT NULL CHECK (role IN ('reader', 'external_reader', 'administrator')),
    expires_at INTEGER NOT NULL,
    CHECK ((role = 'administrator') = (tree_slug IS NULL))
)
"""


def has_external_role(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None and "'external_reader'" in row[0]


def migrate_roles(db: sqlite3.Connection) -> None:
    """Replace the old role constraints inside the caller's write transaction."""
    if not has_external_role(db, "reader_memberships"):
        db.execute(
            "ALTER TABLE reader_memberships RENAME TO previous_reader_memberships"
        )
        db.execute(MEMBERSHIPS)
        db.execute(
            "INSERT INTO reader_memberships SELECT account_id, tree_slug, role FROM previous_reader_memberships"
        )
        db.execute("DROP TABLE previous_reader_memberships")
    if not has_external_role(db, "reader_invitations"):
        sequence = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'reader_invitations'"
        ).fetchone()
        db.execute(
            "ALTER TABLE reader_invitations RENAME TO previous_reader_invitations"
        )
        db.execute(INVITATIONS)
        db.execute("""INSERT INTO reader_invitations
                       SELECT id, token_hash, username, tree_slug, role, expires_at
                       FROM previous_reader_invitations""")
        db.execute("DROP TABLE previous_reader_invitations")
        if sequence is not None:
            updated = db.execute(
                "UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'reader_invitations'",
                (sequence[0],),
            )
            if not updated.rowcount:
                db.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('reader_invitations', ?)",
                    (sequence[0],),
                )
