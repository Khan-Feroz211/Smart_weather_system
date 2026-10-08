/**
 * KisanAI — Home screen feature (F1).
 *
 * Implements the offline-first Home screen:
 *   - rainConfidence()      — rain probability + confidence from a forecast day
 *   - rainConfidenceForDays() — project a forecast array into the 5-day strip
 *                               (used by the Home strip AND the F6 rain feedback)
 *   - localSkill()          — "local skill": agreement between the edge rule-based
 *                             diagnosis and the calibrated model confidence
 *   - mapConnectivity()     — ONLINE/CACHE/OFFLINE badge + penalty from a backend
 *                             response (shared with the connectivity badge)
 *   - formatDataAge()       — human-readable staleness label (EN+UR)
 *   - buildDecisionCard()   — the "Today's action" card text
 *
 * These are PURE functions (no Capacitor / no network) so they can be unit-tested
 * with plain Node in scripts/tests.mjs.  rainConfidence()/localSkill() are
 * reconstructed from the verified backend contracts (api_routes.py,
 * recommendation_engine.py, multi_hazard.py) because the `kisanai.html` prototype
 * is not present on disk — see kisanai/TODO.md lines 9.
 *
 * WMO weather-code reference (used by the backend `recommendation_engine.fetch_live_telemetry`):
 *   0-3 clear, 45-48 fog, 51-57 drizzle, 61-67 rain, 71-77 snow,
 *   80-82 rain showers, 95-99 thunderstorm.
 */
import CONFIG from '../../config.js';
import { T } from '../../i18n.js';

/* ======================================================================
 * Wind / WMO helpers (mirrors recommendation_engine.fetch_live_telemetry)
 * ==================================================================== */
const WMO_RAIN_CODES = [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99];
function isRainCode(code) {
  return typeof code === 'number' && WMO_RAIN_CODES.includes(code);
}

/* ======================================================================
 * rainConfidence(day)
 *
 * Reconstructed from backend contracts.  Input `day` is a normalised
 * forecast record (produced by api.fetchForecast() / the Open-Meteo
 * climate dataset) with optional fields:
 *   { date, pop(0-100), precipitation_sum(mm), high, low, weather_code, wind_speed }
 *
 * Returns:
 *   { confidence(0-1), label('high'|'moderate'|'low'|'none'),
 *     rainExpected(bool), amount_mm, explanation(en) }
 * ==================================================================== */
export function rainConfidence(day) {
  if (!day || typeof day !== 'object') {
    return { confidence: 0.1, label: 'none', rainExpected: false, amount_mm: 0, explanation: 'No forecast data.' };
  }
  const pop = Number(day.pop) || 0;            // precipitation probability, 0-100
  const amount = Number(day.precipitation_sum) || Number(day.rainfall) || 0; // mm
  const code = Number(day.weather_code);

  // WMO rain codes override a missing pop to "low" at minimum.
  const rainCode = isRainCode(code);
  let confidence = 0;
  let rainExpected = false;

  if (pop >= 70 || (rainCode && amount > 0)) {
    confidence = pop >= 70 ? 0.9 : 0.7;
    rainExpected = true;
  } else if (pop >= 40) {
    confidence = 0.6;
    rainExpected = amount > 0;
  } else if (pop >= 20 || rainCode) {
    confidence = 0.3;
    rainExpected = !!rainCode || amount > 0;
  } else {
    confidence = 0.1;
    rainExpected = false;
  }

  // The backend scales instantaneous precipitation by ~10x to approximate
  // hourly mm→period accumulation when only `precipitation` is present.
  const amount_mm = amount > 0
    ? Math.round(amount * 10) / 10
    : (rainExpected ? Math.round(pop * 0.8 * 10) / 10 : 0);

  let label = 'none';
  if (confidence >= 0.7) label = 'high';
  else if (confidence >= 0.5) label = 'moderate';
  else if (confidence >= 0.25) label = 'low';

  const explanation = rainExpected
    ? T('home.rain_expected')
    : (label === 'low' ? 'Possible showers — watch the sky.' : T('home.no_rain'));

  return { confidence, label, rainExpected, amount_mm, explanation };
}

/* ======================================================================
 * rainConfidenceForDays(forecast)
 *
 * `forecast` = array of normalised day records.  Returns:
 *   { today: rainConfidence(...), days: [{...day, rainConfidence:...}] }
 * "today" = the first entry, or today's date if a matching date exists.
 * ==================================================================== */
export function rainConfidenceForDays(forecast) {
  const days = Array.isArray(forecast) ? forecast : [];
  const withConf = days.map((d) => ({ ...d, rainConfidence: rainConfidence(d) }));
  // `today` is the confidence object for the first day (handles empty forecast).
  const today = rainConfidence(days[0]);
  return { today, days: withConf };
}
// helper accessible for tests
rainConfidenceForDays._emptyDay = () => ({ confidence: 0.1, label: 'none', rainExpected: false, amount_mm: 0, explanation: 'No forecast data.' });

/* ======================================================================
 * localSkill(analyzeResult)
 *
 * "Local skill" = how trustworthy the on-device / edge (rule-based) signal
 * is relative to the calibrated model confidence.  Reconstructed from the
 * analyze() contract which returns:
 *   diagnoses[].{ confidence_calibrated, confidence_raw, is_ambiguous,
 *                  uncertainty_entropy:{is_ambiguous,...}, urgency, disease, source }
 *   connectivity_state (effective), status_header
 *
 * Returns: { score(0-1), label('skilled'|'review'|'low'|'offline'),
 *            agreement(bool), explanation(en) }
 * ==================================================================== */
export function localSkill(analyzeResult) {
  if (!analyzeResult || !Array.isArray(analyzeResult.diagnoses) || analyzeResult.diagnoses.length === 0) {
    return { score: 0, label: 'low', agreement: false, explanation: 'No diagnosis to score.' };
  }
  const top = analyzeResult.diagnoses[0];
  const cal = Number(top.confidence_calibrated);
  const raw = Number(top.confidence_raw);
  const ambiguous = !!(top.is_ambiguous || (top.uncertainty_entropy && top.uncertainty_entropy.is_ambiguous));
  const offline = String(analyzeResult.connectivity_state || '').toUpperCase() === 'OFFLINE';

  // Agreement: when calibration keeps confidence close to raw, the model is
  // well-calibrated locally.  A big drop => overconfident raw score.
  let agreement = false;
  if (raw > 0) {
    agreement = Math.abs(cal - raw) <= 0.15;
  } else {
    // rule-based path has no separate raw confidence
    agreement = cal >= 0.5;
  }

  let score = cal;
  let label = 'review';
  if (offline) {
    label = 'offline';
    // Rule-based path is the only signal; confidence is the raw rule match.
    score = Math.max(0.2, cal);
  } else if (ambiguous) {
    label = 'review';
    score = Math.min(score, 0.5);
  } else if (cal >= 0.7) {
    label = 'skilled';
    score = cal;
  } else if (cal >= 0.4) {
    label = 'review';
    score = cal;
  } else {
    label = 'low';
    score = cal;
  }

  let explanation;
  if (offline) {
    explanation = 'Offline — using cached rule-based diagnosis.';
  } else if (label === 'skilled' && agreement) {
    explanation = 'Local rules agree with the model.';
  } else if (ambiguous) {
    explanation = 'Diagnosis is ambiguous — confirm with a photo.';
  } else {
    explanation = 'Low confidence — gather more symptom evidence.';
  }

  return { score: Math.round(score * 100) / 100, label, agreement, explanation };
}

/* ======================================================================
 * Connectivity badge mapping  (ONLINE / CACHE / OFFLINE + penalty)
 *
 * Accepts a *normalised* triple.  The api.js client produces this triple from
 * either an /api/agri/analyze response (connectivity_state + status_header)
 * or an /api/hazards/predict response (degradation_info.mode + query param).
 *
 * Returns: { state, penalty(0-1), penaltyPct, labelKey, subtitleKey,
 *            dataAgeHours, isStale, label(EN), subtitle(EN) }
 * ==================================================================== */
export function mapConnectivity(connectivity_state, status_header, data_age_hours) {
  let state = (connectivity_state || 'ONLINE').toUpperCase();
  const header = status_header || '';
  let age = Number(data_age_hours);
  if (!isFinite(age) || age < 0) age = 0;

  // The analyze() status_header is the authoritative signal.
  if (/OFFLINE/.test(header)) {
    state = 'OFFLINE';
    if (!age) age = 0;
  } else if (/CACHE/.test(header)) {
    state = 'CACHE';
    const m = header.match(/([\d.]+)\s*h\s*stale/i);
    if (m) age = parseFloat(m[1]);
  } else if (state === 'CACHE' || state === 'OFFLINE') {
    // state came from a normalised source
  } else {
    state = 'ONLINE';
  }

  let penalty = 0;
  let penaltyReason = '';
  if (state === 'OFFLINE') {
    penalty = 1.0;
    penaltyReason = 'System is OFFLINE; weather and satellite telemetry withheld.';
  } else if (state === 'CACHE') {
    // 0.05 per hour stale, capped at 0.9 — mirrors integration_bridge.py
    penalty = Math.min(0.9, age * 0.05);
    penaltyReason = `Stale data (${age.toFixed(1)}h old): -${(penalty * 100).toFixed(1)}% confidence penalty`;
  }

  const isStale = state === 'CACHE' && age > 1;
  const label = T(state === 'OFFLINE' ? 'conn.offline' : state === 'CACHE' ? 'conn.cache' : 'conn.online');
  const subtitle = state === 'OFFLINE'
    ? T('conn.offline.subtitle')
    : state === 'CACHE'
      ? T('conn.cache.subtitle', { days: Math.round(age) })
      : T('conn.online.subtitle');

  return {
    state,
    penalty,
    penaltyPct: Math.round(penalty * 100),
    penaltyReason,
    dataAgeHours: age,
    isStale,
    label,
    subtitle,
    labelKey: state === 'OFFLINE' ? 'conn.offline' : state === 'CACHE' ? 'conn.cache' : 'conn.online',
    subtitleKey: state === 'OFFLINE' ? 'conn.offline.subtitle' : state === 'CACHE' ? 'conn.cache.subtitle' : 'conn.online.subtitle',
  };
}

/* ======================================================================
 * formatDataAge(hours) — relative staleness label (EN+UR via T()).
 * ==================================================================== */
export function formatDataAge(hours) {
  const h = Number(hours);
  if (!isFinite(h) || h < 0) return T('offline.sw_cache');
  if (h < 1) return T('offline.hours', { n: '<1' });
  if (h < 48) return T('offline.hours', { n: Math.round(h) });
  return T('offline.days', { n: Math.round(h / 24) });
}

/* ======================================================================
 * buildDecisionCard(rain, site, skill)
 *
 * `rain` = rainConfidence(day) output.  `site` = today's weather record.
 * Returns { title, body, action, urgency } for the Home decision card.
 * ==================================================================== */
export function buildDecisionCard(rain, site, skill) {
  const s = site || {};
  const temp = Number(s.temp) || Number(s.temperature) || 0;
  if (rain.rainExpected) {
    return {
      title: T('home.rain_expected'),
      body: 'Rain is forecast — hold irrigation and check crop stress.',
      action: T('home.check_crops'),
      urgency: 'medium',
    };
  }
  if (rain.confidence >= 0.5) {
    return {
      title: T('home.rain_expected'),
      body: 'Possible showers later — delay irrigation until tomorrow.',
      action: T('home.irrigate_now'),
      urgency: 'low',
    };
  }
  // No rain expected
  const soil = (s.soil_moisture_index != null ? Number(s.soil_moisture_index) : 0.5);
  if (soil < 0.3) {
    return {
      title: T('home.no_rain'),
      body: `No rain expected and soil moisture is low (${Math.round(soil * 100)}%). Irrigate now.`,
      action: T('home.irrigate_now'),
      urgency: 'high',
    };
  }
  return {
    title: T('home.no_rain'),
    body: 'No rain expected — no irrigation needed. Keep monitoring.',
    action: T('home.check_crops'),
    urgency: 'low',
  };
}

/* ======================================================================
 * Open-Meteo dataset → normalised forecast day (pure).
 * Handles the climate daily shape returned by /api/agri/openmeteo.
 * ==================================================================== */
export function normalizeOmDay(date, temp_max, precip_sum, pop, wind, code) {
  return {
    date: date || '',
    high: temp_max == null ? undefined : Number(temp_max),
    low: temp_max == null ? undefined : Number(temp_max) - 6, // approx diurnal range
    precipitation_sum: precip_sum == null ? 0 : Number(precip_sum),
    pop: pop == null ? 0 : Number(pop),
    wind_speed: wind == null ? undefined : Number(wind),
    weather_code: code == null ? undefined : Number(code),
  };
}

export default { rainConfidence, rainConfidenceForDays, localSkill, mapConnectivity, formatDataAge, buildDecisionCard, normalizeOmDay };
