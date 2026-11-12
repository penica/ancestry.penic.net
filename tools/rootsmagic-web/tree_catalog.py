"""Configured RootsMagic files and stable, path-free browser selections."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from reader_external import DatabasePath, ExternalTree
from reader_settings import SiteSettings, site_settings
from store import NAME, PersonID, connect, count_people

HERE: Final = Path(__file__).resolve().parent
LIVE: Final = HERE.parents[1] / "family-tree.rmtree"
LEGACY: Final = HERE.parents[1] / "legacy-tree.rmtree"
TREE_SLUG: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_TREE_SLUG_LENGTH: Final = 240
MAX_PERSON_ID: Final = 2**63 - 1


class TreeConfigurationError(ValueError):
    """The configured default tree has invalid options."""


@dataclass(frozen=True, slots=True)
class Tree:
    """One configured file, identified in URLs independently of person IDs."""

    slug: str
    name: str
    path: Path
    preferred_person: PersonID | None = None
    external: bool = False

    @property
    def database(self) -> DatabasePath:
        return ExternalTree(self.path) if self.external else self.path

    def url(self, path: str) -> str:
        """Keep an internal page or resource URL in this tree."""
        parts = urlsplit(path)
        query = dict(parse_qsl(parts.query))
        query["tree"] = self.slug
        return urlunsplit(parts._replace(query=urlencode(query)))

    def starting_person_and_count(self) -> tuple[PersonID | None, int]:
        """Read the default person and total from the same tree snapshot."""
        with connect(self.database) as db:
            row = db.execute(
                f"SELECT id FROM ({NAME}) ORDER BY id = ? DESC, id LIMIT 1",  # noqa: S608
                (self.preferred_person,),
            ).fetchone()
            return (PersonID(row["id"]) if row else None), count_people(db)


def same_file(left: Path, right: Path) -> bool:
    """Recognize filesystem aliases, including macOS Unicode spellings."""
    if left == right:
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


def tree_file(path: Path) -> Tree:
    """Give known files stable names and other paths collision-safe slugs."""
    resolved = path.expanduser().resolve()
    name = unicodedata.normalize("NFC", resolved.stem)
    if same_file(resolved, LIVE.resolve()):
        return Tree("family-tree", name, resolved)
    if same_file(resolved, LEGACY.resolve()):
        return Tree("legacy-tree", name, resolved)
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    label = re.sub(r"[^a-z0-9]+", "-", folded.casefold()).strip("-")[:200] or "tree"
    suffix = hashlib.sha256(os.fsencode(resolved)).hexdigest()[:10]
    return Tree(f"{label}-{suffix}", name, resolved)


def default_tree(path: Path) -> Tree:
    """Keep stable links and a starting person for a configured tree copy."""
    tree = tree_file(path)
    slug = os.environ.get("ROOTSMAGIC_DEFAULT_TREE_SLUG", "").strip()
    if slug and (len(slug) > MAX_TREE_SLUG_LENGTH or not TREE_SLUG.fullmatch(slug)):
        message = "ROOTSMAGIC_DEFAULT_TREE_SLUG must be a lowercase URL slug."
        raise TreeConfigurationError(message)
    tree = replace(tree, slug=slug) if slug else tree

    value = os.environ.get("ROOTSMAGIC_START_PERSON", "").strip()
    if not value:
        return tree
    message = "ROOTSMAGIC_START_PERSON must be a positive RootsMagic record ID."
    try:
        person = int(value)
    except ValueError as error:
        raise TreeConfigurationError(message) from error
    if not 0 < person <= MAX_PERSON_ID:
        raise TreeConfigurationError(message)
    return replace(tree, preferred_person=PersonID(person))


def available_trees(settings: SiteSettings | None = None) -> tuple[Tree, ...]:
    """Discover only configured paths, keeping the preferred file first."""
    settings = settings or site_settings()
    hosted = bool(os.environ.get("ROOTSMAGIC_ACCOUNTS_DB"))
    configured = os.environ.get("ROOTSMAGIC_DB")
    local_file = None if hosted else settings.sources.file
    override = configured if local_file is None else local_file
    candidates = [Path(override)] if override else [LIVE, LEGACY]
    local_folder = None if hosted else settings.sources.folder
    directory = (
        os.environ.get("ROOTSMAGIC_TREE_DIR") if local_folder is None else local_folder
    )
    if directory:
        root = Path(directory).expanduser().resolve()
        if root.is_dir():
            candidates.extend(
                path
                for path in sorted(
                    root.iterdir(),
                    key=lambda item: item.name.casefold(),
                )
                if path.suffix.casefold() == ".rmtree"
                and path.is_file()
                and path.resolve().parent == root
            )
    files = dict.fromkeys(path.expanduser().resolve() for path in candidates)
    trees: list[Tree] = []
    for path in files:
        if path.is_file() and not any(same_file(path, tree.path) for tree in trees):
            trees.append(tree_file(path))
    if override:
        source = Path(override).expanduser()
        uses_deployment_source = local_file is None or (
            configured and same_file(source, Path(configured).expanduser())
        )
        preferred = (
            default_tree(source) if uses_deployment_source else tree_file(source)
        )
        others = tuple(
            tree for tree in trees if not same_file(tree.path, preferred.path)
        )
        if any(tree.slug == preferred.slug for tree in others):
            message = (
                "ROOTSMAGIC_DEFAULT_TREE_SLUG conflicts with another configured tree."
            )
            raise TreeConfigurationError(message)
        catalog = (preferred, *others)
    else:
        catalog = tuple(trees) or (tree_file(LIVE),)
    if settings.chart.tree:
        selected = next(
            (tree for tree in catalog if tree.slug == settings.chart.tree), None
        )
        if selected is not None:
            others = tuple(tree for tree in catalog if tree.slug != selected.slug)
            if settings.chart.person is not None:
                selected = replace(
                    selected, preferred_person=PersonID(settings.chart.person)
                )
            catalog = (selected, *others)
    names = Counter(tree.name for tree in catalog)
    return tuple(
        replace(tree, name=f"{tree.name} ({tree.path.parent.name})")
        if names[tree.name] > 1
        else tree
        for tree in catalog
    )
