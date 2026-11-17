"""Regression coverage for standalone exports and their visibility boundary."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pedigree_html import read_export, render_html_export
from store import PersonID, connect
from tree_catalog import Tree

FIXTURE = Path(__file__).parent / "fixtures" / "privacy-tree.sql"
REGISTER = "https://registers.penic.net/entry/example"


def make_export_fixture(path: Path) -> None:
    """Create two paths to a deep ancestor and a cycle back to the root."""
    with closing(sqlite3.connect(path)) as db, db:
        db.create_collation("RMNOCASE", lambda a, b: (a > b) - (a < b))
        db.executescript(FIXTURE.read_text())
        for person in range(101, 114):
            db.execute(
                "INSERT INTO PersonTable (PersonID, ParentID, Living, IsPrivate) VALUES (?, ?, 0, 0)",
                (person, person),
            )
            db.execute(
                """INSERT INTO NameTable (NameID, OwnerID, Given, Surname, IsPrimary, IsPrivate)
                   VALUES (?, ?, ?, 'Example', 1, 0)""",
                (person, person, f"Ancestor {person}"),
            )
            db.execute(
                "INSERT INTO FamilyTable (FamilyID, FatherID, IsPrivate) VALUES (?, ?, 0)",
                (person, person + 1 if person < 113 else 20),
            )
        db.execute("INSERT INTO FamilyTable (FamilyID, FatherID, IsPrivate) VALUES (100, 101, 0)")
        db.execute("UPDATE PersonTable SET ParentID=100 WHERE PersonID IN (21, 12)")
        db.execute("INSERT INTO ChildTable (ChildID, FamilyID, IsPrivate) VALUES (13, 1, 0)")
        db.execute("INSERT INTO ChildTable (ChildID, FamilyID, IsPrivate) VALUES (20, 100, 0)")
        db.execute(
            """INSERT INTO EventTable (EventID, EventType, OwnerType, OwnerID, Date, PlaceID, IsPrivate)
               VALUES (100, 18, 0, 20, 'D.+18100000.A+00000000..', 1, 0)"""
        )
        db.execute(
            "UPDATE CitationTable SET Footnote=? WHERE CitationID=91",
            (f"SecretFootnote {REGISTER} {REGISTER} https://registers.penic.net.invalid/entry https://other.invalid/entry",),
        )
        db.execute("UPDATE NameTable SET Given=? WHERE NameID=113", ("</script><script>hostile()</script>",))


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "fixture.rmtree"
        make_export_fixture(path)
        self.tree = Tree("fixture", "Fixture", path)

    def test_export_contains_complete_ancestry_and_fact_links(self) -> None:
        with connect(self.tree.database) as db:
            data = read_export(db, PersonID(20), external=False)
        assert data is not None
        self.assertEqual(data.ancestry["20"], ("1", "100"))
        self.assertEqual(data.ancestry["21"], data.ancestry["12"])
        self.assertIn("113", data.people)
        self.assertEqual(data.families["113"].members[0].id, "20")
        self.assertEqual({p.id for p in data.relatives if p.role == "Partner"}, {"10", "21", "23"})
        self.assertIn("13", {p.id for p in data.relatives})
        self.assertNotIn("24", data.people)
        facts = data.people["20"].facts
        self.assertEqual(sum(f.label == "Residence" for f in facts), 2)
        self.assertTrue(any(f.date == "About 1810" for f in facts))
        self.assertEqual([url for fact in facts for url in fact.links], [REGISTER])
        self.assertTrue(data.families["2"].facts)

    def test_document_excludes_notes_media_and_escapes_embedded_values(self) -> None:
        templates = Environment(
            loader=FileSystemLoader(Path(__file__).resolve().parents[1]), autoescape=select_autoescape(),
        )
        html = render_html_export(templates, self.tree, PersonID(20))
        assert html is not None
        self.assertIn(r"</script>", html)
        for excluded in ("SecretFactNote", "SecretFactDetails", "SecretPersonNote", "SecretFootnote",
                         "SecretPhoto", "<script>hostile()", "other.invalid"):
            self.assertNotIn(excluded, html)

    def test_external_export_uses_the_projected_tree(self) -> None:
        tree = Tree(self.tree.slug, self.tree.name, self.tree.path, external=True)
        with connect(tree.database) as db:
            self.assertIsNone(read_export(db, PersonID(10), external=True))
            data = read_export(db, PersonID(20), external=True)
        assert data is not None
        self.assertTrue({"10", "11", "12", "13", "22", "24", "25"}.isdisjoint(data.people))
        self.assertIn("113", data.people)
        self.assertEqual(tuple(p.id for p in data.families["1"].members), ("21",))
        self.assertTrue(all(not fact.links for person in data.people.values() for fact in person.facts))
        self.assertNotIn("SecretExactSite", data.model_dump_json())
