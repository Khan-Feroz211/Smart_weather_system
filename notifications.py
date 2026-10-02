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
    LOCAL_SMS_GATEWAY_URL – Optional local HTTP gateway for SMS fallback
                             (e.g. a GSM-modem endpoint).  When Twilio is not
                             configured and this is unset, messages are logged.
"""

from __future__ import annotations

import logging
import os
import json
from datetime import datetime
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
LOCAL_SMS_GATEWAY_URL = os.environ.get("LOCAL_SMS_GATEWAY_URL", "").strip()
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


# ---------------------------------------------------------------------------
# Local SMS gateway fallback
# ---------------------------------------------------------------------------
def send_local_sms(to_phone: str, message: str) -> Dict[str, Any]:
    """Send an SMS via a *local* gateway (e.g. a GSM-modem HTTP endpoint).

    If ``LOCAL_SMS_GATEWAY_URL`` is set, a POST request is made to that URL
    with ``{"to": ..., "message": ...}``.  When no local gateway is configured
    the message is logged so the rest of the application keeps working.
    """
    if not NOTIFICATION_ENABLED:
        return {"success": False, "error": "Notifications disabled via NOTIFICATION_ENABLED=false"}

    if not _is_valid_phone(to_phone):
        return {"success": False, "error": f"Invalid phone number: {to_phone}"}

    if LOCAL_SMS_GATEWAY_URL:
        try:
            resp = requests.post(
                LOCAL_SMS_GATEWAY_URL,
                json={"to": to_phone, "message": message},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("[LOCAL SMS] Sent to %s via %s", to_phone, LOCAL_SMS_GATEWAY_URL)
                return {"success": True, "message_sid": "local_sms", "error": None,
                        "channel": "local_sms"}
            logger.error("Local SMS gateway returned %s: %s", resp.status_code, resp.text[:200])
            return {"success": False, "error": f"Local SMS gateway HTTP {resp.status_code}",
                    "channel": "local_sms"}
        except Exception as exc:
            logger.error("Local SMS gateway error: %s", exc)
            return {"success": False, "error": str(exc), "channel": "local_sms"}

    # Graceful fallback: log the message
    logger.info("[LOCAL SMS FALLBACK] To: %s | %s", to_phone, message)
    return {
        "success": True,
        "message_sid": "local_sms_log",
        "error": None,
        "channel": "local_sms",
        "note": "No local SMS gateway configured – message logged only",
    }


def send_notification_auto(user_phone: str, message: str) -> Dict[str, Any]:
    """Send a notification, trying **WhatsApp first** then falling back to SMS.

    This implements the "send on WhatsApp if the number is on WhatsApp,
    otherwise on local SMS" requirement:

    1. When Twilio is configured, a WhatsApp attempt is made.  If the
       number cannot receive WhatsApp (Twilio returns an error) we fall
       back to Twilio SMS.
    2. When Twilio is **not** configured we go straight to the local SMS
       gateway (or log-only fallback).

    Returns a dict with ``success``, ``channel`` ("whatsapp" | "sms"),
    ``message_sid`` and ``error``.
    """
    if not NOTIFICATION_ENABLED:
        return {"success": False, "error": "Notifications disabled via NOTIFICATION_ENABLED=false"}

    if not _is_valid_phone(user_phone):
        return {"success": False, "error": f"Invalid phone number: {user_phone}"}

    client = _get_twilio_client()

    # ---- Twilio is available → try WhatsApp, then SMS ----
    if client is not None:
        wa_result = send_whatsapp(user_phone, message)
        if wa_result.get("success") and wa_result.get("message_sid") not in ("whatsapp_log_only", None):
            return {
                "success": True,
                "channel": "whatsapp",
                "message_sid": wa_result.get("message_sid"),
                "error": None,
            }
        # WhatsApp failed (number not on WhatsApp or other error) → fall back to SMS
        sms_result = send_sms(user_phone, message)
        return {
            "success": sms_result.get("success", False),
            "channel": "sms",
            "message_sid": sms_result.get("message_sid"),
            "error": sms_result.get("error"),
        }

    # ---- Twilio not configured → local SMS gateway or log ----
    sms_result = send_local_sms(user_phone, message)
    return {
        "success": sms_result.get("success", False),
        "channel": "local_sms",
        "message_sid": sms_result.get("message_sid"),
        "error": sms_result.get("error"),
    }


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
