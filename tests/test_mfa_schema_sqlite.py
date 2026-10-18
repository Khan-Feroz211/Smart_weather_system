"""
Phase 1 MFA schema tests, driven against a hermetic temporary SQLite database.

Run from the project root:  python -m unittest tests.test_mfa_schema_sqlite -v

Why SQLite here: db.py is PostgreSQL-only (psycopg2) and no database is
reachable from the test environment, so the authoritative Postgres DDL in
`supabase/schema.sql` section 10 cannot be executed. MFA_SQLITE_DDL below is a
TEST-ONLY translation of that section (text ids instead of uuid/gen_random_uuid,
CURRENT_TIMESTAMP instead of now()). `supabase/schema.sql` section 10 stays
authoritative -- when a table or constraint changes there, change it here too.

Hermetic: temp directory per test, no network, no credentials, no real DB. The
fixture secret is generated locally and is never printed.
"""
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from cryptography.fernet import Fernet  # noqa: E402

import mfa_crypto  # noqa: E402

# Test-only SQLite translation of supabase/schema.sql section 10.
MFA_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id text PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS mfa_factors (
    id               text PRIMARY KEY,
    user_id          text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    type             text NOT NULL CHECK (type IN ('totp', 'webauthn', 'recovery')),
    secret_encrypted text,
    label            text NOT NULL DEFAULT '',
    credential_id    text,
    public_key       text,
    sign_count       integer NOT NULL DEFAULT 0,
    transports       text,
    created_at       text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at     text,
    disabled_at      text,
    UNIQUE (user_id, type, label)
);
CREATE INDEX IF NOT EXISTS mfa_factors_user_id_idx    ON mfa_factors (user_id);
CREATE INDEX IF NOT EXISTS mfa_factors_created_at_idx ON mfa_factors (created_at DESC);

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    id         text PRIMARY KEY,
    user_id    text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    code_hash  text NOT NULL,
    used_at    text,
    created_at text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS mfa_recovery_codes_user_id_idx    ON mfa_recovery_codes (user_id);
CREATE INDEX IF NOT EXISTS mfa_recovery_codes_created_at_idx ON mfa_recovery_codes (created_at DESC);

CREATE TABLE IF NOT EXISTS mfa_used_totp_steps (
    user_id   text REFERENCES users (id) ON DELETE CASCADE,
    factor_id text NOT NULL REFERENCES mfa_factors (id) ON DELETE CASCADE,
    time_step integer NOT NULL,
    used_at   text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (factor_id, time_step)
);
CREATE INDEX IF NOT EXISTS mfa_used_totp_steps_user_id_idx ON mfa_used_totp_steps (user_id);

CREATE TABLE IF NOT EXISTS mfa_attempts (
    id           text PRIMARY KEY,
    user_id      text REFERENCES users (id) ON DELETE CASCADE,
    ip_hash      text,
    attempt_type text NOT NULL CHECK (attempt_type IN ('password', 'totp', 'recovery', 'webauthn', 'reset')),
    success      integer NOT NULL,
    created_at   text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS mfa_attempts_user_created_idx ON mfa_attempts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mfa_attempts_ip_created_idx   ON mfa_attempts (ip_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS mfa_attempts_created_at_idx   ON mfa_attempts (created_at DESC);

CREATE TABLE IF NOT EXISTS mfa_events (
    id              text PRIMARY KEY,
    event_type      text NOT NULL,
    actor           text,
    user_id         text REFERENCES users (id) ON DELETE SET NULL,
    ip_hash         text,
    user_agent_hash text,
    created_at      text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS mfa_events_user_id_idx    ON mfa_events (user_id);
CREATE INDEX IF NOT EXISTS mfa_events_created_at_idx ON mfa_events (created_at DESC);

CREATE TABLE IF NOT EXISTS trusted_devices (
    id             text PRIMARY KEY,
    user_id        text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash     text NOT NULL,
    ua_family      text,
    ip_prefix_hash text,
    device_label   text,
    created_at     text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at     text NOT NULL,
    revoked_at     text
);
CREATE INDEX IF NOT EXISTS trusted_devices_user_id_idx    ON trusted_devices (user_id);
CREATE INDEX IF NOT EXISTS trusted_devices_token_hash_idx ON trusted_devices (token_hash);
CREATE INDEX IF NOT EXISTS trusted_devices_expires_at_idx ON trusted_devices (expires_at);

CREATE TABLE IF NOT EXISTS mfa_login_challenges (
    challenge_id text PRIMARY KEY,
    user_id      text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    state        text NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending', 'verified', 'expired', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    expires_at   text NOT NULL,
    created_at   text NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS mfa_login_challenges_user_id_idx    ON mfa_login_challenges (user_id);
CREATE INDEX IF NOT EXISTS mfa_login_challenges_expires_at_idx ON mfa_login_challenges (expires_at);

CREATE TABLE IF NOT EXISTS mfa_pending_enrollments (
    id               text PRIMARY KEY,
    user_id          text NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    secret_encrypted text NOT NULL,
    created_at       text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       text NOT NULL
);
CREATE INDEX IF NOT EXISTS mfa_pending_enrollments_expires_at_idx ON mfa_pending_enrollments (expires_at);

CREATE TABLE IF NOT EXISTS mfa_outbox (
    id         text PRIMARY KEY,
    user_id    text REFERENCES users (id) ON DELETE CASCADE,
    kind       text NOT NULL,
    payload    text,
    status     text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
    created_at text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at    text
);
CREATE INDEX IF NOT EXISTS mfa_outbox_user_id_idx    ON mfa_outbox (user_id);
CREATE INDEX IF NOT EXISTS mfa_outbox_created_at_idx ON mfa_outbox (created_at DESC);

CREATE TABLE IF NOT EXISTS mfa_reset_requests (
    id                 text PRIMARY KEY,
    user_id            text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    cancel_token_hash  text NOT NULL,
    confirm_token_hash text NOT NULL,
    requested_at       text NOT NULL DEFAULT CURRENT_TIMESTAMP,
    eligible_at        text NOT NULL,
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'cancelled', 'completed', 'expired')),
    used_at            text
);
CREATE INDEX IF NOT EXISTS mfa_reset_requests_user_id_idx      ON mfa_reset_requests (user_id);
CREATE INDEX IF NOT EXISTS mfa_reset_requests_requested_at_idx ON mfa_reset_requests (requested_at DESC);
"""

MFA_TABLES = (
    "mfa_factors", "mfa_recovery_codes", "mfa_used_totp_steps", "mfa_attempts",
    "mfa_events", "trusted_devices", "mfa_login_challenges",
    "mfa_pending_enrollments", "mfa_outbox", "mfa_reset_requests",
)

USER_ID = "11111111-1111-1111-1111-111111111111"


def make_temp_db(test_case):
    """Create a temp SQLite DB with the Phase 1 DDL applied. Returns (path, conn)."""
    tmpdir = tempfile.mkdtemp(prefix="mfa_schema_test_")
    test_case.addCleanup(shutil.rmtree, tmpdir, True)
    path = os.path.join(tmpdir, "mfa_test.db")
    conn = sqlite3.connect(path)
    test_case.addCleanup(conn.close)
    conn.executescript(MFA_SQLITE_DDL)
    conn.execute("INSERT INTO users (id) VALUES (?)", (USER_ID,))
    conn.commit()
    return path, conn


class TestSchemaApplies(unittest.TestCase):
    def setUp(self):
        self.path, self.conn = make_temp_db(self)

    def _names(self, kind):
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
        return {r[0] for r in rows}

    def test_all_tables_created(self):
        names = self._names("table")
        for table in MFA_TABLES:
            self.assertIn(table, names)

    def test_indexes_created(self):
        names = self._names("index")
        for expected in ("mfa_factors_user_id_idx", "mfa_attempts_ip_created_idx",
                         "trusted_devices_token_hash_idx",
                         "mfa_login_challenges_expires_at_idx",
                         "mfa_pending_enrollments_expires_at_idx",
                         "mfa_events_created_at_idx"):
            self.assertIn(expected, names)

    def test_reapplying_the_ddl_is_a_no_op(self):
        self.conn.executescript(MFA_SQLITE_DDL)   # CREATE ... IF NOT EXISTS
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM users").fetchone()[0], 1)

    def test_existing_users_table_still_works(self):
        self.conn.execute("INSERT INTO users (id) VALUES ('22222222-2222-2222-2222-222222222222')")
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM users").fetchone()[0], 2)


class TestConstraints(unittest.TestCase):
    def setUp(self):
        self.path, self.conn = make_temp_db(self)

    def _insert_factor(self, factor_id="f1", factor_type="totp", label="phone"):
        self.conn.execute(
            "INSERT INTO mfa_factors (id, user_id, type, label) VALUES (?, ?, ?, ?)",
            (factor_id, USER_ID, factor_type, label))
        self.conn.commit()

    def test_factor_unique_user_type_label(self):
        self._insert_factor()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_factor(factor_id="f2")

    def test_factor_type_check_rejects_unknown_type(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_factor(factor_type="carrier-pigeon")

    def test_used_totp_step_composite_primary_key(self):
        self._insert_factor()
        self.conn.execute(
            "INSERT INTO mfa_used_totp_steps (user_id, factor_id, time_step) VALUES (?, ?, ?)",
            (USER_ID, "f1", 58000000))
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO mfa_used_totp_steps (user_id, factor_id, time_step) VALUES (?, ?, ?)",
                (USER_ID, "f1", 58000000))

    def test_pending_enrollment_one_per_user(self):
        for row_id in ("p1", "p2"):
            stmt = ("INSERT INTO mfa_pending_enrollments (id, user_id, secret_encrypted, "
                    "expires_at) VALUES (?, ?, ?, ?)")
            if row_id == "p1":
                self.conn.execute(stmt, (row_id, USER_ID, "ciphertext", "2099-01-01"))
                self.conn.commit()
            else:
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(stmt, (row_id, USER_ID, "ciphertext", "2099-01-01"))

    def test_challenge_state_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO mfa_login_challenges (challenge_id, user_id, state, expires_at) "
                "VALUES (?, ?, ?, ?)", ("c1", USER_ID, "not-a-state", "2099-01-01"))

    def test_outbox_status_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO mfa_outbox (id, user_id, kind, status) VALUES (?, ?, ?, ?)",
                ("o1", USER_ID, "lost_device_request", "nope"))

    def test_reset_request_status_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO mfa_reset_requests (id, user_id, cancel_token_hash, "
                "confirm_token_hash, eligible_at, status) VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", USER_ID, "h1", "h2", "2099-01-01", "nope"))


class TestSecretNotPlaintextOnDisk(unittest.TestCase):
    """The headline guarantee: a stored TOTP secret is not readable in the DB file."""

    def setUp(self):
        self.path, self.conn = make_temp_db(self)
        self.key = Fernet.generate_key().decode()
        mfa_crypto.reset_cache()
        self.addCleanup(mfa_crypto.reset_cache)

    def test_secret_bytes_absent_from_the_database_file(self):
        # Locally generated throwaway secret; never printed.
        plain = secrets.token_hex(16).upper()
        with mock.patch.dict(os.environ,
                             {mfa_crypto.ENCRYPTION_KEY_ENV: self.key}, clear=False):
            ciphertext = mfa_crypto.encrypt_secret(plain)
            self.conn.execute(
                "INSERT INTO mfa_factors (id, user_id, type, label, secret_encrypted) "
                "VALUES (?, ?, 'totp', 'phone', ?)", ("f1", USER_ID, ciphertext))
            self.conn.execute(
                "INSERT INTO mfa_pending_enrollments (id, user_id, secret_encrypted, expires_at) "
                "VALUES (?, ?, ?, '2099-01-01')", ("p1", USER_ID, ciphertext))
            self.conn.commit()
            self.conn.close()

            with open(self.path, "rb") as fh:
                raw = fh.read()
            self.assertNotIn(plain.encode("ascii"), raw)
            self.assertNotIn(plain.encode("utf-16-le"), raw)

            reopened = sqlite3.connect(self.path)
            self.addCleanup(reopened.close)
            self.assertEqual(
                reopened.execute("SELECT count(*) FROM mfa_factors").fetchone()[0], 1)
            self.assertEqual(
                reopened.execute(
                    "SELECT count(*) FROM mfa_pending_enrollments").fetchone()[0], 1)
            stored = reopened.execute(
                "SELECT secret_encrypted FROM mfa_factors WHERE id = 'f1'").fetchone()[0]
            self.assertEqual(mfa_crypto.decrypt_secret(stored), plain)

    def test_recovery_codes_store_only_hashes(self):
        from argon2 import PasswordHasher
        code = secrets.token_hex(8)
        code_hash = PasswordHasher().hash(code)
        self.conn.execute(
            "INSERT INTO mfa_recovery_codes (id, user_id, code_hash) VALUES (?, ?, ?)",
            ("rc1", USER_ID, code_hash))
        self.conn.commit()
        self.conn.close()
        with open(self.path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(code.encode("ascii"), raw)


if __name__ == "__main__":
    unittest.main()
