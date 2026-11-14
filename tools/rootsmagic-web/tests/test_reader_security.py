"""Regression tests for account boundaries and the public SQLite projection."""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from PIL import Image
from pyotp import TOTP
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pedigree_research import relationship_paths
from pedigree_store import pedigree, pedigree_details
from reader_access import SESSION_COOKIE, ReaderAccess
from reader_accounts import Accounts, AccountsError, LoginChallenge, LoginThrottled, Role
from reader_api_keys import ApiKeys
from reader_comments import CommentDraft, Comments, ReaderActor
from reader_external import ExternalTree
from reader_tree_state import TreeState
from store import Citation, PersonID, connect, pedigree_search
from tree_catalog import Tree

FIXTURE = Path(__file__).parent / "fixtures" / "privacy-tree.sql"
PASSWORD = "synthetic-password"


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        path = self.root / "fixture.rmtree"
        with closing(sqlite3.connect(path)) as db:
            db.create_collation(
                "RMNOCASE",
                lambda a, b: (
                    (a.casefold() > b.casefold()) - (a.casefold() < b.casefold())
                ),
            )
            db.executescript(FIXTURE.read_text())
        self.tree = Tree("fixture", "Test tree", path)
        self.accounts = Accounts(self.root / "accounts.sqlite3")
        self.accounts.initialize()
        self.state = TreeState(self.root / "comments.sqlite3")

    def test_password_throttle_isolated_by_visitor(self) -> None:
        self.accounts.bootstrap_admin("owner", PASSWORD)
        for _ in range(8):
            self.assertIsNone(
                self.accounts.login("owner", "invalid-password", "192.0.2.1")
            )
        with self.assertRaises(LoginThrottled):
            self.accounts.login("owner", PASSWORD, "192.0.2.1")
        self.assertIsNotNone(self.accounts.login("owner", PASSWORD, "192.0.2.2"))

    def test_authenticator_and_recovery_codes_reject_replay(self) -> None:
        key = self.root / "mfa.key"
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        accounts = Accounts(self.accounts.path, key)
        accounts.bootstrap_admin("owner", PASSWORD)
        login = accounts.login("owner", PASSWORD, None)
        setup = accounts.begin_mfa(login.token, None, PASSWORD)
        code = TOTP(setup.secret).now()
        enrolled = accounts.confirm_mfa(login.token, None, code)
        self.assertIsNone(accounts.session(login.token))
        challenge = accounts.login("owner", PASSWORD, None)
        self.assertIsInstance(challenge, LoginChallenge)
        self.assertIsNone(accounts.complete_mfa(challenge.token, None, code))
        recovery = enrolled.recovery_codes[0]
        self.assertIsNotNone(accounts.complete_mfa(challenge.token, None, recovery))
        self.assertIsNone(accounts.complete_mfa(challenge.token, None, recovery))
        fresh = accounts.login("owner", PASSWORD, None)
        self.assertIsNone(accounts.complete_mfa(fresh.token, None, recovery))

    async def test_administrator_factor_enrollment_and_verified_session(self) -> None:
        key = self.root / "mfa.key"
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        accounts = Accounts(self.accounts.path, key)
        access = ReaderAccess(accounts, "https://reader.test")
        admin = accounts.bootstrap_admin("owner", PASSWORD)
        login = accounts.login("owner", PASSWORD, None)

        def request(path: str, token: str) -> Request:
            return Request({
                "type": "http", "scheme": "https", "path": path, "method": "GET",
                "client": ("192.0.2.2", 80),
                "headers": [(b"host", b"reader.test"),
                            (b"cookie", f"{SESSION_COOKIE}={token}".encode())],
            })

        with patch.dict(os.environ, {"ROOTSMAGIC_REQUIRE_ADMIN_FACTOR": "1"}):
            gate = await access.guard(request("/system/accounts", login.token))
            self.assertEqual(gate.headers["location"], "/account/security")
            self.assertIsNone(await access.guard(request("/account/security/qr", login.token)))
            with self.assertRaises(AccountsError):
                accounts.list_accounts(admin.id)
            setup = accounts.begin_mfa(login.token, None, PASSWORD)
            issued = accounts.confirm_mfa(login.token, None, TOTP(setup.secret).now()).issued
            self.assertIsNone(accounts.session(login.token))
            self.assertTrue(accounts.session(issued.token).verified)
            self.assertIsNone(await access.guard(request("/system/accounts", issued.token)))
            with self.assertRaises(AccountsError):
                accounts.disable_mfa(issued.token, None, PASSWORD, "123456")
            self.assertTrue(accounts.session(issued.token).account.mfa_enabled)

    def test_email_link_is_single_use_and_password_reset_revokes_sessions(self) -> None:
        self.accounts.bootstrap_admin("owner", PASSWORD)
        login = self.accounts.login("owner", PASSWORD, "192.0.2.2")
        updated = self.accounts.update_profile(
            login.token, None, "email", "owner@example.test", PASSWORD
        )
        self.assertIsNone(self.accounts.password_reset_link("owner", "192.0.2.2"))
        recipient, token = self.accounts.email_verification_link(updated.token, None, PASSWORD)
        self.assertEqual(recipient, "owner@example.test")
        self.assertFalse(self.accounts.email_token_available(token, "reset"))
        self.accounts.verify_email_link(token)
        with self.assertRaises(AccountsError):
            self.accounts.verify_email_link(token)
        recipient, reset = self.accounts.password_reset_link("owner", "192.0.2.2")
        self.assertEqual(recipient, "owner@example.test")
        self.assertIsNone(self.accounts.password_reset_link("owner", "192.0.2.3"))
        self.accounts.redeem_password_reset(reset, "replacement-test-password")
        self.assertIsNone(self.accounts.session(updated.token))
        self.assertFalse(self.accounts.email_token_available(reset, "reset"))
        self.assertIsNone(self.accounts.login("owner", PASSWORD, "192.0.2.2"))
        self.assertIsNotNone(self.accounts.login("owner", "replacement-test-password", "192.0.2.2"))

    def test_invite_session_delete_and_id_reuse(self) -> None:
        admin = self.accounts.bootstrap_admin("owner", PASSWORD)
        token = self.accounts.invite(admin.id, "reader", "fixture", Role.READER)
        issued = self.accounts.redeem(token, PASSWORD)
        target = issued.session.account.id
        self.assertIsNone(self.accounts.invitation(token))
        self.assertEqual(
            self.accounts.session(issued.token).account.role_for("fixture"), Role.READER
        )
        comments = Comments(self.state)
        person = PersonID(20)
        actor = ReaderActor(target, "Test reader", False)
        draft = CommentDraft(body="Test comment")
        retained = comments.add(self.tree, person, actor, draft)
        removed = comments.add(self.tree, person, actor, draft)
        comments.remove(self.tree, person, removed.id, actor)
        with self.assertRaises(AccountsError):
            self.accounts.delete_account(admin.id, admin.id, self.state)
        self.accounts.delete_account(admin.id, target, self.state)
        self.assertIsNone(self.accounts.session(issued.token))
        self.assertIsNone(self.accounts.login("reader", PASSWORD, None))
        page = comments.page(
            self.tree, ReaderActor(admin.id, "Test admin", True), person, 25, None
        )
        self.assertEqual(
            [(row.id, row.author) for row in page.comments],
            [(retained.id, "Former member")],
        )
        with self.assertRaises(HTTPException) as denied:
            comments.add(self.tree, person, actor, draft)
        self.assertEqual(denied.exception.status_code, 401)
        replacement = self.accounts.redeem(
            self.accounts.invite(admin.id, "reader", "fixture", Role.READER), PASSWORD
        )
        self.assertGreater(replacement.session.account.id, target)

    def test_comment_quota_includes_removed_comments(self) -> None:
        comments = Comments(self.state)
        actor = ReaderActor(2, "Test reader", False)
        person = PersonID(20)
        for _ in range(20):
            row = comments.add(
                self.tree, person, actor, CommentDraft(body="Test comment")
            )
            comments.remove(self.tree, person, row.id, actor)
        with self.assertRaises(HTTPException) as denied:
            comments.add(self.tree, person, actor, CommentDraft(body="Over quota"))
        self.assertEqual(denied.exception.status_code, 429)

    async def test_accountless_mode_requires_loopback(self) -> None:
        access = ReaderAccess()
        for path in ("/login", "/api/v1/people/20"):
            for peer, status in (("203.0.113.9", 403), ("127.0.0.1", None)):
                request = Request(
                    {
                        "type": "http",
                        "scheme": "http",
                        "path": path,
                        "method": "GET",
                        "server": ("127.0.0.1", 8765),
                        "client": (peer, 12345),
                        "headers": [(b"host", b"localhost:8765")],
                    }
                )
                response = await access.guard(request)
                self.assertEqual(response.status_code if response else None, status)

    def test_external_search_chart_and_details_hide_private_rows(self) -> None:
        external = ExternalTree(self.tree.path)
        people = pedigree_search(external, "", 100, 0).people
        ids = {person.id for person in people}
        self.assertIn(20, ids)
        self.assertTrue(ids.isdisjoint({10, 11, 12, 13}))
        chart = pedigree(external, PersonID(20), 11, 11)
        details = pedigree_details(external, PersonID(20))
        self.assertIsNotNone(chart)
        self.assertIsNotNone(details)
        for record in (chart, details):
            self.assertNotIn("Secret", record.model_dump_json())
        self.assertIsNone(pedigree_details(external, PersonID(10)))
        self.assertIsNone(relationship_paths(external, PersonID(20), PersonID(12)))
        with connect(external) as db, self.assertRaises(sqlite3.DatabaseError):
            db.execute("SELECT Note FROM main.PersonTable").fetchall()

    def test_hosted_media_fails_closed_without_map(self) -> None:
        from pedigree_evidence import StoredMedia, recorded_path

        file = self.root / "sample.png"
        file.touch()
        media = StoredMedia(
            id=1, folder=str(self.root), filename=file.name, caption=None, url=None
        )
        with patch.dict(
            os.environ, {"ROOTSMAGIC_ACCOUNTS_DB": str(self.accounts.path)}, clear=True
        ):
            self.assertIsNone(recorded_path(self.tree.path, media))

    def test_thumbnail_is_bounded_and_rebuilt_when_source_changes(self) -> None:
        from pedigree_evidence import media_thumbnail

        file = self.root / "sample.png"
        Image.new("RGB", (1200, 900), "navy").save(file)
        preview = media_thumbnail(file)
        self.assertIsNotNone(preview)
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.size, (480, 360))
            self.assertEqual(image.format, "JPEG")
        Image.new("RGB", (1200, 900), "red").save(file)
        self.assertNotEqual(media_thumbnail(file), preview)
        file.write_bytes(b"Invalid image")
        self.assertIsNone(media_thumbnail(file))

    def test_api_key_rotation_revocation_and_wide_scope_expiry(self) -> None:
        admin = self.accounts.bootstrap_admin("owner", PASSWORD)
        keys = ApiKeys(self.accounts)
        with self.assertRaises(AccountsError):
            keys.create(admin.id, "Long key", "fixture", "family", 365)
        original = keys.create(admin.id, "Registry", "fixture", "family", 90)
        self.assertIsNotNone(keys.authenticate(original.secret))
        rotated = keys.rotate(admin.id, original.info.id, original.info.version)
        self.assertIsNone(keys.authenticate(original.secret))
        self.assertEqual(rotated.info.lifetime_days, 90)
        keys.revoke(admin.id, rotated.info.id, rotated.info.version)
        self.assertIsNone(keys.authenticate(rotated.secret))

    def test_citations_have_plain_text_in_the_shared_projection(self) -> None:
        citation = Citation(id=1, source="<b>Source</b>", name=None,
                            reference="1", footnote="A &amp; B")
        self.assertEqual(citation.source, "Source")
        self.assertEqual(citation.footnote, "A & B")
