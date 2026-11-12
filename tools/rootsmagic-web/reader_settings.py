"""Saved reader settings, separate from the RootsMagic databases."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SettingsError(RuntimeError):
    """The saved reader settings could not be used."""


class SettingsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChartSettings(SettingsModel):
    tree: Annotated[str, Field(max_length=240)] = ""
    person: Annotated[int | None, Field(ge=1, le=2**63 - 1)] = None
    depth: Literal[3, 5, 8, 11] = 11
    children: bool = False
    siblings: bool = True


class SourceSettings(SettingsModel):
    file: Annotated[str | None, Field(max_length=4096)] = None
    folder: Annotated[str | None, Field(max_length=4096)] = None
    publish: bool | None = None


class SecuritySettings(SettingsModel):
    account_attempts: Annotated[int, Field(ge=4, le=20)] = 8
    address_attempts: Annotated[int, Field(ge=12, le=120)] = 36
    pdf_exports: Annotated[int, Field(ge=1, le=6)] = 2


class BackupSettings(SettingsModel):
    retention: Annotated[int, Field(ge=3, le=60)] = 14


class SiteSettings(SettingsModel):
    chart: ChartSettings = ChartSettings()
    sources: SourceSettings = SourceSettings()
    security: SecuritySettings = SecuritySettings()
    backups: BackupSettings = BackupSettings()


WRITE_LOCK: Final = Lock()
Section = Literal["chart", "sources", "security", "backups"]
SectionSettings = ChartSettings | SourceSettings | SecuritySettings | BackupSettings


def settings_path(accounts_path: Path | None = None) -> Path:
    """Locate the private settings file beside hosted accounts or in Mac app data."""
    if path := os.environ.get("ROOTSMAGIC_SETTINGS_FILE"):
        return Path(path).expanduser()
    if accounts_path is not None:
        return accounts_path.expanduser().with_name("settings.json")
    if accounts := os.environ.get("ROOTSMAGIC_ACCOUNTS_DB"):
        return Path(accounts).expanduser().with_name("settings.json")
    if sys.platform == "darwin":
        return (
            Path.home() / "Library/Application Support/RootsMagic Reader/settings.json"
        )
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "rootsmagic-reader/settings.json"


def site_settings(path: Path | None = None) -> SiteSettings:
    """Read the current values or use the defaults before the first save."""
    path = path or settings_path()
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077 or path.suffix != ".json":
            raise SettingsError("The reader settings must use a private JSON file.")
        with path.open("rb") as source:
            return SiteSettings.model_validate_json(source.read(64 * 1024))
    except FileNotFoundError:
        return SiteSettings()
    except (OSError, ValidationError) as error:
        raise SettingsError("The saved reader settings could not be read.") from error


def save_settings(section: Section, value: SectionSettings) -> None:
    """Replace one section atomically, preserving concurrent updates to other sections."""
    path = settings_path()
    if path.suffix != ".json":
        raise SettingsError("The reader settings must use a private JSON file.")
    with WRITE_LOCK:
        settings = SiteSettings.model_validate(
            site_settings().model_dump() | {section: value}
        )
        temporary: Path | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w", dir=path.parent, prefix=".settings-", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(settings.model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        except OSError as error:
            raise SettingsError("The reader settings could not be saved.") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
