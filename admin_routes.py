"""
admin_routes.py
===============
Admin Panel blueprint (pages under /admin, JSON under /api/admin).

Security model
  * Every route except /admin/login is wrapped in `auth.admin_required`, which
    re-checks on EVERY request: valid signed session -> profile exists and is
    active -> role == 'admin' -> active row in admin_users -> admin session
    (created via /admin/login) not expired / idle.
  * All state-changing routes also require a CSRF token.
  * Destructive actions are guarded (cannot touch yourself or other admins,
    users/alerts must be deactivated before deletion) and written to
    admin_audit_log.
"""

import logging
import time
import uuid
from functools import wraps

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.exceptions import HTTPException

import admin_data as ad
import auth
import db
import supabase_client as sb

logger = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)

STATUS_FILTERS = [("", "All statuses"), ("enabled", "Enabled"), ("deactivated", "Deactivated"),
                  ("active", "Online now"), ("recent", "Recently active"), ("offline", "Offline")]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _int(name, default, lo=1, hi=100000):
    try:
        return min(max(int(request.args.get(name, default)), lo), hi)
    except (TypeError, ValueError):
        return default


def _q():
    return (request.args.get("q") or "").strip()[:100]


def _error_page(message, status=500, title="Something went wrong"):
    return render_template("admin/error.html", title=title, message=message, active=""), status


def db_view(fn):
    """Open a pooled connection for the view; turn DB failures into a friendly page."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with db.connect() as conn:
                return fn(conn, *args, **kwargs)
        except HTTPException:
            raise
        except db.DatabaseUnavailable:
            return _error_page("The database is unreachable. Check SUPABASE_DB_URL and your network, then retry.",
                               503, "Database unavailable")
        except Exception:
            logger.exception("Admin page failed: %s", request.path)
            return _error_page("The query failed. Details were written to the server log. "
                               "If you have not applied supabase/schema.sql yet, do that first.")
    return wrapper


def api_view(fn):
    """Same for JSON endpoints."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with db.connect() as conn:
                return jsonify({"ok": True, "data": fn(conn, *args, **kwargs)})
        except HTTPException:
            raise
        except ad.ActionError as exc:
            return jsonify({"ok": False, "error": exc.message}), exc.status
        except db.DatabaseUnavailable:
            return jsonify({"ok": False, "error": "database_unavailable"}), 503
        except Exception:
            logger.exception("Admin API failed: %s", request.path)
            return jsonify({"ok": False, "error": "server_error"}), 500
    return wrapper


def _valid_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def page_links(data):
    """Pagination links that keep the current filters."""
    args = request.args.to_dict()

    def url(n):
        args["page"] = n
        return url_for(request.endpoint, **request.view_args, **args)
    page, pages = data["page"], data["pages"]
    window = range(max(1, page - 2), min(pages, page + 2) + 1)
    return {"prev": url(page - 1) if page > 1 else None, "next": url(page + 1) if page < pages else None,
            "numbers": [(n, url(n), n == page) for n in window], "page": page, "pages": pages, "total": data["total"]}


def render_list(title, active, columns, data, stats=None, filters=None, charts=None,
                subtitle="", tabs=None, extra=None, empty="No records found.", search=True):
    return render_template(
        "admin/list.html", title=title, subtitle=subtitle, active=active, columns=columns, data=data,
        stats=stats or [], filters=filters or [], charts=charts or [], tabs=tabs or [], extra=extra or [],
        values=request.args, pg=page_links(data), empty=empty, search=search)


def stat(label, value, icon, tone="primary", kind="num", hint=""):
    return {"label": label, "value": value, "icon": icon, "tone": tone, "kind": kind, "hint": hint}


def col(key, label, kind="text", href=None, cls=""):
    return {"key": key, "label": label, "kind": kind, "href": href, "cls": cls}


def audit(conn, action, target_type=None, target_id=None, details=None):
    ad.log_admin_action(conn, g.admin["id"], action, target_type, target_id, details, auth.client_ip())


# --------------------------------------------------------------------------
# Login / logout
# --------------------------------------------------------------------------
@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    state, _user = auth.admin_state()
    if state == "ok":
        return redirect(url_for("admin.dashboard"))
    nxt = auth.safe_next(request.values.get("next"), url_for("admin.dashboard"))
    if not nxt.startswith("/admin"):
        nxt = url_for("admin.dashboard")
    if request.method == "POST":
        if not auth.csrf_valid():
            flash("Your session expired. Please try again.", "error")
            return redirect(url_for("admin.login", next=nxt))
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        key = f"admin|{auth.client_ip()}|{email}"
        if auth.login_limiter.blocked(key):
            flash("Too many attempts. Please wait a few minutes and try again.", "error")
            return render_template("admin/login.html", next=nxt), 429
        generic = "Invalid credentials or insufficient permissions."
        try:
            profile = auth.verify_credentials(email, password) if email and password else None
        except auth.LoginError as exc:
            if "unreachable" in exc.message or "not configured" in exc.message:
                flash(exc.message, "error")
                return render_template("admin/login.html", next=nxt, email=email), 503
            profile = None
        if profile is None or not auth.is_admin_user(profile):
            auth.login_limiter.hit(key)
            flash(generic, "error")
            return render_template("admin/login.html", next=nxt, email=email), 401
        auth.login_limiter.reset(key)
        auth.start_session(profile, admin=True)
        try:
            with db.connect() as conn:
                conn.execute("UPDATE admin_users SET last_login_at = now() WHERE user_id = %s::uuid", (profile["id"],))
                ad.log_admin_action(conn, profile["id"], "admin_login", "user", profile["id"], {}, auth.client_ip())
                conn.commit()
        except Exception:
            logger.exception("could not record admin login")
        auth.log_activity("login", notes="admin panel", user_id=profile["id"])
        return redirect(nxt)
    return render_template("admin/login.html", next=nxt)


@admin_bp.route("/admin/logout", methods=["POST"])
@auth.csrf_protect
def logout():
    uid = session.get("uid")
    if uid:
        try:
            with db.connect() as conn:
                ad.log_admin_action(conn, uid, "admin_logout", "user", uid, {}, auth.client_ip())
                conn.execute("UPDATE users SET is_online = FALSE WHERE id = %s::uuid", (uid,))
                conn.commit()
        except Exception:
            pass
    session.clear()
    flash("Signed out of the admin panel.", "success")
    return redirect(url_for("admin.login"))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@admin_bp.route("/admin")
@admin_bp.route("/admin/dashboard")
@auth.admin_required
@db_view
def dashboard(conn):
    stats = ad.overview_stats(conn)
    return render_template(
        "admin/dashboard.html", active="dashboard", title="Dashboard", stats=stats,
        presence=ad.presence_summary(conn), activity=ad.recent_activity(conn, 10),
        active_minutes=ad.ACTIVE_MINUTES, recent_minutes=ad.RECENT_MINUTES,
        sensor_minutes=ad.SENSOR_ACTIVE_MINUTES)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
@admin_bp.route("/admin/users")
@auth.admin_required
@db_view
def users(conn):
    sort = request.args.get("sort", "registered")
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    data = ad.users_list(conn, _q(), request.args.get("role", ""), request.args.get("status", ""),
                         sort, direction, _int("page", 1))
    return render_template("admin/users.html", active="users", title="All Users", data=data,
                           pg=page_links(data), values=request.args, status_filters=STATUS_FILTERS,
                           me=g.admin["id"])


@admin_bp.route("/admin/users/active")
@auth.admin_required
@db_view
def users_active(conn):
    return render_template("admin/active_users.html", active="users_active", title="Active Users",
                           groups=ad.presence_groups(conn), summary=ad.presence_summary(conn),
                           active_minutes=ad.ACTIVE_MINUTES, recent_minutes=ad.RECENT_MINUTES)


@admin_bp.route("/admin/users/<user_id>")
@auth.admin_required
@db_view
def user_detail(conn, user_id):
    uid = _valid_uuid(user_id)
    user = ad.user_detail(conn, uid) if uid else None
    if user is None:
        return _error_page("That user does not exist.", 404, "User not found")
    return render_template("admin/user_detail.html", active="users", title=user["username"], user=user,
                           me=g.admin["id"])


@admin_bp.route("/admin/activity")
@auth.admin_required
@db_view
def activity(conn):
    data, types, total = ad.activity_page(conn, _q(), request.args.get("type", ""), _int("page", 1))
    columns = [col("username", "User"), col("text", "Activity"), col("weather_condition", "Weather"),
               col("when", "When"), col("activity_date", "Time (UTC)", "dt")]
    filters = [{"name": "type", "label": "Activity type", "type": "select",
                "options": [("", "All activity")] + [(t, t.replace("_", " ")) for t in types]}]
    return render_list("User Activity", "activity", columns, data, filters=filters,
                       stats=[stat("Recorded events", total, "fa-clock-rotate-left")],
                       subtitle="Events recorded for signed-in users (login, weather views, farms created ...).",
                       empty="No activity has been recorded yet.")


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------
@admin_bp.route("/admin/weather")
@auth.admin_required
@db_view
def weather(conn):
    s = ad.weather_overview(conn)
    trend = ad.weather_trend(conn, 14)
    br = ad.weather_breakdowns(conn)
    data = ad.weather_records(conn, _q(), _int("page", 1))
    stats = [stat("Weather records", s["total"], "fa-cloud-sun"),
             stat("Locations", s["locations"], "fa-location-dot", "info"),
             stat("Records (24 h)", s["last_24h"], "fa-clock", "success"),
             stat("Avg temp (24 h)", s["avg_temp_24h"], "fa-temperature-half", "warning", "f1"),
             stat("Avg validation score", s["avg_validation"], "fa-shield-check", "primary", "f2"),
             stat("Weather searches", s["searches"], "fa-magnifying-glass", "info", hint="tracked for signed-in users"),
             stat("Weather views", s["views"], "fa-eye", "info", hint="tracked for signed-in users"),
             stat("Sensor-mode records", s["sensor_records"], "fa-tower-broadcast", "success")]
    charts = [
        {"id": "trend", "title": "Records & average temperature (14 days)", "type": "bar", "labels": trend["labels"],
         "datasets": [{"label": "Records", "data": trend["records"], "yAxisID": "y"},
                      {"label": "Avg temp °C", "data": trend["avg_temp"], "type": "line", "yAxisID": "y1"}], "wide": True},
        {"id": "cond", "title": "Weather conditions", "type": "doughnut", "labels": [r["label"] for r in br["conditions"]],
         "datasets": [{"label": "Records", "data": [r["n"] for r in br["conditions"]]}]},
        {"id": "loc", "title": "Top locations", "type": "bar", "labels": [r["label"] for r in br["locations"]],
         "datasets": [{"label": "Records", "data": [r["n"] for r in br["locations"]]}]},
        {"id": "src", "title": "Data sources", "type": "doughnut", "labels": [r["label"] for r in br["sources"]],
         "datasets": [{"label": "Records", "data": [r["n"] for r in br["sources"]]}]},
        {"id": "qual", "title": "Validation quality", "type": "doughnut", "labels": [r["label"] for r in br["quality"]],
         "datasets": [{"label": "Records", "data": [r["n"] for r in br["quality"]]}]},
    ]
    columns = [col("recorded_at", "Recorded (UTC)", "dt"), col("location", "Location"),
               col("temperature", "Temp °C", "f1"), col("humidity", "Humidity %", "f1"),
               col("pressure", "Pressure", "f1"), col("wind_speed", "Wind", "f1"),
               col("weather_condition", "Condition"), col("data_source", "Source"),
               col("sensor_id", "Sensor"), col("quality_label", "Quality", "status"),
               col("validation_score", "Score", "f2")]
    return render_list("Weather Data & Analytics", "weather", columns, data, stats=stats, charts=charts,
                       subtitle="Latest weather records with source and validation quality.",
                       empty="No weather records yet.")


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
@admin_bp.route("/admin/alerts")
@auth.admin_required
@db_view
def alerts(conn):
    kind = request.args.get("type", "weather")
    if kind not in ad.ALERT_KINDS:
        kind = "weather"
    cfg = ad.ALERT_KINDS[kind]
    s = ad.alert_stats(conn, kind)
    data = ad.alerts_page(conn, kind, request.args.get("status", ""), request.args.get("severity", ""),
                          _q(), _int("page", 1))
    tabs = [{"key": k, "label": v["label"], "url": url_for("admin.alerts", type=k), "active": k == kind}
            for k, v in ad.ALERT_KINDS.items()]
    return render_template("admin/alerts.html", active="alerts", title="Alerts", kind=kind, cfg=cfg, tabs=tabs,
                           stats=s, data=data, pg=page_links(data), values=request.args)


# --------------------------------------------------------------------------
# Agriculture
# --------------------------------------------------------------------------
def _agri_stats(conn):
    s = ad.agri_stats(conn)
    return [stat("Total farms", s["farms"], "fa-tractor"), stat("Total fields", s["fields"], "fa-vector-square", "info"),
            stat("Crop types", s["crop_types"], "fa-wheat-awn", "success"),
            stat("Avg crop health", s["avg_health"], "fa-heart-pulse", "success", "f1", "latest reading per field"),
            stat("Active agri alerts", s["active_alerts"], "fa-triangle-exclamation", "danger"),
            stat("Fields with forecast", s["forecasts"], "fa-chart-line", "primary"),
            stat("Total field area (ha)", s["total_area"], "fa-ruler-combined", "info", "f1")]


@admin_bp.route("/admin/farms")
@auth.admin_required
@db_view
def farms(conn):
    data = ad.farms_page(conn, _q(), _int("page", 1))
    columns = [col("farm_id", "ID"), col("name", "Farm"), col("owner_name", "Owner"), col("location", "Location"),
               col("total_area_ha", "Area (ha)", "f1"), col("fields", "Fields", "num"), col("created_at", "Created", "dt")]
    return render_list("Farms", "farms", columns, data, stats=_agri_stats(conn), empty="No farms yet.")


@admin_bp.route("/admin/fields")
@auth.admin_required
@db_view
def fields(conn):
    data = ad.fields_page(conn, _q(), _int("page", 1))
    columns = [col("field_id", "ID"), col("name", "Field"), col("farm", "Farm"), col("crop_type", "Crop"),
               col("growth_stage", "Stage"), col("area_ha", "Area (ha)", "f1"), col("soil_type", "Soil"),
               col("irrigation_type", "Irrigation"), col("health_score", "Health", "f1"), col("created_at", "Created", "dt")]
    return render_list("Fields", "fields", columns, data, stats=_agri_stats(conn), empty="No fields yet.")


@admin_bp.route("/admin/crops")
@auth.admin_required
@db_view
def crops(conn):
    rows = ad.crops_summary(conn)
    data = {"rows": rows, "total": len(rows), "page": 1, "pages": 1, "per_page": max(len(rows), 1)}
    columns = [col("crop_type", "Crop"), col("fields", "Fields", "num"), col("total_area_ha", "Total area (ha)", "f1"),
               col("avg_target_yield", "Avg target yield (t/ha)", "f2")]
    charts = [{"id": "crop_area", "title": "Area by crop (ha)", "type": "bar", "labels": [r["crop_type"] for r in rows],
               "datasets": [{"label": "Hectares", "data": [r["total_area_ha"] for r in rows]}]}] if rows else []
    return render_list("Crops", "crops", columns, data, stats=_agri_stats(conn), charts=charts,
                       empty="No crop census records yet.", search=False)


@admin_bp.route("/admin/crop-health")
@auth.admin_required
@db_view
def crop_health(conn):
    data = ad.crop_health_page(conn, _q(), _int("page", 1))
    columns = [col("name", "Field"), col("farm", "Farm"), col("crop_type", "Crop"), col("health_score", "Health score", "f1"),
               col("heat_stress", "Heat stress", "f2"), col("frost_risk", "Frost risk", "f2"),
               col("drought_stress", "Drought stress", "f2"), col("excess_moisture", "Excess moisture", "f2"),
               col("recorded_at", "Recorded", "dt")]
    return render_list("Crop Health", "crop_health", columns, data, stats=_agri_stats(conn),
                       subtitle="Latest health reading for each field, lowest score first.",
                       empty="No crop health readings yet.")


@admin_bp.route("/admin/yield-forecasts")
@auth.admin_required
@db_view
def yield_forecasts(conn):
    data = ad.yield_page(conn, _q(), _int("page", 1))
    columns = [col("name", "Field"), col("farm", "Farm"), col("crop_type", "Crop"),
               col("expected_yield_ton_ha", "Expected (t/ha)", "f2"), col("target_yield_ton_ha", "Target (t/ha)", "f2"),
               col("gap", "Gap (t/ha)", "f2"), col("confidence", "Confidence", "frac_pct"),
               col("forecast_date", "Forecast date", "dt")]
    return render_list("Yield Forecasts", "yield", columns, data, stats=_agri_stats(conn),
                       subtitle="Latest forecast for each field.", empty="No yield forecasts yet.")


# --------------------------------------------------------------------------
# Sensors
# --------------------------------------------------------------------------
@admin_bp.route("/admin/sensors")
@auth.admin_required
@db_view
def sensors(conn):
    tab = request.args.get("tab", "status")
    s = ad.sensor_stats(conn)
    stats = [stat("Registered sensors", s["total"], "fa-microchip"),
             stat("Active sensors", s["active"], "fa-signal", "success", hint=f"reading within {ad.SENSOR_ACTIVE_MINUTES} min"),
             stat("Stale sensors", s["stale"], "fa-hourglass-half", "warning"),
             stat("Inactive / faulty", s["inactive"], "fa-plug-circle-xmark", "danger"),
             stat("Avg quality score", s["avg_quality"], "fa-star-half-stroke", "primary", "f2"),
             stat("Readings (24 h)", s["readings_24h"], "fa-database", "info"),
             stat("Validation failure rate", s["failure_rate"], "fa-circle-exclamation", "danger", "pct1"),
             stat("Pipeline log entries", s["pipeline_logs"], "fa-list-check", "info")]
    tabs = [{"key": k, "label": lbl, "url": url_for("admin.sensors", tab=k), "active": k == tab}
            for k, lbl in (("status", "Sensor status"), ("quality", "Quality metrics"), ("logs", "Pipeline logs"))]
    if tab == "quality":
        data = ad.sensor_quality_page(conn, _q(), _int("page", 1))
        columns = [col("sensor_id", "Sensor"), col("total_records", "Records", "num"),
                   col("validation_failures", "Validation failures", "num"), col("duplicate_records", "Duplicates", "num"),
                   col("outlier_corrections", "Outliers corrected", "num"), col("failure_rate", "Failure rate", "pct1"),
                   col("updated_at", "Updated", "dt")]
    elif tab == "logs":
        data = ad.sensor_logs_page(conn, _q(), _int("page", 1))
        columns = [col("created_at", "Logged (UTC)", "dt"), col("sensor_id", "Sensor"), col("source", "Source"),
                   col("location", "Location"), col("quality_label", "Quality", "status"),
                   col("validation_score", "Score", "f2"), col("severity", "Decision", "sev"),
                   col("quality_flags", "Flags")]
    else:
        tab = "status"
        data = ad.sensors_status_page(conn, _q(), _int("page", 1))
        columns = [col("sensor_id", "Sensor ID"), col("sensor_type", "Type"), col("field_id", "Field"),
                   col("last_reading_rel", "Last reading"), col("status", "Status", "sensor_status"),
                   col("uptime_percentage", "Uptime %", "f1"), col("quality_score_avg", "Quality", "f2"),
                   col("calibration_date", "Calibration")]
    return render_list("Sensor Monitoring", "sensors", columns, data, stats=stats, tabs=tabs,
                       subtitle="Uptime is the stored uptime_percentage value; the current pipeline does not recompute it.",
                       empty="No sensor data has been ingested yet.")


# --------------------------------------------------------------------------
# AI monitoring
# --------------------------------------------------------------------------
@admin_bp.route("/admin/ai")
@auth.admin_required
@db_view
def ai(conn):
    s = ad.ai_stats(conn)
    p, fb, ex = s["predictions"], s["feedback"], s["experiments"]
    model = None
    provider = current_app.config.get("MODEL_STATUS_PROVIDER")
    if callable(provider):
        try:
            model = provider()
        except Exception:
            logger.exception("model status provider failed")
    stats = [stat("Predictions logged", p["total"], "fa-brain"),
             stat("Evaluated", p["evaluated"], "fa-circle-check", "success"),
             stat("Pending", p["pending"], "fa-hourglass-half", "warning"),
             stat("Avg absolute error (°C)", p["avg_abs_error"], "fa-bullseye", "primary", "f2"),
             stat("Max absolute error (°C)", p["max_abs_error"], "fa-arrow-up-right-dots", "danger", "f2"),
             stat("Feedback: correct", fb["correct"], "fa-thumbs-up", "success", hint=fb["provenance"]),
             stat("Feedback: incorrect", fb["incorrect"], "fa-thumbs-down", "danger", hint=fb["provenance"]),
             stat("Agri prediction accuracy", None if fb["accuracy"] is None else fb["accuracy"] * 100,
                  "fa-percent", "info", "pct1", fb["provenance"])]
    data = ad.predictions_page(conn, request.args.get("status", ""), _int("page", 1))
    columns = [col("updated_at", "Updated (UTC)", "dt"), col("location", "Location"), col("sensor_id", "Sensor"),
               col("predicted_temperature", "Predicted °C", "f1"), col("actual_temperature", "Actual °C", "f1"),
               col("error_abs", "Abs error", "f2"), col("target_timestamp", "Target time", "dt"),
               col("status", "Status", "status")]
    extra = [
        {"title": "Accuracy by location", "columns": [col("location", "Location"), col("total", "Predictions", "num"),
                                                       col("evaluated", "Evaluated", "num"),
                                                       col("avg_abs_error", "Avg abs error (°C)", "f2")],
         "rows": ad.prediction_by_location(conn)},
        {"title": "Recent experiments", "columns": [col("experiment_name", "Experiment"), col("model_type", "Model"),
                                                     col("status", "Status", "status"), col("start_time", "Started", "dt")],
         "rows": ad.recent_experiments(conn)},
    ]
    if model:
        extra.insert(0, {"title": "Live model", "columns": [col("k", "Property"), col("v", "Value")],
                         "rows": [{"k": k, "v": v} for k, v in model.items()]})
    filters = [{"name": "status", "label": "Status", "type": "select",
                "options": [("", "All"), ("pending", "Pending"), ("evaluated", "Evaluated")]}]
    return render_list("AI Monitoring — Prediction Quality", "ai", columns, data, stats=stats, filters=filters, extra=extra,
                       subtitle=f"Experiments: {ex['experiments']} · Model versions: {ex['model_versions']} "
                                f"(production: {ex['production_models']}). Metrics show N/A until real data exists.",
                       empty="No predictions have been logged yet.", search=False)


# --------------------------------------------------------------------------
# Analytics / settings
# --------------------------------------------------------------------------
@admin_bp.route("/admin/analytics")
@auth.admin_required
@db_view
def analytics(conn):
    days = ad.clamp_days(request.args.get("days", 30))
    return render_template("admin/analytics.html", active="analytics", title="Analytics", days=days,
                           series=ad.all_analytics(conn, days))


@admin_bp.route("/admin/settings")
@auth.admin_required
@db_view
def settings(conn):
    ok, msg = db.ping()
    facts = ad.db_facts(conn) if ok else {}
    return render_template(
        "admin/settings.html", active="settings", title="Settings", db_ok=ok, db_msg=msg, facts=facts,
        supabase=sb.public_status(), admin_email=sb.admin_email(), audit=ad.recent_audit(conn),
        config={"Active window (minutes)": ad.ACTIVE_MINUTES, "Recently-active window (minutes)": ad.RECENT_MINUTES,
                "Sensor active window (minutes)": ad.SENSOR_ACTIVE_MINUTES,
                "Heartbeat interval (seconds)": auth.HEARTBEAT_SECONDS,
                "Admin session (hours)": auth.ADMIN_SESSION_HOURS, "Admin idle timeout (minutes)": auth.ADMIN_IDLE_MINUTES,
                "Public sign-up": "enabled" if auth.ALLOW_SIGNUP else "disabled"})


# --------------------------------------------------------------------------
# JSON API  (/api/admin/*)  -- all protected
# --------------------------------------------------------------------------
@admin_bp.route("/api/admin/stats")
@auth.admin_required
@api_view
def api_stats(conn):
    data = ad.overview_stats(conn)
    data["presence"] = ad.presence_summary(conn)
    return data


@admin_bp.route("/api/admin/users")
@auth.admin_required
@api_view
def api_users(conn):
    return ad.users_list(conn, _q(), request.args.get("role", ""), request.args.get("status", ""),
                         request.args.get("sort", "registered"), request.args.get("dir", "desc"),
                         _int("page", 1), _int("per_page", 20, 1, 100))


@admin_bp.route("/api/admin/active-users")
@auth.admin_required
@api_view
def api_active_users(conn):
    return {"summary": ad.presence_summary(conn), "groups": ad.presence_groups(conn, 25)}


@admin_bp.route("/api/admin/activity")
@auth.admin_required
@api_view
def api_activity(conn):
    return ad.recent_activity(conn, _int("limit", 10, 1, 50))


@admin_bp.route("/api/admin/weather-stats")
@auth.admin_required
@api_view
def api_weather_stats(conn):
    return {"overview": ad.weather_overview(conn), "breakdowns": ad.weather_breakdowns(conn),
            "trend": ad.weather_trend(conn, _int("days", 14, 7, 90))}


@admin_bp.route("/api/admin/alert-stats")
@auth.admin_required
@api_view
def api_alert_stats(conn):
    return {k: ad.alert_stats(conn, k) for k in ad.ALERT_KINDS}


@admin_bp.route("/api/admin/agriculture-stats")
@auth.admin_required
@api_view
def api_agri_stats(conn):
    return {"overview": ad.agri_stats(conn), "crops": ad.crops_summary(conn)}


@admin_bp.route("/api/admin/sensor-stats")
@auth.admin_required
@api_view
def api_sensor_stats(conn):
    return ad.sensor_stats(conn)


@admin_bp.route("/api/admin/ai-stats")
@auth.admin_required
@api_view
def api_ai_stats(conn):
    data = ad.ai_stats(conn)
    data["by_location"] = ad.prediction_by_location(conn)
    return data


@admin_bp.route("/api/admin/analytics")
@auth.admin_required
@api_view
def api_analytics(conn):
    return ad.all_analytics(conn, request.args.get("days", 30))


# --- actions (POST + CSRF) --------------------------------------------------
@admin_bp.route("/api/admin/users/<user_id>/toggle", methods=["POST"])
@auth.admin_required
@auth.csrf_protect
@api_view
def api_user_toggle(conn, user_id):
    uid = _valid_uuid(user_id)
    if not uid:
        raise ad.ActionError("Invalid user id.", 400)
    target = ad.get_user_for_action(conn, uid)
    ad.check_can_modify_user(target, g.admin["id"])
    new_state = not target["is_active"]
    ad.set_user_active(conn, uid, new_state)
    audit(conn, "user_activated" if new_state else "user_deactivated", "user", uid, {"username": target["username"]})
    conn.commit()
    warning = None
    if target["auth_user_id"] and sb.service_key():          # also block sign-in at the Auth provider
        try:
            sb.admin_set_ban(target["auth_user_id"], banned=not new_state)
        except sb.SupabaseError:
            logger.exception("could not update Auth ban state")
            warning = "Saved, but the Supabase Auth ban flag could not be updated."
    return {"is_active": new_state, "warning": warning}


@admin_bp.route("/api/admin/users/<user_id>/delete", methods=["POST"])
@auth.admin_required
@auth.csrf_protect
@api_view
def api_user_delete(conn, user_id):
    uid = _valid_uuid(user_id)
    if not uid:
        raise ad.ActionError("Invalid user id.", 400)
    target = ad.get_user_for_action(conn, uid)
    ad.check_can_modify_user(target, g.admin["id"])
    if target["is_active"]:
        raise ad.ActionError("Deactivate the user before deleting them.", 400)
    if target["auth_user_id"]:
        if not sb.service_key():
            raise ad.ActionError("SUPABASE_SERVICE_ROLE_KEY is not configured, so the login account cannot be removed.", 503)
        try:
            sb.admin_delete_user(target["auth_user_id"])
        except sb.SupabaseError:
            logger.exception("Auth user delete failed")
            raise ad.ActionError("The login account could not be deleted. Nothing was changed.", 502)
    audit(conn, "user_deleted", "user", uid, {"username": target["username"]})
    ad.delete_user_row(conn, uid)                         # no-op if the Auth cascade already removed it
    conn.commit()
    return {"deleted": True}


@admin_bp.route("/api/admin/alerts/<kind>/<int:alert_id>/toggle", methods=["POST"])
@auth.admin_required
@auth.csrf_protect
@api_view
def api_alert_toggle(conn, kind, alert_id):
    if kind not in ad.ALERT_KINDS:
        raise ad.ActionError("Unknown alert type.", 404)
    state = ad.toggle_alert(conn, kind, alert_id)
    audit(conn, "alert_activated" if state else "alert_deactivated", f"{kind}_alert", alert_id)
    conn.commit()
    return {"is_active": state}


@admin_bp.route("/api/admin/alerts/<kind>/<int:alert_id>/delete", methods=["POST"])
@auth.admin_required
@auth.csrf_protect
@api_view
def api_alert_delete(conn, kind, alert_id):
    if kind not in ad.ALERT_KINDS:
        raise ad.ActionError("Unknown alert type.", 404)
    ad.delete_alert(conn, kind, alert_id)
    audit(conn, "alert_deleted", f"{kind}_alert", alert_id)
    conn.commit()
    return {"deleted": True}


# --------------------------------------------------------------------------
# Template filters
# --------------------------------------------------------------------------
@admin_bp.app_template_filter("na")
def _na(value, kind="num"):
    """Format a stat value; missing data is shown as N/A, never as an invented number."""
    if value is None:
        return "N/A"
    try:
        if kind == "f1":
            return f"{float(value):,.1f}"
        if kind == "f2":
            return f"{float(value):,.2f}"
        if kind == "pct1":
            return f"{float(value):.1f}%"
        if kind == "frac_pct":
            return f"{float(value) * 100:.0f}%"
        if kind == "num":
            return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)
    return str(value)
