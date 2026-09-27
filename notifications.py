"""
notifications.py
=================
SMS and WhatsApp notification engine for the Smart Weather System.

Uses Twilio's REST API for both SMS and WhatsApp messaging.  When Twilio
credentials or the `twilio` package are missing the module gracefully
degrades to a local log so the rest of the application keeps working.

Environment variables (set in .env):
    TWILIO_ACCOUNT_SID    – Twilio account SID
    TWILIO_AUTH_TOKEN     – Twilio auth token
    TWILIO_SMS_FROM       – Twilio phone number for SMS (e.g. +14155238886)
    TWILIO_WHATSAPP_FROM  – Twilio WhatsApp sender (e.g. whatsapp:+14155238886)
    NOTIFICATION_ENABLED  – Set to "false" to disable all outgoing messages
"""

from __future__ import annotations

import logging
import os
import json
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
NOTIFICATION_ENABLED = os.environ.get("NOTIFICATION_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Message templates
ALERT_TEMPLATES: Dict[str, str] = {
    "heat_stress": "🌡️ HEAT STRESS ALERT: {field} in {farm}. Temperature rising, consider irrigation. [Smart AgriWeather]",
    "frost_risk": "❄️ FROST RISK ALERT: {field} in {farm}. Frost expected, cover crops recommended. [Smart AgriWeather]",
    "drought": "🏜️ DROUGHT ALERT: {field} in {farm}. Soil moisture low, irrigation recommended. [Smart AgriWeather]",
    "excess_moisture": "💧 EXCESS MOISTURE ALERT: {field} in {farm}. Risk of fungal growth, check drainage. [Smart AgriWeather]",
    "pest_risk": "🐛 PEST RISK ALERT: {field} in {farm}. High risk of {pest}. Inspect and treat. [Smart AgriWeather]",
    "irrigation": "🚿 IRRIGATION DUE: {field} in {farm}. {volume}mm water needed. [Smart AgriWeather]",
    "activity_reminder": "🌾 Farm Activity Reminder: Time to {activity} in {field}. [Smart AgriWeather]",
    "disease_scan": "📸 Disease Scan Completed: {field} in {farm}. Result: {result}. [Smart AgriWeather]",
    "general": "🌾 Smart AgriWeather: {message} [Smart AgriWeather]",
}


def _get_twilio_client():
    """Lazily import and return a Twilio client, or None if unavailable."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return None
    try:
        from twilio.rest import Client  # type: ignore
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except ImportError:
        logger.warning("twilio package not installed – notifications disabled")
        return None
    except Exception as exc:
        logger.warning("Failed to initialise Twilio client: %s", exc)
        return None


def _is_valid_phone(phone: str) -> bool:
    """Basic E.164 phone number validation."""
    if not phone:
        return False
    phone = phone.strip()
    # Must start with + followed by 7-15 digits
    if not phone.startswith("+"):
        return False
    digits = phone[1:].replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    return digits.isdigit() and 7 <= len(digits) <= 15


def send_sms(to_phone: str, message: str) -> Dict[str, Any]:
    """Send an SMS message via Twilio.

    Returns a dict with ``success``, ``message_sid``, ``error`` keys.
    Gracefully degrades to logging when Twilio is not configured.
    """
    if not NOTIFICATION_ENABLED:
        return {"success": False, "error": "Notifications disabled via NOTIFICATION_ENABLED=false"}

    if not _is_valid_phone(to_phone):
        return {"success": False, "error": f"Invalid phone number: {to_phone}"}

    client = _get_twilio_client()
    if client is None:
        # Graceful fallback: log the message so admins know it would have been sent
        logger.info("[SMS FALLBACK] To: %s | %s", to_phone, message)
        return {
            "success": True,
            "message_sid": "sms_log_only",
            "error": None,
            "note": "Twilio not configured – message logged only",
        }

    try:
        msg = client.messages.create(
            body=message,
            from_=TWILIO_SMS_FROM,
            to=to_phone,
        )
        logger.info("SMS sent to %s (sid=%s)", to_phone, msg.sid)
        return {"success": True, "message_sid": msg.sid, "error": None}
    except Exception as exc:
        logger.error("SMS send failed to %s: %s", to_phone, exc)
        return {"success": False, "error": str(exc)}


def send_whatsapp(to_phone: str, message: str) -> Dict[str, Any]:
    """Send a WhatsApp message via Twilio's WhatsApp sandbox / API.

    The recipient phone must be in E.164 format (e.g. +14151234567).
    """
    if not NOTIFICATION_ENABLED:
        return {"success": False, "error": "Notifications disabled via NOTIFICATION_ENABLED=false"}

    if not _is_valid_phone(to_phone):
        return {"success": False, "error": f"Invalid phone number: {to_phone}"}

    client = _get_twilio_client()
    if client is None:
        logger.info("[WHATSAPP FALLBACK] To: %s | %s", to_phone, message)
        return {
            "success": True,
            "message_sid": "whatsapp_log_only",
            "error": None,
            "note": "Twilio not configured – message logged only",
        }

    try:
        msg = client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_FROM or "whatsapp:+14155238886",
            to=f"whatsapp:{to_phone}",
        )
        logger.info("WhatsApp sent to %s (sid=%s)", to_phone, msg.sid)
        return {"success": True, "message_sid": msg.sid, "error": None}
    except Exception as exc:
        logger.error("WhatsApp send failed to %s: %s", to_phone, exc)
        return {"success": False, "error": str(exc)}


def send_farm_notification(
    user_phone: str,
    message: str,
    channel: str = "sms",
) -> Dict[str, Any]:
    """Send a farm-related notification via the requested channel.

    Args:
        user_phone: Recipient phone number in E.164 format.
        message:    The notification body text.
        channel:    "sms" or "whatsapp".
    """
    channel = (channel or "sms").strip().lower()
    if channel == "whatsapp":
        return send_whatsapp(user_phone, message)
    elif channel == "sms":
        return send_sms(user_phone, message)
    else:
        return {"success": False, "error": f"Unknown channel: {channel}"}


def render_alert_message(template_key: str, **kwargs) -> str:
    """Render an alert message from the ALERT_TEMPLATES dict."""
    template = ALERT_TEMPLATES.get(template_key, ALERT_TEMPLATES["general"])
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return ALERT_TEMPLATES["general"].format(message=template_key.upper())


def notify_farm_alert(
    user_phone: str,
    alert_type: str,
    field_name: str,
    farm_name: str,
    channel: str = "sms",
    **extra: Any,
) -> Dict[str, Any]:
    """High-level helper: format and send a farm alert notification."""
    message = render_alert_message(
        alert_type, field=field_name, farm=farm_name, **extra
    )
    return send_farm_notification(user_phone, message, channel=channel)


def get_user_notification_settings(conn, user_id: int) -> Dict[str, Any]:
    """Fetch a user's notification preferences from the database.

    Requires the ``users`` table to have ``phone_number``, ``notification_channel``,
    and ``notifications_enabled`` columns (added by the migration in app_clean.py).
    """
    try:
        row = conn.execute(
            "SELECT phone_number, notification_channel, notifications_enabled "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return {
                "phone_number": row["phone_number"] if row["phone_number"] else None,
                "notification_channel": row["notification_channel"] or "sms",
                "notifications_enabled": bool(row["notifications_enabled"]) if row["notifications_enabled"] is not None else True,
            }
    except Exception as exc:
        logger.warning("Could not fetch notification settings for user %s: %s", user_id, exc)
    return {
        "phone_number": None,
        "notification_channel": "sms",
        "notifications_enabled": True,
    }
