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

from flask import Flask  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import notifications as notif  # noqa: E402
import totp_auth  # noqa: E402
import supabase_client as sb  # noqa: E402
from fake_db import FakeDB  # noqa: E402

USER_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())
PROFILE_COLS = ["id", "user_number", "username", "email", "location", "role", "is_active",
                "last_seen_at", "has_admin_record", "phone_number", "totp_secret",
                "two_factor_pending"]


def make_app():
    app = Flask("t", root_path=ROOT, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    app.secret_key = "t" * 48
    # Register every endpoint referenced by base.html + 2FA templates
    for name in ("dashboard", "agri_dashboard", "farms_list", "agri_alerts_view",
                 "disease_detection", "weather_display", "user_management",
                 "user_access", "add_user"):
        app.add_url_rule(f"/{name}", name, lambda n=name: f"ok:{n}")
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
        with mock.patch.object(totp_auth, "verify_code", return_value=True):
            r = self.client.post("/2fa/verify", data={"code": code})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertEqual(s.get("uid"), USER_ID)
            self.assertNotIn("pending_2fa", s)

    def test_invalid_code_stays_on_page(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp not installed")
        code, secret = self._set_pending("verify", totp_secret=totp_auth.generate_secret())
        with mock.patch.object(totp_auth, "verify_code", return_value=False):
            r = self.client.post("/2fa/verify", data={"code": "wrong"})
        self.assertEqual(r.status_code, 401)
        with self.client.session_transaction() as s:
            self.assertIn("pending_2fa", s)

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

    def test_disable_without_2fa_enabled(self):
        self.login_as(USER_ID, totp_secret=None)
        r = self.client.post("/2fa/disable", data={"code": "123456", "csrf_token": "tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])


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
