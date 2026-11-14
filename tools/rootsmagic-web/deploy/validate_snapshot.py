"""Exercise the deployed Reader against a staged database before activation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pedigree_store import pedigree, pedigree_details  # noqa: E402
from reader_external import ExternalTree  # noqa: E402
from store import connect, pedigree_search  # noqa: E402


def validate(path: Path) -> None:
    for source in (path, ExternalTree(path)):
        with connect(source) as db:
            db.execute("SELECT UniqueID FROM PersonTable LIMIT 1").fetchone()
        directory = pedigree_search(source, "", 5, 0)
        for person in directory.people:
            if (
                pedigree(source, person.id, 11) is None
                or pedigree_details(source, person.id) is None
            ):
                raise ValueError(
                    "Search, pedigree and record disagree on the staged snapshot."
                )


if __name__ == "__main__":
    try:
        validate(Path(sys.argv[1]))
    except (OSError, sqlite3.Error, ValueError) as error:
        sys.exit(f"The reader cannot open the staged tree: {error}")
