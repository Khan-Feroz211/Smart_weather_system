"""
mfa_crypto.py
=============
Encryption-at-rest helper for MFA secrets, plus the MFA startup configuration
guard.  Phase 1: additive only -- nothing here reads or rewrites the existing
`public.users.totp_secret` column, which stays the live store for the current
TOTP flow and becomes a migration source for a later phase.

Key format (environment variable ``MFA_ENCRYPTION_KEY``)
--------------------------------------------------------
A comma-separated list of urlsafe-base64 Fernet keys::

    MFA_ENCRYPTION_KEY=<primary>,<previous>,<older>

The **first** key encrypts; **every** key can decrypt.  Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rotation procedure
------------------
1. Generate a new key.
2. Set ``MFA_ENCRYPTION_KEY="<new>,<old>"`` (new first) and restart/redeploy.
   Everything already stored keeps decrypting; new writes use the new key.
3. Run ``python scripts/rotate_mfa_key.py`` to re-encrypt stored ciphertext to
   the new primary key.
4. Once the script reports 0 rows left to re-encrypt, drop ``<old>`` from
   ``MFA_ENCRYPTION_KEY``.

Secret hygiene
--------------
Nothing in this module logs, prints or embeds in an exception message: a key, a
plaintext secret, a ciphertext, a recovery code, a token or a raw IP address.
Error messages name the *variable* and describe the *shape* of the problem only.
"""

import hashlib
import hmac
import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_ENV = "MFA_ENCRYPTION_KEY"
IP_PEPPER_ENV = "MFA_IP_PEPPER"
SECRET_KEY_ENV = "SECRET_KEY"

_MIN_PEPPER_LENGTH = 16
_MIN_SECRET_KEY_LENGTH = 32

_cache_lock = threading.Lock()
_cached_key_string = None
_cached_multifernet = None


class MfaCryptoError(RuntimeError):
    """Key material is missing/malformed, or a value could not be decrypted.

    The message never contains key material, plaintext or ciphertext.
    """


class MfaConfigError(RuntimeError):
    """Required MFA configuration is missing or unsafe (strict mode only).

    The message lists offending variable NAMES and reasons only, never values.
    """


# --------------------------------------------------------------------------
# Key loading
# --------------------------------------------------------------------------
def _raw_key_string():
    return (os.environ.get(ENCRYPTION_KEY_ENV) or "").strip()


def _split_keys(raw):
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_keys():
    """Return the configured Fernet keys, primary first.

    Raises MfaCryptoError (value-free message) when the variable is unset,
    empty, or holds anything that is not a valid Fernet key.
    """
    raw = _raw_key_string()
    if not raw:
        raise MfaCryptoError(
            f"{ENCRYPTION_KEY_ENV} is not set. It must hold one or more "
            "comma-separated Fernet keys (the first one encrypts)."
        )
    parts = _split_keys(raw)
    if not parts:
        raise MfaCryptoError(
            f"{ENCRYPTION_KEY_ENV} is set but contains no usable key "
            "(expected a comma-separated list of Fernet keys)."
        )
    fernets = []
    for position, part in enumerate(parts, start=1):
        try:
            fernets.append(Fernet(part.encode("ascii")))
        except Exception:  # ValueError / binascii / TypeError
            # Deliberately report the POSITION, never the value.
            raise MfaCryptoError(
                f"{ENCRYPTION_KEY_ENV} entry #{position} is not a valid Fernet key "
                "(expected 32 url-safe base64-encoded bytes)."
            ) from None
    return fernets


def get_multifernet():
    """Return a MultiFernet over the configured keys (primary first).

    Cached per key-string, so changing MFA_ENCRYPTION_KEY (rotation, tests)
    takes effect immediately without a restart.
    """
    global _cached_key_string, _cached_multifernet
    raw = _raw_key_string()
    with _cache_lock:
        if _cached_multifernet is not None and _cached_key_string == raw:
            return _cached_multifernet
    multi = MultiFernet(_load_keys())
    with _cache_lock:
        _cached_key_string = raw
        _cached_multifernet = multi
    return multi


def primary_fernet():
    """Return a Fernet over the primary (first) key only."""
    return _load_keys()[0]


def reset_cache():
    """Drop the cached MultiFernet (used by tests and after key changes)."""
    global _cached_key_string, _cached_multifernet
    with _cache_lock:
        _cached_key_string = None
        _cached_multifernet = None


# --------------------------------------------------------------------------
# Encrypt / decrypt
# --------------------------------------------------------------------------
def encrypt_secret(plaintext):
    """Encrypt a secret with the primary key. str in -> str (token) out."""
    if plaintext is None or plaintext == "":
        raise MfaCryptoError("refusing to encrypt an empty secret")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    return get_multifernet().encrypt(plaintext).decode("ascii")


def decrypt_secret(token):
    """Decrypt a token with any configured key. str in -> str out."""
    if not token:
        raise MfaCryptoError("refusing to decrypt an empty value")
    if isinstance(token, str):
        token = token.encode("ascii", errors="strict")
    try:
        return get_multifernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        raise MfaCryptoError(
            "ciphertext could not be decrypted with any configured "
            f"{ENCRYPTION_KEY_ENV} key"
        ) from None


def decrypts_with_primary(token):
    """True when `token` is already encrypted under the primary key.

    Used by scripts/rotate_mfa_key.py to stay idempotent without comparing
    ciphertext (Fernet tokens are not deterministic).
    """
    if not token:
        return False
    if isinstance(token, str):
        token = token.encode("ascii", errors="ignore")
    try:
        primary_fernet().decrypt(token)
        return True
    except InvalidToken:
        return False


# --------------------------------------------------------------------------
# IP hashing
# --------------------------------------------------------------------------
# TODO(mfa): ip_hash is intentionally NOT wired into any request path in this
# phase. auth.client_ip() returns request.remote_addr with no X-Forwarded-For
# handling, so a trusted-proxy decision must come first or every user behind the
# proxy collapses into a single rate-limit bucket.
# See docs/mfa_threat_model.md (audit conflict #8).
def ip_hash(ip):
    """Return the hex HMAC-SHA256 of a normalised IP, keyed by MFA_IP_PEPPER.

    Never logs or returns the input. Raises MfaCryptoError if the pepper is unset.
    """
    pepper = (os.environ.get(IP_PEPPER_ENV) or "").strip()
    if not pepper:
        raise MfaCryptoError(f"{IP_PEPPER_ENV} is not set; cannot hash client addresses")
    normalised = str(ip or "").strip().lower()
    return hmac.new(pepper.encode("utf-8"), normalised.encode("utf-8"), hashlib.sha256).hexdigest()


def opaque_hash(value):
    """Hex HMAC-SHA256 of any low-entropy identifier (User-Agent family, IP prefix).

    Shares MFA_IP_PEPPER so pseudonymous audit fields cannot be reversed by
    dictionary attack. Returns None for an empty input.
    """
    if value in (None, ""):
        return None
    return ip_hash(value)


def token_hash(token):
    """SHA-256 hex digest of a high-entropy token (trusted-device cookie, reset token).

    No pepper is needed: the input already has >=256 bits of entropy. The token
    itself is never stored or logged.
    """
    if not token:
        raise MfaCryptoError("refusing to hash an empty token")
    if isinstance(token, str):
        token = token.encode("utf-8")
    return hashlib.sha256(token).hexdigest()


# --------------------------------------------------------------------------
# Startup configuration guard
# --------------------------------------------------------------------------
def validate_mfa_config(strict=None):
    """Check the MFA-related environment variables.

    Collects every problem rather than failing on the first one. Returns a list
    of human-readable problem strings that name VARIABLES and reasons only --
    never values.

    strict defaults to ``not auth.is_debug()``:
      * strict  -> raise MfaConfigError when there is any problem.
      * lenient -> logger.warning the same summary and return the problems.

    Returns [] when the configuration is clean.
    """
    import auth  # lazy: avoids an import cycle (auth -> db -> ... -> mfa_crypto)

    if strict is None:
        strict = not auth.is_debug()

    problems = []

    raw = _raw_key_string()
    if not raw:
        problems.append(f"{ENCRYPTION_KEY_ENV} is not set (needs at least one Fernet key)")
    else:
        try:
            _load_keys()
        except MfaCryptoError as exc:
            problems.append(str(exc))

    pepper = (os.environ.get(IP_PEPPER_ENV) or "").strip()
    if not pepper:
        problems.append(f"{IP_PEPPER_ENV} is not set (needed to hash client addresses)")
    elif len(pepper) < _MIN_PEPPER_LENGTH:
        problems.append(
            f"{IP_PEPPER_ENV} is shorter than {_MIN_PEPPER_LENGTH} characters"
        )

    secret_key = (os.environ.get(SECRET_KEY_ENV) or "").strip()
    if not secret_key:
        problems.append(f"{SECRET_KEY_ENV} is not set")
    elif len(secret_key) < _MIN_SECRET_KEY_LENGTH:
        problems.append(
            f"{SECRET_KEY_ENV} is shorter than {_MIN_SECRET_KEY_LENGTH} characters"
        )
    elif (secret_key.lower() in auth._KNOWN_WEAK_SECRETS
            or secret_key.lower().startswith(("smart_weather", "change", "your", "secret"))):
        # Same rule as auth.resolve_secret_key(); not duplicated into a new list.
        problems.append(f"{SECRET_KEY_ENV} is a known-weak or default value")

    if not problems:
        return []

    summary = "MFA configuration problems: " + "; ".join(problems)
    if strict:
        raise MfaConfigError(
            summary + ". Set these variables before starting with DEBUG=False."
        )
    logger.warning("%s (DEBUG mode: continuing anyway)", summary)
    return problems
