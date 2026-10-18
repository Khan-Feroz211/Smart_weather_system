"""
Phase 3 -- DB-backed MFA rate limiting and lockout.

Why this module exists
----------------------
`auth._RateLimiter` is in-memory and per-process: under the project's
multi-worker SocketIO runner every worker keeps its own counters and a restart
clears them, so it cannot express "5 failures in 15 minutes, doubling on a
repeat within 24 hours".  That state lives in the `mfa_attempts` table instead
(schema.sql section 10) and is read back through `db.mfa_recent_attempts`.
`auth._RateLimiter` is deliberately kept as the cheap pre-DB throttle.

Deployment context -- why IPs are never hard-blocked
----------------------------------------------------
This system serves farmers in rural Pakistan, where carrier NAT is widespread:
a single `ip_hash` can represent thousands of unrelated users.  Hard-blocking an
address would lock out real farmers who never made a failed attempt, so the
per-IP control is a 2-second delay only and NEVER returns `locked`.  The
per-account counter is the primary control.  See docs/mfa_threat_model.md.

Privacy
-------
Only `ip_hash` = HMAC-SHA256(MFA_IP_PEPPER, ip) is ever stored or logged, via
`mfa_crypto.ip_hash`.  No raw IP, submitted code, secret, token or password is
logged, returned or placed in an exception message anywhere in this module.

TODO(mfa): `auth.client_ip()` returns `request.remote_addr` with no
X-Forwarded-For handling, so behind a proxy every user collapses into one
`ip_hash` bucket.  A trusted-proxy policy is an open decision -- see
docs/mfa_threat_model.md.  This module takes the raw IP string as given and does
not invent one.
"""
import bisect
import logging
import os
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import db
import mfa_crypto

logger = logging.getLogger(__name__)


def _env_int(name, default):
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# -- configuration (env-overridable, defaults per the Phase 3 brief) --------
WINDOW_MINUTES = _env_int("MFA_WINDOW_MINUTES", 15)
MAX_ATTEMPTS = _env_int("MFA_MAX_ATTEMPTS", 5)
LOCKOUT_MINUTES = _env_int("MFA_LOCKOUT_MINUTES", 15)
LOCKOUT_MAX_MINUTES = _env_int("MFA_LOCKOUT_MAX_MINUTES", 240)
IP_SLOWDOWN_AFTER = _env_int("MFA_IP_SLOWDOWN_AFTER", 10)
IP_SLOWDOWN_SECONDS = 2.0

#: How many attempt / event rows one lookup reads back.  Rows arrive newest
#: first, so a truncated read loses only the oldest ones.
ATTEMPT_ROW_LIMIT = 200
EVENT_ROW_LIMIT = 200


def refresh_config():
    """Re-read the env-backed tunables and return them as a dict.

    The values above are read once at import, which is what a running worker
    wants.  This is the supported entry point for re-reading them after the
    environment changes (tests use it with `mock.patch.dict(os.environ, ...)`;
    an operator can call it from a shell after changing a value).  No secret and
    no raw IP is involved -- these are five integers.

    There is deliberately no per-IP failure *limit* to re-read: no code path
    hard-blocks an address (see the carrier-NAT note above), so a knob implying
    one would mislead an operator into believing a cap exists.
    """
    global WINDOW_MINUTES, MAX_ATTEMPTS, LOCKOUT_MINUTES, LOCKOUT_MAX_MINUTES
    global IP_SLOWDOWN_AFTER
    WINDOW_MINUTES = _env_int("MFA_WINDOW_MINUTES", 15)
    MAX_ATTEMPTS = _env_int("MFA_MAX_ATTEMPTS", 5)
    LOCKOUT_MINUTES = _env_int("MFA_LOCKOUT_MINUTES", 15)
    LOCKOUT_MAX_MINUTES = _env_int("MFA_LOCKOUT_MAX_MINUTES", 240)
    IP_SLOWDOWN_AFTER = _env_int("MFA_IP_SLOWDOWN_AFTER", 10)
    return {
        "MFA_WINDOW_MINUTES": WINDOW_MINUTES,
        "MFA_MAX_ATTEMPTS": MAX_ATTEMPTS,
        "MFA_LOCKOUT_MINUTES": LOCKOUT_MINUTES,
        "MFA_LOCKOUT_MAX_MINUTES": LOCKOUT_MAX_MINUTES,
        "MFA_IP_SLOWDOWN_AFTER": IP_SLOWDOWN_AFTER,
    }


# Generic on purpose: it must not reveal whether the account exists, whether it
# has 2FA enabled, or whether the lock is per-account or per-IP.
GENERIC_LOCKOUT_MESSAGE = "Too many attempts. Please wait and try again."

LOCKOUT_EVENT = "mfa_lockout"

# Patchable so tests can assert the delay without actually waiting.
_sleep = time.sleep

Decision = namedtuple("Decision", "locked retry_after_seconds delay_applied")

_OPEN = Decision(False, 0, False)


def hash_ip(ip):
    """Hash a client address, or return None when hashing is unavailable.

    `mfa_crypto.ip_hash` raises when MFA_IP_PEPPER is unset (the case in the
    test environment and in a fresh dev checkout).  Returning None then leaves
    the per-account control fully active instead of breaking every 2FA route.
    """
    if not ip:
        return None
    try:
        return mfa_crypto.ip_hash(ip)
    except Exception:
        logger.debug("ip hashing unavailable (MFA_IP_PEPPER not set); "
                     "per-account limiting still applies")
        return None


def _now(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_ts(value):
    """Read a timestamp from a DB row (datetime, ISO string, or sqlite format)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _field(row, name, index):
    """Read `name` from a Row/dict/tuple row without assuming the shape."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        try:
            return row[index]
        except (KeyError, IndexError, TypeError):
            return None


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


def _failures_since_last_success(rows):
    """The failures recorded after the account's most recent success.

    Rows arrive newest-first, and stopping at the first success row is what
    implements "a successful verification resets the counter" without deleting
    any audit row.

    Returns `(timestamps, undated)`: the failure timestamps (newest first) and
    the number of failure rows whose `created_at` could not be parsed.  An
    undated row still counts toward the threshold -- the conservative direction,
    chosen deliberately -- but it can never anchor a lockout expiry, because
    there is no instant to measure the expiry from.
    """
    times = []
    undated = 0
    for row in rows or ():
        if _truthy(_field(row, "success", 1)):
            break
        stamp = _parse_ts(_field(row, "created_at", 2))
        if stamp is None:
            undated += 1
        else:
            times.append(stamp)
    return times, undated


def _count_since(times, undated, since):
    """How many of those failures fall at or after `since`."""
    return sum(1 for stamp in times if stamp >= since) + undated


def _prior_lockouts_24h(user_id, now, recent_events=None):
    """How many lockouts this account already had in the last 24 hours.

    Derived from the append-only `mfa_events` log rather than a new column, so
    Phase 1's shipped schema is left alone.
    """
    if user_id is None:
        return 0
    reader = recent_events or db.mfa_recent_events
    cutoff = now - timedelta(hours=24)
    try:
        # Filtered in SQL: every verification writes an mfa_events row, so on a
        # busy account the lockout rows that drive the doubling would otherwise
        # fall outside the newest EVENT_ROW_LIMIT rows and the escalation would
        # quietly reset.
        rows = reader(user_id, EVENT_ROW_LIMIT, LOCKOUT_EVENT) or []
    except Exception:
        return 0
    total = 0
    for row in rows:
        if str(_field(row, "event_type", 1) or "") != LOCKOUT_EVENT:
            continue
        stamp = _parse_ts(_field(row, "created_at", 6))
        # An unparseable timestamp counts as in-window on purpose: the effect is
        # a longer lockout, which is the safe direction for an audit-log glitch.
        if stamp is None or stamp >= cutoff:
            total += 1
    return total


def _lockout_minutes(prior_lockouts):
    """15 min, doubling per prior lockout in 24 h, capped at 4 hours."""
    minutes = LOCKOUT_MINUTES * (2 ** max(0, prior_lockouts))
    return min(minutes, LOCKOUT_MAX_MINUTES)


def _account_decision(user_id, now, recent_attempts=None, recent_events=None):
    """Is this account locked right now, and for how much longer?

    The threshold is counted inside WINDOW_MINUTES, but a lockout can last up to
    LOCKOUT_MAX_MINUTES, so the rows that established it have to stay visible
    after that window has passed.  Reading only the window would end every
    lockout at WINDOW_MINUTES no matter what duration was computed, which would
    make the whole doubling ladder dead code.
    """
    if user_id is None:
        return _OPEN
    reader = recent_attempts or db.mfa_recent_attempts
    lookback = max(WINDOW_MINUTES, LOCKOUT_MAX_MINUTES)
    try:
        rows = reader(now - timedelta(minutes=lookback), user_id, None,
                      ATTEMPT_ROW_LIMIT) or []
    except db.DatabaseUnavailable:
        # No DB means the routes cannot load a profile either, so this opens no
        # bypass; the in-memory limiter still throttles.  TODO(mfa).
        return _OPEN
    except Exception:
        logger.warning("MFA attempt lookup failed; per-account lockout not applied "
                       "for this request")
        return _OPEN

    times, undated = _failures_since_last_success(rows)
    if len(times) + undated < MAX_ATTEMPTS:
        return _OPEN                             # cheap exit: too few failures

    window = timedelta(minutes=WINDOW_MINUTES)
    ordered = sorted(times)                      # oldest -> newest
    duration = None                              # resolved lazily: no event read
    expires = None                               # happens for an open account
    for index, stamp in enumerate(ordered):
        # Failures inside the window that ends at this one, itself included.
        start = bisect.bisect_left(ordered, stamp - window)
        if (index - start + 1) + undated < MAX_ATTEMPTS:
            continue                             # this failure crossed nothing
        if duration is None:
            prior = _prior_lockouts_24h(user_id, now, recent_events=recent_events)
            # The lockout being evaluated was itself logged when the threshold
            # was crossed, so it must not double its own duration.
            duration = timedelta(minutes=_lockout_minutes(max(0, prior - 1)))
        candidate = stamp + duration
        if expires is None or candidate > expires:
            expires = candidate

    if expires is None:
        return _OPEN
    remaining = (expires - now).total_seconds()
    if remaining <= 0:
        return _OPEN
    return Decision(True, int(remaining) + 1, False)


def _ip_slowdown(ip_hash, now, recent_attempts=None):
    """Apply the per-IP delay. Never locks -- see the carrier-NAT note above."""
    if not ip_hash:
        return False
    reader = recent_attempts or db.mfa_recent_attempts
    since = now - timedelta(minutes=WINDOW_MINUTES)
    try:
        rows = reader(since, None, ip_hash, ATTEMPT_ROW_LIMIT) or []
    except Exception:
        return False
    failures = sum(1 for row in rows if not _truthy(_field(row, "success", 1)))
    if failures >= IP_SLOWDOWN_AFTER:
        _sleep(IP_SLOWDOWN_SECONDS)
        return True
    return False


def check(user_id=None, ip_hash=None, now=None, recent_attempts=None,
          recent_events=None, apply_delay=True):
    """Is this account currently locked? Applies the per-IP delay as a side effect.

    `apply_delay=False` evaluates the account lock only and skips the per-IP
    slowdown (and its query).  The slowdown is defined as 2 seconds per request,
    so a caller that has already paid it once must not pay it again -- see
    `record_failure`.
    """
    moment = _now(now)
    decision = _account_decision(user_id, moment, recent_attempts=recent_attempts,
                                 recent_events=recent_events)
    delayed = _ip_slowdown(ip_hash, moment, recent_attempts=recent_attempts) \
        if apply_delay else False
    return decision._replace(delay_applied=delayed)


def record_failure(attempt_type, user_id=None, ip_hash=None, now=None,
                   record_attempt=None, recent_attempts=None, log_event=None,
                   recent_events=None, apply_delay=True):
    """Record a failed attempt and return the resulting Decision.

    Logs an `mfa_lockout` event exactly when this failure crosses the threshold,
    which is also what makes the next lockout double.

    Pass `apply_delay=False` when the caller already called `check()` for this
    request: the 2FA routes gate on `check()` before verifying the code and then
    record the failure, and without this the one rejected code would pay the
    per-IP slowdown twice.  `/login` does not gate on `check()`, so it leaves
    the default alone.
    """
    moment = _now(now)
    writer = record_attempt or db.mfa_record_attempt
    try:
        writer(attempt_type, False, user_id, ip_hash)
    except Exception:
        logger.warning("could not record a failed %s attempt", attempt_type)

    reader = recent_attempts or db.mfa_recent_attempts
    crossed = False
    if user_id is not None:
        # The threshold itself is a window question, so this read stays scoped
        # to WINDOW_MINUTES (unlike the expiry evaluation in _account_decision).
        since = moment - timedelta(minutes=WINDOW_MINUTES)
        try:
            rows = reader(since, user_id, None, ATTEMPT_ROW_LIMIT) or []
            times, undated = _failures_since_last_success(rows)
            crossed = _count_since(times, undated, since) == MAX_ATTEMPTS
        except Exception:
            crossed = False
    if crossed:
        logger_writer = log_event or db.mfa_log_event
        try:
            logger_writer(LOCKOUT_EVENT, actor="mfa_rate_limit", user_id=user_id,
                          ip_hash=ip_hash)
        except Exception:
            logger.warning("could not log the lockout event")

    return check(user_id=user_id, ip_hash=ip_hash, now=moment,
                 recent_attempts=recent_attempts, recent_events=recent_events,
                 apply_delay=apply_delay)


def record_success(attempt_type, user_id=None, ip_hash=None,
                   record_attempt=None):
    """Record a success, which resets the per-account counter."""
    writer = record_attempt or db.mfa_record_attempt
    try:
        writer(attempt_type, True, user_id, ip_hash)
    except Exception:
        logger.warning("could not record a successful %s attempt", attempt_type)
