#!/usr/bin/env python3
"""
Copy the data in the legacy SQLite file (smart_weather.db) into Supabase PostgreSQL.

    python scripts/migrate_sqlite_to_supabase.py --dry-run     # read-only preview, no database needed
    python scripts/migrate_sqlite_to_supabase.py               # perform the migration

* The SQLite file is opened READ-ONLY and is never modified or deleted.
* Run `supabase/schema.sql` first, and run this BEFORE starting the app for the
  first time against Supabase (the app seeds demo rows on start-up).
* Safe to re-run: rows are inserted with ON CONFLICT DO NOTHING on their ids.
* Legacy integer user ids are kept as `users.user_number`; every user gets a new
  UUID.  Legacy users have no Supabase Auth account (they never had passwords):
  they stay as "profile only" until the person signs up with the same email
  (the profile is then linked automatically once the email is confirmed).
* Naive timestamps written by the old app (`datetime.now().isoformat()`) are
  interpreted in THIS machine's timezone; SQLite CURRENT_TIMESTAMP values are UTC.
  Run the script on the machine that produced the data.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import db  # noqa: E402  (after load_dotenv)

JSON_COLS = {"preferences", "trigger_conditions", "quality_flags", "cleaning_notes", "feature_blob",
             "decision_blob", "raw_payload", "cleaned_payload", "feature_payload", "decision_payload",
             "tags", "parameters", "metrics", "artifacts", "recommended_actions"}
TS_COLS = {"created_at", "recorded_at", "activity_date", "updated_at", "forecast_date", "sensor_timestamp",
           "timestamp", "received_at", "stored_at", "last_reading_at", "target_timestamp", "start_time", "end_time"}
BOOL_COLS = {"is_active", "is_done", "is_correct", "is_simulated", "is_production"}
DATE_COLS = {"calibration_date"}
NOT_NULL_TS = {"timestamp", "received_at", "stored_at", "created_at", "recorded_at", "activity_date",
               "updated_at", "forecast_date", "start_time"}

# (table, target columns are the SQLite columns present in the target) -- order respects foreign keys
ORDER = ["users", "farms", "fields", "crop_census", "field_weather", "crop_health", "pest_risks",
         "irrigation_recommendations", "yield_forecasts", "agri_alerts", "weather_data",
         "user_activities", "weather_alerts", "raw_sensor_readings", "validated_sensor_readings",
         "sensor_health", "sensor_pipeline_logs", "sensor_quality_metrics", "prediction_quality_metrics",
         "agri_feedback", "experiments", "model_versions", "dataset_versions",
         "multi_hazard_alerts", "hazard_probabilities"]

# child column -> (parent table, parent column); rows pointing at a missing parent are skipped and reported
FK = {
    "fields": {"farm_id": ("farms", "farm_id")},
    "crop_census": {"field_id": ("fields", "field_id")},
    "field_weather": {"field_id": ("fields", "field_id")},
    "crop_health": {"field_id": ("fields", "field_id")},
    "pest_risks": {"field_id": ("fields", "field_id")},
    "irrigation_recommendations": {"field_id": ("fields", "field_id")},
    "yield_forecasts": {"field_id": ("fields", "field_id")},
    "agri_alerts": {"field_id": ("fields", "field_id"), "farm_id": ("farms", "farm_id")},
    "model_versions": {"experiment_id": ("experiments", "experiment_id")},
    "hazard_probabilities": {"alert_id": ("multi_hazard_alerts", "alert_id")},
}
IDENTITY_TABLES = {  # table -> identity column (for sequence sync)
    "farms": "farm_id", "fields": "field_id", "crop_census": "census_id", "field_weather": "fw_id",
    "crop_health": "health_id", "pest_risks": "risk_id", "irrigation_recommendations": "rec_id",
    "yield_forecasts": "forecast_id", "agri_alerts": "alert_id", "weather_data": "data_id",
    "user_activities": "activity_id", "weather_alerts": "alert_id", "raw_sensor_readings": "reading_id",
    "validated_sensor_readings": "reading_id", "sensor_pipeline_logs": "log_id",
    "sensor_quality_metrics": "metric_id", "prediction_quality_metrics": "metric_id",
    "agri_feedback": "id", "multi_hazard_alerts": "alert_id", "hazard_probabilities": "prob_id",
    "users": "user_number",
}


class Stats:
    def __init__(self):
        self.rows = {}

    def get(self, table):
        return self.rows.setdefault(table, {"source": 0, "inserted": 0, "skipped_fk": 0,
                                            "bad_json": 0, "bad_ts": 0, "conflict": 0})


# --------------------------------------------------------------------------
# value converters
# --------------------------------------------------------------------------
def conv_ts(value):
    """SQLite text -> aware UTC datetime or None. 'T' format = app-local naive; space format = UTC."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if "T" in text:
        return db.as_utc(text)
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return db.as_utc(text)


def conv_json(value):
    """TEXT -> canonical JSON text or None (and flag) when it is not valid JSON."""
    if value in (None, ""):
        return None, False
    try:
        return db.safe_json(json.loads(value)), False
    except (TypeError, ValueError):
        return None, True


def conv_bool(value):
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "t", "yes")


def conv_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def convert_row(table, columns, row, st):
    out = []
    for col, value in zip(columns, row):
        if col in JSON_COLS:
            value, bad = conv_json(value)
            st["bad_json"] += bad
        elif col in TS_COLS:
            parsed = conv_ts(value)
            if parsed is None and value not in (None, "") or (parsed is None and col in NOT_NULL_TS):
                st["bad_ts"] += 1 if value not in (None, "") else 0
                parsed = datetime.now(timezone.utc) if col in NOT_NULL_TS else None
            value = parsed
        elif col in BOOL_COLS:
            value = conv_bool(value)
        elif col in DATE_COLS:
            value = conv_date(value)
        elif value == "" and col.endswith(("_id",)):
            value = None
        out.append(value)
    return out


# --------------------------------------------------------------------------
def sqlite_tables(src):
    return {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def sqlite_columns(src, table):
    return [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", default="smart_weather.db", help="path to the SQLite file (default: smart_weather.db)")
    ap.add_argument("--dry-run", action="store_true", help="read and validate only; do not connect to Supabase")
    ap.add_argument("--allow-non-empty", action="store_true", help="migrate even if users/farms already contain rows")
    args = ap.parse_args()

    if not os.path.exists(args.sqlite):
        sys.exit(f"SQLite file not found: {args.sqlite}")
    src = sqlite3.connect(f"file:{os.path.abspath(args.sqlite)}?mode=ro", uri=True)   # read-only
    present = sqlite_tables(src)
    stats = Stats()

    pg = None
    if not args.dry_run:
        if not db.is_configured():
            sys.exit("SUPABASE_DB_URL is not configured (see .env.example). Use --dry-run to preview.")
        import psycopg2
        import psycopg2.extras
        pg = psycopg2.connect(db._dsn(), options="-c timezone=UTC")
        cur = pg.cursor()
        cur.execute("SELECT to_regclass('public.users') IS NOT NULL")
        if not cur.fetchone()[0]:
            sys.exit("Schema not found. Run supabase/schema.sql first.")
        if not args.allow_non_empty:
            cur.execute("SELECT (SELECT COUNT(*) FROM public.users), (SELECT COUNT(*) FROM public.farms)")
            users_n, farms_n = cur.fetchone()
            if users_n or farms_n:
                sys.exit(f"Target is not empty (users={users_n}, farms={farms_n}). The app's demo seed may already "
                         f"have run. Re-run with --allow-non-empty if that is intended.")

    user_map = {}    # legacy int user_id -> uuid
    valid_ids = {}   # table -> set of ids present in the target (for FK checks)

    for table in ORDER:
        if table not in present:
            continue
        st = stats.get(table)
        cols = sqlite_columns(src, table)
        if table == "users":
            target_cols = ["user_number" if c == "user_id" else c for c in cols]
        else:
            target_cols = list(cols)
        if table in ("user_activities", "weather_alerts"):
            pass  # user_id is remapped below
        cursor = src.execute(f'SELECT * FROM "{table}"')
        batch = []

        def flush():
            nonlocal batch
            if not batch or pg is None:
                st["inserted"] += len(batch) if pg is None else 0
                batch = []
                return
            cur = pg.cursor()
            sql = f'INSERT INTO public.{table} ({", ".join(target_cols)}) VALUES %s ON CONFLICT DO NOTHING'
            psycopg2.extras.execute_values(cur, sql, batch, page_size=500)
            st["inserted"] += cur.rowcount
            st["conflict"] += len(batch) - cur.rowcount
            batch = []

        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                st["source"] += 1
                values = convert_row(table, cols, row, st)
                rec = dict(zip(cols, values))
                # foreign keys
                skip = False
                for child_col, (ptable, pcol) in FK.get(table, {}).items():
                    val = rec.get(child_col)
                    if val is not None and pg is not None and val not in valid_ids.get(ptable, set()):
                        skip = True
                if skip:
                    st["skipped_fk"] += 1
                    continue
                if table in ("user_activities", "weather_alerts"):
                    legacy = rec.get("user_id")
                    if legacy is not None:
                        if pg is not None and legacy not in user_map:
                            st["skipped_fk"] += 1
                            continue
                        rec["user_id"] = user_map.get(legacy)
                    values = [rec[c] for c in cols]
                batch.append(tuple(values))
            if len(batch) >= 500:
                flush()
        flush()

        if pg is not None:
            cur = pg.cursor()
            if table == "users":
                cur.execute("SELECT user_number, id::text FROM public.users")
                user_map = {int(n): i for n, i in cur.fetchall()}
            pk = {"farms": "farm_id", "fields": "field_id", "experiments": "experiment_id",
                  "multi_hazard_alerts": "alert_id"}.get(table)
            if pk:
                cur.execute(f"SELECT {pk} FROM public.{table}")
                valid_ids[table] = {r[0] for r in cur.fetchall()}
            pg.commit()

    # ---- identity sequences ------------------------------------------------
    if pg is not None:
        cur = pg.cursor()
        for table, col in IDENTITY_TABLES.items():
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('public.{table}', '{col}'), "
                f"GREATEST(COALESCE(MAX({col}), 0), 1), COALESCE(MAX({col}), 0) > 0) FROM public.{table}")
        pg.commit()

    # ---- report -------------------------------------------------------------
    print(f"\n{'DRY RUN - nothing was written' if args.dry_run else 'MIGRATION REPORT'}  (source: {args.sqlite})")
    print(f"{'table':32}{'source':>8}{'inserted':>10}{'existing':>10}{'skipped(FK)':>13}{'bad JSON':>10}{'bad time':>10}")
    problems = 0
    for table in ORDER:
        if table not in stats.rows:
            continue
        s = stats.rows[table]
        ins = "-" if args.dry_run else s["inserted"]
        print(f"{table:32}{s['source']:>8}{ins!s:>10}{('-' if args.dry_run else s['conflict']):>10}"
              f"{s['skipped_fk']:>13}{s['bad_json']:>10}{s['bad_ts']:>10}")
        if not args.dry_run and s["source"] != s["inserted"] + s["conflict"] + s["skipped_fk"]:
            problems += 1
    if pg is not None:
        cur = pg.cursor()
        print("\nTarget row counts:")
        for table in ORDER:
            if table in stats.rows:
                cur.execute(f"SELECT COUNT(*) FROM public.{table}")
                print(f"  {table:32}{cur.fetchone()[0]:>8}")
        pg.close()
    src.close()
    if problems:
        print(f"\n⚠️  {problems} table(s) do not add up - investigate before switching over.")
        sys.exit(1)
    print("\nDone. The SQLite file was not modified; keep it as a backup until you have verified the data.")


if __name__ == "__main__":
    main()
