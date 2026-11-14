"""Verify registered media and stage incremental transfers on the NAS."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from typing import BinaryIO

NAS_ROOT: Final = Path("/mnt/archive")
NAS_MEDIA: Final = Path("RootsMagicReader/Media")
PROJECT_MEDIA: Final = {"FamilyTrees": "family-trees", "Archive": "archive"}
BOOKS: Final = Path("Books")
CHUNK_SIZE: Final = 1024 * 1024
CACHE_LIMIT: Final = 8 * 1024 * 1024


class MediaError(RuntimeError):
    """A registered file cannot safely be published or used by the VM."""


class MediaCheck(NamedTuple):
    """One NAS path and the expected contents of a Mac-managed file, if any."""

    path: str
    digest: str | None
    size: int


class MediaChange(NamedTuple):
    """An index in the manifest and the prior NAS digest, if the file exists."""

    index: int
    previous: str | None


@dataclass(frozen=True, slots=True)
class LocalMedia:
    """A registered asset with its optional Mac source for transfer."""

    check: MediaCheck
    source: Path | None = None


def digest(path: Path) -> str:
    """Return the SHA-256 hash of a complete file."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(slots=True)
class PreviewChecksums:
    """Reuse an earlier NAS digest for the same observed file version."""

    path: Path
    previous: dict[str, list[int | str]] = field(init=False, default_factory=dict)
    checked: dict[str, list[int | str]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(self.path, flags), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    return
                raw = stream.read(CACHE_LIMIT + 1)
            if len(raw) <= CACHE_LIMIT:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self.previous = data
        except (OSError, ValueError):
            pass

    def checksum(self, path: Path, info: os.stat_result) -> str:
        stamp = file_stamp(info)
        key = os.fspath(path)
        entry = self.previous.get(key)
        if (
            isinstance(entry, list)
            and len(entry) == 6
            and entry[:5] == list(stamp)
            and isinstance(cached := entry[5], str)
            and len(cached) == 64
            and all(char in "0123456789abcdef" for char in cached)
            and os.access(path, os.R_OK)
        ):
            self.checked[key] = entry
            return cached
        with path.open("rb") as stream:
            before = file_stamp(os.fstat(stream.fileno()))
            value = hashlib.file_digest(stream, "sha256").hexdigest()
            after = file_stamp(os.fstat(stream.fileno()))
        if before != after or file_stamp(path.stat()) != after:
            raise MediaError(f"The NAS file changed during the comparison: {path}")
        self.checked[key] = [*after, value]
        return value

    def save(self) -> None:
        if self.checked == self.previous:
            return
        data = json.dumps(self.checked, separators=(",", ":")).encode()
        if len(data) > CACHE_LIMIT:
            return
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            temporary.replace(self.path)
        except OSError:
            # The preview can still proceed when its optional cache is unavailable.
            pass
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)


def registered_path(folder: str | None, filename: str) -> Path:
    """Decode an absolute recorded path without interpreting traversal."""
    folder = (folder or "").replace("\\", "/")
    if not folder and filename.startswith("/"):
        file = Path(filename)
        folder, filename = str(file.parent), file.name
    if folder.startswith("?/"):
        folder = folder[1:]
    directory = Path(folder)
    if (
        not directory.is_absolute()
        or ".." in directory.parts
        or filename in (".", "..")
        or any(char in "/\\" or ord(char) < 32 for char in filename)
        or any(ord(char) < 32 for char in folder)
    ):
        raise MediaError(f"Unsupported registered media path for {filename!r}.")
    return directory / filename


def local_media(
    db: sqlite3.Connection, projects: Path, *, unavailable: list[str] | None = None
) -> tuple[LocalMedia, ...]:
    """Read and fingerprint the linked files in the exact snapshot being sent."""
    files: dict[str, LocalMedia] = {}
    rows = db.execute(
        """SELECT m.MediaID, m.MediaPath, m.MediaFile
           FROM MultimediaTable AS m
           WHERE EXISTS (SELECT 1 FROM MediaLinkTable WHERE MediaID = m.MediaID)
           ORDER BY m.MediaID"""
    )
    roots = tuple(
        (projects / source, NAS_MEDIA / target)
        for source, target in PROJECT_MEDIA.items()
    )
    archive_roots = tuple(
        (Path("/Volumes") / mount / "FamilyArchive" / prefix, prefix)
        for mount in ("home", "home-1")
        for prefix in (BOOKS, NAS_MEDIA)
    )
    for media_id, folder, filename in rows:
        try:
            if not filename:
                if folder:
                    raise MediaError(f"Media #{media_id} has a path but no filename.")
                continue
            recorded = registered_path(folder, filename)
            for prefix, destination in (*roots, *archive_roots):
                if not recorded.is_relative_to(prefix):
                    continue
                target = (destination / recorded.relative_to(prefix)).as_posix()
                if (prefix, destination) in roots:
                    source = recorded.resolve()
                    if (
                        not source.is_relative_to(prefix.resolve())
                        or not source.is_file()
                    ):
                        raise MediaError(
                            f"Media #{media_id} is missing or outside its Mac folder: {recorded}"
                        )
                    candidate = LocalMedia(
                        MediaCheck(target, digest(source), source.stat().st_size),
                        source,
                    )
                    prior = files.get(target)
                    if prior and prior.source and prior.check != candidate.check:
                        raise MediaError(
                            f"Different source files resolve to the same NAS path: {target}"
                        )
                    files[target] = candidate
                else:
                    files.setdefault(target, LocalMedia(MediaCheck(target, None, 0)))
                break
            else:
                raise MediaError(
                    f"Media #{media_id} is outside the configured Mac and NAS folders: {recorded}"
                )
        except (MediaError, OSError) as error:
            if unavailable is None:
                raise
            unavailable.append(str(error))
    return tuple(files.values())


def check_mount(root: Path) -> None:
    """Require the production NFS mount before resolving or creating files."""
    if root == NAS_ROOT:
        # Accessing Books triggers the existing Linux automount.
        _ = (root / BOOKS).is_dir()
        entries = Path("/proc/self/mountinfo").read_text().splitlines()
        if not any(
            line.split()[4] == str(root)
            and line.split(" - ", 1)[1].split()[0] in {"nfs", "nfs4"}
            for line in entries
        ):
            raise MediaError(f"The NAS is unavailable at {root}.")


def target_path(root: Path, check: MediaCheck) -> Path:
    """Resolve a manifest entry within its authorized NAS directory."""
    relative = Path(check.path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != check.path
    ):
        raise MediaError("An invalid NAS media path was supplied.")
    bases = (
        (BOOKS, NAS_MEDIA)
        if check.digest is None
        else tuple(NAS_MEDIA / name for name in PROJECT_MEDIA.values())
    )
    base = next((path for path in bases if relative.is_relative_to(path)), None)
    if base is None or relative == base:
        raise MediaError(f"The media is outside the permitted NAS folder: {relative}")
    resolved_root = root.resolve()
    anchor = (root / base).resolve(strict=True)
    path = root / relative
    resolved_path = path.resolve()
    if not anchor.is_relative_to(resolved_root) or not resolved_path.is_relative_to(
        anchor
    ):
        raise MediaError(f"The media path leaves its NAS folder: {relative}")
    if check.digest is not None and resolved_path != resolved_root / relative:
        raise MediaError(
            f"A NAS link conflicts with the managed media path: {relative}"
        )
    return path


def media_plan(
    root: Path,
    checks: tuple[MediaCheck, ...],
    *,
    unavailable: list[str] | None = None,
    cache: PreviewChecksums | None = None,
) -> tuple[MediaChange, ...]:
    """Confirm archived assets and compare managed file checksums."""
    check_mount(root)
    changes = []
    for index, check in enumerate(checks):
        try:
            path = target_path(root, check)
            if path.is_symlink() and check.digest is not None:
                raise MediaError(
                    f"A NAS link conflicts with the managed media path: {check.path}"
                )
            try:
                info = path.stat()
            except FileNotFoundError:
                info = None
            if info is not None and stat.S_ISREG(info.st_mode):
                if check.digest is None:
                    with path.open("rb") as stream:
                        _ = stream.read(1)
                    continue
                previous = (
                    cache.checksum(path, info) if cache is not None else digest(path)
                )
                if previous != check.digest:
                    changes.append(MediaChange(index, previous))
            elif check.digest is not None and info is None:
                changes.append(MediaChange(index, None))
            else:
                raise MediaError(
                    f"The VM cannot read the linked media on the NAS: {check.path}"
                )
        except (MediaError, OSError) as error:
            if unavailable is None:
                raise
            unavailable.append(str(error))
    return tuple(changes)


def copy_verified(source: BinaryIO, destination: BinaryIO, check: MediaCheck) -> None:
    """Copy one exact-length file and confirm it matches the snapshot manifest."""
    remaining = check.size
    sha256 = hashlib.sha256()
    while remaining:
        data = source.read(min(remaining, CHUNK_SIZE))
        if not data:
            raise MediaError(f"The media transfer was incomplete: {check.path}")
        destination.write(data)
        sha256.update(data)
        remaining -= len(data)
    if sha256.hexdigest() != check.digest:
        raise MediaError(
            f"The media checksum changed during the transfer: {check.path}"
        )


@dataclass(frozen=True, slots=True)
class StagedMedia:
    """The checks and temporary NAS files for one pending publication."""

    root: Path
    checks: tuple[MediaCheck, ...]
    changes: tuple[MediaChange, ...]
    directory: Path

    @contextmanager
    def install(self) -> Iterator[None]:
        """Publish verified assets and restore old copies if database commit fails."""
        if media_plan(self.root, self.checks) != self.changes:
            raise MediaError(
                "The NAS media changed during publication. Publish again to recheck it."
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        previous_root = self.root / "RootsMagicReader" / "PreviousMedia" / stamp
        installed: list[tuple[Path, Path | None]] = []
        committed = False
        try:
            for index, old_digest in self.changes:
                check = self.checks[index]
                destination = target_path(self.root, check)
                destination.parent.mkdir(parents=True, exist_ok=True)
                backup = None
                if old_digest is not None:
                    backup = previous_root / Path(check.path).relative_to(NAS_MEDIA)
                    if backup.resolve() != self.root.resolve() / backup.relative_to(
                        self.root
                    ):
                        raise MediaError(
                            "The NAS backup path contains a symbolic link."
                        )
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("rb") as source, backup.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    if digest(backup) != old_digest:
                        raise MediaError(
                            f"Unable to verify the prior media copy: {check.path}"
                        )
                (self.directory / str(index)).replace(destination)
                installed.append((destination, backup))
            yield
            committed = True
        finally:
            if not committed:
                for destination, backup in reversed(installed):
                    if backup is None:
                        destination.unlink(missing_ok=True)
                    else:
                        backup.replace(destination)


@contextmanager
def stage_media(
    root: Path,
    checks: tuple[MediaCheck, ...],
    changes: tuple[MediaChange, ...],
    source: BinaryIO,
) -> Iterator[StagedMedia]:
    """Keep transferred bytes in a temporary folder on the same NAS filesystem."""
    check_mount(root)
    if not changes:
        yield StagedMedia(root, checks, changes, root)
        return
    reader = (root / "RootsMagicReader").resolve(strict=True)
    if reader != root.resolve() / "RootsMagicReader":
        raise MediaError("The managed NAS reader folder contains a symbolic link.")
    with TemporaryDirectory(prefix=".media-publish-", dir=reader) as directory:
        staging = Path(directory)
        for index, _ in changes:
            check = checks[index]
            _ = target_path(root, check)
            with (staging / str(index)).open("xb") as output:
                copy_verified(source, output, check)
                output.flush()
                os.fsync(output.fileno())
            if digest(staging / str(index)) != check.digest:
                raise MediaError(
                    f"The NAS copy failed checksum verification: {check.path}"
                )
        yield StagedMedia(root, checks, changes, staging)
