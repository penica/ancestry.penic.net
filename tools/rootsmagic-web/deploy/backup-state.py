"""Take consistent online backups of the hosted reader's private state."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Final

RETENTION: Final = 14
SNAPSHOT_NAME: Final = re.compile(r"\d{8}T\d{12}Z")


class BackupError(RuntimeError):
    """A state backup could not be verified."""


def private_file(path: Path) -> None:
    """Require a private regular file and reject symbolic links."""
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise BackupError(f"{path.name} must be a private regular file.")


def backup_database(source: Path, destination: Path) -> None:
    """Copy a live SQLite file without taking a raw copy of its journal."""
    private_file(source)
    with (
        closing(
            sqlite3.connect(source.absolute().as_uri() + "?mode=ro", uri=True)
        ) as active,
        closing(sqlite3.connect(destination)) as snapshot,
    ):
        active.backup(snapshot)
        if snapshot.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise BackupError(
                f"The {destination.name} backup failed its integrity check."
            )
    destination.chmod(0o600)


def backup_state(
    accounts: Path,
    comments: Path,
    key: Path,
    destination: Path,
    settings: Path | None = None,
    status_file: Path | None = None,
) -> Path:
    """Publish a complete backup directory only after verifying all included files."""
    os.umask(0o077)
    destination.mkdir(mode=0o700, exist_ok=True)
    mode = destination.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise BackupError("The state backup directory must be private.")
    now = datetime.now(UTC)
    name = now.strftime("%Y%m%dT%H%M%S%fZ")
    snapshot = destination / name
    retention = RETENTION
    settings = settings or accounts.with_name("settings.json")
    with TemporaryDirectory(prefix=".incoming-", dir=destination) as directory:
        staging = Path(directory) / name
        staging.mkdir(mode=0o700)
        backup_database(accounts, staging / "accounts.sqlite3")
        try:
            backup_database(comments, staging / "comments.sqlite3")
        except FileNotFoundError:
            # The comments store is created when the first comment is posted.
            pass
        private_file(key)
        shutil.copyfile(key, staging / "mfa.key")
        (staging / "mfa.key").chmod(0o600)
        try:
            private_file(settings)
        except FileNotFoundError:
            pass
        else:
            shutil.copyfile(settings, staging / "settings.json")
            retention = saved_retention(staging / "settings.json")
        staging.rename(snapshot)
    snapshots = sorted(
        path
        for path in destination.iterdir()
        if SNAPSHOT_NAME.fullmatch(path.name)
        and path.is_dir()
        and not path.is_symlink()
    )
    for expired in snapshots[:-retention]:
        shutil.rmtree(expired)
    if status_file is not None:
        publish_status(status_file, snapshot, now)
    return snapshot


def saved_retention(settings: Path) -> int:
    """Read the bounded retention setting from the copied configuration."""
    try:
        with settings.open("rb") as source:
            parsed = json.loads(source.read(64 * 1024))
        value = parsed.get("backups", {}).get("retention", RETENTION)
    except (ValueError, AttributeError) as error:
        raise BackupError("The reader backup settings are invalid.") from error
    if type(value) is not int or not 3 <= value <= 60:
        raise BackupError("The reader backup retention is invalid.")
    return value


def publish_status(path: Path, snapshot: Path, now: datetime) -> None:
    """Expose verification metadata to the app while keeping the backup private."""
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", dir=path.parent, prefix=".latest-", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(
                {
                    "verified_at": now.isoformat(timespec="seconds"),
                    "comments": (snapshot / "comments.sqlite3").is_file(),
                    "settings": (snapshot / "settings.json").is_file(),
                },
                output,
            )
            output.write("\n")
            os.fchmod(output.fileno(), 0o640)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    """Back up the files used by the systemd reader service."""
    accounts = Path(os.environ["ROOTSMAGIC_ACCOUNTS_DB"])
    comments = Path(
        os.environ.get("ROOTSMAGIC_COMMENTS_DB")
        or accounts.with_name("comments.sqlite3")
    )
    key = Path(os.environ["ROOTSMAGIC_BACKUP_KEY"])
    destination = Path(os.environ["ROOTSMAGIC_BACKUP_DIR"])
    settings = os.environ.get("ROOTSMAGIC_SETTINGS_FILE")
    status_file = os.environ.get("ROOTSMAGIC_BACKUP_STATUS_FILE")
    snapshot = backup_state(
        accounts,
        comments,
        key,
        destination,
        Path(settings).expanduser() if settings else None,
        Path(status_file) if status_file else None,
    )
    print(f"Verified reader state backup {snapshot.name}.")


if __name__ == "__main__":
    main()
