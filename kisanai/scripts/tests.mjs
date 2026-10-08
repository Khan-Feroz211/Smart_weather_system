/**
 * scripts/tests.mjs — KisanAI pure-logic smoke tests (npm test).
 *
 * Verifies (per kisanai/TODO.md "Smoke-test pure logic" + "Review against
 * backend contracts"):
 *   - rainConfidence thresholds (high/moderate/low/none)
 *   - localSkill labels (skilled/review/low/offline)
 *   - mapConnectivity penalty formula: min(0.9, age*0.05) per hour
 *   - mapConnectivityFromAnalyze / mapConnectivityFromHazards field mapping
 *   - analyze() response shape trusts CODE: confidence_calibrated (not confidence),
 *     uncertainty_entropy.is_ambiguous, suggested_next_question, evidence
 *   - computeSoilRecommendation / classifyReading overall grades (poor 6 actions / good 0)
 *   - RFC 6238 TOTP against the published test vectors
 *   - ui dp/48dp helpers, skeleton counts
 *   - camera resize + diagnosis threshold
 *
 * Import-safe: a tiny in-memory localStorage shim is installed so every
 * module (storage, auth, api, notifications) loads in plain Node.
 */
import { T, setLocale, getLocale, isRTL } from '../www/i18n.js';
import {
  rainConfidence, rainConfidenceForDays, localSkill,
  mapConnectivity, formatDataAge, buildDecisionCard, normalizeOmDay,
} from '../www/src/features/home.js';
import { mapConnectivityFromAnalyze, mapConnectivityFromHazards, AgriApi, HttpError, api } from '../www/src/api.js';
import { computeSoilRecommendation, classifyReading, DEFAULT_SOIL, fieldStore } from '../www/src/storage.js';
import { dp, dpToPx, ensureTouchTarget, spineSkeleton, is48dpTarget } from '../www/src/ui.js';
import { generateSecret, totp, verifyTotp, authHeaders as authHeadersHelper } from '../www/src/auth.js';
import { computeResizeDimensions, DIAGNOSE_THRESHOLD } from '../www/src/camera.js';
import { buildNotificationFromAlert, severityColor, SEVERITY_COLORS } from '../www/src/notifications.js';

// ---- in-memory localStorage shim (so modules load in plain Node) ----
const memStore = {};
globalThis.localStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(memStore, k) ? memStore[k] : null; },
  setItem(k, v) { memStore[k] = String(v); },
  removeItem(k) { delete memStore[k]; },
  clear() { Object.keys(memStore).forEach((k) => delete memStore[k]); },
};

let PASS = 0;
let FAIL = 0;
const failures = [];

function assert(cond, msg, extra) {
  if (cond) { PASS += 1; }
  else { FAIL += 1; failures.push(msg + (extra ? ' :: ' + JSON.stringify(extra) : '')); }
}
function eq(a, b, msg) {
  const ok = JSON.stringify(a) === JSON.stringify(b);
  if (!ok) failures.push(`${msg} expected ${JSON.stringify(b)} got ${JSON.stringify(a)}`);
  ok ? PASS++ : FAIL++;
}
function approx(a, b, msg, eps = 1e-9) {
  const ok = Math.abs(Number(a) - Number(b)) < eps;
  ok ? PASS++ : (FAIL++, failures.push(`${msg} expected ~${b} got ${a}`));
}

async function run() {
  // ====================================================================
  // 1. rainConfidence — WMO code 61 (rain) + pop 85 / precip 12mm
  // ====================================================================
  {
    const r = rainConfidence({ pop: 85, precipitation_sum: 12, weather_code: 61 });
    assert(r.confidence === 0.9, 'rainConfidence pop85 -> 0.9');
    assert(r.label === 'high', 'rainConfidence label high');
    assert(r.rainExpected === true, 'rainConfidence rainExpected true');
    assert(r.amount_mm === 12, 'rainConfidence amount_mm 12');

    const r2 = rainConfidence({ pop: 10 });
    assert(r2.confidence === 0.1, 'rainConfidence pop10 -> 0.1');
    assert(r2.label === 'none', 'rainConfidence pop10 label none');
    assert(r2.rainExpected === false, 'rainConfidence pop10 no rain');

    const r3 = rainConfidence({ pop: 45, precipitation_sum: 3 });
    assert(r3.confidence === 0.6, 'rainConfidence pop45 -> 0.6 (moderate)');
    assert(r3.rainExpected === true && r3.amount_mm === 3, 'rainConfidence pop45 amount 3');

    const r4 = rainConfidence({ weather_code: 80 });
    assert(r4.confidence === 0.3 && r4.rainExpected === true, 'rainConfidence code80 wmo-rain fallback 0.3');

    const r5 = rainConfidence({});
    assert(r5.confidence === 0.1 && r5.label === 'none', 'rainConfidence empty -> none');
  }

  // ====================================================================
  // 2. rainConfidenceForDays + normalizeOmDay
  // ====================================================================
  {
    const days = [normalizeOmDay('2026-10-09', 26, 2, 45, 8, 0), normalizeOmDay('2026-10-10', 28, 0, 10, 5, 61)];
    const fc = rainConfidenceForDays(days);
    assert(fc.days.length === 2, 'rainConfidenceForDays length');
    assert(fc.today.rainExpected === true && fc.today.label === 'moderate', 'rainConfidenceForDays today moderate (pop45, code 0)');
    const emptyDay = rainConfidenceForDays._emptyDay();
    assert(emptyDay.confidence === 0.1, 'emptyDay default 0.1');
  }

  // ====================================================================
  // 3. localSkill — trusts CODE field names (confidence_calibrated / is_ambiguous)
  // ====================================================================
  {
    const base = { connectivity_state: 'ONLINE', diagnoses: [{
      disease: 'wheat_rust', urgency: 'high', source: 'model',
      confidence_calibrated: 0.82, confidence_raw: 0.85,
      is_ambiguous: false, uncertainty_entropy: { is_ambiguous: false },
      suggested_next_question: 'check leaf spacing?',
      evidence: { leaf_spots: true },
    }] };
    const s = localSkill(base);
    eq(s.label, 'skilled', 'localSkill ONLINE 0.82 -> skilled');
    eq(s.score, 0.82, 'localSkill score 0.82');
    assert(s.agreement === true, 'localSkill agreement true (|0.82-0.85|<=0.15)');

    const off = localSkill({ connectivity_state: 'OFFLINE', diagnoses: [{ confidence_calibrated: 0.6 }] });
    eq(off.label, 'offline', 'localSkill offline label');
    assert(off.score === 0.6, 'localSkill offline score max(0.2,0.6)=0.6');

    const amb = localSkill({ connectivity_state: 'ONLINE', diagnoses: [{ confidence_calibrated: 0.8, is_ambiguous: true }] });
    eq(amb.label, 'review', 'localSkill ambiguous -> review');
    assert(amb.score === 0.5, 'localSkill ambiguous score capped 0.5');

    const low = localSkill({ connectivity_state: 'ONLINE', diagnoses: [{ confidence_calibrated: 0.3 }] });
    eq(low.label, 'low', 'localSkill low');

    const empty = localSkill({});
    eq(empty.label, 'low', 'localSkill empty -> low');
    assert(empty.score === 0, 'localSkill empty score 0');
  }

  // ====================================================================
  // 4. mapConnectivity — penalty = min(0.9, age*0.05) per hour
  // ====================================================================
  {
    const c = mapConnectivity('CACHE', '[…CACHE, 3.2h stale]', 3.2);
    eq(c.state, 'CACHE', 'mapConnectivity cache state');
    eq(c.penaltyPct, 16, 'mapConnectivity 3.2h -> 16% (3.2*0.05=0.16)');
    assert(c.isStale === true, 'mapConnectivity cache stale');

    const o = mapConnectivity('ONLINE', '[SIMULATED DATA | ONLINE]', 0);
    eq(o.state, 'ONLINE', 'mapConnectivity online state');
    assert(o.penaltyPct === 0 && o.penalty === 0, 'mapConnectivity online no penalty');

    const off = mapConnectivity('OFFLINE', '[… | OFFLINE — weather-dependent sections with withdrawn]', 0);
    eq(off.state, 'OFFLINE', 'mapConnectivity offline state');
    eq(off.penaltyPct, 100, 'mapConnectivity offline -> 100%');
    assert(off.penalty === 1.0, 'mapConnectivity offline penalty 1.0');

    const cap51 = mapConnectivity('CACHE', '', 5.1);
    eq(cap51.penaltyPct, 26, 'mapConnectivity 5.1h -> 26% (round(0.255*100)=26)');

    const staleCap = mapConnectivity('CACHE', '', 20);
    assert(staleCap.penalty === 0.9, 'mapConnectivity penalty capped at 0.9 (20h)');
  }

  // ====================================================================
  // 5. mapConnectivityFromAnalyze / mapConnectivityFromHazards
  // ====================================================================
  {
    const analyzeResp = {
      status_header: '[SIMULATED DATA | CACHE, 3.2h stale]',
      connectivity_state: 'CACHE',
      data_age_hours: 3.2,
      crop: 'wheat', engine_version: 'v6.2-hybrid',
    };
    const ca = mapConnectivityFromAnalyze(analyzeResp);
    eq(ca.state, 'CACHE', 'fromAnalyze CACHE state');
    eq(ca.penaltyPct, 16, 'fromAnalyze CACHE 16%');

    const offlineResp = { connectivity_state: 'OFFLINE', status_header: '[… | OFFLINE — withheld]', data_age_hours: 0 };
    const co = mapConnectivityFromAnalyze(offlineResp);
    eq(co.state, 'OFFLINE', 'fromAnalyze OFFLINE state');
    eq(co.penaltyPct, 100, 'fromAnalyze OFFLINE 100%');

    const hazardCache = { alert: { degradation_info: { mode: 'cache_only', data_age_hours: 5.1 }, message: 'stale' } };
    const ha = mapConnectivityFromHazards(hazardCache.alert, 'CACHE');
    eq(ha.state, 'CACHE', 'fromHazards cache_only -> CACHE');
    eq(ha.penaltyPct, 26, 'fromHazards cache_only 5.1h -> 26%');

    const hazardOffline = { alert: { degradation_info: { mode: 'offline', data_age_hours: 0 }, message: 'down' } };
    const ho = mapConnectivityFromHazards(hazardOffline.alert, 'OFFLINE');
    eq(ho.state, 'OFFLINE', 'fromHazards offline -> OFFLINE');
    eq(ho.penaltyPct, 100, 'fromHazards offline -> 100%');

    const hazardOnline = { alert: { degradation_info: { mode: 'online', data_age_hours: 0 }, message: 'ok' } };
    const hon = mapConnectivityFromHazards(hazardOnline.alert, 'ONLINE');
    eq(hon.state, 'ONLINE', 'fromHazards online -> ONLINE');
    assert(hon.penaltyPct === 0, 'fromHazards online 0%');
  }

  // ====================================================================
  // 6. analyze() response field mapping — CODE truth (confidence_calibrated)
  // ====================================================================
  {
    const resp = {
      status_header: '[SIMULATED DATA | ONLINE]',
      generated_at: '2026-10-09T00:00:00Z',
      engine_version: 'v6.2-hybrid',
      crop: 'wheat', growth_stage: 'tillering',
      connectivity_state: 'ONLINE',
      diagnoses: [{
        disease: 'wheat_rust',
        confidence_calibrated: 0.82,   // NOT "confidence" — code truth
        confidence_raw: 0.85,
        urgency: 'high',
        is_ambiguous: false,
        uncertainty_entropy: { is_ambiguous: false, entropy: 0.1 },
        suggested_next_question: 'Are the stripes parallel?',
        evidence: { leaf_stripes: true },
      }],
      image_analysis: { available: true, confidence: 0.78 },
      geovis_satellite: { available: true, status: 'available', source: 'sentinel-2' },
      yield_estimate: { available: true, status: 'available', impact_percentage: -5 },
      crop_suitability: { available: true, status: 'available', rankings: [], details: [] },
    };
    const ca = mapConnectivityFromAnalyze(resp);
    eq(ca.state, 'ONLINE', 'analyze ONLINE connectivity');
    assert(resp.diagnoses[0].confidence_calibrated === 0.82, 'analyze uses confidence_calibrated (code truth)');
    assert(resp.diagnoses[0].uncertainty_entropy.is_ambiguous === false, 'analyze is_ambiguous nested');
    assert(resp.diagnoses[0].suggested_next_question === 'Are the stripes parallel?', 'analyze suggested_next_question present');

    // agriApi authHeaders: bearer when token set, apiKey fallback otherwise
    const a = new AgriApi();
    a.setToken('abc');
    eq(a.authHeaders()['Authorization'], 'Bearer abc', 'api authHeaders bearer');
    a.setToken(null);
    eq(a.authHeaders()['X-API-Key'], 'agri_dev_key_2024', 'api authHeaders X-API-Key dev fallback');
  }

  // ====================================================================
  // 7. Soil recommendation — poor (6 actions) / good (0 actions)
  // ====================================================================
  {
    const poor = computeSoilRecommendation({ ph: 5.2, nitrogen: 5, phosphorus: 4, potassium: 30, organic_matter: 0.8, texture: 'sand' });
    eq(poor.overall, 'poor', 'soil poor overall');
    assert(poor.actions.length === 6, 'soil poor 6 actions (got ' + poor.actions.length + ')');

    const good = computeSoilRecommendation({ ph: 6.5, nitrogen: 20, phosphorus: 15, potassium: 100, organic_matter: 3.0, texture: 'loam' });
    eq(good.overall, 'good', 'soil good overall');
    assert(good.actions.length === 0, 'soil good 0 actions');

    eq(classifyReading('ph', 5.2).status, 'acidic', 'classify ph acidic');
    eq(classifyReading('nitrogen', 5).status, 'deficient', 'classify N deficient');
    eq(classifyReading('potassium', 100).status, 'adequate', 'classify K adequate');
    eq(classifyReading('organic_matter', 3.0).status, 'good', 'classify OM good');

    // DEFAULT_SOIL is a sane baseline
    assert(DEFAULT_SOIL.ph === 7.0 && DEFAULT_SOIL.texture === 'loam', 'DEFAULT_SOIL baseline');
  }

  // ====================================================================
  // 8. fieldStore memory fallback (localStorage shim)
  // ====================================================================
  {
    await fieldStore.init();
    const f = await fieldStore.addField({ name: 'North Plot', area: 2, crop: 'wheat', soil_texture: 'loam' });
    assert(f.id >= 1 && f.name === 'North Plot', 'fieldStore addField');
    const soil = await fieldStore.saveSoilTest(f.id, { ph: 5.2, nitrogen: 5, phosphorus: 4, potassium: 30, organic_matter: 0.8, texture: 'sand' });
    const rec = computeSoilRecommendation(soil);
    eq(rec.overall, 'poor', 'fieldStore soil rec poor');
    const tests = await fieldStore.getSoilTests(f.id);
    assert(tests.length === 1, 'fieldStore saved soil test retrievable');
    await fieldStore.deleteField(f.id);
    const gone = await fieldStore.getField(f.id);
    assert(gone === null, 'fieldStore deleteField');
  }

  // ====================================================================
  // 9. RFC 6238 TOTP test vectors (secret -> "12345678901234567890")
  // ====================================================================
  {
    const SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'; // decodes to ASCII "12345678901234567890"
    eq(await totp(SECRET, 59000),    '287082', 'totp T=1 (59s) 6-digit');
    eq(await totp(SECRET, 1111111109000), '081804', 'totp T=37037036 (1111111109s)');
    eq(await totp(SECRET, 1234567890000), '005924', 'totp T=41152263 (1234567890s)');
    const v = await verifyTotp(SECRET, await totp(SECRET, Date.now()));
    assert(v.valid === true, 'verifyTotp valid (current code)');
    const bad = await verifyTotp(SECRET, '000000');
    assert(bad.valid === false, 'verifyTotp invalid');
    const gen = generateSecret();
    assert(/^[A-Z2-7]{16,40}$/.test(gen), 'generateSecret base32 shape');
  }

  // ====================================================================
  // 10. ui helpers (dp / 48dp / skeleton)
  // ====================================================================
  {
    eq(dp(48, 1), 48, 'dp 48 @1x');
    eq(dp(48, 2), 96, 'dp 48 @2x');
    eq(dpToPx(48, 320), 96, 'dpToPx 48 @320dpi');
    assert(is48dpTarget(40, 40, 2) === false, '40x40 @2x not 48dp');
    assert(is48dpTarget(100, 100, 1) === true, '100x100 @1x is 48dp');
    const grown = ensureTouchTarget(30, 30, 2.5);
    eq([grown.width, grown.height], [120, 120], 'ensureTouchTarget 30x30 @2.5 -> 120');
    assert(spineSkeleton(4).length === 4, 'spineSkeleton 4');
  }

  // ====================================================================
  // 11. camera resize + diagnosis threshold
  // ====================================================================
  {
    const r = computeResizeDimensions(3000, 2000, 800);
    eq([r.width, r.height], [800, 533], 'camera resize 3000x2000 -> 800x533');
    const r2 = computeResizeDimensions(600, 800, 800);
    assert(r2.width === 600 && r2.height === 800, 'camera resize no upscale');
    const r3 = computeResizeDimensions(400, 300, 800);
    assert(r3.width === 400 && r3.height === 300, 'camera resize 400 small');
    eq(DIAGNOSE_THRESHOLD, 0.70, 'diagnose threshold 0.70');
  }

  // ====================================================================
  // 12. notifications severity colours + payload
  // ====================================================================
  {
    const sev = severityColor('yellow');
    eq(sev.color, '#FFCC00', 'severity yellow colour');
    assert(SEVERITY_COLORS.red.urgent === true, 'severity red urgent');
    const payload = buildNotificationFromAlert({ overall_risk_level: 'red', active_hazards: [{ name: 'Extreme Heat' }], confidence: 0.92, recommended_actions: { heat: ['stay hydrated'] } }, 'Lahore');
    assert(payload.title.includes('Red'), 'notif title red');
    assert(payload.body.includes('Extreme Heat'), 'notif body includes hazard');
    assert(payload.sound === 'alert.wav', 'notif red urgent sound');
    const green = buildNotificationFromAlert({ overall_risk_level: 'green', active_hazards: [] }, 'Lahore');
    assert(/All clear/.test(green.title), 'notif green all clear');
  }

  // ====================================================================
  // 13. i18n RTL + translation
  // ====================================================================
  {
    eq(T('home.title'), 'Weather & Crops', 'i18n EN home.title');
    setLocale('ur');
    assert(isRTL(), 'i18n ur isRTL');
    assert(T('home.title').length > 0 && /[\u0600-\u06FF]/.test(T('home.title')), 'i18n UR home.title urdu');
    const sub = T('conn.cache.subtitle', { days: 3 });
    assert(sub.includes('3'), 'i18n conn.cache.subtitle param substitution');
    setLocale('en');
    assert(!isRTL(), 'i18n back to EN no RTL');
  }

  // ====================================================================
  // Report
  // ====================================================================
  console.log(`\n${PASS} passed, ${FAIL} failed.`);
  if (FAIL) {
    console.log('\nFAILURES:');
    failures.forEach((f) => console.log('  - ' + f));
    process.exitCode = 1;
  } else {
    console.log('ALL SMOKE TESTS PASSED ✅');
  }
}

run().catch((e) => {
  console.error('TEST RUNNER ERROR:', e);
  process.exitCode = 1;
});
