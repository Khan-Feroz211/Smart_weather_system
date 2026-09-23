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

    class _RowCursor(_ext.cursor):
        _cols = ()

        def execute(self, query, vars=None):
            super().execute(query, vars)
            self._cols = [d[0] for d in self.description] if self.description else []
            return self

        def _wrap(self, values):
            return None if values is None else Row(self._cols, values)

        def fetchone(self):
            return self._wrap(super().fetchone())

        def fetchall(self):
            return [self._wrap(v) for v in super().fetchall()]

        def fetchmany(self, size=None):
            rows = super().fetchmany(size) if size else super().fetchmany()
            return [self._wrap(v) for v in rows]

        def __iter__(self):
            return iter(self.fetchall())
else:  # pragma: no cover
    _RowCursor = None


class Connection:
    """Thin wrapper around a pooled psycopg2 connection (sqlite-like API)."""

    def __init__(self, raw, pool):
        self._raw = raw
        self._pool = pool
        self._closed = False

    def cursor(self):
        return self._raw.cursor()

    def execute(self, sql, params=None):
        cur = self._raw.cursor()
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
                    cursor_factory=_RowCursor,
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
