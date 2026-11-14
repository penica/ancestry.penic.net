"""Recorded source links and ID-addressed media for the read-only pedigree."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from store import EVENTS, PersonID, Profile, Record, connect

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping

URL_PATTERN: Final = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
MEDIA_MAP: Final = TypeAdapter(dict[str, str])
INLINE_IMAGE_TYPES: Final = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
CITATION_EVIDENCE: Final = """
    SELECT DISTINCT c.CitationID AS id, s.Name COLLATE BINARY AS label,
        coalesce(c.Footnote, '') || ' ' || coalesce(c.ShortFootnote, '') || ' ' ||
        coalesce(c.Bibliography, '') || ' ' || coalesce(c.ActualText, '') || ' ' ||
        coalesce(c.Comments, '') || ' ' ||
        coalesce(CAST(c.Fields AS TEXT), '') || ' ' ||
        coalesce(s.ActualText, '') || ' ' || coalesce(s.Comments, '') || ' ' ||
        coalesce(CAST(s.Fields AS TEXT), '') AS text
    FROM CitationLinkTable l JOIN CitationTable c USING (CitationID)
    LEFT JOIN SourceTable s USING (SourceID)
    WHERE l.OwnerType = 2 AND l.OwnerID = ? ORDER BY c.CitationID
"""
# Imported web tags have used both citation IDs and citation-link IDs. A tag
# with two conflicting owners is deliberately not assigned to either person.
CITATION_TAGS: Final = """
    SELECT u.URL, u.Name FROM URLTable u
    LEFT JOIN CitationTable c ON c.CitationID = u.OwnerID
    LEFT JOIN CitationLinkTable l ON l.LinkID = u.OwnerID
    WHERE u.OwnerType = 4
      AND (c.CitationID IS NULL OR l.CitationID IS NULL OR c.CitationID = l.CitationID)
      AND coalesce(l.CitationID, c.CitationID) = ? ORDER BY u.LinkID
"""


class SourceLink(Record):
    """A recorded HTTP(S) URL attributed to an event citation."""

    url: str
    label: str
    citation_id: int


class StoredMedia(Record):
    """Only database fields can supply local media locations."""

    id: int
    folder: str | None
    filename: str | None
    caption: str | None
    url: str | None

    @property
    def display_filename(self) -> str:
        return (
            Path((self.filename or "").replace("\\", "/")).name or f"Media #{self.id}"
        )


class MediaItem(Record):
    """Public media metadata without exposing local filesystem paths."""

    id: int
    filename: str
    caption: str | None
    available: bool
    url: str | None
    citation_id: int | None
    is_image: bool = False


class EventEvidence(Record):
    """Sources and scans belonging to a single recorded fact."""

    sources: tuple[SourceLink, ...]
    media: tuple[MediaItem, ...]


class PersonEvidence(Record):
    """Event-ID index for the selected person's details panel."""

    person_id: PersonID
    events: dict[int, EventEvidence]


class RecordImages(Record):
    """Image galleries for the server-rendered record, without local paths."""

    profile: tuple[MediaItem, ...]
    events: dict[int, tuple[MediaItem, ...]]


def safe_url(value: str | None) -> str | None:
    """Accept navigable web URLs, without credentials or control characters."""
    if not value or any(char.isspace() or not char.isprintable() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in value
        or "\x7f" in value
    ):
        return None
    return value


@lru_cache(maxsize=8)
def media_roots(config: str) -> Mapping[Path, Path]:
    """Parse configured media prefixes, denying filesystem media on invalid input."""
    try:
        entries = MEDIA_MAP.validate_json(config, strict=True)
    except ValidationError:
        return {}

    roots: dict[Path, Path] = {}
    for source, target in entries.items():
        prefix, root = Path(source), Path(target)
        if prefix in roots or any(
            not path.is_absolute()
            or path == path.parent
            or ".." in path.parts
            or "\x00" in str(path)
            for path in (prefix, root)
        ):
            return {}
        roots[prefix] = root
    return roots


def mapped_media_path(directory: Path, filename: str, config: str) -> Path | None:
    """Resolve within the most specific configured directory, including symlinks."""
    if not directory.is_absolute() or ".." in directory.parts:
        return None
    roots = media_roots(config)
    for prefix in (directory, *directory.parents):
        if (target := roots.get(prefix)) is None:
            continue
        root = target.resolve(strict=True)
        if root == root.parent:
            return None
        resolved = (root / directory.relative_to(prefix) / filename).resolve(
            strict=True
        )
        return resolved if resolved.is_relative_to(root) else None
    return None


def recorded_path(database: Path, media: StoredMedia) -> Path | None:
    """Resolve registered paths, using only mapped media roots when configured."""
    filename = media.filename or ""
    folder = (media.folder or "").replace("\\", "/")
    if not folder and filename.startswith("/"):
        recorded = Path(filename)
        folder, filename = str(recorded.parent), recorded.name
    if (
        not filename
        or filename in (".", "..")
        or any(char in filename for char in ("/", "\\", "\x00"))
        or "\x00" in folder
    ):
        return None
    # RootsMagic's macOS root marker is stored as ?\Users\... or ?\Volumes\....
    if folder.startswith("?/"):
        folder = folder[1:]
    if folder.startswith(("?", "~", "$", "%")) or re.match(r"^[A-Za-z]:", folder):
        return None
    directory = Path(folder)
    try:
        if (config := os.environ.get("ROOTSMAGIC_MEDIA_MAP")) is not None:
            resolved = mapped_media_path(directory, filename, config)
        else:
            local = (
                directory if directory.is_absolute() else database.parent / directory
            )
            resolved = (local / filename).resolve(strict=True)
        if resolved is not None and resolved.is_file() and os.access(resolved, os.R_OK):
            return resolved
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def media_file(path: Path, media_id: int) -> Path | None:
    """Look up a file by database media ID; never accept an HTTP-supplied path."""
    with connect(path) as db:
        row = db.execute(
            """SELECT MediaID AS id, MediaPath AS folder, MediaFile AS filename,
                      Caption AS caption, URL AS url
               FROM MultimediaTable WHERE MediaID = ?""",
            (media_id,),
        ).fetchone()
        if row is None:
            return None
        return recorded_path(path, StoredMedia.model_validate(dict(row)))


def linked_media_rows(
    db: sqlite3.Connection, owner_type: int, owner_id: int
) -> sqlite3.Cursor:
    """Select one owner's media and exactly its linked citation media."""
    return db.execute(
        """SELECT DISTINCT m.MediaID AS id, m.MediaPath AS folder,
                  m.MediaFile COLLATE BINARY AS filename,
                  m.Caption COLLATE BINARY AS caption, m.URL AS url,
                  CASE WHEN l.OwnerType = 4 THEN l.OwnerID END AS citation_id
           FROM MediaLinkTable l JOIN MultimediaTable m USING (MediaID)
           WHERE (l.OwnerType = :type AND l.OwnerID = :id)
              OR (l.OwnerType = 4 AND l.OwnerID IN (
                  SELECT CitationID FROM CitationLinkTable
                  WHERE OwnerType = :type AND OwnerID = :id))
           ORDER BY m.MediaID, citation_id""",
        {"type": owner_type, "id": owner_id},
    )


def event_media(
    db: sqlite3.Connection, path: Path, event_id: int
) -> tuple[MediaItem, ...]:
    """Include direct fact media and media from exactly its linked citations."""
    return tuple(
        MediaItem(
            id=media.id,
            filename=media.display_filename,
            caption=media.caption,
            available=resolved is not None,
            url=safe_url(media.url),
            citation_id=row["citation_id"],
            is_image=resolved is not None
            and resolved.suffix.lower() in INLINE_IMAGE_TYPES,
        )
        for row in linked_media_rows(db, 2, event_id)
        for media in (StoredMedia.model_validate(dict(row)),)
        for resolved in (recorded_path(path, media),)
    )


def owner_images(
    db: sqlite3.Connection, path: Path, owners: tuple[tuple[int, int], ...]
) -> tuple[MediaItem, ...]:
    """Deduplicate image links within one gallery and retain unavailable scans."""
    images: dict[int, MediaItem] = {}
    for owner_type, owner_id in owners:
        for row in linked_media_rows(db, owner_type, owner_id):
            media = StoredMedia.model_validate(dict(row))
            if media.id in images:
                continue
            resolved = recorded_path(path, media)
            extension = (resolved or Path(media.filename or "")).suffix.lower()
            if extension not in INLINE_IMAGE_TYPES:
                continue
            images[media.id] = MediaItem(
                id=media.id,
                filename=media.display_filename,
                caption=media.caption,
                available=resolved is not None,
                url=safe_url(media.url),
                citation_id=row["citation_id"],
                is_image=resolved is not None,
            )
    return tuple(images.values())


def record_images(db: sqlite3.Connection, path: Path, record: Profile) -> RecordImages:
    """Read image links from the same snapshot as the selected full record."""
    families = db.execute(
        "SELECT FamilyID FROM FamilyTable WHERE FatherID = :id OR MotherID = :id "
        "ORDER BY FamilyID",
        {"id": record.id},
    )
    owners = (
        ((0, int(record.id)),)
        + tuple((7, name.id) for name in record.names)
        + tuple((1, int(row["FamilyID"])) for row in families)
    )
    events = {
        event.id: owner_images(db, path, ((2, event.id),)) for event in record.events
    }
    fact_image_ids = {image.id for images in events.values() for image in images}
    profile = owner_images(db, path, owners)
    return RecordImages(
        profile=tuple(image for image in profile if image.id not in fact_image_ids),
        events=events,
    )


def event_sources(db: sqlite3.Connection, event_id: int) -> tuple[SourceLink, ...]:
    """Extract links from citation/source fields and unambiguous recorded web tags."""
    links: dict[tuple[int, str], SourceLink] = {}
    for row in db.execute(CITATION_EVIDENCE, (event_id,)):
        citation_id = int(row["id"])
        label = row["label"] or f"Citation #{citation_id}"
        candidates = [
            (match.rstrip(".,;:)]}"), label)
            for match in URL_PATTERN.findall(unescape(row["text"]))
        ]
        candidates.extend(
            (tag["URL"], tag["Name"] or label)
            for tag in db.execute(CITATION_TAGS, (citation_id,))
        )
        for candidate, title in candidates:
            url = safe_url(candidate)
            if url:
                links[(citation_id, url)] = SourceLink(
                    url=url,
                    label=title,
                    citation_id=citation_id,
                )
    return tuple(links.values())


def person_evidence(path: Path, person_id: PersonID) -> PersonEvidence | None:
    """Read event evidence in one snapshot, without altering any tree or media."""
    with connect(path) as db:
        if (
            db.execute(
                "SELECT 1 FROM PersonTable WHERE PersonID = ?", (person_id,)
            ).fetchone()
            is None
        ):
            return None
        events = {
            int(row["id"]): EventEvidence(
                sources=event_sources(db, int(row["id"])),
                media=event_media(db, path, int(row["id"])),
            )
            for row in db.execute(EVENTS, {"id": person_id})
        }
        return PersonEvidence(person_id=person_id, events=events)
