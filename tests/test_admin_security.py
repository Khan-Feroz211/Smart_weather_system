"""
Security + rendering tests for the admin panel and authentication layer.

Run from the project root:   python -m unittest tests.test_admin_security -v

They use `tests/fake_db.py` (no PostgreSQL / Supabase needed).  They prove the
authorisation logic, CSRF handling, error handling and that every admin page
renders; they do NOT execute the SQL (run scripts/check_supabase.py for that).
"""
import os
import re
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

import admin_routes  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import supabase_client as sb  # noqa: E402
from fake_db import FakeDB  # noqa: E402

ADMIN_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
OTHER_ID = str(uuid.uuid4())
PROFILE_COLS = ["id", "user_number", "username", "email", "location", "role", "is_active", "last_seen_at", "has_admin_record"]


def make_app():
    app = Flask("t", root_path=ROOT, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    app.secret_key = "t" * 48
    app.config["TESTING"] = False        # keep real error handling paths
    for name in ("dashboard", "agri_dashboard", "farms_list", "agri_alerts_view", "user_management", "weather_display"):
        app.add_url_rule(f"/{name}", name, lambda: "ok")
    auth.init_app(app)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.register_blueprint(admin_routes.admin_bp)
    return app


class Base(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.fake = FakeDB()
        p = mock.patch.object(db, "connect", self.fake.connect)
        p.start()
        self.addCleanup(p.stop)
        auth.login_limiter._hits.clear()

    def profile(self, uid, role="user", active=True, admin_record=False):
        return (PROFILE_COLS, [(uid, 1, "someone", "s@x.com", "Lahore", role, active, "2026-09-19 09:59:00", admin_record)])

    def login_as(self, uid, role="user", admin_session=False, admin_record=None, active=True, age=0, idle=0):
        self.fake.on("AS has_admin_record", *self.profile(uid, role, active, role == "admin" if admin_record is None else admin_record))
        with self.client.session_transaction() as s:
            s["uid"] = uid
            s["_csrf"] = "tok"
            if admin_session:
                s["admin_at"] = time.time() - age
                s["admin_seen"] = time.time() - idle

    def csrf(self):
        return {"X-CSRF-Token": "tok"}


class TestAuthorization(Base):
    ADMIN_GETS = ["/admin", "/admin/dashboard", "/admin/users", "/admin/users/active", "/admin/activity",
                  "/admin/weather", "/admin/alerts", "/admin/farms", "/admin/fields", "/admin/crops",
                  "/admin/crop-health", "/admin/yield-forecasts", "/admin/sensors", "/admin/ai",
                  "/admin/analytics", "/admin/settings", f"/admin/users/{USER_ID}"]
    API_GETS = ["/api/admin/stats", "/api/admin/users", "/api/admin/active-users", "/api/admin/activity",
                "/api/admin/weather-stats", "/api/admin/alert-stats", "/api/admin/agriculture-stats",
                "/api/admin/sensor-stats", "/api/admin/ai-stats", "/api/admin/analytics"]

    def test_unauthenticated_pages_redirect_to_admin_login(self):
        for path in self.ADMIN_GETS:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/admin/login", r.headers["Location"], path)

    def test_unauthenticated_api_is_401(self):
        for path in self.API_GETS:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertFalse(r.get_json()["ok"])

    def test_unauthenticated_actions_are_401_not_reachable(self):
        for path in (f"/api/admin/users/{USER_ID}/toggle", f"/api/admin/users/{USER_ID}/delete",
                     "/api/admin/alerts/weather/1/toggle", "/api/admin/alerts/weather/1/delete"):
            self.assertEqual(self.client.post(path, headers=self.csrf()).status_code, 401, path)

    def test_normal_user_is_redirected_away_from_admin(self):
        self.login_as(USER_ID, "user", admin_session=True)      # even with a forged admin_at flag
        for path in self.ADMIN_GETS:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertNotIn("/admin", r.headers["Location"], path)

    def test_normal_user_api_is_403(self):
        self.login_as(USER_ID, "user", admin_session=True)
        for path in self.API_GETS:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.post(f"/api/admin/users/{OTHER_ID}/delete", headers=self.csrf()).status_code, 403)

    def test_role_admin_without_admin_users_row_is_denied(self):
        self.login_as(ADMIN_ID, "admin", admin_session=True, admin_record=False)
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 403)

    def test_admin_role_must_use_admin_login(self):
        self.login_as(ADMIN_ID, "admin", admin_session=False)
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login", r.headers["Location"])
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 401)

    def test_deactivated_user_session_is_dropped(self):
        self.login_as(ADMIN_ID, "admin", admin_session=True, active=False)
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 401)
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)

    def test_admin_session_expires_absolute_and_idle(self):
        self.login_as(ADMIN_ID, "admin", admin_session=True, age=auth.ADMIN_SESSION_HOURS * 3600 + 60)
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 401)
        self.login_as(ADMIN_ID, "admin", admin_session=True, idle=auth.ADMIN_IDLE_MINUTES * 60 + 60)
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 401)

    def test_forged_uid_that_is_not_a_uuid_is_rejected(self):
        with self.client.session_transaction() as s:
            s["uid"] = "1 OR 1=1"
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 401)


class TestAdminPages(Base):
    def setUp(self):
        super().setUp()
        self.login_as(ADMIN_ID, "admin", admin_session=True)

    def test_every_admin_page_renders(self):
        paths = TestAuthorization.ADMIN_GETS + ["/admin/alerts?type=agri", "/admin/alerts?type=hazard&status=active&severity=critical&q=a",
                                                "/admin/sensors?tab=quality", "/admin/sensors?tab=logs", "/admin/sensors?tab=status&q=s",
                                                "/admin/users?q=a&role=user&status=offline&sort=last_active&dir=asc&page=2",
                                                "/admin/analytics?days=7", "/admin/activity?type=login&q=a", "/admin/ai?status=pending"]
        for path in paths:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"{path}: {r.data[:300]!r}")
            self.assertIn(b"Smart Weather", r.data)

    def test_every_admin_api_returns_json(self):
        for path in TestAuthorization.API_GETS:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.get_json()["ok"], path)

    def test_dashboard_numbers_come_from_the_database(self):
        cols = ["total_users", "active_users", "new_users_today", "weather_records", "active_alerts",
                "total_farms", "total_fields", "active_sensors"]
        self.fake.on("AS total_users", cols, [(128, 24, 5, 1245, 18, 7, 19, 4)])
        html = self.client.get("/admin/dashboard").get_data(as_text=True)
        for n in ("128", "24", "1,245", "18"):
            self.assertIn(n, html)

    def test_missing_metrics_show_na_not_fake_numbers(self):
        self.fake.on("AS max_abs_error", ["total", "pending", "evaluated", "avg_abs_error", "max_abs_error"],
                     [(0, 0, 0, None, None)])
        self.fake.on("AS simulated", ["total", "correct", "incorrect", "simulated"], [(0, 0, 0, 0)])
        html = self.client.get("/admin/ai").get_data(as_text=True)
        self.assertIn("N/A", html)

    def test_no_secret_ever_reaches_the_browser(self):
        secrets_ = ["SERVICE-ROLE-SECRET-VALUE-123", "DB-PASSWORD-SECRET", "anon-key-value"]
        for path in TestAuthorization.ADMIN_GETS + TestAuthorization.API_GETS + ["/admin/login"]:
            body = self.client.get(path).get_data(as_text=True)
            for s in secrets_:
                self.assertNotIn(s, body, f"{s} leaked in {path}")

    def test_database_outage_shows_error_page_not_a_crash(self):
        self.fake.down = True
        # profile lookup needs the DB too -> treated as db_error (503) rather than an exception
        r = self.client.get("/admin/dashboard")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 503)

    def test_query_failure_is_handled(self):
        def boom(sql, params):
            raise RuntimeError("relation does not exist")
        self.fake.overrides.insert(0, ("AS total_users", boom))
        r = self.client.get("/admin/dashboard")
        self.assertEqual(r.status_code, 500)
        self.assertIn(b"query failed", r.data.lower())
        self.assertEqual(self.client.get("/api/admin/stats").status_code, 500)


class TestAdminActions(Base):
    def setUp(self):
        super().setUp()
        self.login_as(ADMIN_ID, "admin", admin_session=True)

    def target(self, uid, role="user", active=True, auth_id=None):
        self.fake.on("FROM users WHERE id = %s::uuid", ["id", "auth_user_id", "username", "role", "is_active"],
                     [(uid, auth_id, "victim", role, active)])

    def test_csrf_required(self):
        r = self.client.post(f"/api/admin/users/{OTHER_ID}/toggle")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "invalid_csrf_token")
        r = self.client.post(f"/api/admin/users/{OTHER_ID}/toggle", headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(r.status_code, 400)

    def test_toggle_user_flips_flag_and_writes_audit(self):
        self.target(OTHER_ID, active=True)
        r = self.client.post(f"/api/admin/users/{OTHER_ID}/toggle", headers=self.csrf())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.get_json()["data"]["is_active"])
        self.assertTrue(self.fake.statements("UPDATE users SET is_active"))
        self.assertTrue(self.fake.statements("INSERT INTO admin_audit_log"))

    def test_cannot_touch_self_or_other_admins(self):
        self.target(ADMIN_ID, role="admin")
        self.assertEqual(self.client.post(f"/api/admin/users/{ADMIN_ID}/toggle", headers=self.csrf()).status_code, 400)
        self.target(OTHER_ID, role="admin")
        r = self.client.post(f"/api/admin/users/{OTHER_ID}/delete", headers=self.csrf())
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.fake.statements("DELETE FROM users"))

    def test_active_user_cannot_be_deleted(self):
        self.target(OTHER_ID, active=True)
        r = self.client.post(f"/api/admin/users/{OTHER_ID}/delete", headers=self.csrf())
        self.assertEqual(r.status_code, 400)
        self.assertIn("Deactivate", r.get_json()["error"])
        self.assertFalse(self.fake.statements("DELETE FROM users"))

    def test_delete_deactivated_user_removes_auth_account_first(self):
        self.target(OTHER_ID, active=False, auth_id=OTHER_ID)
        with mock.patch.object(sb, "admin_delete_user", return_value=True) as m:
            r = self.client.post(f"/api/admin/users/{OTHER_ID}/delete", headers=self.csrf())
        self.assertEqual(r.status_code, 200, r.data)
        m.assert_called_once_with(OTHER_ID)
        self.assertTrue(self.fake.statements("DELETE FROM users"))

    def test_delete_aborts_if_auth_deletion_fails(self):
        self.target(OTHER_ID, active=False, auth_id=OTHER_ID)
        with mock.patch.object(sb, "admin_delete_user", side_effect=sb.SupabaseError("boom")):
            r = self.client.post(f"/api/admin/users/{OTHER_ID}/delete", headers=self.csrf())
        self.assertEqual(r.status_code, 502)
        self.assertFalse(self.fake.statements("DELETE FROM users"))

    def test_bad_user_id_is_rejected(self):
        self.assertEqual(self.client.post("/api/admin/users/not-a-uuid/toggle", headers=self.csrf()).status_code, 400)
        self.assertEqual(self.client.get("/admin/users/not-a-uuid").status_code, 404)

    def test_alert_delete_requires_inactive(self):
        self.fake.overrides.insert(0, ("DELETE FROM weather_alerts", lambda s, p: __import__("fake_db").FakeCursor([], [], rowcount=0)))
        self.fake.on("SELECT is_active FROM weather_alerts", ["is_active"], [(True,)])
        r = self.client.post("/api/admin/alerts/weather/5/delete", headers=self.csrf())
        self.assertEqual(r.status_code, 400)
        self.assertIn("Deactivate", r.get_json()["error"])

    def test_unknown_alert_kind_and_sql_injection_attempts(self):
        self.assertEqual(self.client.post("/api/admin/alerts/users;DROP/1/toggle", headers=self.csrf()).status_code, 404)
        self.assertEqual(self.client.post("/api/admin/alerts/nope/1/toggle", headers=self.csrf()).status_code, 404)
        # sort / filter parameters are whitelisted, never interpolated
        r = self.client.get("/admin/users?sort=created_at;DROP TABLE users&dir=desc;--&role=x'--&status=1 OR 1=1")
        self.assertEqual(r.status_code, 200)
        for sql, _ in self.fake.log:
            self.assertNotIn("DROP TABLE", sql)
        r = self.client.get("/admin/users?q=%25'%3B DROP TABLE users--")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all("DROP" not in s for s, _ in self.fake.log))
        # the search text travels only as a bound parameter, with LIKE wildcards escaped
        self.assertTrue(any(any("\\%" in str(x) for x in p) for _, p in self.fake.log))


class TestLogin(Base):
    def admin_profile_row(self, role="admin", admin_record=True):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(ADMIN_ID,)])
        self.fake.on("AS has_admin_record", *self.profile(ADMIN_ID, role, True, admin_record))

    def post_login(self, path, email="a@x.com", password="pw", token="tok"):
        with self.client.session_transaction() as s:
            s["_csrf"] = "tok"
        return self.client.post(path, data={"email": email, "password": password, "csrf_token": token})

    def test_admin_login_success_creates_admin_session(self):
        self.admin_profile_row()
        with mock.patch.object(sb, "sign_in_with_password", return_value={"id": ADMIN_ID, "email": "a@x.com"}):
            r = self.post_login("/admin/login")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/admin/dashboard"))
        with self.client.session_transaction() as s:
            self.assertEqual(s["uid"], ADMIN_ID)
            self.assertIn("admin_at", s)
        self.assertTrue(self.fake.statements("UPDATE admin_users SET last_login_at"))

    def test_normal_user_cannot_use_admin_login(self):
        self.admin_profile_row(role="user", admin_record=False)
        with mock.patch.object(sb, "sign_in_with_password", return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login("/admin/login")
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid credentials or insufficient permissions", r.data)
        with self.client.session_transaction() as s:
            self.assertNotIn("uid", s)

    def test_wrong_password_uses_same_generic_message_and_rate_limits(self):
        with mock.patch.object(sb, "sign_in_with_password", side_effect=sb.InvalidCredentials("bad")):
            codes = [self.post_login("/admin/login").status_code for _ in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_login_requires_csrf_token(self):
        with mock.patch.object(sb, "sign_in_with_password", return_value={"id": ADMIN_ID}) as m:
            r = self.post_login("/admin/login", token="forged")
        self.assertEqual(r.status_code, 302)
        m.assert_not_called()

    def test_supabase_outage_is_reported_not_crashed(self):
        with mock.patch.object(sb, "sign_in_with_password", side_effect=sb.SupabaseUnavailable("down")):
            r = self.post_login("/admin/login")
        self.assertEqual(r.status_code, 503)
        self.assertIn(b"unreachable", r.data)

    def test_open_redirect_is_blocked(self):
        for bad in ("https://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)"):
            self.assertEqual(auth.safe_next(bad, "/safe"), "/safe")
        self.assertEqual(auth.safe_next("/admin/users?page=2", "/safe"), "/admin/users?page=2")

    def test_user_login_and_heartbeat(self):
        self.fake.on("SELECT public.ensure_profile", ["ensure_profile"], [(USER_ID,)])
        self.fake.on("AS has_admin_record", *self.profile(USER_ID, "user", True, False))
        with mock.patch.object(sb, "sign_in_with_password", return_value={"id": USER_ID, "email": "u@x.com"}):
            r = self.post_login("/login")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.fake.statements("login_count = login_count + 1"))
        self.assertTrue(self.fake.statements("INSERT INTO user_activities"))
        self.fake.log.clear()
        r = self.client.post("/api/heartbeat")
        self.assertEqual(r.status_code, 200)
        upd = self.fake.statements("UPDATE users SET last_seen_at = now()")
        self.assertTrue(upd)
        self.assertEqual(upd[0][1], [USER_ID])           # only the caller's own row is touched

    def test_heartbeat_requires_login(self):
        self.assertEqual(self.client.post("/api/heartbeat").status_code, 401)
        self.assertFalse(self.fake.statements("last_seen_at"))


class TestConfigSafety(unittest.TestCase):
    def test_weak_secret_rejected_outside_debug(self):
        for weak in ("", "short", "smart_weather_ai_2024", "smart_weather_secret_key_that_is_long_enough_123"):
            with mock.patch.dict(os.environ, {"SECRET_KEY": weak, "DEBUG": "False", "FLASK_DEBUG": ""}):
                with self.assertRaises(RuntimeError):
                    auth.resolve_secret_key()

    def test_weak_secret_gets_random_key_in_debug_and_strong_key_is_kept(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "smart_weather_ai_2024", "DEBUG": "True"}):
            k1, k2 = auth.resolve_secret_key(), auth.resolve_secret_key()
            self.assertNotEqual(k1, k2)
            self.assertGreaterEqual(len(k1), 32)
        with mock.patch.dict(os.environ, {"SECRET_KEY": "k" * 40, "DEBUG": "False"}):
            self.assertEqual(auth.resolve_secret_key(), "k" * 40)

    def test_placeholder_config_counts_as_not_configured(self):
        with mock.patch.dict(os.environ, {"SUPABASE_DB_URL": "YOUR_SUPABASE_DB_URL", "SUPABASE_URL": "YOUR_SUPABASE_PROJECT_URL"}):
            self.assertFalse(db.is_configured())
            self.assertFalse(sb.is_configured())
            self.assertIsNone(db.get_db_connection())

    def test_direct_supabase_db_url_uses_session_pooler_candidates(self):
        dsn = "postgresql://postgres:secret@db.dvkxwdeaewtablvxsvze.supabase.co:5432/postgres"
        candidates = db._candidate_dsns(dsn)
        self.assertIn(dsn, candidates)
        self.assertTrue(any("pooler.supabase.com" in c for c in candidates))
        self.assertTrue(any("postgres.dvkxwdeaewtablvxsvze" in c for c in candidates))

    def test_service_role_key_never_in_frontend_files(self):
        pattern = re.compile(r"service_role|SERVICE_ROLE|SUPABASE_SERVICE", re.I)
        offenders = []
        for folder in ("static", "templates"):
            for base, _, files in os.walk(os.path.join(ROOT, folder)):
                for f in files:
                    if f.endswith((".js", ".html", ".css")):
                        with open(os.path.join(base, f), encoding="utf-8", errors="ignore") as fh:
                            text = fh.read()
                        if pattern.search(text):
                            offenders.append(os.path.join(base, f))
        # settings.html only prints the variable NAMES coming from Python (never values)
        self.assertEqual([o for o in offenders if not o.endswith("settings.html")], [])

    def test_row_is_sqlite_row_compatible(self):
        r = db.Row(["a", "b"], [1, 2])
        self.assertEqual((r[0], r["b"], dict(r), list(r), len(r)), (1, 2, {"a": 1, "b": 2}, [1, 2], 2))
        with self.assertRaises(IndexError):
            r["zzz"]

    def test_safe_json_handles_nan_numpy_and_datetime(self):
        import datetime
        out = db.safe_json({"a": float("nan"), "b": [float("inf"), 1.5], "c": datetime.datetime(2026, 1, 1)})
        self.assertNotIn("NaN", out)
        self.assertNotIn("Infinity", out)

    def test_as_utc(self):
        self.assertEqual(db.as_utc("2026-09-19T10:00:00Z").isoformat(), "2026-09-19T10:00:00+00:00")
        self.assertIsNone(db.as_utc("garbage"))
        self.assertIsNone(db.as_utc(None))
        self.assertIsNotNone(db.as_utc("2026-09-19T10:00:00").tzinfo)


if __name__ == "__main__":
    unittest.main()
