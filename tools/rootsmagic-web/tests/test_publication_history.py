"""Regression tests for restoring a publication after an interrupted NAS write."""

import importlib
import io
import sqlite3
import sys
import unittest
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
publisher = importlib.import_module("publish-tree")
history = importlib.import_module("publication_history")
media = importlib.import_module("publish_media")


class PublicationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        parent = Path(directory.name)
        self.root, self.nas = parent / "reader", parent / "nas"
        for name in ("trees", "incoming", "backups", "state"):
            (self.root / name).mkdir(parents=True)
        self.relative = "RootsMagicReader/Media/penic/photo.jpg"
        self.target = self.nas / self.relative
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"first")
        self.name = "Test.rmtree"
        self.tree = self.root / "trees" / self.name
        self.make_tree(self.tree, (1, "stable-one"))
        self.source = parent / "staged.rmtree"
        self.make_tree(self.source, (1, "stable-one"), (2, "stable-two"))
        self.first = media.digest(self.tree)

    @staticmethod
    def make_tree(path, *people):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE PersonTable (PersonID INTEGER, UniqueID TEXT)")
            for name in ("FamilyTable", "ChildTable", "EventTable", "NameTable"):
                db.execute(f"CREATE TABLE {name} (id INTEGER)")  # noqa: S608
            db.executemany("INSERT INTO PersonTable VALUES (?, ?)", people)

    def publish(self, source, *, previous=None, photo=None, other=None):
        content = source.read_bytes()
        with (
            patch.object(
                publisher.sys,
                "stdin",
                type("Stream", (), {"buffer": io.BytesIO(content)}),
            ),
            patch.object(publisher, "validate_reader"),
        ):
            if photo is None:
                return publisher.receive_snapshot(
                    self.root,
                    self.name,
                    media.digest(source),
                    previous_checksum=previous,
                )
            parts = [(self.relative, photo)]
            if other is not None:
                parts.append(("RootsMagicReader/Media/penic/new.jpg", other))
            checks = tuple(
                media.MediaCheck(name, sha256(data).hexdigest(), len(data))
                for name, data in parts
            )
            plan = media.media_plan(self.nas, checks)
            payload = b"".join(parts[index][1] for index, _ in plan)
            with media.stage_media(
                self.nas, checks, plan, io.BytesIO(payload)
            ) as staged:
                return publisher.receive_snapshot(
                    self.root,
                    self.name,
                    media.digest(source),
                    staged,
                    previous_checksum=previous,
                )

    def test_recovery_restores_media_and_tree_together(self):
        copy = history.atomic_copy

        def interrupt(source, destination, checksum, mode):
            if destination == self.tree:
                raise OSError("simulated power loss")
            return copy(source, destination, checksum, mode)

        with (
            patch.object(history, "atomic_copy", side_effect=interrupt),
            self.assertRaises(OSError),
        ):
            self.publish(self.source, photo=b"second", other=b"added")
        self.assertEqual(self.target.read_bytes(), b"second")
        self.assertEqual(media.digest(self.tree), self.first)
        self.assertTrue((self.root / "incoming" / history.PENDING).is_file())
        publisher.main(["--recover", str(self.root)])
        publisher.main(["--recover", str(self.root)])
        self.assertEqual(media.digest(self.tree), media.digest(self.source))
        self.assertFalse((self.root / "incoming" / history.PENDING).exists())
        with patch.object(publisher, "validate_reader"):
            preview = publisher.rollback_publication(self.root, self.name, None)
            self.assertEqual(len(preview["media"]), 2)
            self.assertFalse(preview["applied"])
            self.assertTrue(
                publisher.rollback_publication(self.root, self.name, preview["plan"])[
                    "applied"
                ]
            )
            with self.assertRaises(publisher.PreviewChanged):
                publisher.rollback_publication(self.root, self.name, preview["plan"])
        self.assertEqual(media.digest(self.tree), self.first)
        self.assertEqual(self.target.read_bytes(), b"first")
        self.assertFalse(self.target.with_name("new.jpg").exists())

    def test_stale_preview_and_reused_id_leave_active_tree_intact(self):
        with self.assertRaises(publisher.PreviewChanged):
            self.publish(self.source, previous="f" * 64)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "UPDATE PersonTable SET UniqueID = 'different' WHERE PersonID = 1"
            )
        with self.assertRaisesRegex(publisher.PublishError, "reassign RootsMagic IDs"):
            self.publish(self.source)
        self.assertEqual(media.digest(self.tree), self.first)
        self.assertEqual(self.target.read_bytes(), b"first")

    def test_cannot_reuse_a_previously_removed_id(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("DELETE FROM PersonTable WHERE PersonID = 1")
        self.publish(self.source)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO PersonTable VALUES (1, 'different')")
        with self.assertRaisesRegex(publisher.PublishError, "#1"):
            self.publish(self.source)

    def test_database_rejected_before_media_changes(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("DROP TABLE ChildTable")
        with self.assertRaisesRegex(
            publisher.PublishError, "required RootsMagic tables"
        ):
            self.publish(self.source, photo=b"second")
        self.assertEqual(media.digest(self.tree), self.first)
        self.assertEqual(self.target.read_bytes(), b"first")


if __name__ == "__main__":
    unittest.main()
