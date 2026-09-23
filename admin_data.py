"""
admin_data.py
=============
Read/write queries behind the Admin Panel.

Rules followed everywhere in this module
  * Every number comes from the database (COUNT / AVG / SUM ...) - nothing is
    hard-coded; missing data is returned as 0 or None (rendered "N/A").
  * Lists are paginated in SQL (LIMIT/OFFSET); dashboards use aggregate queries,
    never "load everything into Python".
  * SQL fragments (table names, ORDER BY columns) come from whitelists in this
    file; user input is only ever passed as bound parameters.
"""

from datetime import datetime, timezone

import db

ACTIVE_MINUTES = 5          # Active         : last_seen_at within 5 minutes
RECENT_MINUTES = 60         # Recently active: within 60 minutes (but not active)
SENSOR_ACTIVE_MINUTES = 60  # Active sensor  : status 'active' and a reading within 60 minutes
PER_PAGE_DEFAULT = 20

_PRESENCE_CASE = f"""
    CASE WHEN NOT u.is_active THEN 'deactivated'
         WHEN u.last_seen_at >= now() - interval '{ACTIVE_MINUTES} minutes' THEN 'active'
         WHEN u.last_seen_at >= now() - interval '{RECENT_MINUTES} minutes' THEN 'recent'
         ELSE 'offline' END
"""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def rel_time(value):
    """'YYYY-MM-DD HH:MM:SS' (UTC) -> '2 minutes ago'. None -> 'never'."""
    if not value:
        return "never"
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return str(value)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 45:
        return "just now"
    for size, unit in ((86400 * 30, "month"), (86400 * 7, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            n = secs // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def clamp_page(page, per_page, total):
    pages = max(1, -(-total // per_page))
    return min(max(1, page), pages), pages


def paged(conn, select_sql, from_sql, where_sql, params, order_sql, page, per_page=PER_PAGE_DEFAULT):
    """COUNT + LIMIT/OFFSET.  All *_sql arguments are code constants; values go in `params`."""
    total = conn.execute(f"SELECT COUNT(*) FROM {from_sql} {where_sql}", params).fetchone()[0]
    page, pages = clamp_page(page, per_page, total)
    rows = _rows(conn.execute(
        f"SELECT {select_sql} FROM {from_sql} {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s",
        list(params) + [per_page, (page - 1) * per_page]))
    return {"rows": rows, "total": total, "page": page, "pages": pages, "per_page": per_page}


def _where(clauses):
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def overview_stats(conn):
    row = conn.execute(f"""
        SELECT
          (SELECT COUNT(*) FROM users)                                            AS total_users,
          (SELECT COUNT(*) FROM users WHERE is_active
              AND last_seen_at >= now() - interval '{ACTIVE_MINUTES} minutes')    AS active_users,
          (SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('day', now())) AS new_users_today,
          (SELECT COUNT(*) FROM weather_data)                                     AS weather_records,
          (SELECT COUNT(*) FROM weather_alerts WHERE is_active)                   AS active_alerts,
          (SELECT COUNT(*) FROM farms)                                            AS total_farms,
          (SELECT COUNT(*) FROM fields)                                           AS total_fields,
          (SELECT COUNT(*) FROM sensor_health WHERE status = 'active'
              AND last_reading_at >= now() - interval '{SENSOR_ACTIVE_MINUTES} minutes') AS active_sensors
    """).fetchone()
    return dict(row)


ACTIVITY_PHRASES = {
    "login": "logged in", "logout": "logged out",
    "weather_search": "searched weather", "weather_view": "viewed weather",
    "alert_view": "viewed alerts", "farm_created": "created a farm",
    "field_created": "created a field", "prediction_generated": "generated a prediction",
    "recommendation_viewed": "viewed recommendations",
}


def describe_activity(activity_type, notes=None):
    phrase = ACTIVITY_PHRASES.get(activity_type) or (activity_type or "did something").replace("_", " ")
    return f"{phrase} ({notes})" if notes and activity_type in ("weather_search",) else phrase


def recent_activity(conn, limit=10):
    rows = _rows(conn.execute("""
        SELECT ua.activity_id, ua.activity_type, ua.notes, ua.weather_condition, ua.activity_date,
               COALESCE(u.username, 'A visitor') AS username
          FROM user_activities ua LEFT JOIN users u ON u.id = ua.user_id
         ORDER BY ua.activity_date DESC LIMIT %s""", (limit,)))
    for r in rows:
        r["text"] = describe_activity(r["activity_type"], r["notes"])
        r["when"] = rel_time(r["activity_date"])
    return rows


def presence_summary(conn):
    row = conn.execute(f"""
        SELECT COUNT(*) FILTER (WHERE is_active AND last_seen_at >= now() - interval '{ACTIVE_MINUTES} minutes') AS active,
               COUNT(*) FILTER (WHERE is_active AND last_seen_at <  now() - interval '{ACTIVE_MINUTES} minutes'
                                  AND last_seen_at >= now() - interval '{RECENT_MINUTES} minutes') AS recent,
               COUNT(*) FILTER (WHERE is_active AND (last_seen_at IS NULL
                                  OR last_seen_at < now() - interval '{RECENT_MINUTES} minutes')) AS offline,
               COUNT(*) FILTER (WHERE NOT is_active) AS deactivated
          FROM users""").fetchone()
    return dict(row)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
_USER_SORTS = {
    "registered": "u.created_at", "last_active": "u.last_seen_at",
    "username": "lower(u.username)", "email": "lower(u.email)",
}
_USER_STATUS = {
    "enabled": "u.is_active", "deactivated": "NOT u.is_active",
    "active": f"(u.is_active AND u.last_seen_at >= now() - interval '{ACTIVE_MINUTES} minutes')",
    "recent": (f"(u.is_active AND u.last_seen_at < now() - interval '{ACTIVE_MINUTES} minutes' "
               f"AND u.last_seen_at >= now() - interval '{RECENT_MINUTES} minutes')"),
    "offline": f"(u.is_active AND (u.last_seen_at IS NULL OR u.last_seen_at < now() - interval '{RECENT_MINUTES} minutes'))",
}


def users_list(conn, q="", role="", status="", sort="registered", direction="desc", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(u.username ILIKE %s OR u.email ILIKE %s OR u.location ILIKE %s)")
        params += [like, like, like]
    if role in ("user", "admin"):
        clauses.append("u.role = %s")
        params.append(role)
    if status in _USER_STATUS:
        clauses.append(_USER_STATUS[status])
    col = _USER_SORTS.get(sort, _USER_SORTS["registered"])
    order = f"{col} {'ASC' if direction == 'asc' else 'DESC'} NULLS LAST, u.user_number DESC"
    result = paged(
        conn,
        f"u.id::text AS id, u.user_number, u.username, u.email, u.location, u.role, u.is_active, "
        f"u.created_at, u.last_seen_at, u.last_login_at, u.login_count, ({_PRESENCE_CASE}) AS presence",
        "users u", _where(clauses), params, order, page, per_page)
    for r in result["rows"]:
        r["last_seen_rel"] = rel_time(r["last_seen_at"])
    return result


def presence_groups(conn, per_group=50):
    """Active / recently active / offline users for /admin/users/active."""
    out = {}
    for key, cond, order in (
        ("active", _USER_STATUS["active"], "u.last_seen_at DESC"),
        ("recent", _USER_STATUS["recent"], "u.last_seen_at DESC"),
        ("offline", _USER_STATUS["offline"], "u.last_seen_at DESC NULLS LAST"),
    ):
        total = conn.execute(f"SELECT COUNT(*) FROM users u WHERE {cond}").fetchone()[0]
        rows = _rows(conn.execute(
            f"SELECT u.id::text AS id, u.username, u.location, u.last_seen_at, ({_PRESENCE_CASE}) AS presence "
            f"FROM users u WHERE {cond} ORDER BY {order} LIMIT %s", (per_group,)))
        for r in rows:
            r["last_seen_rel"] = rel_time(r["last_seen_at"])
        out[key] = {"total": total, "rows": rows}
    return out


def user_detail(conn, user_id):
    row = conn.execute(f"""
        SELECT u.id::text AS id, u.user_number, u.auth_user_id::text AS auth_user_id, u.username, u.email,
               u.location, u.role, u.is_active, u.created_at, u.updated_at, u.last_seen_at,
               u.last_login_at, u.login_count, ({_PRESENCE_CASE}) AS presence,
               (SELECT COUNT(*) FROM user_activities WHERE user_id = u.id) AS activity_count,
               (SELECT COUNT(*) FROM weather_alerts WHERE user_id = u.id)  AS alert_count
          FROM users u WHERE u.id = %s::uuid""", (user_id,)).fetchone()
    if row is None:
        return None
    user = dict(row)
    user["last_seen_rel"] = rel_time(user["last_seen_at"])
    user["activities"] = recent_activity_for(conn, user_id, 25)
    user["alerts"] = _rows(conn.execute(
        "SELECT alert_id, alert_type, severity, message, is_active, created_at FROM weather_alerts "
        "WHERE user_id = %s::uuid ORDER BY created_at DESC LIMIT 10", (user_id,)))
    return user


def recent_activity_for(conn, user_id, limit):
    rows = _rows(conn.execute(
        "SELECT activity_id, activity_type, notes, weather_condition, activity_date FROM user_activities "
        "WHERE user_id = %s::uuid ORDER BY activity_date DESC LIMIT %s", (user_id, limit)))
    for r in rows:
        r["text"] = describe_activity(r["activity_type"], r["notes"])
        r["when"] = rel_time(r["activity_date"])
    return rows


def activity_page(conn, q="", activity_type="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        clauses.append("(u.username ILIKE %s OR ua.activity_type ILIKE %s)")
        like = f"%{db.like_escape(q)}%"
        params += [like, like]
    if activity_type:
        clauses.append("ua.activity_type = %s")
        params.append(activity_type)
    result = paged(
        conn,
        "ua.activity_id, ua.activity_type, ua.notes, ua.weather_condition, ua.activity_date, "
        "COALESCE(u.username, 'A visitor') AS username",
        "user_activities ua LEFT JOIN users u ON u.id = ua.user_id", _where(clauses), params,
        "ua.activity_date DESC, ua.activity_id DESC", page, per_page)
    for r in result["rows"]:
        r["text"] = describe_activity(r["activity_type"], r["notes"])
        r["when"] = rel_time(r["activity_date"])
    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT activity_type FROM user_activities WHERE activity_type IS NOT NULL ORDER BY 1 LIMIT 50").fetchall()]
    total_events = conn.execute("SELECT COUNT(*) FROM user_activities").fetchone()[0]
    return result, types, total_events


class ActionError(Exception):
    """A refused admin action (safe to show to the admin)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def get_user_for_action(conn, user_id):
    row = conn.execute("SELECT id::text AS id, auth_user_id::text AS auth_user_id, username, role, is_active "
                       "FROM users WHERE id = %s::uuid", (user_id,)).fetchone()
    if row is None:
        raise ActionError("User not found.", 404)
    return dict(row)


def check_can_modify_user(target, acting_admin_id):
    if target["id"] == acting_admin_id:
        raise ActionError("You cannot change or delete your own account from the admin panel.", 400)
    if target["role"] == "admin":
        raise ActionError("Administrator accounts cannot be changed here. Use scripts/create_admin.py.", 400)


def set_user_active(conn, user_id, active):
    conn.execute("UPDATE users SET is_active = %s, is_online = CASE WHEN %s THEN is_online ELSE FALSE END "
                 "WHERE id = %s::uuid", (active, active, user_id))


def delete_user_row(conn, user_id):
    conn.execute("DELETE FROM users WHERE id = %s::uuid", (user_id,))


def log_admin_action(conn, admin_id, action, target_type=None, target_id=None, details=None, ip=None):
    conn.execute(
        "INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details, ip_address) "
        "VALUES (%s::uuid, %s, %s, %s, %s::jsonb, %s)",
        (admin_id, action, target_type, None if target_id is None else str(target_id),
         db.safe_json(details or {}), ip))


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------
def weather_overview(conn):
    stats = dict(conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT location) AS locations,
               COUNT(*) FILTER (WHERE recorded_at >= now() - interval '24 hours') AS last_24h,
               AVG(temperature) FILTER (WHERE recorded_at >= now() - interval '24 hours') AS avg_temp_24h,
               AVG(validation_score) AS avg_validation
          FROM weather_data""").fetchone())
    tracked = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE activity_type = 'weather_search'), "
        "       COUNT(*) FILTER (WHERE activity_type = 'weather_view') FROM user_activities").fetchone()
    stats["searches"], stats["views"] = tracked[0], tracked[1]
    stats["sensor_records"] = conn.execute(
        "SELECT COUNT(*) FROM weather_data WHERE ingestion_mode = 'sensor'").fetchone()[0]
    return stats


def weather_breakdowns(conn):
    def grp(expr, limit=8):
        return _rows(conn.execute(
            f"SELECT {expr} AS label, COUNT(*) AS n FROM weather_data GROUP BY 1 ORDER BY n DESC, 1 LIMIT %s", (limit,)))
    return {
        "conditions": grp("COALESCE(weather_condition, 'Unknown')"),
        "locations": grp("location"),
        "sources": grp("COALESCE(data_source, 'unknown')"),
        "quality": grp("COALESCE(quality_label, 'not assessed')"),
    }


def weather_records(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(location ILIKE %s OR sensor_id ILIKE %s OR weather_condition ILIKE %s)")
        params += [like, like, like]
    return paged(
        conn,
        "data_id, location, temperature, humidity, pressure, wind_speed, weather_condition, "
        "COALESCE(data_source, '—') AS data_source, COALESCE(sensor_id, '—') AS sensor_id, "
        "COALESCE(quality_label, '—') AS quality_label, validation_score, recorded_at",
        "weather_data", _where(clauses), params, "recorded_at DESC, data_id DESC", page, per_page)


# --------------------------------------------------------------------------
# Alerts (weather / agriculture / multi-hazard)
# --------------------------------------------------------------------------
CRITICAL = "('critical','high','red','extreme')"
WARNING = "('warning','medium','moderate','orange','yellow')"

ALERT_KINDS = {
    "weather": {
        "label": "Weather alerts", "table": "weather_alerts",
        "select": "a.alert_id AS id, a.alert_type, a.severity, a.message, a.created_at, a.is_active, "
                  "COALESCE(u.username, '—') AS owner",
        "from": "weather_alerts a LEFT JOIN users u ON u.id = a.user_id", "owner_label": "User",
        "sev_col": "severity", "type_col": "alert_type",
    },
    "agri": {
        "label": "Agriculture alerts", "table": "agri_alerts",
        "select": "a.alert_id AS id, a.alert_type, a.severity, a.message, a.created_at, a.is_active, "
                  "COALESCE(fl.name, fm.name, '—') AS owner",
        "from": "agri_alerts a LEFT JOIN fields fl ON fl.field_id = a.field_id "
                "LEFT JOIN farms fm ON fm.farm_id = COALESCE(a.farm_id, fl.farm_id)", "owner_label": "Field / Farm",
        "sev_col": "severity", "type_col": "alert_type",
    },
    "hazard": {
        "label": "Multi-hazard alerts", "table": "multi_hazard_alerts",
        "select": "a.alert_id AS id, a.hazard_type AS alert_type, a.risk_level AS severity, "
                  "COALESCE(a.message, '') AS message, a.created_at, a.is_active, a.location AS owner",
        "from": "multi_hazard_alerts a", "owner_label": "Location",
        "sev_col": "risk_level", "type_col": "hazard_type",
    },
}


def alert_stats(conn, kind):
    cfg = ALERT_KINDS[kind]
    row = conn.execute(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_active) AS active,
               COUNT(*) FILTER (WHERE lower({cfg['sev_col']}) IN {CRITICAL}) AS critical,
               COUNT(*) FILTER (WHERE lower({cfg['sev_col']}) IN {WARNING}) AS warning
          FROM {cfg['table']}""").fetchone()
    return dict(row)


def alerts_page(conn, kind, status="", severity="", q="", page=1, per_page=PER_PAGE_DEFAULT):
    cfg = ALERT_KINDS[kind]
    clauses, params = [], []
    if status == "active":
        clauses.append("a.is_active")
    elif status == "inactive":
        clauses.append("NOT a.is_active")
    sev_col = "a." + cfg["sev_col"]
    if severity == "critical":
        clauses.append(f"lower({sev_col}) IN {CRITICAL}")
    elif severity == "warning":
        clauses.append(f"lower({sev_col}) IN {WARNING}")
    if q:
        like = f"%{db.like_escape(q)}%"
        msg_col = "a.message"
        type_col = "a." + cfg["type_col"]
        clauses.append(f"({msg_col} ILIKE %s OR {type_col} ILIKE %s)")
        params += [like, like]
    result = paged(conn, cfg["select"], cfg["from"], _where(clauses), params,
                   "a.is_active DESC, a.created_at DESC, a.alert_id DESC", page, per_page)
    for r in result["rows"]:
        r["when"] = rel_time(r["created_at"])
    return result


def toggle_alert(conn, kind, alert_id):
    table = ALERT_KINDS[kind]["table"]           # whitelisted
    row = conn.execute(f"UPDATE {table} SET is_active = NOT is_active WHERE alert_id = %s RETURNING is_active",
                       (alert_id,)).fetchone()
    if row is None:
        raise ActionError("Alert not found.", 404)
    return row[0]


def delete_alert(conn, kind, alert_id):
    table = ALERT_KINDS[kind]["table"]
    cur = conn.execute(f"DELETE FROM {table} WHERE alert_id = %s AND NOT is_active", (alert_id,))
    if cur.rowcount == 0:
        exists = conn.execute(f"SELECT is_active FROM {table} WHERE alert_id = %s", (alert_id,)).fetchone()
        if exists is None:
            raise ActionError("Alert not found.", 404)
        raise ActionError("Deactivate the alert before deleting it.", 400)


# --------------------------------------------------------------------------
# Agriculture
# --------------------------------------------------------------------------
def agri_stats(conn):
    row = conn.execute("""
        SELECT (SELECT COUNT(*) FROM farms)  AS farms,
               (SELECT COUNT(*) FROM fields) AS fields,
               (SELECT COUNT(DISTINCT crop_type) FROM crop_census) AS crop_types,
               (SELECT AVG(health_score) FROM (
                    SELECT DISTINCT ON (field_id) health_score FROM crop_health
                     ORDER BY field_id, recorded_at DESC) h) AS avg_health,
               (SELECT COUNT(*) FROM agri_alerts WHERE is_active) AS active_alerts,
               (SELECT COUNT(*) FROM (SELECT DISTINCT field_id FROM yield_forecasts) y) AS forecasts,
               (SELECT COALESCE(SUM(area_ha), 0) FROM fields) AS total_area
    """).fetchone()
    return dict(row)


def farms_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(f.name ILIKE %s OR f.owner_name ILIKE %s OR f.location ILIKE %s)")
        params += [like, like, like]
    return paged(
        conn,
        "f.farm_id, f.name, f.owner_name, f.location, f.total_area_ha, f.created_at, "
        "(SELECT COUNT(*) FROM fields fl WHERE fl.farm_id = f.farm_id) AS fields",
        "farms f", _where(clauses), params, "f.created_at DESC, f.farm_id DESC", page, per_page)


_FIELD_LATERALS = """
    fields fl JOIN farms f ON f.farm_id = fl.farm_id
    LEFT JOIN LATERAL (SELECT crop_type, growth_stage, target_yield_ton_ha FROM crop_census
                        WHERE field_id = fl.field_id ORDER BY created_at DESC LIMIT 1) cc ON TRUE
    LEFT JOIN LATERAL (SELECT health_score, heat_stress, frost_risk, drought_stress, excess_moisture, recorded_at
                         FROM crop_health WHERE field_id = fl.field_id ORDER BY recorded_at DESC LIMIT 1) ch ON TRUE
    LEFT JOIN LATERAL (SELECT expected_yield_ton_ha, target_yield_ton_ha, confidence, forecast_date
                         FROM yield_forecasts WHERE field_id = fl.field_id ORDER BY forecast_date DESC LIMIT 1) yf ON TRUE
"""


def fields_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(fl.name ILIKE %s OR f.name ILIKE %s OR cc.crop_type ILIKE %s)")
        params += [like, like, like]
    return paged(
        conn,
        "fl.field_id, fl.name, f.name AS farm, fl.area_ha, fl.soil_type, fl.irrigation_type, "
        "cc.crop_type, cc.growth_stage, ch.health_score, fl.created_at",
        _FIELD_LATERALS, _where(clauses), params, "fl.created_at DESC, fl.field_id DESC", page, per_page)


def crops_summary(conn):
    return _rows(conn.execute("""
        SELECT crop_type, COUNT(DISTINCT field_id) AS fields, SUM(area_ha) AS total_area_ha,
               AVG(target_yield_ton_ha) AS avg_target_yield
          FROM crop_census GROUP BY crop_type ORDER BY total_area_ha DESC, crop_type"""))


def crop_health_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = ["ch.recorded_at IS NOT NULL"], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(fl.name ILIKE %s OR f.name ILIKE %s)")
        params += [like, like]
    return paged(
        conn,
        "fl.field_id, fl.name, f.name AS farm, cc.crop_type, ch.health_score, ch.heat_stress, ch.frost_risk, "
        "ch.drought_stress, ch.excess_moisture, ch.recorded_at",
        _FIELD_LATERALS, _where(clauses), params, "ch.health_score ASC NULLS LAST, fl.field_id", page, per_page)


def yield_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = ["yf.forecast_date IS NOT NULL"], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(fl.name ILIKE %s OR f.name ILIKE %s OR cc.crop_type ILIKE %s)")
        params += [like, like, like]
    return paged(
        conn,
        "fl.field_id, fl.name, f.name AS farm, cc.crop_type, yf.expected_yield_ton_ha, yf.target_yield_ton_ha, "
        "(yf.target_yield_ton_ha - yf.expected_yield_ton_ha) AS gap, yf.confidence, yf.forecast_date",
        _FIELD_LATERALS, _where(clauses), params, "yf.forecast_date DESC, fl.field_id", page, per_page)


# --------------------------------------------------------------------------
# Sensors
# --------------------------------------------------------------------------
def sensor_stats(conn):
    row = conn.execute(f"""
        SELECT (SELECT COUNT(*) FROM sensor_health) AS total,
               (SELECT COUNT(*) FROM sensor_health WHERE status = 'active'
                   AND last_reading_at >= now() - interval '{SENSOR_ACTIVE_MINUTES} minutes') AS active,
               (SELECT COUNT(*) FROM sensor_health WHERE status = 'active'
                   AND (last_reading_at IS NULL OR last_reading_at < now() - interval '{SENSOR_ACTIVE_MINUTES} minutes')) AS stale,
               (SELECT COUNT(*) FROM sensor_health WHERE status <> 'active') AS inactive,
               (SELECT AVG(quality_score_avg) FROM sensor_health) AS avg_quality,
               (SELECT COUNT(*) FROM raw_sensor_readings WHERE received_at >= now() - interval '24 hours') AS readings_24h,
               (SELECT COUNT(*) FROM sensor_pipeline_logs) AS pipeline_logs,
               (SELECT (100.0 * SUM(validation_failures) / NULLIF(SUM(total_records), 0))::float8
                  FROM sensor_quality_metrics) AS failure_rate
    """).fetchone()
    return dict(row)


def sensors_status_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        clauses.append("sh.sensor_id ILIKE %s")
        params.append(f"%{db.like_escape(q)}%")
    result = paged(
        conn,
        "sh.sensor_id, t.sensor_type, t.field_id, sh.last_reading_at, sh.status, sh.uptime_percentage, "
        "sh.quality_score_avg, sh.calibration_date, "
        f"(sh.status = 'active' AND sh.last_reading_at >= now() - interval '{SENSOR_ACTIVE_MINUTES} minutes') AS is_live",
        "sensor_health sh LEFT JOIN LATERAL (SELECT sensor_type, field_id FROM validated_sensor_readings v "
        "WHERE v.sensor_id = sh.sensor_id ORDER BY v.timestamp DESC LIMIT 1) t ON TRUE",
        _where(clauses), params, "sh.last_reading_at DESC NULLS LAST, sh.sensor_id", page, per_page)
    for r in result["rows"]:
        r["last_reading_rel"] = rel_time(r["last_reading_at"])
    return result


def sensor_quality_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        clauses.append("sensor_id ILIKE %s")
        params.append(f"%{db.like_escape(q)}%")
    return paged(
        conn,
        "sensor_id, total_records, validation_failures, duplicate_records, outlier_corrections, "
        "(100.0 * validation_failures / NULLIF(total_records, 0))::float8 AS failure_rate, updated_at",
        "sensor_quality_metrics", _where(clauses), params, "updated_at DESC, sensor_id", page, per_page)


def sensor_logs_page(conn, q="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if q:
        like = f"%{db.like_escape(q)}%"
        clauses.append("(sensor_id ILIKE %s OR location ILIKE %s)")
        params += [like, like]
    return paged(
        conn,
        "log_id, sensor_id, source, location, quality_label, validation_score, "
        "decision_payload ->> 'severity' AS severity, quality_flags::text AS quality_flags, created_at",
        "sensor_pipeline_logs", _where(clauses), params, "created_at DESC, log_id DESC", page, per_page)


# --------------------------------------------------------------------------
# AI monitoring
# --------------------------------------------------------------------------
def ai_stats(conn):
    pred = dict(conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'pending')   AS pending,
               COUNT(*) FILTER (WHERE status = 'evaluated') AS evaluated,
               AVG(error_abs) FILTER (WHERE status = 'evaluated') AS avg_abs_error,
               MAX(error_abs) FILTER (WHERE status = 'evaluated') AS max_abs_error
          FROM prediction_quality_metrics""").fetchone())
    fb = dict(conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE is_correct)     AS correct,
               COUNT(*) FILTER (WHERE NOT is_correct) AS incorrect,
               COUNT(*) FILTER (WHERE is_simulated)   AS simulated
          FROM agri_feedback""").fetchone())
    fb["accuracy"] = (fb["correct"] / fb["total"]) if fb["total"] else None
    fb["provenance"] = ("no feedback yet" if not fb["total"]
                        else "simulated data" if fb["simulated"] == fb["total"]
                        else "includes real feedback")
    exp = dict(conn.execute("""
        SELECT (SELECT COUNT(*) FROM experiments) AS experiments,
               (SELECT COUNT(*) FROM experiments WHERE status = 'running') AS running,
               (SELECT COUNT(*) FROM model_versions) AS model_versions,
               (SELECT COUNT(*) FROM model_versions WHERE is_production) AS production_models
    """).fetchone())
    return {"predictions": pred, "feedback": fb, "experiments": exp}


def prediction_by_location(conn):
    return _rows(conn.execute("""
        SELECT COALESCE(location, 'unknown') AS location, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'evaluated') AS evaluated,
               AVG(error_abs) FILTER (WHERE status = 'evaluated') AS avg_abs_error
          FROM prediction_quality_metrics GROUP BY 1 ORDER BY total DESC LIMIT 10"""))


def predictions_page(conn, status="", page=1, per_page=PER_PAGE_DEFAULT):
    clauses, params = [], []
    if status in ("pending", "evaluated"):
        clauses.append("status = %s")
        params.append(status)
    return paged(
        conn,
        "metric_id, location, sensor_id, predicted_temperature, actual_temperature, error_abs, "
        "target_timestamp, status, updated_at",
        "prediction_quality_metrics", _where(clauses), params, "updated_at DESC, metric_id DESC", page, per_page)


def recent_experiments(conn, limit=8):
    return _rows(conn.execute(
        "SELECT experiment_name, model_type, status, start_time, end_time FROM experiments "
        "ORDER BY start_time DESC LIMIT %s", (limit,)))


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
_SERIES = {                       # key -> (table, timestamp column) -- whitelist
    "users": ("users", "created_at"),
    "weather": ("weather_data", "recorded_at"),
    "alerts": ("weather_alerts", "created_at"),
    "farms": ("farms", "created_at"),
    "sensors": ("raw_sensor_readings", "received_at"),
}


def clamp_days(days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    return min(max(days, 7), 90)


def _series_result(rows):
    return {"labels": [r["day"] for r in rows], "values": [r["n"] for r in rows]}


def daily_counts(conn, key, days=30):
    table, col = _SERIES[key]
    days = clamp_days(days)
    rows = _rows(conn.execute(f"""
        SELECT to_char(d::date, 'YYYY-MM-DD') AS day, COALESCE(c.n, 0) AS n
          FROM generate_series((current_date - (%s::int - 1))::timestamp, current_date::timestamp, interval '1 day') AS d
          LEFT JOIN (SELECT ({col})::date AS day, COUNT(*) AS n FROM {table}
                      WHERE {col} >= current_date - (%s::int - 1) GROUP BY 1) c ON c.day = d::date
         ORDER BY d""", (days, days)))
    return _series_result(rows)


def daily_active_users(conn, days=30):
    days = clamp_days(days)
    rows = _rows(conn.execute("""
        SELECT to_char(d::date, 'YYYY-MM-DD') AS day, COALESCE(c.n, 0) AS n
          FROM generate_series((current_date - (%s::int - 1))::timestamp, current_date::timestamp, interval '1 day') AS d
          LEFT JOIN (SELECT activity_date::date AS day, COUNT(DISTINCT user_id) AS n FROM user_activities
                      WHERE user_id IS NOT NULL AND activity_date >= current_date - (%s::int - 1)
                      GROUP BY 1) c ON c.day = d::date
         ORDER BY d""", (days, days)))
    return _series_result(rows)


def prediction_series(conn, days=30):
    days = clamp_days(days)
    rows = _rows(conn.execute("""
        SELECT to_char(d::date, 'YYYY-MM-DD') AS day, COALESCE(c.n, 0) AS n, c.avg_err
          FROM generate_series((current_date - (%s::int - 1))::timestamp, current_date::timestamp, interval '1 day') AS d
          LEFT JOIN (SELECT updated_at::date AS day, COUNT(*) AS n, AVG(error_abs) AS avg_err
                       FROM prediction_quality_metrics
                      WHERE status = 'evaluated' AND updated_at >= current_date - (%s::int - 1)
                      GROUP BY 1) c ON c.day = d::date
         ORDER BY d""", (days, days)))
    return {"labels": [r["day"] for r in rows], "evaluated": [r["n"] for r in rows],
            "avg_error": [None if r["avg_err"] is None else round(r["avg_err"], 3) for r in rows]}


def all_analytics(conn, days=30):
    return {
        "days": clamp_days(days),
        "users": daily_counts(conn, "users", days),
        "daily_active": daily_active_users(conn, days),
        "weather": daily_counts(conn, "weather", days),
        "alerts": daily_counts(conn, "alerts", days),
        "farms": daily_counts(conn, "farms", days),
        "sensors": daily_counts(conn, "sensors", days),
        "predictions": prediction_series(conn, days),
    }


def weather_trend(conn, days=14):
    days = clamp_days(days)
    rows = _rows(conn.execute("""
        SELECT to_char(d::date, 'YYYY-MM-DD') AS day, COALESCE(c.n, 0) AS n, c.avg_temp, c.avg_hum
          FROM generate_series((current_date - (%s::int - 1))::timestamp, current_date::timestamp, interval '1 day') AS d
          LEFT JOIN (SELECT recorded_at::date AS day, COUNT(*) AS n, AVG(temperature) AS avg_temp,
                            AVG(humidity) AS avg_hum FROM weather_data
                      WHERE recorded_at >= current_date - (%s::int - 1) GROUP BY 1) c ON c.day = d::date
         ORDER BY d""", (days, days)))
    r1 = lambda v: None if v is None else round(v, 1)
    return {"labels": [r["day"] for r in rows], "records": [r["n"] for r in rows],
            "avg_temp": [r1(r["avg_temp"]) for r in rows], "avg_humidity": [r1(r["avg_hum"]) for r in rows]}


# --------------------------------------------------------------------------
# Settings page
# --------------------------------------------------------------------------
def db_facts(conn):
    row = conn.execute("SELECT current_database() AS db, version() AS version").fetchone()
    return {"database": row["db"], "version": row["version"].split(" on ")[0]}


def recent_audit(conn, limit=15):
    rows = _rows(conn.execute("""
        SELECT l.action, l.target_type, l.target_id, l.created_at, COALESCE(u.username, '—') AS admin
          FROM admin_audit_log l LEFT JOIN users u ON u.id = l.admin_id
         ORDER BY l.created_at DESC LIMIT %s""", (limit,)))
    for r in rows:
        r["when"] = rel_time(r["created_at"])
    return rows
