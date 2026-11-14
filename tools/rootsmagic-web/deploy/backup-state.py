"""Take consistent online backups of the hosted reader's private state."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from typing import BinaryIO

RETENTION: Final = 14
SNAPSHOT_NAME: Final = re.compile(r"\d{8}T\d{12}Z")
NAS_WRITER: Final = """
import os, re, shutil, sys, tempfile
from pathlib import Path

destination, name = Path(sys.argv[1]), sys.argv[2]
retention = int(sys.argv[3])
with tempfile.NamedTemporaryFile(dir=destination, prefix='.incoming-', delete=False) as stream:
    temporary = Path(stream.name)
    try:
        shutil.copyfileobj(sys.stdin.buffer, stream)
        stream.flush()
        os.fsync(stream.fileno())
        temporary.replace(destination / name)
    finally:
        temporary.unlink(missing_ok=True)
files = sorted(path for path in destination.glob('*.tar.age')
               if re.fullmatch(r'\\d{8}T\\d{12}Z\\.tar\\.age', path.name)
               and path.is_file() and not path.is_symlink())
for expired in files[:-retention]:
    expired.unlink()
"""


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
    encrypted_destination: Path | None = None,
    recipient: str | None = None,
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
    if (encrypted_destination is None) != (recipient is None):
        raise BackupError(
            "Set both the encrypted backup destination and public recipient."
        )
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
        if encrypted_destination is not None and recipient is not None:
            encrypt_snapshot(staging, encrypted_destination, recipient, retention)
            snapshot = encrypt_snapshot(staging, destination, recipient, retention)
        else:
            staging.rename(snapshot)
        if status_file is not None:
            publish_status(
                status_file,
                staging if encrypted_destination else snapshot,
                now,
                encrypted_destination is not None,
            )
    snapshots = sorted(
        path for path in destination.iterdir()
        if not path.is_symlink() and (
            (SNAPSHOT_NAME.fullmatch(path.name) and path.is_dir())
            or (
                SNAPSHOT_NAME.fullmatch(path.name.removesuffix(".tar.age"))
                and path.name.endswith(".tar.age") and path.is_file()
            )
        )
    )
    for expired in snapshots[:-retention]:
        if expired.is_dir():
            shutil.rmtree(expired)
        else:
            expired.unlink()
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


def encrypt_snapshot(
    staging: Path, destination: Path, recipient: str, retention: int
) -> Path:
    """Store only ciphertext on the backup destination."""
    mode = destination.lstat().st_mode
    if not stat.S_ISDIR(mode):
        raise BackupError("The encrypted backup destination must be a real directory.")
    final = destination / f"{staging.name}.tar.age"
    with NamedTemporaryFile(dir=staging.parent, suffix=".tar") as archive:
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for path in sorted(staging.iterdir()):
                bundle.add(path, arcname=path.name, recursive=False)
        archive.flush()
        if not os.access(destination, os.W_OK):
            owner = destination.stat().st_uid
            if owner == 0 or os.geteuid() != 0:
                raise BackupError("The encrypted backup directory is not writable.")
            # NAS root-squash applies to filesystem writes. Only ciphertext is
            # passed to the destination owner; the archive stays root-readable.
            with NamedTemporaryFile(dir=staging.parent) as ciphertext:
                encrypt_archive(archive.name, recipient, ciphertext)
                ciphertext.seek(0)
                command = [
                    "runuser",
                    "-u",
                    pwd.getpwuid(owner).pw_name,
                    "--",
                    "python3",
                    "-c",
                    NAS_WRITER,
                    str(destination),
                    final.name,
                    str(retention),
                ]
                result = subprocess.run(
                    command,
                    stdin=ciphertext,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
                if result.returncode:
                    raise BackupError("The encrypted NAS copy could not be saved.")
            return final
        with NamedTemporaryFile(
            dir=destination, prefix=".incoming-", delete=False
        ) as output:
            temporary = Path(output.name)
            try:
                encrypt_archive(archive.name, recipient, output)
                temporary.replace(final)
            finally:
                temporary.unlink(missing_ok=True)
    encrypted = sorted(
        path
        for path in destination.glob("*.tar.age")
        if SNAPSHOT_NAME.fullmatch(path.name.removesuffix(".tar.age"))
        and not path.is_symlink()
    )
    for expired in encrypted[:-retention]:
        expired.unlink()
    return final


def encrypt_archive(archive: str, recipient: str, output: BinaryIO) -> None:
    result = subprocess.run(
        ["age", "--encrypt", "--recipient", recipient, archive],
        stdout=output,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    if result.returncode or os.fstat(output.fileno()).st_size == 0:
        raise BackupError(
            "Encryption failed. Check the age installation and public recipient."
        )
    os.fsync(output.fileno())


def publish_status(
    path: Path, snapshot: Path, now: datetime, off_host: bool = False
) -> None:
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
                    "off_host": off_host,
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
    encrypted = os.environ.get("ROOTSMAGIC_ENCRYPTED_BACKUP_DIR")
    mount = os.environ.get("ROOTSMAGIC_ENCRYPTED_BACKUP_MOUNT")
    if encrypted and (
        not mount
        or not Path(mount).is_mount()
        or not Path(encrypted).resolve().is_relative_to(Path(mount).resolve())
    ):
        raise BackupError("The configured off-host backup mount is unavailable.")
    snapshot = backup_state(
        accounts,
        comments,
        key,
        destination,
        Path(settings).expanduser() if settings else None,
        Path(status_file) if status_file else None,
        Path(encrypted) if encrypted else None,
        os.environ.get("ROOTSMAGIC_BACKUP_RECIPIENT"),
    )
    print(f"Verified reader state backup {snapshot.name}.")


if __name__ == "__main__":
    main()
