"""
db.py
=====
Single access point to the Supabase PostgreSQL database.

Why a direct PostgreSQL connection (and not only the Supabase REST client)?
The application runs ~150 hand-written SQL statements (joins, GROUP BY, AVG,
transactions).  Those need real SQL, which Supabase exposes through its
PostgreSQL endpoint.  The connection string is a *backend secret* (like the
service-role key) and is never sent to the browser.

Supabase Auth (login) is handled separately in `supabase_client.py`.

Compatibility notes (so existing routes/templates keep working unchanged):
  * Rows behave like `sqlite3.Row`: `row['col']`, `row[0]`, `dict(row)`.
  * `timestamptz` / `timestamp` columns are returned as 'YYYY-MM-DD HH:MM:SS'
    strings (UTC) -- the templates slice them (`created_at[:16]`).
  * `json` / `jsonb` columns are returned as raw JSON text -- existing code
    calls `json.loads(...)` on them.
  * `conn.execute(sql, params)` exists and returns a cursor, like sqlite3.
  * Use `%s` placeholders (never string-format user input into SQL).
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

try:  # keep the module importable (for tests / tooling) without the driver
    import psycopg2
    import psycopg2.extensions as _ext
    import psycopg2.pool as _pool
    IntegrityError = psycopg2.IntegrityError
    DatabaseError = psycopg2.Error
except ImportError:  # pragma: no cover - exercised only when driver missing
    psycopg2 = None
    _ext = None
    _pool = None

    class IntegrityError(Exception):
        pass

    class DatabaseError(Exception):
        pass

logger = logging.getLogger(__name__)

_PLACEHOLDER_MARKERS = ("YOUR_", "[YOUR", "<your", "your-password", "example.com")


class DatabaseUnavailable(RuntimeError):
    """Raised when the database is not configured or cannot be reached."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def _dsn():
    value = (os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not value or any(m.lower() in value.lower() for m in _PLACEHOLDER_MARKERS):
        return None
    return value


def is_configured():
    return _dsn() is not None


def _candidate_dsns(dsn):
    """Return the original DSN first, then a list of Supabase session-pooler fallbacks."""
    dsn = (dsn or "").strip()
    if not dsn:
        return []
    parts = urlsplit(dsn)
    host = (parts.hostname or "").lower()
    if not host.endswith(".supabase.co") or not host.startswith("db."):
        return [dsn]
    ref = host[len("db.") : -len(".supabase.co")]
    username = parts.username or "postgres"
    if username == "postgres":
        username = f"postgres.{ref}"
    password = parts.password or ""
    database = parts.path.lstrip("/") or "postgres"
    port = parts.port or 5432
    regions = (
        "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
        "eu-west-1", "eu-west-2", "eu-central-1", "ap-southeast-1",
        "ap-southeast-2", "ap-northeast-1", "ap-south-1", "sa-east-1",
    )
    poolers = [
        f"postgresql://{username}:{quote(password)}@aws-0-{region}.pooler.supabase.com:{port}/{database}"
        for region in regions
    ]
    return [dsn] + poolers


def _pick_working_dsn(dsn):
    for candidate in _candidate_dsns(dsn):
        try:
            if psycopg2 is None:
                return candidate
            with psycopg2.connect(candidate, connect_timeout=3, application_name="smart-weather-system"):
                pass
            return candidate
        except Exception:
            continue
    return dsn


# --------------------------------------------------------------------------
# sqlite3.Row-compatible row object
# --------------------------------------------------------------------------
class Row:
    __slots__ = ("_keys", "_values", "_index")

    def __init__(self, keys, values):
        self._keys = keys
        self._values = tuple(values)
        self._index = {k: i for i, k in enumerate(keys)}

    def __getitem__(self, item):
        if isinstance(item, (int, slice)):
            return self._values[item]
        try:
            return self._values[self._index[item]]
        except KeyError:
            raise IndexError(f"No item with that key: {item!r}") from None

    def keys(self):
        return list(self._keys)

    def get(self, key, default=None):
        i = self._index.get(key)
        return default if i is None else self._values[i]

    def items(self):
        return list(zip(self._keys, self._values))

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Row({dict(zip(self._keys, self._values))!r})"


# --------------------------------------------------------------------------
# Driver setup (only when psycopg2 is present)
# --------------------------------------------------------------------------
if psycopg2 is not None:
    # timestamptz(1184)/timestamp(1114) -> 'YYYY-MM-DD HH:MM:SS' (session TZ is UTC)
    _TS_STR = _ext.new_type((1184, 1114), "TS_STR", lambda v, cur: v[:19] if v else None)
    _DATE_STR = _ext.new_type((1082,), "DATE_STR", lambda v, cur: v)
    _JSON_STR = _ext.new_type((114, 3802), "JSON_STR", lambda v, cur: v)
    for _t in (_TS_STR, _DATE_STR, _JSON_STR):
        _ext.register_type(_t)

    class CursorWrapper:
        """Thin Python adapter that keeps sqlite-like row semantics without subclassing psycopg2 cursors."""

        def __init__(self, raw):
            self._raw = raw
            self._cols = [d[0] for d in getattr(raw, "description", None) or []]

        def execute(self, query, vars=None):
            self._raw.execute(query, vars)
            self._cols = [d[0] for d in getattr(self._raw, "description", None) or []]
            return self

        def _wrap(self, values):
            if values is None:
                return None
            if isinstance(values, Row):
                return values
            return Row(self._cols, values) if self._cols else values

        def fetchone(self):
            return self._wrap(self._raw.fetchone())

        def fetchall(self):
            return [self._wrap(v) for v in self._raw.fetchall()]

        def fetchmany(self, size=None):
            rows = self._raw.fetchmany(size) if size is not None else self._raw.fetchmany()
            return [self._wrap(v) for v in rows]

        def __iter__(self):
            return iter(self.fetchall())

        def __getattr__(self, name):
            return getattr(self._raw, name)

        def close(self):
            return self._raw.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._raw.__exit__(exc_type, exc, tb)
else:  # pragma: no cover
    CursorWrapper = None


class Connection:
    """Thin wrapper around a pooled psycopg2 connection (sqlite-like API)."""

    def __init__(self, raw, pool):
        self._raw = raw
        self._pool = pool
        self._closed = False

    def cursor(self):
        raw = self._raw.cursor()
        return CursorWrapper(raw) if CursorWrapper is not None else raw

    def execute(self, sql, params=None):
        cur = self.cursor()
        try:
            cur.execute(sql, params)
        except Exception:
            cur.close()
            raise
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        """Return the connection to the pool (rolls back anything uncommitted)."""
        if self._closed:
            return
        self._closed = True
        try:
            if not self._raw.closed:
                self._raw.rollback()
        except Exception:
            pass
        _last_used[id(self._raw)] = time.monotonic()
        try:
            self._pool.putconn(self._raw, close=bool(self._raw.closed))
        except Exception as exc:  # pool already closed etc.
            logger.debug("putconn failed: %s", exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self._raw.rollback()
            except Exception:
                pass
        self.close()
        return False


# --------------------------------------------------------------------------
# Pool
# --------------------------------------------------------------------------
_pool_obj = None
_pool_lock = threading.Lock()
_last_used = {}
last_error = None
_IDLE_PING_SECONDS = 30


def _get_pool():
    global _pool_obj
    if _pool_obj is not None:
        return _pool_obj
    if psycopg2 is None:
        raise DatabaseUnavailable("psycopg2 is not installed (pip install -r requirements.txt)")
    dsn = _dsn()
    if not dsn:
        raise DatabaseUnavailable("SUPABASE_DB_URL is not configured")
    chosen_dsn = _pick_working_dsn(dsn)
    with _pool_lock:
        if _pool_obj is None:
            try:
                _pool_obj = _pool.ThreadedConnectionPool(
                    int(os.environ.get("DB_POOL_MIN", "1")),
                    int(os.environ.get("DB_POOL_MAX", "8")),
                    chosen_dsn,
                    connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
                    application_name="smart-weather-system",
                    options="-c timezone=UTC -c statement_timeout=%d"
                            % int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "20000")),
                    keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                )
            except Exception as exc:
                # Never leak the DSN (it contains the password). Give a specific,
                # actionable message for the most common failure: the connection
                # never got as far as talking to Postgres at all (DNS / network),
                # which almost always means the *direct* db.<ref>.supabase.co host
                # is being used from a network that can't reach it over IPv6.
                text = str(exc)
                if "could not translate host name" in text or "Name or service not known" in text \
                        or "getaddrinfo failed" in text:
                    raise DatabaseUnavailable(
                        "Could not resolve the Supabase database hostname (DNS lookup failed). "
                        "This usually means SUPABASE_DB_URL is using the *direct* connection host "
                        "(db.<project-ref>.supabase.co), which is IPv6-only on most networks. "
                        "The app now tries the Session Pooler fallback automatically; if it still fails, "
                        "switch to the Session Pooler connection string from the Supabase dashboard "
                        "(Project Settings -> Database -> Connection string -> 'Session pooler') "
                        "and update SUPABASE_DB_URL in .env. See supabase/migration_notes.md."
                    ) from exc
                raise DatabaseUnavailable(f"Could not connect to the database: {type(exc).__name__}: {text}") from exc
    return _pool_obj


def reset_pool():
    """Close the pool (used by tests and after credential changes)."""
    global _pool_obj
    with _pool_lock:
        if _pool_obj is not None:
            try:
                _pool_obj.closeall()
            except Exception:
                pass
        _pool_obj = None


def connect():
    """Return a pooled `Connection`; raises `DatabaseUnavailable` on failure."""
    global last_error
    pool = _get_pool()
    for attempt in (1, 2):
        try:
            raw = pool.getconn()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            raise DatabaseUnavailable(last_error) from exc
        try:
            idle = time.monotonic() - _last_used.get(id(raw), 0)
            if raw.closed or idle > _IDLE_PING_SECONDS:
                if raw.closed:
                    raise psycopg2.OperationalError("connection closed")
                with raw.cursor() as cur:  # cheap liveness check for idle sockets
                    cur.execute("SELECT 1")
                raw.rollback()
            last_error = None
            return Connection(raw, pool)
        except Exception as exc:
            try:
                pool.putconn(raw, close=True)
            except Exception:
                pass
            if attempt == 2:
                last_error = f"{type(exc).__name__}: {exc}"
                raise DatabaseUnavailable(last_error) from exc
    raise DatabaseUnavailable("unreachable")  # pragma: no cover


def get_db_connection():
    """Legacy-friendly helper: returns a Connection or None (never raises)."""
    try:
        return connect()
    except DatabaseUnavailable as exc:
        logger.error("Database connection error: %s", exc)
        print(f"Database connection error: {exc}")
        return None


@contextmanager
def transaction():
    """`with db.transaction() as conn:` -> commit on success, rollback on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping():
    """(ok, message) health probe for the admin settings page."""
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True, "connected"
    except DatabaseUnavailable as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def as_utc(value):
    """
    Convert a datetime / ISO string to an aware UTC datetime for timestamptz
    columns.  Naive values are assumed to be *server-local* (that is what
    `datetime.now()` produces throughout this code base).  Returns None if the
    value is empty or unparseable.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.astimezone()  # interpret naive time as local time
    return value.astimezone(timezone.utc)


def like_escape(text):
    """Escape user text for use inside an ILIKE '%...%' parameter."""
    return (text or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_for_json(value):
    """Recursively make a value JSON-safe (NaN/Inf -> None, numpy/datetime -> plain)."""
    if isinstance(value, dict):
        return {str(k): _clean_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean_for_json(v) for v in value]
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):            # numpy scalar
        try:
            return _clean_for_json(value.item())
        except Exception:
            pass
    if hasattr(value, "tolist"):          # numpy array
        try:
            return _clean_for_json(value.tolist())
        except Exception:
            pass
    return str(value)


def safe_json(value):
    """
    json.dumps that can never fail on NaN / numpy / datetime values.
    PostgreSQL `jsonb` rejects the non-standard `NaN` token that json.dumps
    emits by default, so every value stored in a jsonb column goes through here.
    """
    import json
    return json.dumps(_clean_for_json(value), allow_nan=False)


def query_df(sql, params=None):
    """Run a SELECT and return a pandas DataFrame (empty frame keeps the column names)."""
    import pandas as pd
    with connect() as conn:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


# --------------------------------------------------------------------------
# 10. MFA (Phase 1) accessors
# --------------------------------------------------------------------------
# Thin accessors for the mfa_* / trusted_devices tables defined in
# supabase/schema.sql section 10.  Phase 1 additions: NO route calls these yet.
# They raise DatabaseUnavailable like everything else in this module, use %s
# placeholders, and no function here logs, prints or returns its arguments --
# the values passed in are secrets, hashes or tokens.
#
# public.users.totp_secret is deliberately untouched by all of these: it remains
# the live store for the current TOTP flow and the migration source for a later
# phase.
#
# mfa_events is APPEND-ONLY: only mfa_log_event (INSERT) and mfa_recent_events
# (SELECT) exist on purpose.  No update/delete helper is provided; a retention
# job in a later phase is the only thing allowed to delete.
# --------------------------------------------------------------------------

def mfa_insert_factor(user_id, factor_type, secret_encrypted=None, label="",
                      credential_id=None, public_key=None, transports=None):
    """Insert a second-factor row and return its id (text). Never logs its arguments."""
    with transaction() as conn:
        row = conn.execute(
            "INSERT INTO mfa_factors (user_id, type, secret_encrypted, label, "
            "credential_id, public_key, transports) "
            "VALUES (%s::uuid, %s, %s, %s, %s, %s, %s) RETURNING id::text",
            (user_id, factor_type, secret_encrypted, label or "",
             credential_id, public_key, transports),
        ).fetchone()
        return row[0] if row else None


def mfa_get_factors(user_id, include_disabled=False):
    """Return the user's factor rows (active only by default). Never logs its arguments."""
    sql = ("SELECT id::text AS id, user_id::text AS user_id, type, secret_encrypted, label, "
           "credential_id, public_key, sign_count, transports, created_at, last_used_at, "
           "disabled_at FROM mfa_factors WHERE user_id = %s::uuid")
    if not include_disabled:
        sql += " AND disabled_at IS NULL"
    sql += " ORDER BY created_at"
    with connect() as conn:
        return conn.execute(sql, (user_id,)).fetchall()


def mfa_disable_factor(factor_id, user_id=None):
    """Soft-delete a factor (sets disabled_at). Returns the number of rows changed."""
    sql = "UPDATE mfa_factors SET disabled_at = now() WHERE id = %s::uuid AND disabled_at IS NULL"
    params = [factor_id]
    if user_id is not None:
        sql += " AND user_id = %s::uuid"
        params.append(user_id)
    with transaction() as conn:
        return conn.execute(sql, tuple(params)).rowcount


def mfa_touch_factor_last_used(factor_id):
    """Stamp last_used_at on a factor. Returns the number of rows changed."""
    with transaction() as conn:
        return conn.execute(
            "UPDATE mfa_factors SET last_used_at = now() WHERE id = %s::uuid",
            (factor_id,),
        ).rowcount


def mfa_insert_recovery_codes(user_id, code_hashes):
    """Store Argon2id hashes of freshly issued recovery codes.

    `code_hashes` must already be hashes -- plain codes are never accepted,
    stored or logged. Returns the number of rows inserted.
    """
    hashes = [h for h in (code_hashes or []) if h]
    if not hashes:
        return 0
    with transaction() as conn:
        for code_hash in hashes:
            conn.execute(
                "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (%s::uuid, %s)",
                (user_id, code_hash),
            )
        return len(hashes)


def mfa_get_unused_recovery_codes(user_id):
    """Return the user's unused recovery-code rows (id + hash). Never logs its arguments."""
    with connect() as conn:
        return conn.execute(
            "SELECT id::text AS id, code_hash, created_at FROM mfa_recovery_codes "
            "WHERE user_id = %s::uuid AND used_at IS NULL ORDER BY created_at",
            (user_id,),
        ).fetchall()


def mfa_mark_recovery_code_used(code_id):
    """Burn a recovery code. Returns 1 when it was still unused, 0 otherwise (single-use guard)."""
    with transaction() as conn:
        return conn.execute(
            "UPDATE mfa_recovery_codes SET used_at = now() WHERE id = %s::uuid AND used_at IS NULL",
            (code_id,),
        ).rowcount


def mfa_try_consume_totp_step(factor_id, time_step, user_id=None):
    """Replay primitive: claim a (factor, time_step) pair.

    Returns True when the step was newly recorded (code accepted) and False when
    it had already been used (replay). The INSERT ... ON CONFLICT DO NOTHING is
    the lock, so two concurrent verifications cannot both win.

    TODO(mfa): rows in mfa_used_totp_steps are never pruned -- only the last
    ~90 s of steps can still be replayed, so everything older is dead weight.
    A retention job belongs to a later phase; see docs/mfa_threat_model.md.
    """
    with transaction() as conn:
        row = conn.execute(
            "INSERT INTO mfa_used_totp_steps (user_id, factor_id, time_step) "
            "VALUES (%s::uuid, %s::uuid, %s) ON CONFLICT (factor_id, time_step) DO NOTHING "
            "RETURNING time_step",
            (user_id, factor_id, int(time_step)),
        ).fetchone()
        return row is not None


def mfa_get_or_create_totp_factor_id(user_id, label="legacy:users.totp_secret"):
    """Return the id (text) of the user's TOTP replay *anchor* factor, creating it once.

    This is replay bookkeeping, NOT a secret store and NOT a migration:
    `secret_encrypted` is deliberately left NULL and public.users.totp_secret
    remains the one and only live secret store.  The anchor exists because
    mfa_used_totp_steps.factor_id is NOT NULL REFERENCES mfa_factors (id), so a
    synthetic id cannot be used.  UNIQUE (user_id, type, label) makes the
    INSERT ... ON CONFLICT DO NOTHING + SELECT pair idempotent and race-safe.

    Never logs its arguments.
    """
    with transaction() as conn:
        conn.execute(
            "INSERT INTO mfa_factors (user_id, type, label) VALUES (%s::uuid, %s, %s) "
            "ON CONFLICT (user_id, type, label) DO NOTHING",
            (user_id, "totp", label),
        )
        row = conn.execute(
            "SELECT id::text AS id FROM mfa_factors "
            "WHERE user_id = %s::uuid AND type = %s AND label = %s",
            (user_id, "totp", label),
        ).fetchone()
        return row[0] if row else None


def mfa_record_attempt(attempt_type, success, user_id=None, ip_hash=None):
    """Append an attempt row. `ip_hash` must already be hashed -- never a raw IP.

    TODO(mfa): mfa_attempts has no retention and an unauthenticated writer --
    every failed /login inserts a row and the login limiter is keyed on
    ip|email, so varying the email makes the insert rate effectively unbounded.
    Only the last MFA_WINDOW_MINUTES (plus the lockout tail) is ever read, so
    everything older is dead weight: a retention job belongs with the
    mfa_used_totp_steps one in a later phase.  See docs/mfa_threat_model.md.
    """
    with transaction() as conn:
        conn.execute(
            "INSERT INTO mfa_attempts (user_id, ip_hash, attempt_type, success) "
            "VALUES (%s::uuid, %s, %s, %s)",
            (user_id, ip_hash, attempt_type, bool(success)),
        )


def mfa_count_recent_attempts(since, user_id=None, ip_hash=None, attempt_type=None,
                              success=None):
    """Count attempts since `since` (datetime/ISO string), filtered by whatever is given."""
    sql = "SELECT count(*) FROM mfa_attempts WHERE created_at >= %s"
    params = [as_utc(since)]
    if user_id is not None:
        sql += " AND user_id = %s::uuid"
        params.append(user_id)
    if ip_hash is not None:
        sql += " AND ip_hash = %s"
        params.append(ip_hash)
    if attempt_type is not None:
        sql += " AND attempt_type = %s"
        params.append(attempt_type)
    if success is not None:
        sql += " AND success = %s"
        params.append(bool(success))
    with connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return int(row[0]) if row else 0


def mfa_recent_attempts(since, user_id=None, ip_hash=None, limit=200):
    """Return attempt rows since `since` (datetime/ISO string), newest first.

    Companion to mfa_count_recent_attempts for callers that need the ordering
    and the success flags, not just a total.  `ip_hash` is already hashed --
    never a raw IP.  Never logs its arguments.
    """
    sql = "SELECT attempt_type, success, created_at FROM mfa_attempts WHERE created_at >= %s"
    params = [as_utc(since)]
    if user_id is not None:
        sql += " AND user_id = %s::uuid"
        params.append(user_id)
    if ip_hash is not None:
        sql += " AND ip_hash = %s"
        params.append(ip_hash)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(int(limit))
    with connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def mfa_log_event(event_type, actor=None, user_id=None, ip_hash=None, user_agent_hash=None):
    """Append one row to the APPEND-ONLY mfa_events audit log.

    Callers must pass hashes, never a raw IP or User-Agent, and never a secret,
    code or token. There is deliberately no update/delete counterpart.
    """
    with transaction() as conn:
        conn.execute(
            "INSERT INTO mfa_events (event_type, actor, user_id, ip_hash, user_agent_hash) "
            "VALUES (%s, %s, %s::uuid, %s, %s)",
            (event_type, actor, user_id, ip_hash, user_agent_hash),
        )


def mfa_recent_events(user_id=None, limit=100, event_type=None):
    """Read back the audit log (SELECT only). Never logs its arguments.

    `event_type` filters in SQL so a caller after one kind of event (the lockout
    counter, for instance) is not starved by the busier event types sharing the
    same `limit`.
    """
    sql = ("SELECT id::text AS id, event_type, actor, user_id::text AS user_id, ip_hash, "
           "user_agent_hash, created_at FROM mfa_events")
    clauses = []
    params = []
    if user_id is not None:
        clauses.append("user_id = %s::uuid")
        params.append(user_id)
    if event_type is not None:
        clauses.append("event_type = %s")
        params.append(event_type)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(int(limit))
    with connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def trusted_device_insert(user_id, token_hash, expires_at, ua_family=None,
                          ip_prefix_hash=None, device_label=None):
    """Store a remember-this-device record. `token_hash` is a hash, never the token."""
    with transaction() as conn:
        row = conn.execute(
            "INSERT INTO trusted_devices (user_id, token_hash, ua_family, ip_prefix_hash, "
            "device_label, expires_at) VALUES (%s::uuid, %s, %s, %s, %s, %s) RETURNING id::text",
            (user_id, token_hash, ua_family, ip_prefix_hash, device_label, as_utc(expires_at)),
        ).fetchone()
        return row[0] if row else None


def trusted_device_find_by_token_hash(token_hash):
    """Return the matching live device row (not revoked, not expired) or None."""
    with connect() as conn:
        return conn.execute(
            "SELECT id::text AS id, user_id::text AS user_id, token_hash, ua_family, "
            "ip_prefix_hash, device_label, created_at, expires_at, revoked_at "
            "FROM trusted_devices WHERE token_hash = %s AND revoked_at IS NULL "
            "AND expires_at > now()",
            (token_hash,),
        ).fetchone()


def trusted_device_revoke(device_id, user_id=None):
    """Revoke a trusted device. Returns the number of rows changed."""
    sql = "UPDATE trusted_devices SET revoked_at = now() WHERE id = %s::uuid AND revoked_at IS NULL"
    params = [device_id]
    if user_id is not None:
        sql += " AND user_id = %s::uuid"
        params.append(user_id)
    with transaction() as conn:
        return conn.execute(sql, tuple(params)).rowcount


def mfa_challenge_insert(challenge_id, user_id, expires_at, state="pending"):
    """Create a server-side login challenge. `challenge_id` is app-minted and never logged."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO mfa_login_challenges (challenge_id, user_id, state, expires_at) "
            "VALUES (%s, %s::uuid, %s, %s)",
            (challenge_id, user_id, state, as_utc(expires_at)),
        )
        return challenge_id


def mfa_challenge_get(challenge_id):
    """Return the challenge row or None. Never logs its arguments."""
    with connect() as conn:
        return conn.execute(
            "SELECT challenge_id, user_id::text AS user_id, state, attempts, expires_at, "
            "created_at FROM mfa_login_challenges WHERE challenge_id = %s",
            (challenge_id,),
        ).fetchone()


def mfa_challenge_bump_attempts(challenge_id):
    """Atomically increment the attempt counter and return the new value (or None)."""
    with transaction() as conn:
        row = conn.execute(
            "UPDATE mfa_login_challenges SET attempts = attempts + 1 "
            "WHERE challenge_id = %s RETURNING attempts",
            (challenge_id,),
        ).fetchone()
        return int(row[0]) if row else None


def mfa_challenge_set_state(challenge_id, state, only_if_pending=True):
    """Move a challenge to a terminal state. Returns rows changed (0 = already consumed)."""
    sql = "UPDATE mfa_login_challenges SET state = %s WHERE challenge_id = %s"
    params = [state, challenge_id]
    if only_if_pending:
        sql += " AND state = 'pending'"
    with transaction() as conn:
        return conn.execute(sql, tuple(params)).rowcount


def mfa_challenge_delete_expired():
    """Drop expired challenges (housekeeping). Returns the number of rows removed."""
    with transaction() as conn:
        return conn.execute(
            "DELETE FROM mfa_login_challenges WHERE expires_at <= now()"
        ).rowcount


def mfa_pending_enrollment_upsert(user_id, secret_encrypted, expires_at):
    """Store the single in-flight enrolment secret (ciphertext) for a user.

    UNIQUE (user_id) means a new enrolment replaces any earlier one.
    `secret_encrypted` must already be mfa_crypto ciphertext; never logged.
    """
    with transaction() as conn:
        conn.execute(
            "INSERT INTO mfa_pending_enrollments (user_id, secret_encrypted, expires_at) "
            "VALUES (%s::uuid, %s, %s) ON CONFLICT (user_id) DO UPDATE SET "
            "secret_encrypted = EXCLUDED.secret_encrypted, expires_at = EXCLUDED.expires_at, "
            "created_at = now()",
            (user_id, secret_encrypted, as_utc(expires_at)),
        )


def mfa_pending_enrollment_get(user_id):
    """Return the user's unexpired pending enrolment row or None. Never logs its arguments."""
    with connect() as conn:
        return conn.execute(
            "SELECT id::text AS id, user_id::text AS user_id, secret_encrypted, created_at, "
            "expires_at FROM mfa_pending_enrollments "
            "WHERE user_id = %s::uuid AND expires_at > now()",
            (user_id,),
        ).fetchone()


def mfa_pending_enrollment_delete(user_id):
    """Drop a pending enrolment (completed or abandoned). Returns rows removed."""
    with transaction() as conn:
        return conn.execute(
            "DELETE FROM mfa_pending_enrollments WHERE user_id = %s::uuid",
            (user_id,),
        ).rowcount


def mfa_outbox_enqueue(kind, user_id=None, payload=None):
    """Queue a lost-device / support request for later delivery.

    `payload` is JSON-serialised with safe_json; it must never carry a secret,
    code or token. Returns the new row id (text).
    """
    with transaction() as conn:
        row = conn.execute(
            "INSERT INTO mfa_outbox (user_id, kind, payload) "
            "VALUES (%s::uuid, %s, %s::jsonb) RETURNING id::text",
            (user_id, kind, safe_json(payload or {})),
        ).fetchone()
        return row[0] if row else None


def mfa_outbox_claim_pending(limit=20):
    """Return up to `limit` pending outbox rows, oldest first."""
    with connect() as conn:
        return conn.execute(
            "SELECT id::text AS id, user_id::text AS user_id, kind, payload, status, "
            "created_at, sent_at FROM mfa_outbox WHERE status = 'pending' "
            "ORDER BY created_at LIMIT %s",
            (int(limit),),
        ).fetchall()


def mfa_outbox_mark_sent(outbox_id, status="sent"):
    """Mark an outbox row delivered (or failed/cancelled). Returns rows changed."""
    with transaction() as conn:
        return conn.execute(
            "UPDATE mfa_outbox SET status = %s, sent_at = now() "
            "WHERE id = %s::uuid AND status = 'pending'",
            (status, outbox_id),
        ).rowcount


def mfa_reset_request_insert(user_id, cancel_token_hash, confirm_token_hash, eligible_at):
    """Open a delayed second-factor reset. Both token arguments are hashes, never tokens."""
    with transaction() as conn:
        row = conn.execute(
            "INSERT INTO mfa_reset_requests (user_id, cancel_token_hash, confirm_token_hash, "
            "eligible_at) VALUES (%s::uuid, %s, %s, %s) RETURNING id::text",
            (user_id, cancel_token_hash, confirm_token_hash, as_utc(eligible_at)),
        ).fetchone()
        return row[0] if row else None


def mfa_reset_request_get_active(user_id):
    """Return the user's pending reset request or None. Never logs its arguments."""
    with connect() as conn:
        return conn.execute(
            "SELECT id::text AS id, user_id::text AS user_id, cancel_token_hash, "
            "confirm_token_hash, requested_at, eligible_at, status, used_at "
            "FROM mfa_reset_requests WHERE user_id = %s::uuid AND status = 'pending' "
            "ORDER BY requested_at DESC",
            (user_id,),
        ).fetchone()


def mfa_reset_request_set_status(request_id, status, only_if_pending=True):
    """Complete / cancel / expire a reset request. Returns rows changed (0 = race lost)."""
    sql = ("UPDATE mfa_reset_requests SET status = %s, "
           "used_at = CASE WHEN %s IN ('completed', 'cancelled') THEN now() ELSE used_at END "
           "WHERE id = %s::uuid")
    params = [status, status, request_id]
    if only_if_pending:
        sql += " AND status = 'pending'"
    with transaction() as conn:
        return conn.execute(sql, tuple(params)).rowcount
