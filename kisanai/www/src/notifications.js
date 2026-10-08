/**
 * KisanAI — Local notifications (src/notifications.js).
 *
 * F7: Alerts.
 *   - scheduleDailyPrompt() — a daily "tap for today's hazard alert" reminder.
 *   - scheduleHazardAlert(alert) — turn a /api/hazards/predict alert into a
 *     local notification, deduplicated by (location+date) with severity
 *     colours green/yellow/orange/red.
 *   - SEVERITY_COLORS maps the NWS/INFORM overall_risk_level (from
 *     multi_hazard.py) to notification presentation.
 *
 * Dedup: a rolling 24h window per (location, hazard) key prevents the daily
 * prompt from spamming the same alert.  The schedule is kept in localStorage
 * so it survives app restarts even when the native scheduler is unavailable.
 *
 * Import-safe in Node: LocalNotifications is lazy-imported behind isNative();
 * only the pure schedulers run in tests.
 */
import { getLocalNotifications } from './caps.js';

/** NWS/INFORM risk colour → notification presentation. */
export const SEVERITY_COLORS = {
  green: { color: '#00E450', label: 'Green', icon: '✅', urgent: false },
  yellow: { color: '#FFCC00', label: 'Yellow', icon: '⚠️', urgent: true },
  orange: { color: '#FF6600', label: 'Orange', icon: '🔥', urgent: true },
  red: { color: '#CC0000', label: 'Red', icon: '🚨', urgent: true },
};

export function severityColor(level) {
  return SEVERITY_COLORS[String(level || 'green').toLowerCase()] || SEVERITY_COLORS.green;
}

const NOTIF_QUEUE_KEY = 'kisanai_notif_queue';
const DAILY_PROMPT_KEY = 'kisanai_daily_prompt_due';

/** Local-only dedup log keyed by `${location}:${date}`. */
function readQueue() {
  try { return JSON.parse(localStorage.getItem(NOTIF_QUEUE_KEY) || '[]'); } catch (e) { return []; }
}
function writeQueue(q) {
  try { localStorage.setItem(NOTIF_QUEUE_KEY, JSON.stringify(q)); } catch (e) { /* ignore */ }
}

/** True if (location, date, hazard) was already notified in the rolling window. */
export function isDuplicate(location, date, hazard, windowHours = 24) {
  const key = `${(location || '')}:${(date || '')}:${(hazard || '')}`;
  const now = Date.now();
  const q = readQueue();
  const fresh = q.filter((e) => now - e.ts < windowHours * 3600 * 1000);
  const hit = fresh.some((e) => e.key === key);
  if (!hit) fresh.push({ key, ts: now });
  writeQueue(fresh);
  return hit;
}

/** Dedup the alert-level notification (all hazards for one location on one day). */
export function isAlertDuplicate(location, date, windowHours = 24) {
  const key = `alert:${(location || '')}:${(date || '')}`;
  const now = Date.now();
  const q = readQueue();
  const fresh = q.filter((e) => now - e.ts < windowHours * 3600 * 1000);
  const hit = fresh.some((e) => e.key === key);
  if (!hit) fresh.push({ key, ts: now });
  writeQueue(fresh);
  return hit;
}

/**
 * Convert a /api/hazards/predict `alert` (or /api/hazards/alerts[]) into a
 * notification payload ready for LocalNotifications.schedule.
 */
export function buildNotificationFromAlert(alert, location, opts = {}) {
  const level = alert.overall_risk_level || 'green';
  const sev = severityColor(level);
  const hazardList = Array.isArray(alert.active_hazards) ? alert.active_hazards : [];
  const names = hazardList.map((h) => (typeof h === 'object' ? h.name : h)).filter(Boolean);
  const date = opts.date || new Date().toISOString().slice(0, 10);

  const title = level === 'green'
    ? `${sev.icon} KisanAI: All clear for ${location || 'your area'}`
    : `${sev.icon} ${sev.label} alert for ${location || 'your area'}`;
  const body = level === 'green'
    ? 'No active hazards today. Safe for field work.'
    : (`${names.length ? names.join(', ') : 'Hazard'} detected`
       + (alert.confidence ? ` (confidence ${(Number(alert.confidence) * 100).toFixed(0)}%)` : '')
       + `. Tap for recommended actions.`).slice(0, 200);

  return {
    title,
    body,
    urgency: sev.urgent ? 'high' : 'default',
    color: sev.color,
    icon: sev.icon,
    level,
    date,
    location,
    recommended_actions: alert.recommended_actions || {},
    sound: sev.urgent ? 'alert.wav' : 'default',
  };
}

/** Should we show the daily prompt today?  (reschedules itself) */
export function shouldShowDailyPrompt(today = new Date()) {
  const key = today.toISOString().slice(0, 10);
  const last = typeof localStorage !== 'undefined' ? localStorage.getItem(DAILY_PROMPT_KEY) : null;
  return last !== key;
}
export function markDailyPromptShown(today = new Date()) {
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(DAILY_PROMPT_KEY, today.toISOString().slice(0, 10)); } catch (e) {}
}

/**
 * Schedule a local notification.  Bridges the Capacitor LocalNotifications
 * plugin when native, otherwise no-ops (the prompt is replayed via the
 * Service Worker / daily check).
 */
export async function scheduleNotification(payload, whenSeconds = 0) {
  const LocalNotifications = await getLocalNotifications();
  if (!LocalNotifications) {
    // Web fallback: persist to a pending list the SW / home view can render.
    return { scheduled: false, pending: true, payload };
  }
  const id = Math.abs(hashCode(payload.title + payload.date)).toString();
  try {
    const scheduled = await LocalNotifications.schedule({
      notifications: [{
        title: payload.title,
        body: payload.body,
        id: parseInt(id, 10) || 0,
        schedule: { at: new Date(Date.now() + whenSeconds * 1000) },
        sound: payload.sound,
        actionTypeId: payload.level,
      }],
    });
    // Android notification colour (requires hex without # on some OEM skins).
    try { await LocalNotifications.setColor({ color: payload.color }); } catch (e) { /* ignore */ }
    return { scheduled: true, id, pending: false, scheduled_ids: scheduled?.notifications?.map((n) => n.id) || [] };
  } catch (e) {
    return { scheduled: false, pending: true, error: e.message, payload };
  }
}

function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) { h = ((h << 5) - h) + str.charCodeAt(i); h |= 0; }
  return h;
}

/** Convenience: schedule a hazard alert, honours dedup + severity colour. */
export async function scheduleHazardAlert(alert, location, opts = {}) {
  const date = opts.date || new Date().toISOString().slice(0, 10);
  if (isAlertDuplicate(location, date)) {
    return { scheduled: false, duplicate: true, message: 'Duplicate alert suppressed (24h window).' };
  }
  if (alert.overall_risk_level === 'green') {
    // Only notify for green if explicitly requested (quiet default).
    if (!opts.notifyGreen) return { scheduled: false, duplicate: false, message: 'Green — no notification.' };
  }
  const payload = buildNotificationFromAlert(alert, location, { date });
  return scheduleNotification(payload, 0);
}

export async function scheduleDailyPrompt() {
  if (!shouldShowDailyPrompt()) return { scheduled: false, message: 'Already shown today.' };
  const payload = {
    title: '🌾 KisanAI',
    body: 'Tap for today’s hazard alert and field advice.',
    urgency: 'default',
    color: '#2E7D32',
    icon: '☀️',
    level: 'green',
    date: new Date().toISOString().slice(0, 10),
    location: '',
  };
  markDailyPromptShown();
  return scheduleNotification(payload, 10 * 60); // ~10 min from now
}

export default { SEVERITY_COLORS, severityColor, buildNotificationFromAlert, scheduleNotification, scheduleHazardAlert, scheduleDailyPrompt, isDuplicate, shouldShowDailyPrompt };
