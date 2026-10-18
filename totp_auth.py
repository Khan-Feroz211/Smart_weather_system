"""
totp_auth.py
============
Two-factor authentication (2FA) using TOTP (Time-based One-Time Password,
RFC 6238) with authenticator apps (Google Authenticator, Authy, Microsoft
Authenticator, etc.).

Uses the ``pyotp`` library for the TOTP algorithm and ``qrcode`` for QR-code
generation.  If either package is missing the module degrades gracefully:
``is_available()`` returns ``False`` and every function raises
``TOTPNotAvailable``.

Environment variables:
    TWO_FACTOR_ISSUER   – label shown in the authenticator app (default: "Smart AgriWeather")
"""
from __future__ import annotations

import base64
import hmac
import io
import logging
import os
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

# --- optional dependencies ------------------------------------------------
try:
    import pyotp  # type: ignore
except ImportError:  # pragma: no cover
    pyotp = None

try:
    import qrcode  # type: ignore
except ImportError:  # pragma: no cover
    qrcode = None


ISSUER = os.environ.get("TWO_FACTOR_ISSUER", "Smart AgriWeather")


class TOTPNotAvailable(RuntimeError):
    """Raised when the TOTP libraries are not installed."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def is_available() -> bool:
    """Return True when the TOTP libraries are installed."""
    return pyotp is not None and qrcode is not None


def generate_secret() -> str:
    """Generate a random base32-encoded TOTP secret (e.g. ``JBSWY3DPEHPK3PXP``)."""
    if pyotp is None:
        raise TOTPNotAvailable("pyotp is not installed (pip install pyotp)")
    return pyotp.random_base32()


def get_provisioning_uri(secret: str, email: str, issuer: str | None = None) -> str:
    """Build the ``otpauth://`` URI that authenticator apps scan."""
    if pyotp is None:
        raise TOTPNotAvailable("pyotp is not installed (pip install pyotp)")
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer or ISSUER)


def verify_code(secret: str, code: str, valid_window: int = 1) -> bool:
    """LEGACY / COMPAT ONLY -- verify a TOTP *code* against *secret*, as a bool.

    A ``valid_window`` of 1 allows one step of clock skew (±30 s).

    This function is pure: it touches no database and therefore offers **no
    replay protection** -- a code stays usable for the whole ±1-step window.
    New call sites must use :func:`verify_totp`, which claims the matched time
    step through ``db.mfa_try_consume_totp_step`` so a captured code cannot be
    used twice.  Kept unchanged for third-party/legacy callers and unit tests.
    """
    if pyotp is None:
        return False
    if not secret or not code:
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(code.strip(), valid_window=valid_window)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Replay-protected verification (Phase 2)
# ---------------------------------------------------------------------------
# verify_totp walks the ±1-step window itself because pyotp's TOTP.verify()
# only reports *whether* a code matched, never *which* time step matched -- and
# the step is exactly what has to be claimed to stop a replay.
#
# Nothing in here logs, returns or interpolates the secret, the submitted code
# or the matched step: the mfa_events rows carry an event name and the user id
# only.
# ---------------------------------------------------------------------------
CODE_DIGITS = 6

#: Reasons verify_totp can return.  ``expired_pending`` is reserved for the
#: caller's own pending-state expiry (this module never produces it) so routes
#: can map one vocabulary onto one generic user-facing message.
VERIFY_REASONS = ("ok", "invalid", "replay", "locked", "expired_pending")

_DRIFT_RUN_THRESHOLD = 5     # consecutive skew-only successes before we suspect drift
_DRIFT_EVENT_LIMIT = 25      # how far back in mfa_events the run is measured
_ACCEPT_EVENTS = ("totp_verify_ok_exact", "totp_verify_ok_skew")


def canonical_code(code, digits: int = CODE_DIGITS) -> str | None:
    """Return the stripped *code* when it is exactly *digits* ASCII digits, else None.

    Pure format guard: no database access, no logging.  ``str.isdigit`` is not
    used on purpose -- it accepts non-ASCII digit characters that pyotp would
    then reject with an exception.
    """
    text = str(code or "").strip()
    if len(text) != int(digits):
        return None
    return text if all("0" <= ch <= "9" for ch in text) else None


def _result(ok: bool, reason: str, drift_notice: bool = False) -> dict:
    return {"ok": bool(ok), "reason": reason, "drift_notice": bool(drift_notice)}


def _log(log_event, event_type, user_id):
    """Append one mfa_events row (event name + user id only); never raises."""
    writer = log_event or db.mfa_log_event
    try:
        writer(event_type, actor="totp_auth", user_id=user_id)
    except Exception:
        # The audit write must never decide whether a login succeeds.
        logger.debug("mfa event not recorded: %s", event_type)


def _as_utc_datetime(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.astimezone()
    return datetime.fromtimestamp(float(now), tz=timezone.utc)


def _drift_suspected(user_id, log_event=None, recent_events=None) -> bool:
    """True when the last few accepted codes in a row all needed clock skew.

    Derived from the append-only mfa_events log instead of a per-user counter
    column, and logged once per run so the notice is not repeated.  The TOTP
    window is never widened for a user -- this only raises a notice.
    """
    if user_id is None:
        return False
    reader = recent_events or db.mfa_recent_events
    try:
        rows = reader(user_id, _DRIFT_EVENT_LIMIT) or []
    except Exception:
        return False                     # advisory only: never block a login
    run = 0
    for row in rows:                     # newest first
        try:
            event_type = row["event_type"]
        except Exception:
            continue
        if event_type == "clock_drift_suspected":
            return False                 # already reported in this window
        if event_type not in _ACCEPT_EVENTS:
            continue
        if event_type != "totp_verify_ok_skew":
            break                        # the run of skew-only successes ended
        run += 1
    if run < _DRIFT_RUN_THRESHOLD:
        return False
    _log(log_event, "clock_drift_suspected", user_id)
    return True


def verify_totp(secret, code, *, factor_id=None, user_id=None, now=None,
                valid_window: int = 1, digits: int = CODE_DIGITS,
                consume_step=None, log_event=None, recent_events=None) -> dict:
    """Verify a TOTP *code* and claim its time step so it cannot be replayed.

    Returns ``{"ok": bool, "reason": str, "drift_notice": bool}`` where *reason*
    is one of :data:`VERIFY_REASONS`.  The matched time step is never returned,
    logged or hinted at, and neither is the secret or the submitted code.

    ``factor_id`` is the ``mfa_factors`` row the replay store keys on (see
    ``db.mfa_get_or_create_totp_factor_id``).  Pass ``factor_id=None`` for
    enrolment, where no factor row exists yet.

    The ``consume_step`` / ``log_event`` / ``recent_events`` hooks default to
    the matching ``db`` accessors and exist so tests can drive a real store.
    """
    # 1. Cheap guards first: a malformed code costs zero database work.
    candidate = canonical_code(code, digits)
    if pyotp is None or not secret or candidate is None:
        return _result(False, "invalid")

    # 2. Walk the window ourselves and remember which step matched.
    try:
        totp = pyotp.TOTP(secret, digits=int(digits))
        current = int(totp.timecode(_as_utc_datetime(now)))
        window = max(0, int(valid_window))
        matched = None
        for step in range(current - window, current + window + 1):
            # No early exit on match or mismatch: the number of comparisons
            # must not depend on which step (if any) matched.
            if hmac.compare_digest(totp.generate_otp(step), candidate):
                matched = step
    except Exception:
        return _result(False, "invalid")

    if matched is None:
        _log(log_event, "totp_verify_failed", user_id)
        return _result(False, "invalid")

    accepted_event = "totp_verify_ok_exact" if matched == current else "totp_verify_ok_skew"

    # 3. Enrolment: the secret is not persisted yet, so there is no factor row
    #    to key the replay store on.
    #    TODO(mfa): enrolment is not replay-protected (docs/mfa_threat_model.md).
    #    Harmless today -- the code only proves possession before the secret is
    #    written, and it is written exactly once -- but revisit when enrolment
    #    moves to mfa_pending_enrollments.
    if factor_id is None:
        _log(log_event, accepted_event, user_id)
        return _result(True, "ok")

    # 4. The INSERT ... ON CONFLICT DO NOTHING *is* the acceptance decision, so
    #    two concurrent verifications of the same code cannot both win.
    consume = consume_step or db.mfa_try_consume_totp_step
    try:
        claimed = consume(factor_id, matched, user_id)
    except Exception:
        # Fail closed: an unreachable replay store must never accept a code.
        _log(log_event, "replay_store_unavailable", user_id)
        return _result(False, "locked")
    if not claimed:
        _log(log_event, "totp_replay_rejected", user_id)
        return _result(False, "replay")

    _log(log_event, accepted_event, user_id)
    drift = _drift_suspected(user_id, log_event=log_event, recent_events=recent_events)
    return _result(True, "ok", drift)


def current_code(secret: str) -> str:
    """Return the current 6-digit code for *secret* (useful for testing)."""
    if pyotp is None:
        raise TOTPNotAvailable("pyotp is not installed")
    return pyotp.TOTP(secret).now()


def generate_qr_base64(otpauth_uri: str) -> str | None:
    """Render *otpauth_uri* as a QR code and return it as a base64 PNG string.

    The result can be embedded directly in an ``<img src="data:image/png;base64,
    ...">`` tag.  Returns ``None`` when the ``qrcode`` package is missing.
    """
    if qrcode is None:
        logger.warning("qrcode package not installed – cannot generate QR code")
        return None

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(otpauth_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return b64


def generate_qr_svg(otpauth_uri: str) -> str | None:
    """Like ``generate_qr_base64`` but returns an SVG string.

    SVG is resolution-independent and works well in templates without any
    base64 decoding on the client.
    """
    if qrcode is None:
        return None
    # qrcode's SvgPathImage is lightweight and has no extra deps.
    try:
        factory = qrcode.image.svg.SvgImage
    except Exception:
        return None
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(otpauth_uri)
    qr.make(fit=True)
    img = qr.make_image(image_factory=factory)
    return str(img.to_string())  # type: ignore[arg-type]
