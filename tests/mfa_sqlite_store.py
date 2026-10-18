"""
Hermetic SQLite-backed MFA store for the Phase 2/3 tests.

Why this exists: db.py is PostgreSQL-only (psycopg2) and no database is
reachable from the test environment, so the atomicity of the replay primitive
(`INSERT ... ON CONFLICT DO NOTHING` under concurrency) cannot be proven against
the real backend, and a mock would only prove the mock.  This module implements
the handful of accessors Phase 2/3 needs against a real sqlite3 file, with the
same signatures as their db.py counterparts, so they can be injected through the
`consume_step` / `log_event` / `recent_events` hooks of `totp_auth.verify_totp`
and the db hooks of `mfa_rate_limit`.

`MFA_SQLITE_DDL` is imported from tests.test_mfa_schema_sqlite on purpose: there
is exactly ONE test-only translation of supabase/schema.sql section 10, and
`supabase/schema.sql` stays authoritative.

Hermetic: one temp directory per instance, no network, no credentials, nothing
printed.  Secrets, codes and raw IPs are never passed to or stored by anything
here -- `ip_hash` is already a hash, like in db.py.

Foreign keys are intentionally left at sqlite's default (off), matching
tests/test_mfa_schema_sqlite.py, so a test can claim a time step for a synthetic
factor id without first materialising users/mfa_factors rows.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:  # importable as tests.mfa_sqlite_store or as a top-level module
    from tests.test_mfa_schema_sqlite import MFA_SQLITE_DDL
except ImportError:  # pragma: no cover
    from test_mfa_schema_sqlite import MFA_SQLITE_DDL


def sqlite_timestamp(value=None):
    """Render *value* the way sqlite's CURRENT_TIMESTAMP does (UTC, second precision)."""
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class SqliteMfaStore:
    """A real sqlite MFA store with db.py-compatible accessor signatures."""

    def __init__(self):
        self._dir = tempfile.mkdtemp(prefix="mfa_store_")
        self.path = os.path.join(self._dir, "mfa_test.db")
        self.calls = []                  # accessor names, so tests can assert "no DB access"
        self._lock = threading.Lock()
        self._local = threading.local()
        self._conns = []
        conn = self._conn()
        conn.executescript(MFA_SQLITE_DDL)

    # -- plumbing ----------------------------------------------------------
    def _conn(self):
        """One connection per thread (the concurrency test needs genuinely separate handles)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # isolation_level=None -> autocommit, so our explicit BEGIN IMMEDIATE
            # is the only transaction in play.  check_same_thread=False lets the
            # owning test close every handle from one place.
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Durability is irrelevant here: the file lives in a temp dir that is
            # deleted on teardown, and one fsync per INSERT costs ~13 ms, which
            # would make the Phase 3 suite take tens of seconds.  Locking and the
            # PRIMARY KEY conflict -- the atomic gate the concurrency test proves
            # -- are unaffected by these pragmas.
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA journal_mode=MEMORY")
            self._local.conn = conn
            with self._lock:
                self._conns.append(conn)
        return conn

    def _record(self, name):
        with self._lock:
            self.calls.append(name)

    def inspect(self, sql, params=()):
        """Read rows for assertions WITHOUT appearing in `calls`."""
        return self._conn().execute(sql, tuple(params)).fetchall()

    def close(self):
        with self._lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        shutil.rmtree(self._dir, ignore_errors=True)

    # -- accessors (same signatures as db.py) ------------------------------
    def try_consume_totp_step(self, factor_id, time_step, user_id=None):
        """Claim a (factor, time_step) pair. True = newly recorded, False = replay.

        BEGIN IMMEDIATE + INSERT, with the PRIMARY KEY (factor_id, time_step)
        violation as the gate -- the sqlite equivalent of Postgres's
        ON CONFLICT DO NOTHING RETURNING.  The insert IS the decision, so two
        threads cannot both win.
        """
        self._record("try_consume_totp_step")
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO mfa_used_totp_steps (user_id, factor_id, time_step) "
                "VALUES (?, ?, ?)",
                (user_id, str(factor_id), int(time_step)))
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return False
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return True

    def record_attempt(self, attempt_type, success, user_id=None, ip_hash=None,
                       created_at=None):
        """Append an attempt row. `ip_hash` is already hashed -- never a raw IP.

        `created_at` is a test-only extra so a suite can inject a fixed clock
        instead of sleeping.
        """
        self._record("record_attempt")
        self._conn().execute(
            "INSERT INTO mfa_attempts (id, user_id, ip_hash, attempt_type, success, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, user_id, ip_hash, attempt_type, 1 if success else 0,
             sqlite_timestamp(created_at)))

    def recent_attempts(self, since, user_id=None, ip_hash=None, limit=200):
        """Attempt rows since `since`, newest first."""
        self._record("recent_attempts")
        sql = ("SELECT attempt_type, success, created_at FROM mfa_attempts "
               "WHERE created_at >= ?")
        params = [sqlite_timestamp(since)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if ip_hash is not None:
            sql += " AND ip_hash = ?"
            params.append(ip_hash)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))
        return self._conn().execute(sql, tuple(params)).fetchall()

    def count_recent_attempts(self, since, user_id=None, ip_hash=None,
                              attempt_type=None, success=None):
        """Count attempts since `since`, filtered by whatever is given."""
        self._record("count_recent_attempts")
        sql = "SELECT count(*) FROM mfa_attempts WHERE created_at >= ?"
        params = [sqlite_timestamp(since)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if ip_hash is not None:
            sql += " AND ip_hash = ?"
            params.append(ip_hash)
        if attempt_type is not None:
            sql += " AND attempt_type = ?"
            params.append(attempt_type)
        if success is not None:
            sql += " AND success = ?"
            params.append(1 if success else 0)
        row = self._conn().execute(sql, tuple(params)).fetchone()
        return int(row[0]) if row else 0

    def log_event(self, event_type, actor=None, user_id=None, ip_hash=None,
                  user_agent_hash=None, created_at=None):
        """Append one row to the append-only audit log (event name + hashes only)."""
        self._record("log_event")
        self._conn().execute(
            "INSERT INTO mfa_events (id, event_type, actor, user_id, ip_hash, "
            "user_agent_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, event_type, actor, user_id, ip_hash, user_agent_hash,
             sqlite_timestamp(created_at)))

    def recent_events(self, user_id=None, limit=100, event_type=None):
        """Read back the audit log, newest first.

        `rowid DESC` breaks the ties that CURRENT_TIMESTAMP's one-second
        granularity would otherwise produce inside a single test.
        """
        self._record("recent_events")
        sql = ("SELECT id, event_type, actor, user_id, ip_hash, user_agent_hash, "
               "created_at FROM mfa_events")
        clauses = []
        params = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))
        return self._conn().execute(sql, tuple(params)).fetchall()
