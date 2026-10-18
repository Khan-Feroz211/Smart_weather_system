"""
Tests for TOTP-based 2FA and WhatsApp/SMS notification routing.

Run from the project root:  python -m unittest tests.test_auth_2fa -v
"""
import os
import sys
import time
import unittest
import uuid
from unittest import mock

os.environ.setdefault("SECRET_KEY", "x" * 48)
os.environ["DEBUG"] = "True"
os.environ["SUPABASE_URL"] = "https://abcdefgh.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "anon-key-value"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "SERVICE-ROLE-SECRET-VALUE-123"
os.environ["SUPABASE_DB_URL"] = "postgresql://postgres.abc:DB-PASSWORD-SECRET@host:5432/postgres"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, render_template  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import mfa_rate_limit  # noqa: E402
import notifications as notif  # noqa: E402
import totp_auth  # noqa: E402
import supabase_client as sb  # noqa: E402
from fake_db import FakeDB  # noqa: E402

USER_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())
PROFILE_COLS = ["id", "user_number", "username", "email", "location", "role", "is_active",
                "last_seen_at", "has_admin_record", "phone_number", "totp_secret",
                "two_factor_pending"]

# totp_auth.verify_totp result shapes (the structured replacement for the old
# bool contract of verify_code).
VERIFY_OK = {"ok": True, "reason": "ok", "drift_notice": False}
VERIFY_DRIFT = {"ok": True, "reason": "ok", "drift_notice": True}
VERIFY_INVALID = {"ok": False, "reason": "invalid", "drift_notice": False}
VERIFY_REPLAY = {"ok": False, "reason": "replay", "drift_notice": False}
VERIFY_LOCKED = {"ok": False, "reason": "locked", "drift_notice": False}

# mfa_rate_limit.check() outcomes (Phase 3).
GATE_OPEN = mfa_rate_limit.Decision(False, 0, False)
GATE_LOCKED = mfa_rate_limit.Decision(True, 300, False)


def make_app():
    app = Flask("t", root_path=ROOT, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    app.secret_key = "t" * 48
    # Register every endpoint referenced by base.html + 2FA templates
    for name in ("dashboard", "agri_dashboard", "farms_list", "agri_alerts_view",
                 "disease_detection", "weather_display", "full_weather_map",
                 "satellite_map", "user_management", "user_access", "add_user"):
        app.add_url_rule(f"/{name}", name, lambda n=name: f"ok:{n}")
    # The "dashboard" rule above is a stub, not a template, so this probe renders
    # the real dashboard.html -- that is where the clock-drift notice block lives.
    app.add_url_rule("/_probe/dashboard", "_probe_dashboard",
                     lambda: render_template("dashboard.html"))
    auth.init_app(app)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["TESTING"] = True
    return app


def profile(uid, role="user", active=True, admin_record=False,
            totp_secret=None, two_factor_pending=False, phone=None):
    return (PROFILE_COLS, [(uid, 1, "someone", "s@x.com", "Lahore", role, active,
                            "2026-09-19 09:59:00", admin_record, phone,
                            totp_secret, two_factor_pending)])


class Base(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.fake = FakeDB()
        p = mock.patch.object(db, "connect", self.fake.connect)
        p.start()
        self.addCleanup(p.stop)
        auth.login_limiter._hits.clear()
        auth.mfa_limiter._hits.clear()

    def post_login(self, email="a@x.com", password="pw", token="tok"):
        with self.client.session_transaction() as s:
            s["_csrf"] = "tok"
        return self.client.post("/login",
                                data={"email": email, "password": password,
                                      "csrf_token": token})

    def login_as(self, uid, role="user", totp_secret=None, two_factor_pending=False, phone=None):
        self.fake.on("AS has_admin_record", *profile(uid, role, True, False,
                                                      totp_secret=totp_secret,
                                                      two_factor_pending=two_factor_pending,
                                                      phone=phone))
        with self.client.session_transaction() as s:
            s["uid"] = uid
            s["_csrf"] = "tok"

    def set_csrf(self):
        """Put a CSRF token in the session (for CSRF-protected routes)."""
        with self.client.session_transaction() as s:
            s["_csrf"] = "tok"


class TestTOTPModule(unittest.TestCase):
    """Pure unit tests for the totp_auth helper module."""

    def test_secret_generation_and_verification(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp/qrcode not installed")
        secret = totp_auth.generate_secret()
        self.assertIsInstance(secret, str)
        self.assertGreaterEqual(len(secret), 16)
        code = totp_auth.current_code(secret)
        self.assertEqual(len(code), 6)
        self.assertTrue(totp_auth.verify_code(secret, code))

    def test_invalid_code_rejected(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp/qrcode not installed")
        secret = totp_auth.generate_secret()
        self.assertFalse(totp_auth.verify_code(secret, "000000"))
        self.assertFalse(totp_auth.verify_code(secret, ""))
        self.assertFalse(totp_auth.verify_code("", "123456"))

    def test_provisioning_uri_contains_secret_and_issuer(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp/qrcode not installed")
        secret = totp_auth.generate_secret()
        uri = totp_auth.get_provisioning_uri(secret, "user@example.com")
        self.assertIn(secret, uri)
        self.assertIn("otpauth://", uri)
        self.assertIn("issuer=Smart", uri)

    def test_qr_code_generation(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp/qrcode not installed")
        secret = totp_auth.generate_secret()
        uri = totp_auth.get_provisioning_uri(secret, "user@example.com")
        b64 = totp_auth.generate_qr_base64(uri)
        self.assertIsNotNone(b64)
        self.assertIsInstance(b64, str)
        import base64
        base64.b64decode(b64)


class TestNotificationAuto(unittest.TestCase):
    """Tests for send_notification_auto (WhatsApp-first, SMS-fallback)."""

    def setUp(self):
        self._prev = {
            "enabled": notif.NOTIFICATION_ENABLED,
            "wa": notif.TWILIO_WHATSAPP_FROM,
            "sms": notif.TWILIO_SMS_FROM,
            "local": notif.LOCAL_SMS_GATEWAY_URL,
            "sid": notif.TWILIO_ACCOUNT_SID,
            "token": notif.TWILIO_AUTH_TOKEN,
        }
        notif.NOTIFICATION_ENABLED = True

    def tearDown(self):
        for k, v in self._prev.items():
            attr = {"enabled": "NOTIFICATION_ENABLED", "wa": "TWILIO_WHATSAPP_FROM",
                    "sms": "TWILIO_SMS_FROM", "local": "LOCAL_SMS_GATEWAY_URL",
                    "sid": "TWILIO_ACCOUNT_SID", "token": "TWILIO_AUTH_TOKEN"}[k]
            setattr(notif, attr, v)

    def test_disabled_returns_error(self):
        notif.NOTIFICATION_ENABLED = False
        result = notif.send_notification_auto("+15551234567", "hi")
        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])

    def test_invalid_phone_rejected(self):
        result = notif.send_notification_auto("not-a-phone", "hi")
        self.assertFalse(result["success"])
        self.assertIn("Invalid", result["error"])

    def test_twilio_not_configured_uses_local_sms(self):
        notif.TWILIO_ACCOUNT_SID = ""
        notif.TWILIO_AUTH_TOKEN = ""
        result = notif.send_notification_auto("+15551234567", "test")
        self.assertTrue(result["success"])
        self.assertEqual(result["channel"], "local_sms")

    def test_whatsapp_success_does_not_fallback(self):
        notif.TWILIO_ACCOUNT_SID = "ACxxx"
        notif.TWILIO_AUTH_TOKEN = "token"
        notif.TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"
        notif.TWILIO_SMS_FROM = "+14155238886"
        with mock.patch.object(notif, "_get_twilio_client", return_value=mock.Mock()):
            with mock.patch.object(notif, "send_whatsapp", return_value={
                "success": True, "message_sid": "WAv1", "error": None
            }) as wa:
                with mock.patch.object(notif, "send_sms") as sms:
                    result = notif.send_notification_auto("+15551234567", "hi")
                    wa.assert_called_once()
                    sms.assert_not_called()
        self.assertTrue(result["success"])
        self.assertEqual(result["channel"], "whatsapp")

    def test_whatsapp_failure_falls_back_to_sms(self):
        notif.TWILIO_ACCOUNT_SID = "ACxxx"
        notif.TWILIO_AUTH_TOKEN = "token"
        notif.TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"
        notif.TWILIO_SMS_FROM = "+14155238886"
        with mock.patch.object(notif, "_get_twilio_client", return_value=mock.Mock()):
            with mock.patch.object(notif, "send_whatsapp", return_value={
                "success": False, "error": "not on whatsapp"
            }):
                with mock.patch.object(notif, "send_sms", return_value={
                    "success": True, "message_sid": "SMv1", "error": None
                }) as sms:
                    result = notif.send_notification_auto("+15551234567", "hi")
                    sms.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(result["channel"], "sms")

    def test_local_sms_gateway_success(self):
        notif.LOCAL_SMS_GATEWAY_URL = "http://local-gateway/sms"
        notif.TWILIO_ACCOUNT_SID = ""
        notif.TWILIO_AUTH_TOKEN = ""
        with mock.patch.object(notif.requests, "post", return_value=mock.Mock(
            status_code=200, text="OK"
        )) as post:
            result = notif.send_notification_auto("+15551234567", "hi")
            post.assert_called_once()
        self.assertTrue(result["success"])
        self.assertEqual(result["channel"], "local_sms")

    def test_local_sms_gateway_failure(self):
        notif.LOCAL_SMS_GATEWAY_URL = "http://broken-gateway/sms"
        notif.TWILIO_ACCOUNT_SID = ""
        notif.TWILIO_AUTH_TOKEN = ""
        with mock.patch.object(notif.requests, "post", side_effect=ConnectionError("refused")):
            result = notif.send_notification_auto("+15551234567", "hi")
        self.assertFalse(result["success"])


class TestLoginWithout2FA(Base):
    """Users without 2FA should log in normally."""

    def test_user_login_proceeds_without_2fa(self):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=None,
                                                      two_factor_pending=False))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertEqual(s["uid"], USER_ID)
            self.assertNotIn("pending_2fa", s)

    def test_login_redirects_to_2fa_verify_when_enabled(self):
        secret = totp_auth.generate_secret() if totp_auth.is_available() else "JBSWY3DPEHPK3PXP"
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=secret,
                                                      two_factor_pending=False))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/2fa/verify", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)
            self.assertIn("pending_2fa", s)
            self.assertEqual(s["pending_2fa"]["step"], "verify")

    def test_login_redirects_to_2fa_setup_when_pending(self):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=None,
                                                      two_factor_pending=True))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/2fa/setup", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)
            self.assertEqual(s["pending_2fa"]["step"], "setup")


class Test2FAVerify(Base):
    """Tests for /2fa/verify route (no CSRF protection on this route)."""

    def _set_pending(self, step="verify", totp_secret=None, phone=None):
        pending_secret = totp_secret or (totp_auth.generate_secret() if totp_auth.is_available() else "JBSWY3DPEHPK3PXP")
        if totp_auth.is_available():
            code = totp_auth.current_code(pending_secret)
        else:
            code = "000000"
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=pending_secret,
                                                      two_factor_pending=False,
                                                      phone=phone))
        with self.client.session_transaction() as s:
            s["pending_2fa"] = {
                "uid": USER_ID,
                "email": "u@x.com",
                "username": "someone",
                "step": step,
                "next": "/dashboard",
                "created": time.time(),
            }
        return code, pending_secret

    def test_verify_page_requires_pending_state(self):
        r = self.client.get("/2fa/verify")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_valid_code_completes_login(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_OK):
            r = self.client.post("/2fa/verify", data={"code": code})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertEqual(s.get("uid"), USER_ID)
            self.assertNotIn("pending_2fa", s)
            self.assertNotIn("mfa_clock_drift", s)

    def test_invalid_code_stays_on_page(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_INVALID):
            r = self.client.post("/2fa/verify", data={"code": "wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid code. Please try again.", r.data)
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)

    def test_replayed_code_is_rejected_exactly_like_an_invalid_one(self):
        """A reused code must reveal nothing: same status, same generic text."""
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_INVALID):
            invalid = self.client.post("/2fa/verify", data={"code": "wrong"})
        code, secret = self._set_pending("verify", totp_secret=secret)
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_REPLAY):
            replay = self.client.post("/2fa/verify", data={"code": code})
        self.assertEqual(replay.status_code, invalid.status_code)
        self.assertEqual(replay.status_code, 401)
        self.assertIn(b"Invalid code. Please try again.", replay.data)
        for leak in (b"replay", b"reused", b"already", b"locked", b"time step"):
            self.assertNotIn(leak, replay.data.lower())
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)       # pending state is not consumed

    def test_locked_replay_store_is_rejected_generically(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_LOCKED):
            r = self.client.post("/2fa/verify", data={"code": code})
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid code. Please try again.", r.data)
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)

    def test_anchor_failure_fails_closed(self):
        """No replay anchor (db down) => the code is not accepted."""
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(db, "mfa_get_or_create_totp_factor_id",
                               side_effect=db.DatabaseUnavailable("simulated")):
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/verify", data={"code": code})
                verify.assert_not_called()
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid code. Please try again.", r.data)

    def test_drift_notice_is_flagged_and_rendered_on_the_dashboard(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_DRIFT):
            r = self.client.post("/2fa/verify", data={"code": code})
        self.assertEqual(r.status_code, 302)
        with self.client.session_transaction() as s:
            self.assertTrue(s.get("mfa_clock_drift"))
        page = self.client.get("/_probe/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"out of sync", page.data)

    def test_drift_notice_is_shown_once_not_on_every_page_load(self):
        """The Bootstrap dismiss is client-side only, so the flag is one-shot."""
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_DRIFT):
            self.client.post("/2fa/verify", data={"code": code})
        first = self.client.get("/_probe/dashboard")
        self.assertIn(b"out of sync", first.data)
        second = self.client.get("/_probe/dashboard")
        self.assertNotIn(b"out of sync", second.data)
        with self.client.session_transaction() as s:
            self.assertNotIn("mfa_clock_drift", s)

    def test_malformed_code_is_rejected_without_any_database_work(self):
        """Plan D5: a malformed code is the cheapest DoS vector.

        The guard inside verify_totp is not enough -- the route must not load the
        profile, read the limiter or upsert the replay anchor first.
        """
        self._set_pending("verify", totp_secret="JBSWY3DPEHPK3PXP")
        self.fake.log.clear()
        with mock.patch.object(mfa_rate_limit, "check") as check:
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/verify", data={"code": "abcdef"})
                check.assert_not_called()
                verify.assert_not_called()
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid code. Please try again.", r.data)
        self.assertEqual(self.fake.log, [])          # no query at all
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)

    def test_dashboard_has_no_drift_notice_by_default(self):
        self.login_as(USER_ID)
        page = self.client.get("/_probe/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"out of sync", page.data)

    def test_empty_code_shows_error(self):
        self._set_pending("verify", totp_secret="JBSWY3DPEHPK3PXP")
        r = self.client.post("/2fa/verify", data={"code": ""})
        self.assertEqual(r.status_code, 400)

    def test_expired_pending_state_redirects_to_login(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret="JBSWY3DPEHPK3PXP",
                                                      two_factor_pending=False))
        with self.client.session_transaction() as s:
            s["pending_2fa"] = {
                "uid": USER_ID, "email": "u@x.com", "username": "someone",
                "step": "verify", "next": "/dashboard",
                "created": time.time() - 9999,  # expired
            }
        r = self.client.post("/2fa/verify", data={"code": "123456"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


class Test2FASetup(Base):
    """Tests for /2fa/setup route."""

    def _set_pending_setup(self):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=None,
                                                      two_factor_pending=True))
        with self.client.session_transaction() as s:
            s["pending_2fa"] = {
                "uid": USER_ID,
                "email": "u@x.com",
                "username": "someone",
                "step": "setup",
                "next": "/dashboard",
                "created": time.time(),
            }

    def test_setup_page_requires_pending_state(self):
        r = self.client.get("/2fa/setup")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_setup_shows_qr_and_secret(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        r = self.client.get("/2fa/setup")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"data:image/png;base64,", r.data)
        self.assertIn(b"secret", r.data.lower())

    def test_setup_with_valid_code_enables_2fa(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        with self.client.session_transaction() as s:
            secret = s.get("_2fa_setup_secret")
        self.assertIsNotNone(secret)
        code = totp_auth.current_code(secret)
        # Allow the UPDATE to succeed (FakeDB default returns empty cursor for UPDATE)
        r = self.client.post("/2fa/setup", data={"code": code})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertEqual(s.get("uid"), USER_ID)
            self.assertNotIn("pending_2fa", s)
            self.assertNotIn("_2fa_setup_secret", s)

    def test_setup_with_invalid_code_shows_error(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        r = self.client.post("/2fa/setup", data={"code": "wrong"}, follow_redirects=True)
        self.assertIn(b"Invalid code", r.data)
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)  # still pending

    def test_setup_malformed_code_costs_no_database_work(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        self.fake.log.clear()
        with mock.patch.object(mfa_rate_limit, "check") as check:
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/setup", data={"code": "12345"},
                                     follow_redirects=True)
                check.assert_not_called()
                verify.assert_not_called()
        self.assertIn(b"Invalid code", r.data)
        self.assertEqual(self.fake.log, [])
        with self.client.session_transaction() as s:
            self.assertIn("_2fa_setup_secret", s)    # the user can still retry

    def test_setup_on_a_locked_account_changes_nothing(self):
        """The documented deviation: /2fa/setup answers a lockout with a redirect.

        Security requirements are the same as /2fa/verify's 429 -- generic
        message, no code compared, pending state and secret kept.
        """
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        with self.client.session_transaction() as s:
            secret = s.get("_2fa_setup_secret")
        with mock.patch.object(mfa_rate_limit, "check", return_value=GATE_LOCKED):
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/setup",
                                     data={"code": totp_auth.current_code(secret)})
                verify.assert_not_called()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/2fa/setup", r.headers["Location"])
        self.assertEqual(self.fake.statements("SET totp_secret"), [])
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)
            self.assertEqual(s.get("_2fa_setup_secret"), secret)
            messages = [m for _cat, m in s.get("_flashes", [])]
        self.assertEqual(messages, [mfa_rate_limit.GENERIC_LOCKOUT_MESSAGE])

    def test_setup_records_success_only_after_the_secret_is_saved(self):
        """A success row resets the counter, so a failed save must not write one."""
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        with self.client.session_transaction() as s:
            secret = s.get("_2fa_setup_secret")

        def explode(sql, params):
            raise RuntimeError("write failed")
        self.fake.overrides.insert(0, ("UPDATE users SET totp_secret", explode))
        with mock.patch.object(mfa_rate_limit, "record_success") as record:
            r = self.client.post("/2fa/setup",
                                 data={"code": totp_auth.current_code(secret)})
            record.assert_not_called()
        self.assertEqual(r.status_code, 302)
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)
            self.assertEqual(s.get("_2fa_setup_secret"), secret)

    def test_setup_empty_code_redirects(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._set_pending_setup()
        self.client.get("/2fa/setup")
        r = self.client.post("/2fa/setup", data={"code": ""})
        # The route flashes a message and redirects (secret was already consumed in GET)
        self.assertEqual(r.status_code, 302)


class TestSignupWith2FA(Base):
    """Tests for signup with phone number and 2FA opt-in."""

    def post_signup(self, email="new@user.com", password="password123",
                    username="newuser", phone="+15551234567", enable_2fa="on"):
        with self.client.session_transaction() as s:
            s["_csrf"] = "tok"
        data = {"email": email, "password": password, "csrf_token": "tok",
                "username": username, "location": "Lahore"}
        if phone:
            data["phone_number"] = phone
        if enable_2fa:
            data["enable_2fa"] = enable_2fa
        return self.client.post("/signup", data=data)

    def test_signup_with_valid_phone_and_2fa(self):
        user_dict = {"id": USER_ID, "email": "new@user.com", "user_metadata": {}}
        fake_user, needs_conf = (user_dict, False)
        with mock.patch.object(sb, "sign_up", return_value=(fake_user, needs_conf)):
            r = self.post_signup(enable_2fa="on")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        self.assertTrue(self.fake.statements("UPDATE users SET"))

    def test_signup_with_invalid_phone_rejected(self):
        with mock.patch.object(sb, "sign_up", return_value=(None, False)):
            r = self.post_signup(phone="not-a-phone")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"Phone number", r.data)

    def test_signup_without_2fa(self):
        user_dict = {"id": USER_ID, "email": "new@user.com", "user_metadata": {}}
        with mock.patch.object(sb, "sign_up", return_value=(user_dict, False)):
            r = self.post_signup(enable_2fa=None)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_signup_checks_totp_availability(self):
        with mock.patch.object(totp_auth, "is_available", return_value=False):
            r = self.post_signup(enable_2fa="on")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"pyotp", r.data)

    def test_signup_success_sets_two_factor_pending(self):
        user_dict = {"id": USER_ID, "email": "new@user.com", "user_metadata": {}}
        with mock.patch.object(sb, "sign_up", return_value=(user_dict, False)):
            self.post_signup(enable_2fa="on")
        # The UPDATE statement should contain two_factor_pending = TRUE
        updates = self.fake.statements("UPDATE users SET")
        self.assertTrue(any("two_factor_pending" in s[0] for s in updates))


class Test2FADisable(Base):
    """Tests for /2fa/disable (POST, CSRF-protected)."""

    def test_disable_requires_login(self):
        self.set_csrf()
        r = self.client.post("/2fa/disable", data={"code": "123456", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_disable_with_valid_code(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        self.login_as(USER_ID, totp_secret=secret)
        code = totp_auth.current_code(secret)
        r = self.client.post("/2fa/disable", data={"code": code, "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])

    def test_disable_with_invalid_code(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        self.login_as(USER_ID, totp_secret=secret)
        r = self.client.post("/2fa/disable", data={"code": "wrong", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])

    def test_disable_malformed_code_costs_no_limiter_or_verify_work(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self.login_as(USER_ID, totp_secret=totp_auth.generate_secret())
        with mock.patch.object(mfa_rate_limit, "check") as check:
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/disable",
                                     data={"code": "nope", "csrf_token": "tok"})
                check.assert_not_called()
                verify.assert_not_called()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.fake.statements("SET totp_secret = NULL"), [])

    def test_disable_without_2fa_enabled(self):
        self.login_as(USER_ID, totp_secret=None)
        r = self.client.post("/2fa/disable", data={"code": "123456", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])


class TestPhase3RateLimitRoutes(Base):
    """Phase 3: DB-backed lockout wired into the 2FA routes (audit finding C2).

    Only `ip_hash` ever reaches the limiter, so nothing here asserts on a raw
    address -- the assertions check that the raw address is absent instead.
    """

    LOGIN_KEY = "127.0.0.1|a@x.com"

    def _pending_verify(self, secret):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=secret,
                                                      two_factor_pending=False))
        with self.client.session_transaction() as s:
            s["pending_2fa"] = {
                "uid": USER_ID, "email": "u@x.com", "username": "someone",
                "step": "verify", "next": "/dashboard", "created": time.time(),
                "limiter_key": self.LOGIN_KEY,
            }

    # -- lockout on /2fa/verify -------------------------------------------
    def test_locked_account_gets_429_and_the_generic_message(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        self._pending_verify(secret)
        with mock.patch.object(mfa_rate_limit, "check", return_value=GATE_LOCKED):
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/verify",
                                     data={"code": totp_auth.current_code(secret)})
                verify.assert_not_called()      # no code is even compared
        self.assertEqual(r.status_code, 429)
        self.assertIn(mfa_rate_limit.GENERIC_LOCKOUT_MESSAGE.encode(), r.data)
        # The lock reveals nothing: no raw address, no remaining time, no hint
        # about whether the account exists or has 2FA.
        for leak in (b"127.0.0.1", b"locked", b"minutes", b"retry"):
            self.assertNotIn(leak, r.data.lower())
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)     # pending state is NOT consumed
            self.assertNotIn("uid", s)

    def test_in_memory_throttle_short_circuits_before_the_database(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        self._pending_verify(secret)
        for _ in range(auth.mfa_limiter.limit):
            auth.mfa_limiter.hit(f"127.0.0.1|{USER_ID}")
        with mock.patch.object(mfa_rate_limit, "check") as check:
            r = self.client.post("/2fa/verify",
                                 data={"code": totp_auth.current_code(secret)})
            check.assert_not_called()
        self.assertEqual(r.status_code, 429)
        self.assertIn(mfa_rate_limit.GENERIC_LOCKOUT_MESSAGE.encode(), r.data)

    def test_a_rejected_code_is_recorded_as_a_failure(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        self._pending_verify(totp_auth.generate_secret())
        with mock.patch.object(mfa_rate_limit, "check", return_value=GATE_OPEN):
            with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_INVALID):
                with mock.patch.object(mfa_rate_limit, "record_failure") as record:
                    r = self.client.post("/2fa/verify", data={"code": "000000"})
        self.assertEqual(r.status_code, 401)
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(record.call_args.args[0], "totp")
        self.assertEqual(kwargs["user_id"], USER_ID)
        self.assertIsNone(kwargs["ip_hash"])            # no MFA_IP_PEPPER here
        self.assertNotIn("127.0.0.1", str(record.call_args))

    # -- D8: the login counter survives the password step -----------------
    def test_login_counter_is_not_reset_when_a_2fa_step_follows(self):
        secret = totp_auth.generate_secret() if totp_auth.is_available() else "JBSWY3DPEHPK3PXP"
        for _ in range(3):
            auth.login_limiter.hit(self.LOGIN_KEY)
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=secret,
                                                      two_factor_pending=False))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login()
        self.assertIn("/2fa/verify", r.headers["Location"])
        self.assertEqual(len(auth.login_limiter._hits[self.LOGIN_KEY]), 3)
        with self.client.session_transaction() as s:
            self.assertEqual(s["pending_2fa"]["limiter_key"], self.LOGIN_KEY)

    def test_login_counter_is_reset_after_a_successful_verification(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        for _ in range(3):
            auth.login_limiter.hit(self.LOGIN_KEY)
        self._pending_verify(secret)
        with mock.patch.object(mfa_rate_limit, "check", return_value=GATE_OPEN):
            with mock.patch.object(totp_auth, "verify_totp", return_value=VERIFY_OK):
                r = self.client.post("/2fa/verify",
                                     data={"code": totp_auth.current_code(secret)})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(self.LOGIN_KEY, auth.login_limiter._hits)

    def test_failed_password_attempt_is_recorded_without_a_user_id(self):
        with mock.patch.object(auth, "verify_credentials",
                               side_effect=auth.LoginError("Invalid email or password.")):
            with mock.patch.object(mfa_rate_limit, "record_failure") as record:
                r = self.post_login()
        self.assertEqual(r.status_code, 401)
        record.assert_called_once()
        self.assertEqual(record.call_args.args[0], "password")
        self.assertIsNone(record.call_args.kwargs["user_id"])
        self.assertIsNone(record.call_args.kwargs["ip_hash"])
        self.assertNotIn("127.0.0.1", str(record.call_args))

    def test_password_success_is_only_recorded_when_no_mfa_step_follows(self):
        """A success row resets the counter, so it must wait for the MFA step."""
        secret = totp_auth.generate_secret() if totp_auth.is_available() else "JBSWY3DPEHPK3PXP"
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=secret,
                                                      two_factor_pending=False))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            with mock.patch.object(mfa_rate_limit, "record_success") as record:
                self.post_login()
        record.assert_not_called()

        self.fake.overrides = []
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *profile(USER_ID, "user", True, False,
                                                      totp_secret=None,
                                                      two_factor_pending=False))
        with mock.patch.object(sb, "sign_in_with_password",
                               return_value={"id": USER_ID, "email": "u@x.com"}):
            with mock.patch.object(mfa_rate_limit, "record_success") as record:
                self.post_login()
        record.assert_called_once()
        self.assertEqual(record.call_args.args[0], "password")
        self.assertEqual(record.call_args.kwargs["user_id"], USER_ID)

    # -- /2fa/disable ------------------------------------------------------
    def test_locked_account_cannot_disable_2fa(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        secret = totp_auth.generate_secret()
        self.login_as(USER_ID, totp_secret=secret)
        with mock.patch.object(mfa_rate_limit, "check", return_value=GATE_LOCKED):
            with mock.patch.object(totp_auth, "verify_totp") as verify:
                r = self.client.post("/2fa/disable",
                                     data={"code": totp_auth.current_code(secret),
                                           "csrf_token": "tok"})
                verify.assert_not_called()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        # Nothing changed: the secret was never cleared.
        self.assertEqual(self.fake.statements("SET totp_secret = NULL"), [])
        with self.client.session_transaction() as s:
            messages = [m for _cat, m in s.get("_flashes", [])]
        self.assertEqual(messages, [mfa_rate_limit.GENERIC_LOCKOUT_MESSAGE])


class Test2FASendTest(Base):
    """Tests for /2fa/send-test notification endpoint (POST, CSRF-protected)."""

    def test_send_test_requires_login(self):
        self.set_csrf()
        r = self.client.post("/2fa/send-test", data={"csrf_token": "tok"})
        self.assertEqual(r.status_code, 401)
        import json
        data = json.loads(r.data)
        self.assertEqual(data["error"], "not_authenticated")

    def test_send_test_no_phone(self):
        self.login_as(USER_ID, totp_secret=None, phone=None)
        r = self.client.post("/2fa/send-test", data={"csrf_token": "tok"})
        self.assertEqual(r.status_code, 400)
        import json
        data = json.loads(r.data)
        self.assertIn("phone", data["error"].lower())

    def test_send_test_with_phone_sends_notification(self):
        self.login_as(USER_ID, totp_secret=None, phone="+15551234567")
        with mock.patch.object(auth, "send_notification_auto",
                               return_value={"success": True, "channel": "local_sms"}) as sn:
            r = self.client.post("/2fa/send-test", data={"csrf_token": "tok"})
            sn.assert_called_once_with("+15551234567", mock.ANY)
        self.assertEqual(r.status_code, 200)
        import json
        data = json.loads(r.data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["channel"], "local_sms")


if __name__ == "__main__":
    unittest.main()
