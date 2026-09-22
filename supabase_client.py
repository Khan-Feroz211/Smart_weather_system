"""
supabase_client.py
==================
Centralised Supabase configuration and Supabase Auth access.

* Credentials come ONLY from environment variables (see `.env.example`).
* `SUPABASE_SERVICE_ROLE_KEY` is used server-side only (admin user management).
  It is never rendered into templates or sent to the browser.
* Talks to the documented Supabase Auth (GoTrue) REST API with `requests`
  (already a project dependency).  A single lazily-created HTTP session is
  shared, and no per-user auth state is kept in this process.

Data queries do NOT go through this module -- see `db.py`.
"""

import logging
import os
import threading
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_PLACEHOLDER_MARKERS = ("YOUR_", "<your", "[YOUR")
_TIMEOUT = float(os.environ.get("SUPABASE_HTTP_TIMEOUT", "10"))

_session = None
_session_lock = threading.Lock()


class SupabaseError(Exception):
    """Base error for Supabase Auth calls (message is safe to log, not to show)."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class SupabaseNotConfigured(SupabaseError):
    pass


class SupabaseUnavailable(SupabaseError):
    """Network / timeout / 5xx."""


class InvalidCredentials(SupabaseError):
    pass


class UserAlreadyExists(SupabaseError):
    pass


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def _env(name):
    value = (os.environ.get(name) or "").strip()
    if not value or any(m.lower() in value.lower() for m in _PLACEHOLDER_MARKERS):
        return None
    return value


def _normalize_project_url(value):
    if not value:
        return None
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host.endswith(".supabase.co"):
        if host.startswith("db."):
            ref = host[len("db.") : -len(".supabase.co")]
            return f"https://{ref}.supabase.co"
        return f"{parsed.scheme or 'https'}://{host}"
    if "/dashboard/project/" in value.lower():
        ref = value.lower().split("/dashboard/project/", 1)[1].split("/", 1)[0]
        return f"https://{ref}.supabase.co"
    return value


def url():
    value = _env("SUPABASE_URL")
    return _normalize_project_url(value) if value else None


def anon_key():
    return _env("SUPABASE_ANON_KEY")


def service_key():
    return _env("SUPABASE_SERVICE_ROLE_KEY")


def admin_email():
    value = _env("ADMIN_EMAIL")
    return value.lower() if value else None


def is_configured(require_service_key=False):
    if not (url() and anon_key()):
        return False
    return bool(service_key()) if require_service_key else True


def public_status():
    """Booleans only -- safe to show on the admin settings page."""
    return {
        "SUPABASE_URL": bool(url()),
        "SUPABASE_ANON_KEY": bool(anon_key()),
        "SUPABASE_SERVICE_ROLE_KEY": bool(service_key()),
        "ADMIN_EMAIL": bool(admin_email()),
    }


def _http():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
    return _session


def _request(method, path, *, key, json=None, params=None, bearer=None):
    base = url()
    if not base or not key:
        raise SupabaseNotConfigured("Supabase URL / key not configured")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {bearer or key}",
        "Content-Type": "application/json",
    }
    try:
        resp = _http().request(method, f"{base}/auth/v1{path}", headers=headers,
                               json=json, params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise SupabaseUnavailable(f"Supabase Auth unreachable: {type(exc).__name__}") from exc
    if resp.status_code >= 500:
        raise SupabaseUnavailable(f"Supabase Auth error {resp.status_code}", status=resp.status_code)
    return resp


def _error_from(resp):
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("error_code") or body.get("error") or ""
    msg = body.get("msg") or body.get("error_description") or body.get("message") or resp.text[:200]
    return msg, code


# --------------------------------------------------------------------------
# Public (anon-key) auth API used by /login, /signup, /admin/login
# --------------------------------------------------------------------------
def sign_in_with_password(email, password):
    """
    Verify credentials with Supabase Auth.  Returns the Auth user dict
    (`id`, `email`, `email_confirmed_at`, ...).  Raises `InvalidCredentials`.
    The returned tokens are intentionally discarded: authorisation is enforced
    server-side from the database on every request.
    """
    resp = _request("POST", "/token", key=anon_key(), params={"grant_type": "password"},
                    json={"email": email, "password": password})
    if resp.status_code == 200:
        return resp.json().get("user") or {}
    msg, code = _error_from(resp)
    if resp.status_code in (400, 401, 422):
        raise InvalidCredentials(msg, status=resp.status_code, code=code)
    raise SupabaseError(msg, status=resp.status_code, code=code)


def sign_up(email, password, metadata=None):
    """Create an account.  Returns (user_dict, needs_email_confirmation)."""
    resp = _request("POST", "/signup", key=anon_key(),
                    json={"email": email, "password": password, "data": metadata or {}})
    if resp.status_code in (200, 201):
        body = resp.json()
        user = body.get("user") or body
        return user, not bool(body.get("access_token"))
    msg, code = _error_from(resp)
    if "already" in msg.lower() or code in ("user_already_exists", "email_exists"):
        raise UserAlreadyExists(msg, status=resp.status_code, code=code)
    raise SupabaseError(msg, status=resp.status_code, code=code)


# --------------------------------------------------------------------------
# Service-role auth admin API (server-side only)
# --------------------------------------------------------------------------
def admin_create_user(email, password, metadata=None, confirm_email=True):
    resp = _request("POST", "/admin/users", key=service_key(),
                    json={"email": email, "password": password,
                          "email_confirm": confirm_email, "user_metadata": metadata or {}})
    if resp.status_code in (200, 201):
        return resp.json()
    msg, code = _error_from(resp)
    if resp.status_code == 422 or "already" in msg.lower() or code in ("email_exists", "user_already_exists"):
        raise UserAlreadyExists(msg, status=resp.status_code, code=code)
    raise SupabaseError(msg, status=resp.status_code, code=code)


def admin_find_user_by_email(email):
    """Scan the Auth user list for an exact email (fine for small projects)."""
    email = email.lower()
    page = 1
    while page <= 50:
        resp = _request("GET", "/admin/users", key=service_key(),
                        params={"page": page, "per_page": 200})
        if resp.status_code != 200:
            msg, code = _error_from(resp)
            raise SupabaseError(msg, status=resp.status_code, code=code)
        users = resp.json().get("users", [])
        for user in users:
            if (user.get("email") or "").lower() == email:
                return user
        if len(users) < 200:
            return None
        page += 1
    return None


def admin_update_user(user_id, **attrs):
    resp = _request("PUT", f"/admin/users/{user_id}", key=service_key(), json=attrs)
    if resp.status_code == 200:
        return resp.json()
    msg, code = _error_from(resp)
    raise SupabaseError(msg, status=resp.status_code, code=code)


def admin_set_ban(user_id, banned):
    """Block (or unblock) sign-in for an Auth user."""
    return admin_update_user(user_id, ban_duration="876000h" if banned else "none")


def admin_delete_user(user_id):
    resp = _request("DELETE", f"/admin/users/{user_id}", key=service_key())
    if resp.status_code in (200, 204):
        return True
    if resp.status_code == 404:
        return False
    msg, code = _error_from(resp)
    raise SupabaseError(msg, status=resp.status_code, code=code)
