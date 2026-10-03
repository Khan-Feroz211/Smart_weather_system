"""
auth.py
=======
User authentication for the Smart Weather System, backed by Supabase Auth.

Design
------
* Passwords are verified by Supabase Auth (never stored or handled by this app).
* After a successful sign-in the Flask session holds ONLY the profile UUID
  (`session['uid']`), signed with SECRET_KEY, HttpOnly, SameSite=Lax.
* Authorisation is decided server-side on EVERY request by re-reading the
  profile from PostgreSQL (`is_active`, `role`, `admin_users`).  Deactivating
  or deleting a user therefore takes effect on their next request.
* Admin access additionally requires a session created through /admin/login
  (`session['admin_at']`), an absolute lifetime and an idle timeout.
* Public pages of the original application stay public: login is optional and
  only enables heartbeat / activity tracking for signed-in users.
"""

import hmac
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

import db
import supabase_client as sb
import totp_auth
from notifications import send_notification_auto, _is_valid_phone

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__)

# --- tunables (env overridable) -------------------------------------------
ACTIVE_WINDOW_MINUTES = 5                 # "Active" = last_seen_at within this window
HEARTBEAT_SECONDS = int(os.environ.get("HEARTBEAT_SECONDS", "45"))
ADMIN_SESSION_HOURS = float(os.environ.get("ADMIN_SESSION_HOURS", "8"))
ADMIN_IDLE_MINUTES = float(os.environ.get("ADMIN_IDLE_MINUTES", "60"))
ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "true").strip().lower() in ("1", "true", "yes", "on")

_KNOWN_WEAK_SECRETS = {
    "smart_weather_ai_2024", "change-me", "changeme", "secret", "dev", "development",
    "your-secret-key", "your_secret_key",
}


# --------------------------------------------------------------------------
# Configuration helpers
# --------------------------------------------------------------------------
def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def is_debug():
    return _truthy(os.environ.get("DEBUG")) or _truthy(os.environ.get("FLASK_DEBUG"))


def resolve_secret_key():
    """
    Return the Flask secret key.  A missing / short / well-known key is NOT
    accepted: in development a random per-process key is generated (sessions
    reset on restart); otherwise the app refuses to start.
    """
    key = os.environ.get("SECRET_KEY", "").strip()
    weak = (len(key) < 32 or key.lower() in _KNOWN_WEAK_SECRETS
            or key.lower().startswith(("smart_weather", "change", "your", "secret")))
    if not weak:
        return key
    if is_debug():
        logger.warning("SECRET_KEY is missing or weak - using a temporary random key "
                       "(sessions reset on restart). Generate one with: "
                       "python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("⚠️  SECRET_KEY is missing/weak: using a temporary random key (dev only).")
        return secrets.token_hex(32)
    raise RuntimeError(
        "SECRET_KEY must be set to a random value of at least 32 characters. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")


# --------------------------------------------------------------------------
# CSRF (for the state-changing routes defined in this module and admin_routes)
# --------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_valid():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("_csrf", "")
    return bool(expected) and hmac.compare_digest(str(expected), str(sent))


def csrf_protect(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not csrf_valid():
            if request.path.startswith("/api/") or request.is_json or request.headers.get("X-CSRF-Token") is not None:
                return jsonify({"ok": False, "error": "invalid_csrf_token"}), 400
            flash("Your session expired. Please try again.", "error")
            return redirect(request.referrer or url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Tiny in-memory rate limiter (per process) for login / signup
# --------------------------------------------------------------------------
class _RateLimiter:
    def __init__(self, limit, window_seconds):
        self.limit, self.window = limit, window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key):
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            return len(q) >= self.limit

    def hit(self, key):
        with self._lock:
            self._hits[key].append(time.monotonic())

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


login_limiter = _RateLimiter(8, 300)
signup_limiter = _RateLimiter(5, 3600)


def client_ip():
    return request.remote_addr or "unknown"


# --------------------------------------------------------------------------
# Current user / admin state
# --------------------------------------------------------------------------
_PROFILE_SQL = """
    SELECT u.id::text AS id, u.user_number, u.username, u.email, u.location, u.role,
           u.is_active, u.last_seen_at, u.phone_number, u.totp_secret,
           u.two_factor_pending,
           EXISTS (SELECT 1 FROM admin_users a WHERE a.user_id = u.id AND a.is_active) AS has_admin_record
      FROM users u
     WHERE u.id = %s::uuid
"""


def current_user():
    """Profile of the signed-in user (cached per request) or None."""
    if "_user_loaded" in g:
        return g.get("_user")
    g._user_loaded = True
    g._user = None
    g.db_error = False
    uid = session.get("uid")
    if not uid:
        return None
    try:
        uuid.UUID(str(uid))
    except ValueError:
        session.clear()
        return None
    try:
        with db.connect() as conn:
            row = conn.execute(_PROFILE_SQL, (uid,)).fetchone()
    except db.DatabaseUnavailable:
        g.db_error = True
        return None
    except Exception:
        logger.exception("current_user lookup failed")
        g.db_error = True
        return None
    if row is None or not row["is_active"]:
        session.clear()            # deleted or deactivated -> session is no longer valid
        return None
    g._user = row
    return row


def is_admin_user(user):
    return bool(user) and user["role"] == "admin" and bool(user["has_admin_record"])


def admin_state():
    """
    Returns (state, user).  state is one of:
      'ok' | 'anonymous' | 'expired' | 'forbidden' | 'reauth' | 'db_error'
    """
    user = current_user()
    if user is None:
        return ("db_error" if g.get("db_error") else "anonymous"), None
    if not is_admin_user(user):
        return "forbidden", user
    admin_at = session.get("admin_at")
    if not admin_at:
        return "reauth", user                      # admin role, but never signed in via /admin/login
    now = time.time()
    if now - float(admin_at) > ADMIN_SESSION_HOURS * 3600 or \
            now - float(session.get("admin_seen", admin_at)) > ADMIN_IDLE_MINUTES * 60:
        session.pop("admin_at", None)
        session.pop("admin_seen", None)
        return "expired", user
    if request.method != "GET" or not request.path.startswith("/api/"):
        session["admin_seen"] = now                # user-driven requests keep the session alive; background polling does not
    return "ok", user


def _wants_json():
    return request.path.startswith("/api/") or request.is_json or \
        request.headers.get("X-Requested-With") == "fetch"


def admin_required(view):
    """Server-side guard for every /admin page and /api/admin endpoint."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        state, user = admin_state()
        if state == "ok":
            g.admin = user
            return view(*args, **kwargs)
        if _wants_json():
            codes = {"anonymous": 401, "expired": 401, "reauth": 401, "forbidden": 403, "db_error": 503}
            return jsonify({"ok": False, "error": state}), codes.get(state, 401)
        if state == "forbidden":
            flash("You do not have access to the admin panel.", "error")
            return redirect(url_for("dashboard"))
        if state == "db_error":
            return render_template("admin/error.html", title="Database unavailable",
                                   message="The admin panel cannot reach the database right now. "
                                           "Check SUPABASE_DB_URL and try again."), 503
        return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))
    return wrapper


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            if _wants_json():
                return jsonify({"ok": False, "error": "not_authenticated"}), 401
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def safe_next(target, default):
    if not target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    return target


# --------------------------------------------------------------------------
# Credential verification shared by /login and /admin/login
# --------------------------------------------------------------------------
class LoginError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def verify_credentials(email, password):
    """
    Check the password with Supabase Auth, make sure a profile exists and is
    active, record the login, and return the profile row.
    Raises LoginError with a user-safe message.
    """
    try:
        auth_user = sb.sign_in_with_password(email, password)
    except sb.InvalidCredentials as exc:
        if exc.code == "email_not_confirmed":
            raise LoginError("Please confirm your email address before signing in.")
        raise LoginError("Invalid email or password.")
    except sb.SupabaseNotConfigured:
        raise LoginError("Sign-in is not configured on this server (Supabase keys missing).")
    except sb.SupabaseUnavailable:
        raise LoginError("The authentication service is unreachable. Please try again shortly.")
    except sb.SupabaseError:
        logger.exception("Supabase Auth error during sign-in")
        raise LoginError("Sign-in failed. Please try again.")

    auth_id = auth_user.get("id")
    if not auth_id:
        raise LoginError("Sign-in failed. Please try again.")
    try:
        with db.connect() as conn:
            pid = conn.execute(
                "SELECT public.ensure_profile(%s::uuid, %s, %s::jsonb, TRUE)",
                (auth_id, auth_user.get("email") or email, db.safe_json(auth_user.get("user_metadata") or {}))
            ).fetchone()[0]
            if pid is None:
                conn.rollback()
                raise LoginError("Your account could not be linked to a profile. Contact the administrator.")
            profile = conn.execute(_PROFILE_SQL, (str(pid),)).fetchone()
            if profile is None or not profile["is_active"]:
                conn.rollback()
                raise LoginError("This account has been deactivated.")
            # Sync phone_number from auth metadata (set during signup) if available
            meta_phone = (auth_user.get("user_metadata") or {}).get("phone_number")
            if meta_phone and not profile.get("phone_number"):
                try:
                    conn.execute(
                        "UPDATE users SET phone_number = %s WHERE id = %s::uuid",
                        (meta_phone, str(pid))
                    )
                    conn.commit()
                except Exception:
                    logger.debug("Could not sync phone_number from metadata (column may not exist yet)")
            conn.execute(
                "UPDATE users SET last_login_at = now(), last_seen_at = now(), "
                "login_count = login_count + 1, is_online = TRUE WHERE id = %s::uuid", (str(pid),))
            conn.commit()
            return profile
    except db.DatabaseUnavailable:
        raise LoginError("The database is unreachable. Please try again shortly.")
    except LoginError:
        raise
    except Exception:
        logger.exception("Profile lookup failed during sign-in")
        raise LoginError("Sign-in failed. Please try again.")


def start_session(profile, admin=False):
    """Fresh session (prevents fixation)."""
    session.clear()
    session["uid"] = profile["id"]
    session.permanent = True
    now = time.time()
    session["login_at"] = now
    if admin:
        session["admin_at"] = now
        session["admin_seen"] = now
    csrf_token()
    g.pop("_user_loaded", None)
    g.pop("_user", None)


def log_activity(activity_type, notes=None, weather_condition=None, dedupe_seconds=0, user_id=None):
    """
    Record an event in `user_activities` for the signed-in user.
    Best-effort: never raises, never blocks the request on failure.
    Anonymous visitors are not tracked (no identity to attach).
    """
    try:
        uid = user_id or session.get("uid")
        if not uid:
            return
        with db.connect() as conn:
            if dedupe_seconds:
                conn.execute(
                    "INSERT INTO user_activities (user_id, activity_type, weather_condition, notes) "
                    "SELECT %s::uuid, %s, %s, %s WHERE NOT EXISTS ("
                    "  SELECT 1 FROM user_activities WHERE user_id = %s::uuid AND activity_type = %s "
                    "  AND activity_date > now() - make_interval(secs => %s))",
                    (uid, activity_type, weather_condition, notes, uid, activity_type, int(dedupe_seconds)))
            else:
                conn.execute(
                    "INSERT INTO user_activities (user_id, activity_type, weather_condition, notes) "
                    "VALUES (%s::uuid, %s, %s, %s)", (uid, activity_type, weather_condition, notes))
            conn.commit()
    except Exception as exc:   # tracking must never break a page
        logger.debug("activity log skipped: %s", exc)


# --------------------------------------------------------------------------
# Routes: /login /signup /logout /api/heartbeat
# --------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"), url_for("dashboard"))
    if request.method == "POST":
        if not csrf_valid():
            flash("Your session expired. Please try again.", "error")
            return redirect(url_for("auth.login", next=nxt))
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        key = f"{client_ip()}|{email}"
        if login_limiter.blocked(key):
            flash("Too many attempts. Please wait a few minutes and try again.", "error")
            return render_template("login.html", next=nxt), 429
        if not _EMAIL_RE.match(email) or not password:
            flash("Enter your email and password.", "error")
            return render_template("login.html", next=nxt), 400
        try:
            profile = verify_credentials(email, password)
        except LoginError as exc:
            login_limiter.hit(key)
            flash(exc.message, "error")
            return render_template("login.html", next=nxt, email=email), 401
        login_limiter.reset(key)

        # --- Two-factor authentication check ---
        totp_secret = profile.get("totp_secret")
        two_factor_pending = profile.get("two_factor_pending")
        if totp_secret:
            # 2FA is fully set up → require TOTP code
            _set_pending_2fa(profile, nxt, step="verify")
            return redirect(url_for("auth.verify_2fa"))
        if two_factor_pending:
            # User opted into 2FA during signup but hasn't completed setup
            _set_pending_2fa(profile, nxt, step="setup")
            return redirect(url_for("auth.setup_2fa"))

        start_session(profile)
        log_activity("login", user_id=profile["id"])
        flash(f"Welcome back, {profile['username']}!", "success")
        return redirect(nxt)
    return render_template("login.html", next=nxt)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not ALLOW_SIGNUP:
        flash("Registration is disabled.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if not csrf_valid():
            flash("Your session expired. Please try again.", "error")
            return redirect(url_for("auth.signup"))
        if signup_limiter.blocked(client_ip()):
            flash("Too many sign-up attempts. Please try again later.", "error")
            return render_template("signup.html"), 429
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        username = (request.form.get("username") or "").strip()
        location = (request.form.get("location") or "").strip()[:80]
        phone_number = (request.form.get("phone_number") or "").strip()
        enable_2fa = request.form.get("enable_2fa") == "on"
        errors = []
        if not _EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if username and not _USERNAME_RE.match(username):
            errors.append("Username: 3-30 letters, numbers, dots, dashes or underscores.")
        if phone_number and not _is_valid_phone(phone_number):
            errors.append("Phone number must be in E.164 format (e.g. +15551234567).")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("signup.html", email=email, username=username, location=location,
                                   phone_number=phone_number, enable_2fa=enable_2fa), 400
        if enable_2fa and not totp_auth.is_available():
            flash("Two-factor authentication requires the pyotp library, which is not installed.", "error")
            return render_template("signup.html", email=email, username=username, location=location,
                                   phone_number=phone_number, enable_2fa=enable_2fa), 400
        signup_limiter.hit(client_ip())
        try:
            meta = {"location": location}
            if username:
                meta["username"] = username
            if phone_number:
                meta["phone_number"] = phone_number
            if enable_2fa:
                meta["enable_2fa"] = True
            _user, needs_confirmation = sb.sign_up(email, password, meta)
        except sb.UserAlreadyExists:
            # do not reveal whether the address is registered
            flash("If this email is new, your account was created - check your inbox, then sign in.", "success")
            return redirect(url_for("auth.login"))
        except sb.SupabaseNotConfigured:
            flash("Registration is not configured on this server (Supabase keys missing).", "error")
            return render_template("signup.html", email=email, username=username, location=location,
                                   phone_number=phone_number, enable_2fa=enable_2fa), 503
        except sb.SupabaseUnavailable:
            flash("The authentication service is unreachable. Please try again shortly.", "error")
            return render_template("signup.html", email=email, username=username, location=location,
                                   phone_number=phone_number, enable_2fa=enable_2fa), 503
        except sb.SupabaseError as exc:
            flash(str(exc)[:160] or "Could not create the account.", "error")
            return render_template("signup.html", email=email, username=username, location=location,
                                   phone_number=phone_number, enable_2fa=enable_2fa), 400

        # Persist phone_number + two-factor-pending flag on the profile
        auth_id = (_user or {}).get("id")
        if auth_id:
            try:
                with db.connect() as conn:
                    updates, params = [], []
                    if phone_number:
                        updates.append("phone_number = %s")
                        params.append(phone_number)
                    if enable_2fa:
                        updates.append("two_factor_pending = TRUE")
                    if updates:
                        params.append(auth_id)
                        conn.execute(
                            "UPDATE users SET " + ", ".join(updates) + " WHERE auth_user_id = %s::uuid",
                            params,
                        )
                        conn.commit()
            except db.DatabaseUnavailable:
                logger.warning("Could not persist phone_number/2fa flag to profile (db unavailable)")
            except Exception:
                logger.exception("Failed to persist phone_number/2fa flag to profile")

        if enable_2fa and totp_auth.is_available():
            flash("Account created! You will set up two-factor authentication after you confirm your email.",
                  "success")
        else:
            flash("Account created. Check your email to confirm it, then sign in."
                  if needs_confirmation else "Account created. You can sign in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("signup.html")


@auth_bp.route("/logout", methods=["POST"])
@csrf_protect
def logout():
    uid = session.get("uid")
    if uid:
        log_activity("logout", user_id=uid)
        try:
            with db.connect() as conn:
                conn.execute("UPDATE users SET is_online = FALSE WHERE id = %s::uuid", (uid,))
                conn.commit()
        except Exception:
            pass
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------------------
# Two-factor authentication (TOTP)
# --------------------------------------------------------------------------
_PENDING_2FA_SECONDS = 600  # 2FA pending state expires after 10 minutes


def _set_pending_2fa(profile, next_url, step="verify"):
    """Store a minimal pending-2FA marker in the session.

    The profile is NOT fully authenticated here — the caller must still prove
    possession of their authenticator device before ``start_session`` is called.
    """
    session.clear()
    session["pending_2fa"] = {
        "uid": str(profile["id"]),
        "email": profile["email"],
        "username": profile["username"],
        "step": step,
        "next": next_url,
        "created": time.time(),
    }


def _get_pending_2fa():
    """Return the pending-2FA dict or None (clears expired entries)."""
    pending = session.get("pending_2fa")
    if not pending:
        return None
    if time.time() - float(pending.get("created", 0)) > _PENDING_2FA_SECONDS:
        session.pop("pending_2fa", None)
        return None
    return pending


def _clear_pending_2fa():
    session.pop("pending_2fa", None)


def _load_profile_by_id(uid):
    """Load a profile by UUID from the DB (used during 2FA flow)."""
    try:
        uuid.UUID(str(uid))
    except (ValueError, TypeError):
        return None
    try:
        with db.connect() as conn:
            return conn.execute(_PROFILE_SQL, (str(uid),)).fetchone()
    except db.DatabaseUnavailable:
        return None
    except Exception:
        logger.exception("Could not load profile during 2FA flow")
        return None


@auth_bp.route("/2fa/setup", methods=["GET", "POST"])
def setup_2fa():
    """First-time 2FA setup: show QR code, verify a test code, store the secret."""
    if not totp_auth.is_available():
        flash("Two-factor authentication is not available on this server.", "error")
        return redirect(url_for("dashboard"))

    pending = _get_pending_2fa()
    if not pending or pending.get("step") != "setup":
        flash("You must complete sign-in first to set up two-factor authentication.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        secret = session.pop("_2fa_setup_secret", None)
        if not secret or not code:
            flash("Please enter the code from your authenticator app.", "error")
            return redirect(url_for("auth.setup_2fa"))
        if not totp_auth.verify_code(secret, code):
            flash("Invalid code. Please check your authenticator app and try again.", "error")
            # keep a fresh secret in session so the user can retry
            session["_2fa_setup_secret"] = secret
            return redirect(url_for("auth.setup_2fa"))
        # Success — persist the secret and clear the pending flag
        profile = _load_profile_by_id(pending["uid"])
        if profile and profile["is_active"]:
            try:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE users SET totp_secret = %s, two_factor_pending = FALSE "
                        "WHERE id = %s::uuid", (secret, str(pending["uid"]))
                    )
                    conn.commit()
            except db.DatabaseUnavailable:
                flash("Database unavailable — 2FA could not be saved. Try again.", "error")
                session["_2fa_setup_secret"] = secret
                return redirect(url_for("auth.setup_2fa"))
            except Exception:
                logger.exception("Failed to persist TOTP secret")
                flash("Could not save 2FA settings. Try again.", "error")
                session["_2fa_setup_secret"] = secret
                return redirect(url_for("auth.setup_2fa"))
            _clear_pending_2fa()
            start_session(profile)
            log_activity("2fa_enabled", user_id=profile["id"])
            flash("Two-factor authentication is now enabled. Welcome!", "success")
            return redirect(pending.get("next") or url_for("dashboard"))
        flash("Your account could not be loaded. Please try signing in again.", "error")
        _clear_pending_2fa()
        return redirect(url_for("auth.login"))

    # GET — generate (or reuse) a secret for this session
    secret = session.get("_2fa_setup_secret")
    if not secret:
        secret = totp_auth.generate_secret()
        session["_2fa_setup_secret"] = secret
    uri = totp_auth.get_provisioning_uri(secret, pending["email"])
    qr_png = totp_auth.generate_qr_base64(uri)
    return render_template("2fa_setup.html",
                           email=pending["email"],
                           secret=secret,
                           otpauth_uri=uri,
                           qr_png=qr_png)


@auth_bp.route("/2fa/verify", methods=["GET", "POST"])
def verify_2fa():
    """2FA verification step during login (user already has TOTP configured)."""
    if not totp_auth.is_available():
        flash("Two-factor authentication is not available on this server.", "error")
        return redirect(url_for("auth.login"))

    pending = _get_pending_2fa()
    if not pending or pending.get("step") != "verify":
        flash("You must sign in first to verify your identity.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if not code:
            flash("Please enter the 6-digit code from your authenticator app.", "error")
            return render_template("2fa_verify.html", email=pending["email"]), 400
        profile = _load_profile_by_id(pending["uid"])
        if profile is None:
            flash("Your session expired. Please sign in again.", "error")
            _clear_pending_2fa()
            return redirect(url_for("auth.login"))
        secret = profile.get("totp_secret")
        if not secret or not totp_auth.verify_code(secret, code):
            flash("Invalid code. Please try again.", "error")
            return render_template("2fa_verify.html", email=pending["email"]), 401
        # Success
        _clear_pending_2fa()
        start_session(profile)
        log_activity("login_2fa", user_id=profile["id"])
        flash(f"Welcome back, {profile['username']}!", "success")
        return redirect(pending.get("next") or url_for("dashboard"))

    return render_template("2fa_verify.html", email=pending["email"])


@auth_bp.route("/2fa/disable", methods=["POST"])
@csrf_protect
def disable_2fa():
    """Allow a signed-in user to disable 2FA from their profile/settings."""
    user = current_user()
    if user is None:
        flash("Please sign in first.", "error")
        return redirect(url_for("auth.login"))
    secret = user.get("totp_secret")
    if not secret:
        flash("Two-factor authentication is not enabled.", "info")
        return redirect(url_for("dashboard"))
    code = (request.form.get("code") or "").strip()
    if not totp_auth.verify_code(secret, code):
        flash("Invalid code — 2FA was not disabled.", "error")
        return redirect(url_for("dashboard"))
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE users SET totp_secret = NULL WHERE id = %s::uuid", (user["id"],)
            )
            conn.commit()
    except Exception:
        logger.exception("Failed to disable 2FA")
        flash("Could not disable 2FA. Please try again.", "error")
        return redirect(url_for("dashboard"))
    flash("Two-factor authentication has been disabled.", "success")
    log_activity("2fa_disabled", user_id=user["id"])
    # Force session refresh so current_user() picks up the change
    g.pop("_user_loaded", None)
    g.pop("_user", None)
    return redirect(url_for("dashboard"))


@auth_bp.route("/2fa/send-test", methods=["POST"])
@csrf_protect
def send_2fa_test_notification():
    """Send a test notification via WhatsApp (auto-fallback to local SMS)."""
    user = current_user()
    if user is None:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    phone = user.get("phone_number")
    if not phone or not _is_valid_phone(phone):
        return jsonify({"ok": False, "error": "No valid phone number on your account"}), 400
    result = send_notification_auto(phone, "🔐 Smart AgriWeather 2FA test — your verification channel works!")
    return jsonify({"ok": result.get("success", False),
                    "channel": result.get("channel"),
                    "result": result})


@auth_bp.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """
    Called every ~45 s by static/js/heartbeat.js for signed-in users.
    Updates users.last_seen_at (rate-limited in SQL to one write per 10 s per user).
    No CSRF token needed: it only refreshes the caller's own timestamp and the
    session cookie is SameSite=Lax.
    """
    user = current_user()
    if user is None:
        return jsonify({"ok": False, "error": "not_authenticated"}), 401
    try:
        with db.connect() as conn:
            conn.execute(
                "UPDATE users SET last_seen_at = now(), is_online = TRUE WHERE id = %s::uuid "
                "AND (last_seen_at IS NULL OR last_seen_at < now() - interval '10 seconds')", (user["id"],))
            conn.commit()
    except db.DatabaseUnavailable:
        return jsonify({"ok": False, "error": "db_unavailable"}), 503
    except Exception:
        logger.exception("heartbeat failed")
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify({"ok": True, "interval": HEARTBEAT_SECONDS})


# --------------------------------------------------------------------------
# App wiring
# --------------------------------------------------------------------------
def _inject():
    user = current_user()
    return {
        "current_user": user,
        "csrf_token": csrf_token,
        "heartbeat_seconds": HEARTBEAT_SECONDS,
        "allow_signup": ALLOW_SIGNUP,
        "totp_available": totp_auth.is_available,
    }


def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    path = request.path
    if path.startswith(("/admin", "/api/admin", "/login", "/signup", "/2fa", "/api/heartbeat", "/dashboard/activity")):
        response.headers["Cache-Control"] = "no-store"
    return response


def init_app(app):
    secure = os.environ.get("SESSION_COOKIE_SECURE")
    app.config.update(
        SESSION_COOKIE_NAME="sw_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_truthy(secure) if secure is not None else (not is_debug()),
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("SESSION_DAYS", "7"))),
    )
    app.register_blueprint(auth_bp)
    app.context_processor(_inject)
    app.after_request(_security_headers)
