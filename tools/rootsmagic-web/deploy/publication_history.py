"""Durable journal for one published tree and its changed NAS assets."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, TypedDict

from publish_media import MediaCheck, MediaError, check_mount, digest, target_path

if TYPE_CHECKING:
    from publish_media import StagedMedia


class MediaVersion(TypedDict):
    path: str
    before: str | None
    after: str | None


class Publication(TypedDict):
    id: str
    name: str
    before: str | None
    after: str
    nas: str | None
    media: list[MediaVersion]
    retained: dict[str, str]
    rollback_of: str | None


PENDING = ".publication-pending.json"
READ_LOCK = ".publish-read.lock"


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path: Path, mode: int) -> None:
    if path.is_dir():
        return
    ensure_directory(path.parent, mode)
    path.mkdir(mode=mode, exist_ok=True)
    sync_directory(path.parent)


def atomic_copy(source: Path, destination: Path, checksum: str, mode: int) -> None:
    ensure_directory(destination.parent, 0o755 if mode == 0o644 else 0o700)
    with NamedTemporaryFile(
        dir=destination.parent, prefix=".publication-", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
            if digest(temporary) != checksum:
                raise MediaError(
                    f"Publication checksum changed for {destination.name}."
                )
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)


def write_journal(path: Path, entry: Publication) -> None:
    ensure_directory(path.parent, 0o700)
    with NamedTemporaryFile(dir=path.parent, prefix=".journal-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(json.dumps(entry, ensure_ascii=False).encode())
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def journal(root: Path, entry_id: str) -> Path:
    return root / "backups" / "publications" / f"{entry_id}.json"


def tree_blob(root: Path, version: str) -> Path:
    return root / "backups" / "tree-versions" / f"{version}.rmtree"


def media_blob(nas: Path, version: str) -> Path:
    directory = nas / "RootsMagicReader" / "PublicationMedia"
    if directory.resolve() != nas.resolve() / directory.relative_to(nas):
        raise MediaError("The NAS publication archive contains a symbolic link.")
    return directory / version


def checksum_at(path: Path) -> str | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise MediaError(f"An unexpected file is in the publication path {path.name}.")
    return digest(path) if path.exists() else None


def archive(source: Path, stored: Path, version: str) -> None:
    known = checksum_at(stored)
    if known is None:
        atomic_copy(source, stored, version, 0o400)
    elif known != version:
        raise MediaError(
            f"An archived publication version failed verification: {stored.name}."
        )


def prepare_media(media: StagedMedia) -> list[MediaVersion]:
    from publish_media import media_plan

    if media_plan(media.root, media.checks) != media.changes:
        raise MediaError("The NAS media changed after the preview. Open a new preview.")
    versions: list[MediaVersion] = []
    for index, before in media.changes:
        check = media.checks[index]
        if check.digest is None:
            raise MediaError("A changed media file has no checksum.")
        if before:
            archive(
                target_path(media.root, check), media_blob(media.root, before), before
            )
        archive(
            media.directory / str(index),
            media_blob(media.root, check.digest),
            check.digest,
        )
        versions.append({"path": check.path, "before": before, "after": check.digest})
    return versions


def retained_media(media: StagedMedia) -> dict[str, str]:
    """Fingerprint the unchanged managed files that the next tree will use."""
    changed = {index for index, _ in media.changes}
    return {
        check.path: check.digest
        for index, check in enumerate(media.checks)
        if check.digest is not None and index not in changed
    }


def expected_tree(root: Path, entry: Publication) -> Path:
    return root / "trees" / entry["name"]


def media_destinations(entry: Publication) -> list[tuple[Path, Path, MediaVersion]]:
    if not entry["media"]:
        return []
    if not entry["nas"]:
        raise MediaError("The publication has no recorded NAS path.")
    nas = Path(entry["nas"])
    check_mount(nas)
    return [
        (
            nas,
            target_path(
                nas,
                MediaCheck(version["path"], version["after"] or version["before"], 0),
            ),
            version,
        )
        for version in entry["media"]
    ]


def verify_versions(root: Path, entry: Publication, *, recovering: bool) -> None:
    current = checksum_at(expected_tree(root, entry))
    allowed = (entry["before"], entry["after"]) if recovering else (entry["before"],)
    if current not in allowed:
        raise MediaError("The active tree changed since the publication preview.")
    if checksum_at(tree_blob(root, entry["after"])) != entry["after"]:
        raise MediaError("The archived tree failed checksum verification.")
    for nas, destination, version in media_destinations(entry):
        before, after = version["before"], version["after"]
        current = checksum_at(destination)
        if current != before and not (recovering and current == after):
            raise MediaError(
                f"The NAS media changed since this publication: {version['path']}."
            )
        if after and checksum_at(media_blob(nas, after)) != after:
            raise MediaError(
                f"The archived media failed checksum verification: {version['path']}."
            )
    if entry.get("retained") and entry["nas"]:
        nas = Path(entry["nas"])
        check_mount(nas)
        for path, checksum in entry["retained"].items():
            destination = target_path(nas, MediaCheck(path, checksum, 0))
            if checksum_at(destination) != checksum:
                raise MediaError(
                    f"The NAS media changed since this publication: {path}."
                )


def finish(root: Path, entry: Publication) -> None:
    """Under the publisher lock, replay the prepared transaction idempotently."""
    with (root / "incoming" / READ_LOCK).open("ab") as reader_lock:
        fcntl.flock(reader_lock, fcntl.LOCK_EX)
        finish_locked(root, entry)


def finish_locked(root: Path, entry: Publication) -> None:
    verify_versions(root, entry, recovering=True)
    for nas, destination, version in media_destinations(entry):
        after = version["after"]
        if checksum_at(destination) == after:
            continue
        if after:
            atomic_copy(media_blob(nas, after), destination, after, 0o644)
        else:
            destination.unlink()
            sync_directory(destination.parent)
    destination = expected_tree(root, entry)
    after = entry["after"]
    if checksum_at(destination) != after:
        atomic_copy(tree_blob(root, after), destination, after, 0o400)
    write_journal(journal(root, entry["id"]), entry)
    pending = root / "incoming" / PENDING
    pending.unlink()
    sync_directory(pending.parent)


def activate(root: Path, entry: Publication) -> None:
    verify_versions(root, entry, recovering=False)
    write_journal(root / "incoming" / PENDING, entry)
    finish(root, entry)


def recover(root: Path) -> None:
    pending = root / "incoming" / PENDING
    if pending.is_file():
        finish(root, json.loads(pending.read_text()))
