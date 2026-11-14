#!/usr/bin/env python3
"""Publish a verified read-only RootsMagic snapshot over SSH."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from publish_media import (
    NAS_ROOT,
    MediaChange,
    MediaCheck,
    MediaError,
    PreviewChecksums,
    copy_verified,
    digest,
    local_media,
    media_plan,
    stage_media,
)
from publish_preview import (
    PublishPreview,
    TargetPreview,
    index_snapshot,
    preview_publication,
)

if TYPE_CHECKING:
    from typing import BinaryIO

    from publish_media import LocalMedia, StagedMedia

SERVER: Final = os.environ.get("ROOTSMAGIC_PUBLISH_SERVER", "")
PRODUCTION: Final = Path("/srv/rootsmagic-reader")
PROJECTS: Final = Path.home() / "Projects"
SSH_TIMEOUT: Final = 540
TABLES: Final = frozenset(
    {"PersonTable", "NameTable", "EventTable", "FamilyTable", "ChildTable"}
)


class PublishError(RuntimeError):
    """A snapshot failed verification or cannot replace the current tree safely."""


class PreviewChanged(PublishError):
    """The source or destination changed after a publication preview."""


class PublishResult(TypedDict):
    status: Literal["published", "already_published"]
    snapshot: str
    backup: str | None
    copied: int
    replaced: int
    reused: int


def tree_filename(name: str) -> str:
    """Normalize Mac spelling and restrict the destination to a single tree name."""
    name = unicodedata.normalize("NFC", name)
    if (
        not name.strip()
        or name.startswith(".")
        or not name.casefold().endswith(".rmtree")
        or any(char in "/\\" or ord(char) < 32 for char in name)
    ):
        message = "Use a single visible .rmtree filename for the destination."
        raise PublishError(message)
    return name


def open_tree(path: Path) -> sqlite3.Connection:
    """Open the source or a published copy without database write access."""
    return sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)


def binary_order(left: str, right: str) -> int:
    """Supply the collation name for a quick_check that skips index ordering."""
    return (left > right) - (left < right)


def verify_snapshot(path: Path) -> None:
    """Check basic RootsMagic schema, standalone journal mode, and SQLite pages."""
    with closing(open_tree(path)) as db:
        db.create_collation("RMNOCASE", binary_order)
        db.execute("PRAGMA trusted_schema=OFF")
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        if not TABLES.issubset(tables):
            message = "The snapshot is missing required RootsMagic tables."
            raise PublishError(message)
        if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            message = "The snapshot requires a separate SQLite journal."
            raise PublishError(message)
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            message = "SQLite quick_check failed for the snapshot."
            raise PublishError(message)


def make_snapshot(source: Path, snapshot: Path) -> None:
    """Capture a consistent source database, including committed WAL records."""
    with (
        closing(open_tree(source)) as original,
        closing(sqlite3.connect(snapshot)) as copy,
    ):
        original.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")
    verify_snapshot(snapshot)


def sync_directory(path: Path) -> None:
    """Flush a published filename or its backup link to disk."""
    directory = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def current_checksum(destination: Path) -> str | None:
    """Inspect a live tree without following links or reading SQLite sidecars."""
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise PublishError(f"The published path is not a regular file: {destination}.")
    if any(
        os.path.lexists(f"{destination}{suffix}")
        for suffix in ("-wal", "-shm", "-journal")
    ):
        raise PublishError(f"SQLite sidecar files exist beside {destination}.")
    return digest(destination) if destination.exists() else None


def publication_directories(root: Path) -> tuple[Path, Path, Path]:
    trees, incoming, backups = (
        root / name for name in ("trees", "incoming", "backups")
    )
    for directory in (trees, incoming, backups):
        if directory.is_symlink() or not directory.is_dir():
            raise PublishError(f"Expected a real directory at {directory}.")
    if len({directory.stat().st_dev for directory in (trees, incoming, backups)}) != 1:
        raise PublishError(
            "Trees, incoming, and backups must be on the same filesystem."
        )
    return trees, incoming, backups


def inspect_target(
    root: Path,
    name: str,
    expected: str,
    nas: Path | None,
    checks: tuple[MediaCheck, ...],
    *,
    verify_media: bool = True,
) -> TargetPreview:
    """Read the published record index and NAS media status before sending files."""
    trees, incoming, _ = publication_directories(root)
    destination = trees / tree_filename(name)
    with (incoming / ".publish.lock").open("ab") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        checksum = current_checksum(destination)
        records = (
            index_snapshot(destination) if checksum and checksum != expected else {}
        )
    unavailable: list[str] = []
    cache = (
        PreviewChecksums(incoming / ".preview-media-checksums.json")
        if nas is not None and not verify_media
        else None
    )
    changes = (
        media_plan(nas, checks, unavailable=unavailable, cache=cache)
        if nas is not None
        else ()
    )
    if cache is not None:
        cache.save()
    return {
        "checksum": checksum,
        "records": records,
        "changes": changes,
        "unavailable": unavailable,
    }


def receive_snapshot(
    root: Path,
    name: str,
    expected: str,
    media: StagedMedia | None = None,
    *,
    previous_checksum: str | None = None,
) -> PublishResult:
    """Verify the stream before replacing the live file under a publisher lock."""
    name = tree_filename(name)
    trees, incoming, backups = publication_directories(root)
    destination = trees / name
    with NamedTemporaryFile(prefix="tree-", dir=incoming, delete=False) as stream:
        staged = Path(stream.name)
        try:
            shutil.copyfileobj(sys.stdin.buffer, stream)
            stream.flush()
            os.fsync(stream.fileno())
            if digest(staged) != expected:
                message = "The received file does not match the snapshot checksum."
                raise PublishError(message)
            verify_snapshot(staged)
            staged.chmod(0o400)

            with (incoming / ".publish.lock").open("ab") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                current = current_checksum(destination)
                if previous_checksum is not None and previous_checksum != (
                    current or "-"
                ):
                    raise PreviewChanged(
                        "The published tree changed. Open a new preview."
                    )
                already_published = current == expected
                backup = None
                if current is not None and not already_published:
                    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
                    previous = backups / f"{stamp}--{name}"
                    os.link(destination, previous)
                    sync_directory(backups)
                    backup = previous.name
                with media.install() if media is not None else nullcontext():
                    if not already_published:
                        staged.replace(destination)
                if not already_published:
                    sync_directory(trees)
                changes = media.changes if media is not None else ()
                return {
                    "status": "published"
                    if changes or not already_published
                    else "already_published",
                    "snapshot": name,
                    "backup": backup,
                    "copied": sum(change.previous is None for change in changes),
                    "replaced": sum(change.previous is not None for change in changes),
                    "reused": len(media.checks) - len(changes)
                    if media is not None
                    else 0,
                }
        finally:
            staged.unlink(missing_ok=True)


def describe(result: PublishResult, destination: Path) -> str:
    """Format a publication result for the original command-line workflow."""
    status = "Published" if result["status"] == "published" else "Already published"
    lines = [f"{status} {destination}"]
    if result["copied"] or result["replaced"] or result["reused"]:
        lines.append(
            f"Copied {result['copied']}, updated {result['replaced']}, "
            f"and reused {result['reused']} linked media files."
        )
    if result["replaced"]:
        lines.append("Previous versions were kept in the NAS PreviousMedia folder.")
    if result["backup"]:
        lines.append(f"Previous tree snapshot saved as {result['backup']}.")
    return "\n".join(lines)


def run_remote(
    server: str, *args: str, stream: BinaryIO | None = None, data: bytes | None = None
) -> bytes:
    """Run the same receiver code on the VM over the verified SSH connection."""
    path = Path(__file__).resolve()
    media_code = path.with_name("publish_media.py").read_text(encoding="utf-8")
    preview_code = path.with_name("publish_preview.py").read_text(encoding="utf-8")
    receiver = path.read_text(encoding="utf-8")
    script = (
        "import sys, types\n"
        "media = types.ModuleType('publish_media')\n"
        "sys.modules['publish_media'] = media\n"
        f"exec({media_code!r}, media.__dict__)\n"
        "preview = types.ModuleType('publish_preview')\n"
        "sys.modules['publish_preview'] = preview\n"
        f"exec({preview_code!r}, preview.__dict__)\n"
        f"exec({receiver!r})\n"
    )
    command = shlex.join(("python3", "-I", "-c", script, *args))
    result = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "--",
            server,
            command,
        ],
        stdin=stream,
        input=data,
        capture_output=True,
        timeout=SSH_TIMEOUT,
        check=False,
    )
    if result.returncode:
        reason = result.stderr.decode(errors="replace").strip()[-700:]
        if result.returncode == 3:
            raise PreviewChanged(reason)
        raise PublishError(reason or f"SSH exited with status {result.returncode}.")
    return result.stdout


def valid_digest(value: str) -> bool:
    """Recognize the lowercase SHA-256 value used in the transfer protocol."""
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def decode_checks(data: bytes) -> tuple[MediaCheck, ...]:
    """Decode and validate one media manifest received from the Mac."""
    rows = json.loads(data)
    if not isinstance(rows, list) or any(
        not isinstance(row, list) or len(row) != 3 for row in rows
    ):
        raise PublishError("The media manifest is invalid.")
    checks = tuple(MediaCheck(*row) for row in rows)
    if any(
        not isinstance(path, str)
        or type(size) is not int
        or size < 0
        or (
            expected is not None
            and (not isinstance(expected, str) or not valid_digest(expected))
        )
        or (expected is None and size != 0)
        for path, expected, size in checks
    ) or len({check.path for check in checks}) != len(checks):
        raise PublishError("The media manifest is invalid.")
    return checks


def decode_changes(
    data: bytes, checks: tuple[MediaCheck, ...]
) -> tuple[MediaChange, ...]:
    """Validate the ordered list of files the VM needs from the Mac."""
    rows = json.loads(data)
    if not isinstance(rows, list) or any(
        not isinstance(row, list) or len(row) != 2 for row in rows
    ):
        raise PublishError("The media transfer list is invalid.")
    changes = tuple(MediaChange(*row) for row in rows)
    indices = [index for index, _ in changes]
    if any(
        type(index) is not int
        or not 0 <= index < len(checks)
        or checks[index].digest is None
        or (
            previous is not None
            and (not isinstance(previous, str) or not valid_digest(previous))
        )
        for index, previous in changes
    ) or indices != sorted(set(indices)):
        raise PublishError("The media transfer list is invalid.")
    return changes


def stream_snapshot(
    snapshot: Path,
    name: str,
    server: str,
    production: Path,
    nas: Path,
    media: tuple[LocalMedia, ...] | None = None,
    *,
    target: TargetPreview | None = None,
) -> PublishResult:
    """Send just the files requested by the VM and the verified SQLite snapshot."""
    receiver_args = (str(production), name, digest(snapshot))
    previous = ((target["checksum"] or "-"),) if target is not None else ()
    if media is None:
        with snapshot.open("rb") as stream:
            result = run_remote(
                server, "--receive", *receiver_args, "--json", *previous, stream=stream
            )
    else:
        checks = tuple(asset.check for asset in media)
        manifest = json.dumps(checks).encode() + b"\n"
        if target is None:
            response = run_remote(server, "--plan-media", str(nas), data=manifest)
            changes = decode_changes(response, checks)
        else:
            changes = target["changes"]
        with TemporaryDirectory(prefix="rootsmagic-transfer-") as directory:
            bundle = Path(directory) / "snapshot-with-media"
            with bundle.open("w+b") as stream:
                stream.write(manifest)
                stream.write(json.dumps(changes).encode() + b"\n")
                for index, _ in changes:
                    asset = media[index]
                    if asset.source is None:
                        raise PublishError(
                            "The VM requested a copy of a NAS-only file."
                        )
                    with asset.source.open("rb") as source:
                        copy_verified(source, stream, asset.check)
                        if source.read(1):
                            raise MediaError(
                                f"The Mac file changed during publication: {asset.source}"
                            )
                with snapshot.open("rb") as source:
                    shutil.copyfileobj(source, stream)
                stream.seek(0)
                result = run_remote(
                    server,
                    "--receive-with-media",
                    *receiver_args,
                    str(nas),
                    *previous,
                    stream=stream,
                )
    return json.loads(result)


def plan_publication(
    snapshot: Path,
    name: str,
    server: str,
    production: Path,
    nas: Path | None,
    checks: tuple[MediaCheck, ...],
    unavailable: list[str],
    *,
    verify_media: bool = False,
) -> tuple[PublishPreview, TargetPreview]:
    checksum = digest(snapshot)
    response = run_remote(
        server,
        "--preview-target",
        str(production),
        name,
        checksum,
        str(nas) if nas is not None else "-",
        *(("--verify-media",) if verify_media else ()),
        data=json.dumps(checks).encode() + b"\n",
    )
    target: TargetPreview = json.loads(response)
    target["changes"] = decode_changes(json.dumps(target["changes"]).encode(), checks)
    return preview_publication(
        snapshot, name, checksum, target, checks, unavailable
    ), target


def main(argv: list[str]) -> None:
    """Handle the user-facing command or a snapshot received over SSH."""
    if argv[:1] == ["--receive"] and len(argv) in (4, 5, 6):
        _, root, name, expected, *options = argv
        previous = options[1] if len(options) == 2 else None
        result = receive_snapshot(
            Path(root), name, expected, previous_checksum=previous
        )
        print(
            json.dumps(result)
            if options[:1] == ["--json"]
            else describe(result, Path(root) / "trees" / name)
        )
        return
    if (
        argv[:1] == ["--preview-target"]
        and len(argv) >= 5
        and argv[5:] in ([], ["--verify-media"])
    ):
        _, root, name, expected, nas, *options = argv
        checks = decode_checks(sys.stdin.buffer.readline())
        target = inspect_target(
            Path(root),
            name,
            expected,
            None if nas == "-" else Path(nas),
            checks,
            verify_media=bool(options),
        )
        print(json.dumps(target))
        return
    if argv[:1] == ["--plan-media"] and len(argv) == 2:
        _, nas = argv
        print(
            json.dumps(
                media_plan(Path(nas), decode_checks(sys.stdin.buffer.readline()))
            )
        )
        return
    if argv[:1] == ["--receive-with-media"] and len(argv) in (5, 6):
        _, root, name, expected, nas, *options = argv
        stream = sys.stdin.buffer
        checks = decode_checks(stream.readline())
        changes = decode_changes(stream.readline(), checks)
        with stage_media(Path(nas), checks, changes, stream) as media:
            result = receive_snapshot(
                Path(root),
                name,
                expected,
                media,
                previous_checksum=options[0] if options else None,
            )
        print(json.dumps(result))
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="saved RootsMagic .rmtree file")
    parser.add_argument(
        "--name", help="published filename, defaults to the source filename"
    )
    parser.add_argument("--server", default=SERVER, help="SSH destination")
    parser.add_argument(
        "--production", type=Path, default=PRODUCTION, help="remote service folder"
    )
    parser.add_argument(
        "--with-media", action="store_true", help="publish linked media before the tree"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable publication result"
    )
    preview_options = parser.add_mutually_exclusive_group()
    preview_options.add_argument(
        "--preview",
        action="store_true",
        help="compare the tree and media before transfer",
    )
    preview_options.add_argument(
        "--expect-plan", help="publish only if the preview still matches"
    )
    parser.add_argument(
        "--verify-media",
        action="store_true",
        help="read every managed NAS file during a preview",
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=PROJECTS,
        help="Mac folder containing projects with linked media",
    )
    parser.add_argument(
        "--nas-root", type=Path, default=NAS_ROOT, help="NAS mount as seen by the VM"
    )
    args = parser.parse_args(argv)
    if not args.server:
        parser.error("Set ROOTSMAGIC_PUBLISH_SERVER or pass --server.")
    source = args.source.expanduser()
    tree_filename(source.name)
    name = tree_filename(args.name or source.name)
    with TemporaryDirectory(prefix="rootsmagic-publish-") as directory:
        snapshot = Path(directory) / "snapshot.rmtree"
        make_snapshot(source, snapshot)
        media = None
        unavailable: list[str] = []
        if args.with_media:
            with closing(open_tree(snapshot)) as db:
                media = local_media(
                    db,
                    args.projects_root.expanduser(),
                    unavailable=unavailable
                    if args.preview or args.expect_plan
                    else None,
                )
        target = None
        if args.preview or args.expect_plan:
            preview, target = plan_publication(
                snapshot,
                name,
                args.server,
                args.production,
                args.nas_root if media is not None else None,
                tuple(item.check for item in media or ()),
                unavailable,
                verify_media=args.verify_media or bool(args.expect_plan),
            )
            if args.preview:
                print(json.dumps(preview, indent=None if args.json else 2))
                return
            if preview["unavailable"] or preview["plan"] != args.expect_plan:
                raise PreviewChanged(
                    "The tree or its linked media changed. Open a new preview."
                )
        result = stream_snapshot(
            snapshot,
            name,
            args.server,
            args.production,
            args.nas_root,
            media,
            target=target,
        )
    print(
        json.dumps(result)
        if args.json
        else describe(result, args.production / "trees" / name)
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except subprocess.TimeoutExpired:
        sys.exit("Snapshot publish over SSH timed out.")
    except PreviewChanged as error:
        print(error, file=sys.stderr)
        sys.exit(3)
    except (OSError, PublishError, MediaError, sqlite3.Error, ValueError) as error:
        sys.exit(f"Snapshot publish failed: {error}")
