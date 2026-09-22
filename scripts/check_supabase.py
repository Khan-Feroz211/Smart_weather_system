#!/usr/bin/env python3
"""
Post-setup verification.  Read-only.

    python scripts/check_supabase.py

Checks: env variables, database connection, that every table / column the app needs exists,
RLS enabled everywhere, at least one active admin, and that EVERY admin-panel query executes
(so a typo or missing column shows up here instead of on the dashboard).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import admin_data as ad  # noqa: E402
import db  # noqa: E402
import supabase_client as sb  # noqa: E402

TABLES = ["users", "admin_users", "weather_data", "user_activities", "weather_alerts", "multi_hazard_alerts",
          "hazard_probabilities", "farms", "fields", "crop_census", "field_weather", "crop_health", "pest_risks",
          "irrigation_recommendations", "yield_forecasts", "agri_alerts", "agri_feedback", "raw_sensor_readings",
          "validated_sensor_readings", "sensor_health", "sensor_pipeline_logs", "sensor_quality_metrics",
          "prediction_quality_metrics", "experiments", "model_versions", "dataset_versions", "admin_audit_log"]
fails = 0


def ok(label, good, detail=""):
    global fails
    print(f"  {'✓' if good else '✗'} {label}{(' - ' + detail) if detail else ''}")
    fails += 0 if good else 1


print("Environment")
for name, present in sb.public_status().items():
    ok(name, present)
ok("SUPABASE_DB_URL", db.is_configured())
if not db.is_configured():
    sys.exit(1)

print("\nDatabase")
try:
    conn = db.connect()
except db.DatabaseUnavailable as exc:
    ok("connection", False, str(exc))
    sys.exit(1)
ok("connection", True)
with conn:
    have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    missing = [t for t in TABLES if t not in have]
    ok(f"{len(TABLES)} required tables exist", not missing, ("missing: " + ", ".join(missing)) if missing else "")
    if missing:
        print("\nRun supabase/schema.sql first.")
        sys.exit(1)
    no_rls = [r[0] for r in conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity")]
    ok("row level security enabled on every table", not no_rls, ", ".join(no_rls))
    n_admin = conn.execute("SELECT COUNT(*) FROM users u JOIN admin_users a ON a.user_id = u.id "
                           "WHERE u.role = 'admin' AND u.is_active AND a.is_active").fetchone()[0]
    ok("at least one active administrator", n_admin > 0, "run scripts/create_admin.py" if not n_admin else f"{n_admin} found")

    print("\nAdmin panel queries")
    checks = {
        "overview_stats": lambda: ad.overview_stats(conn), "presence_summary": lambda: ad.presence_summary(conn),
        "recent_activity": lambda: ad.recent_activity(conn), "users_list": lambda: ad.users_list(conn, "a", "user", "active"),
        "presence_groups": lambda: ad.presence_groups(conn), "activity_page": lambda: ad.activity_page(conn),
        "weather_overview": lambda: ad.weather_overview(conn), "weather_breakdowns": lambda: ad.weather_breakdowns(conn),
        "weather_records": lambda: ad.weather_records(conn, "x"), "weather_trend": lambda: ad.weather_trend(conn),
        "agri_stats": lambda: ad.agri_stats(conn), "farms_page": lambda: ad.farms_page(conn),
        "fields_page": lambda: ad.fields_page(conn, "x"), "crops_summary": lambda: ad.crops_summary(conn),
        "crop_health_page": lambda: ad.crop_health_page(conn), "yield_page": lambda: ad.yield_page(conn),
        "sensor_stats": lambda: ad.sensor_stats(conn), "sensors_status_page": lambda: ad.sensors_status_page(conn),
        "sensor_quality_page": lambda: ad.sensor_quality_page(conn), "sensor_logs_page": lambda: ad.sensor_logs_page(conn),
        "ai_stats": lambda: ad.ai_stats(conn), "prediction_by_location": lambda: ad.prediction_by_location(conn),
        "predictions_page": lambda: ad.predictions_page(conn, "pending"), "recent_experiments": lambda: ad.recent_experiments(conn),
        "all_analytics": lambda: ad.all_analytics(conn, 30), "db_facts": lambda: ad.db_facts(conn),
        "recent_audit": lambda: ad.recent_audit(conn),
    }
    for kind in ad.ALERT_KINDS:
        checks[f"alert_stats[{kind}]"] = (lambda k=kind: ad.alert_stats(conn, k))
        checks[f"alerts_page[{kind}]"] = (lambda k=kind: ad.alerts_page(conn, k, "active", "critical", "x"))
    for name, fn in checks.items():
        try:
            fn()
            conn.rollback()
            ok(name, True)
        except Exception as exc:
            conn.rollback()
            ok(name, False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}")

print(f"\n{'All checks passed.' if not fails else str(fails) + ' check(s) failed.'}")
sys.exit(1 if fails else 0)
