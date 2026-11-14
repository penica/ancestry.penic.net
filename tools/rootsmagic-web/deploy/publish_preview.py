"""Read-only comparison of RootsMagic snapshots for the Publish preview."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
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
    identities: dict[str, str | None]
    links: dict[str, dict[str, int]]


class IdentityChange(TypedDict):
    person: int
    kind: Literal["reused", "removed", "renumbered"]
    replacement: int | None
    links: dict[str, int]


class PublishPreview(TypedDict):
    snapshot: str
    status: Literal["new", "changed", "same"]
    records: list[RecordChanges]
    new_media: list[str]
    updated_media: list[str]
    reused: int
    unavailable: list[str]
    identities: list[IdentityChange]
    plan: str


def blob_digest(data: bytes) -> dict[str, str]:
    return {"blob_sha256": sha256(data).hexdigest()}


def person_identities(path: Path) -> dict[str, str | None]:
    with closing(
        sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        return {
            str(person): identity
            for person, identity in db.execute(
                "SELECT PersonID, nullif(lower(trim(UniqueID)), '') FROM PersonTable"
            )
        }


def potential_link_changes(root: Path, name: str) -> dict[str, dict[str, int]]:
    configuration = root / "state" / "publisher.json"
    settings = (
        json.loads(configuration.read_text()).get(name, {})
        if configuration.is_file()
        else {}
    )
    slug = settings.get("tree_slug")
    database = settings.get("registers_database")
    sources = {
        "comments.sqlite3": {
            "comments": ("person_id", "Comments"),
            "follow_ups": ("person_id", "Follow-ups"),
            "chart_links": ("person_id", "Shared charts"),
            "saved_charts": ("person_id", "Saved charts"),
        },
        "accounts.sqlite3": {"reader_person_links": ("person_id", "My person links")},
    }
    result: dict[str, dict[str, int]] = {}
    for filename, tables in sources.items():
        path = root / "state" / filename
        if not path.is_file():
            continue
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            present = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
            for table in present & tables.keys():
                column, label = tables[table]
                for person, count in db.execute(
                    f"SELECT {column}, count(*) FROM {table} WHERE (? IS NULL OR tree_slug = ?) GROUP BY {column}",
                    (slug, slug),
                ):  # noqa: S608
                    result.setdefault(str(person), {})[label] = count
    if database and slug:
        if not re.fullmatch(r"[a-z0-9_]+", database) or not re.fullmatch(
            r"[a-z0-9-]+", slug
        ):
            raise ValueError(
                "The configured Registers database or tree slug is invalid."
            )
        query = """SELECT l.external_person_id, count(*)
                   FROM registers_ancestrypersonreference r
                   JOIN registers_externalpersonlink l ON l.id = r.link_id
                   WHERE r.tree_slug = :'tree_slug'
                   GROUP BY l.external_person_id"""
        registry = subprocess.run(
            [
                "sudo",
                "-n",
                "-u",
                "postgres",
                "psql",
                "-AtX",
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                f"tree_slug={slug}",
            ],
            input=query,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if registry.returncode:
            raise ValueError(
                "The publisher could not count the configured Registers references."
            )
        for person, count in (
            line.split("|", 1) for line in registry.stdout.splitlines()
        ):
            if person.isascii() and person.isdigit():
                result.setdefault(person, {})["Registers references"] = int(count)
    return result


def identity_changes(
    before: dict[str, str | None],
    after: dict[str, str | None],
    links: dict[str, dict[str, int]],
) -> list[IdentityChange]:
    index = {identity: person for person, identity in after.items() if identity}
    changes: list[IdentityChange] = []
    for person in sorted(before, key=int):
        if person in after and before[person] == after[person]:
            continue
        replacement = index.get(before[person])
        changes.append(
            {
                "person": int(person),
                "replacement": int(replacement) if replacement is not None else None,
                "kind": "reused"
                if person in after
                else "renumbered"
                if replacement is not None
                else "removed",
                "links": links.get(person, {}),
            }
        )
    return changes


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
    identities = (
        identity_changes(
            target["identities"], person_identities(snapshot), target["links"]
        )
        if previous and previous != checksum
        else []
    )
    plan = sha256(
        json.dumps(
            (name, checksum, previous, checks, changes, missing, identities),
            sort_keys=True,
        ).encode()
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
        "identities": identities,
        "plan": plan,
    }
