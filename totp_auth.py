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
import io
import logging
import os

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
    """Verify a user-supplied TOTP *code* against *secret*.

    A ``valid_window`` of 1 allows one step of clock skew (±30 s).
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
