"""Read-only comparison of RootsMagic snapshots for the Publish preview."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Literal, TypedDict

if TYPE_CHECKING:
    from pathlib import Path

    from publish_media import MediaChange, MediaCheck

RECORDS: Final = {
    "PersonTable": "People",
    "NameTable": "Names",
    "EventTable": "Facts",
    "FamilyTable": "Families",
    "ChildTable": "Child links",
    "SourceTable": "Sources",
    "CitationTable": "Citations",
    "CitationLinkTable": "Citation links",
    "MultimediaTable": "Media records",
    "MediaLinkTable": "Media links",
    "PlaceTable": "Places",
}

RecordIndex = dict[str, dict[str, str]]


class RecordChanges(TypedDict):
    record: str
    added: int
    updated: int
    removed: int


class TargetPreview(TypedDict):
    checksum: str | None
    records: RecordIndex
    changes: tuple[MediaChange, ...]
    unavailable: list[str]


class PublishPreview(TypedDict):
    snapshot: str
    status: Literal["new", "changed", "same"]
    records: list[RecordChanges]
    new_media: list[str]
    updated_media: list[str]
    reused: int
    unavailable: list[str]
    plan: str


def blob_digest(data: bytes) -> dict[str, str]:
    return {"blob_sha256": sha256(data).hexdigest()}


def index_snapshot(path: Path) -> RecordIndex:
    """Return row fingerprints so the comparison need not copy either tree."""
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        tables = {
            name
            for (name,) in db.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        index: RecordIndex = {}
        for table in RECORDS.keys() & tables:
            rows = db.execute(f"SELECT * FROM {table}")  # noqa: S608
            columns = tuple(column[0] for column in rows.description)
            index[table] = {
                str(row[0]): sha256(
                    json.dumps(
                        (columns, row), default=blob_digest, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                for row in rows
            }
    return index


def record_changes(current: RecordIndex, previous: RecordIndex) -> list[RecordChanges]:
    changes: list[RecordChanges] = []
    for table, label in RECORDS.items():
        before, after = previous.get(table, {}), current.get(table, {})
        added = len(after.keys() - before.keys())
        removed = len(before.keys() - after.keys())
        updated = sum(before[row] != after[row] for row in before.keys() & after.keys())
        if added or updated or removed:
            changes.append(
                {
                    "record": label,
                    "added": added,
                    "updated": updated,
                    "removed": removed,
                }
            )
    return changes


def preview_publication(
    snapshot: Path,
    name: str,
    checksum: str,
    target: TargetPreview,
    checks: tuple[MediaCheck, ...],
    unavailable: list[str],
) -> PublishPreview:
    previous = target["checksum"]
    changes = target["changes"]
    missing = unavailable + target["unavailable"]
    plan = sha256(
        json.dumps((name, checksum, previous, checks, changes, missing)).encode()
    ).hexdigest()
    return {
        "snapshot": name,
        "status": "new"
        if previous is None
        else "same"
        if checksum == previous
        else "changed",
        "records": []
        if checksum == previous
        else record_changes(index_snapshot(snapshot), target["records"]),
        "new_media": [
            checks[index].path for index, digest in changes if digest is None
        ],
        "updated_media": [
            checks[index].path for index, digest in changes if digest is not None
        ],
        "reused": len(checks) - len(changes) - len(target["unavailable"]),
        "unavailable": missing,
        "plan": plan,
    }
