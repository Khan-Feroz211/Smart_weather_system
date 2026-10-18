"""
Tests for scripts/rotate_mfa_key.py, driven against a hermetic temp SQLite DB.

Run from the project root:  python -m unittest tests.test_rotate_mfa_key -v

The pure core `rotate_rows(conn, placeholder, dry_run)` is exercised with
placeholder "?" against the same test-only DDL used by
tests/test_mfa_schema_sqlite.py. No db.connect() call is made (psycopg2 is not
installed), no network, no credentials. Fixture keys and secrets are generated
per test and are never printed.
"""
import contextlib
import io
import os
import secrets as pysecrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from cryptography.fernet import Fernet  # noqa: E402

import mfa_crypto  # noqa: E402
import rotate_mfa_key  # noqa: E402  (scripts/ is on sys.path)

try:  # works under `python -m unittest discover` and as a plain module
    from tests.test_mfa_schema_sqlite import MFA_SQLITE_DDL, USER_ID
except ImportError:  # pragma: no cover
    from test_mfa_schema_sqlite import MFA_SQLITE_DDL, USER_ID

SQLITE_TARGETS = (
    ("mfa_factors", "id", "secret_encrypted"),
    ("mfa_pending_enrollments", "id", "secret_encrypted"),
)


class RotateBase(unittest.TestCase):
    def setUp(self):
        self.key_old = Fernet.generate_key().decode()
        self.key_new = Fernet.generate_key().decode()
        self.plain = pysecrets.token_hex(16).upper()   # throwaway, never printed
        mfa_crypto.reset_cache()
        self.addCleanup(mfa_crypto.reset_cache)

        tmpdir = tempfile.mkdtemp(prefix="mfa_rotate_test_")
        self.addCleanup(shutil.rmtree, tmpdir, True)
        self.path = os.path.join(tmpdir, "rotate_test.db")
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executescript(MFA_SQLITE_DDL)
        self.conn.execute("INSERT INTO users (id) VALUES (?)", (USER_ID,))
        self.conn.commit()

    def env(self, key_string):
        return mock.patch.dict(
            os.environ, {mfa_crypto.ENCRYPTION_KEY_ENV: key_string}, clear=False)

    def seed_under_old_key(self):
        mfa_crypto.reset_cache()
        with self.env(self.key_old):
            token = mfa_crypto.encrypt_secret(self.plain)
        mfa_crypto.reset_cache()
        self.conn.execute(
            "INSERT INTO mfa_factors (id, user_id, type, label, secret_encrypted) "
            "VALUES ('f1', ?, 'totp', 'phone', ?)", (USER_ID, token))
        self.conn.execute(
            "INSERT INTO mfa_pending_enrollments (id, user_id, secret_encrypted, expires_at) "
            "VALUES ('p1', ?, ?, '2099-01-01')", (USER_ID, token))
        self.conn.commit()
        return token

    def stored(self, table="mfa_factors", row_id="f1"):
        return self.conn.execute(
            f"SELECT secret_encrypted FROM {table} WHERE id = ?", (row_id,)).fetchone()[0]

    def rotate(self, key_string, dry_run=False):
        mfa_crypto.reset_cache()
        with self.env(key_string):
            counts = rotate_mfa_key.rotate_rows(
                self.conn, placeholder="?", dry_run=dry_run, targets=SQLITE_TARGETS)
        self.conn.commit()
        mfa_crypto.reset_cache()
        return counts


class TestRotateRows(RotateBase):
    def test_reencrypts_to_the_new_primary(self):
        old_token = self.seed_under_old_key()
        counts = self.rotate(f"{self.key_new},{self.key_old}")
        self.assertEqual(counts["reencrypted"], 2)
        self.assertEqual(counts["already_current"], 0)
        self.assertEqual(counts["scanned"], 2)
        new_token = self.stored()
        self.assertNotEqual(new_token, old_token)
        # The rotated value now decrypts under the NEW key alone.
        mfa_crypto.reset_cache()
        with self.env(self.key_new):
            self.assertEqual(mfa_crypto.decrypt_secret(new_token), self.plain)

    def test_is_idempotent(self):
        self.seed_under_old_key()
        self.rotate(f"{self.key_new},{self.key_old}")
        second = self.rotate(f"{self.key_new},{self.key_old}")
        self.assertEqual(second["reencrypted"], 0)
        self.assertEqual(second["already_current"], 2)

    def test_dry_run_leaves_ciphertext_untouched(self):
        old_token = self.seed_under_old_key()
        counts = self.rotate(f"{self.key_new},{self.key_old}", dry_run=True)
        self.assertEqual(counts["reencrypted"], 2)
        self.assertEqual(self.stored(), old_token)
        self.assertEqual(self.stored("mfa_pending_enrollments", "p1"), old_token)

    def test_null_secret_is_skipped(self):
        self.conn.execute(
            "INSERT INTO mfa_factors (id, user_id, type, label) "
            "VALUES ('f9', ?, 'webauthn', 'key')", (USER_ID,))
        self.conn.commit()
        counts = self.rotate(f"{self.key_new},{self.key_old}")
        self.assertEqual(counts["skipped_null"], 1)
        self.assertEqual(counts["reencrypted"], 0)

    def test_undecryptable_row_fails_clearly_without_values(self):
        self.seed_under_old_key()
        unrelated = Fernet.generate_key().decode()
        with self.assertRaises(rotate_mfa_key.RotationError) as ctx:
            self.rotate(f"{self.key_new},{unrelated}")
        message = str(ctx.exception)
        self.assertIn("mfa_factors", message)
        self.assertIn("f1", message)
        self.assertNotIn(self.plain, message)
        self.assertNotIn(self.key_new, message)
        self.assertNotIn(unrelated, message)


class TestSingleKeyRefusal(RotateBase):
    def test_refuses_single_key_without_force(self):
        with self.env(self.key_new):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    rotate_mfa_key.main([])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("--force", out.getvalue())
            self.assertNotIn(self.key_new, out.getvalue())

    def test_missing_key_variable_exits(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    rotate_mfa_key.main([])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn(mfa_crypto.ENCRYPTION_KEY_ENV, out.getvalue())


class TestOutputHygiene(RotateBase):
    def test_printed_counts_contain_no_secret_material(self):
        self.seed_under_old_key()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            counts = self.rotate(f"{self.key_new},{self.key_old}")
            rotate_mfa_key._print_counts(counts, dry_run=False)
        printed = out.getvalue()
        self.assertIn("re-encrypted", printed)
        for forbidden in (self.plain, self.key_new, self.key_old, self.stored()):
            self.assertNotIn(forbidden, printed)


if __name__ == "__main__":
    unittest.main()
