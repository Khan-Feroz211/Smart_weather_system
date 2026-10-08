/**
 * KisanAI — App entry point (www/app.js).
 *
 * Bootstraps the framework-free Capacitor PWA:
 *   1. Registers the Service Worker (F8 offline).
 *   2. Initialises auth (Keystore token) + the field store (SQLite).
 *   3. Wires the bearer token from auth → api so every request is signed
 *      (Authorization: Bearer <token>) with an X-API-Key dev fallback.
 *   4. Starts the hash router with the view renderers for all 8 features.
 *
 * Loaded as an ES module from index.html:
 *   <script type="module" src="app.js"></script>
 */
import CONFIG from './config.js';
import { T, setLocale, getLocale, isRTL, applyTranslations, observeLocale } from './i18n.js';
import { api } from './src/api.js';
import auth, { initAuth } from './src/auth.js';
import { mapConnectivity, rainConfidenceForDays, localSkill, buildDecisionCard, formatDataAge, normalizeOmDay } from './src/features/home.js';
import { fieldStore, computeSoilRecommendation } from './src/storage.js';
import { capturePhoto, diagnoseImage, DIAGNOSE_THRESHOLD, computeResizeDimensions } from './src/camera.js';
import { scheduleHazardAlert, scheduleDailyPrompt, severityColor, buildNotificationFromAlert } from './src/notifications.js';
import { DISTRICTS, findDistricts, resolveDistrict, getCurrentLocation, LOCATION_DEFAULT } from './src/location.js';
import { buildSkeleton, registerBackHandler, onOrientationChange, applyRtl, speak } from './src/ui.js';
import Router, { navigate } from './src/router.js';
// Crop + symptom catalogue (mirrors backend crop_database.py, offline-safe).
import { CROPS, SYMPTOMS } from './src/crops.js';

const rootEl = () => (typeof document !== 'undefined' ? document.getElementById('app') : null);

/* ======================================================================
 * Connectivity badge (F1 header) — renders the ONLINE/CACHE/OFFLINE badge.
 * ==================================================================== */
function renderConnectivity(conn) {
  const sev = conn.state === 'OFFLINE' ? '#CC0000'
    : conn.state === 'CACHE' ? '#FFCC00'
    : '#00E450';
  const dot = conn.state === 'OFFLINE' ? '●' : conn.state === 'CACHE' ? '◒' : '●';
  return `
    <div class="conn-badge" style="border-color:${sev}">
      <span class="conn-dot" style="color:${sev}">${dot}</span>
      <span class="conn-label">${conn.label}</span>
      <span class="conn-sub">${conn.subtitle}</span>
      ${conn.penaltyPct > 0 ? `<span class="conn-penalty">−${conn.penaltyPct}%</span>` : ''}
    </div>`;
}

/* ======================================================================
 * Home view (F1 + F6 + F7)
 * ==================================================================== */
async function renderHome(ctx) {
  const el = rootEl();
  if (!el) return;
  el.innerHTML = `
    <section class="view home-view">
      <header class="view-header">
        <h1 data-i18n="home.title"></h1>
        <div id="home-location" class="location-chip">${LOCATION_DEFAULT.name}</div>
      </header>
      <div id="home-conn"></div>
      <div id="home-skel" class="skeletons"></div>
      <div id="home-content"></div>
    </section>`;
  applyTranslations(el);
  buildSkeleton(document.getElementById('home-skel'), 4);

  // Fetch a 5-day forecast.  Try the AgriAdvisor telemetry first; fall back
  // to Open-Meteo climate dataset (free, no key) when offline/unavailable.
  let conn = mapConnectivity('OFFLINE', '', 0);
  let days = [];
  try {
    const status = await api.getStatus();
    if (!status.offline) {
      conn = mapConnectivity('ONLINE', '', 0);
    }
    // 1) Prefer a normaliseable Open-Meteo daily forecast.
    const om = await api.fetchOpenMeteo(LOCATION_DEFAULT.name, 'climate');
    if (!om.offline && om.data && om.data.climate) {
      conn = mapConnectivity('ONLINE', '', 0);
      const c = om.data.climate.parameters || {};
      const times = c.times || [];
      const tmax = c.temperature_2m_max || [];
      const psum = c.precipitation_sum || [];
      const wspd = c.wind_speed_10m_max || [];
      const pop = c.precipitation_sum || []; // Open-Meteo climate has no pop; approximate from precip
      days = times.map((d, i) => normalizeOmDay(d, tmax[i], psum[i], pop[i] > 0 ? 60 : 20, wspd[i], 0));
    }
  } catch (e) {
    // keep cached/offline defaults
  }

  // If we still have nothing, use a tiny offline rule-based stub so the strip
  // is never empty (Phase 6 graceful degradation — never crash).
  if (!days.length) {
    days = [
      normalizeOmDay('today', 28, 0, 10, 5, 0),
      normalizeOmDay('tomorrow', 30, 0, 15, 6, 1),
      normalizeOmDay('+2d', 26, 2, 40, 8, 80),
      normalizeOmDay('+3d', 24, 0, 10, 7, 0),
      normalizeOmDay('+4d', 22, 5, 50, 9, 61),
    ];
    conn = mapConnectivity('OFFLINE', '', 0);
  }

  const fc = rainConfidenceForDays(days);
  const today = fc.today;
  const action = buildDecisionCard(today, days[0], null);
  const skill = { label: 'local', score: 0.6 };

  document.getElementById('home-skel').innerHTML = '';
  document.getElementById('home-conn').innerHTML = renderConnectivity(conn);
  document.getElementById('home-content').innerHTML = `
    <div class="decision-card">
      <div class="confidence-chip">
        <span class="rain-confidence" style="background:${today.label==='none'?'#eee':today.label==='high'?'#E8F5E9':today.label==='moderate'?'#FFF8E1':'#FFEBEE'}">
          ${Math.round(today.confidence*100)}% · <span data-i18n="home.rain_confidence"></span>
        </span>
        <span class="local-skill" data-i18n="home.local_skill"></span>
      </div>
      <h2 class="decision-title">${action.title}</h2>
      <p class="decision-body">${action.body}</p>
      <button class="btn btn-primary btn-48" data-i18n="${action.action === T('home.irrigate_now') ? '' : ''}">${action.action}</button>
    </div>
    <div class="strip-5day">
      ${fc.days.map((d, i) => `
        <div class="day-card ${i===0?'today':''}">
          <div class="day-name">${d.date || T('home.day')+' '+(i+1)}</div>
          <div class="day-pop">${Math.round((d.rainConfidence.confidence||0)*100)}%</div>
          <div class="day-temps">H ${d.high||'—'}° · L ${d.low||'—'}°</div>
          <div class="day-bar" style="height:${Math.round((d.rainConfidence.confidence||0)*40)}px;background:${d.rainConfidence.rainExpected?'#42A5F5':'#cfd'}"></div>
        </div>`).join('')}
    </div>
    <div class="openmeteo-fallback" data-i18n="home.openmeteo_fallback"></div>`;
  applyTranslations(el);
}

/* ======================================================================
 * Scan view (F3)
 * ==================================================================== */
async function renderScan(ctx) {
  const el = rootEl();
  if (!el) return;
  el.innerHTML = `
    <section class="view scan-view">
      <header class="view-header"><h1 data-i18n="scan.title"></h1></header>
      <div id="scan-result" class="scan-result"></div>
      <div class="scan-actions">
        <button class="btn btn-48" id="scan-camera" data-i18n="scan.camera"></button>
        <button class="btn btn-48" id="scan-gallery" data-i18n="scan.gallery"></button>
      </div>
    </section>`;
  applyTranslations(el);
  const crop = (ctx.params && ctx.params[0]) || 'wheat';

  document.getElementById('scan-camera').addEventListener('click', async () => {
    const photo = await capturePhoto({ source: 'camera' });
    if (!photo) return;
    document.getElementById('scan-result').innerHTML = '<p data-i18n="scan.processing"></p>';
    applyTranslations(el);
    const res = await diagnoseImage(photo, api, { crop });
    renderDiagnosis(res, el);
  });
  document.getElementById('scan-gallery').addEventListener('click', async () => {
    const photo = await capturePhoto({ source: 'gallery' });
    if (!photo) return;
    const res = await diagnoseImage(photo, api, { crop });
    renderDiagnosis(res, el);
  });
}

function renderDiagnosis(res, el) {
  const box = el.querySelector('#scan-result') || document.createElement('div');
  if (res.offline || res.sure === false && !res.sure) {
    // <70% confidence → "Not sure, retake"
    if (res.offline) {
      box.innerHTML = `<p data-i18n="scan.no_disease"></p>`;
    } else {
      box.innerHTML = `
        <div class="diagnosis low">
          <span data-i18n="scan.not_sure"></span>
          <small data-i18n="scan.not_sure.subtitle" data-i18n-attr="data-pct">${Math.round(res.confidence*100)}</small>
        </div>`;
      const sub = box.querySelector('small');
      if (res.confidence != null && sub) sub.setAttribute('data-pct', String(Math.round(res.confidence*100)));
    }
    applyTranslations(el);
    return;
  }
  box.innerHTML = `
    <div class="diagnosis confident">
      <h3>${res.disease}</h3>
      <p><strong data-i18n="scan.confidence"></strong> ${Math.round(res.confidence*100)}%</p>
      <p><strong data-i18n="scan.urgency"></strong> ${res.urgency}</p>
      ${res.treatment ? `<p><strong data-i18n="scan.treatment"></strong> ${res.treatment}</p>` : ''}
      ${res.explanation ? `<p><strong data-i18n="scan.explanation"></strong> ${res.explanation}</p>` : ''}
      <p><small data-i18n="scan.no_doses"></small></p>
    </div>`;
  applyTranslations(el);
}

/* ======================================================================
 * Symptoms view (F4) — offline rule-based via /api/agri/analyze
 * ==================================================================== */
async function renderSymptoms(ctx) {
  const el = rootEl();
  if (!el) return;
  const crop = 'wheat';
  const cropData = CROPS[crop];
  const symptomKeys = Object.keys(cropData.symptoms || {});

  el.innerHTML = `
    <section class="view symptoms-view">
      <header class="view-header"><h1 data-i18n="symptoms.title"></h1></header>
      <form id="symptom-form">
        ${symptomKeys.map((k) => `<label class="chk"><input type="checkbox" name="${k}"> <span>${cropData.symptoms[k].replace(/_/g,' ')}</span></label>`).join('')}
        <select id="symptom-stage">${cropData.stages.map((s) => `<option value="${s}">${s}</option>`).join('')}</select>
        <button type="submit" class="btn btn-primary btn-48" data-i18n="symptoms.analyse"></button>
      </form>
      <div id="symptom-result"></div>
    </section>`;
  applyTranslations(el);

  document.getElementById('symptom-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const symptoms = {};
    symptomKeys.forEach((k) => { symptoms[k] = !!form.elements[k].checked; });
    const stage = form.elements['symptom-stage'].value;
    document.getElementById('symptom-result').innerHTML = '<p data-i18n="loading"></p>';
    applyTranslations(el);
    // connectivity_state is forwarded so the backend applies staleness penalties.
    const res = await api.analyze({
      crop, symptoms, stage,
      connectivity_state: navigator ? (navigator.onLine ? 'ONLINE' : 'OFFLINE') : 'OFFLINE',
      data_age_hours: 0,
    });
    renderDiagnoses(res, el);
  });
}

function renderDiagnoses(res, el) {
  const box = el.querySelector('#symptom-result');
  if (!box) return;
  if (res.offline) {
    box.innerHTML = `<p data-i18n="symptoms.offline"></p>`;
    applyTranslations(el);
    return;
  }
  const diags = (res.data && res.data.diagnoses) || [];
  box.innerHTML = diags.map((d) => `
    <div class="diagnosis">
      <h3>${d.disease}</h3>
      <p><strong data-i18n="scan.confidence"></strong> ${Math.round((d.confidence_calibrated||0)*100)}%</p>
      <p><strong data-i18n="scan.urgency"></strong> ${d.urgency}</p>
      ${d.explanation ? `<p>${d.explanation}</p>` : ''}
    </div>`).join('');
  applyTranslations(el);
}

/* ======================================================================
 * Fields view (F5) — SQLite repository + soil recommendation
 * ==================================================================== */
async function renderFields(ctx) {
  const el = rootEl();
  if (!el) return;
  el.innerHTML = `
    <section class="view fields-view">
      <header class="view-header"><h1 data-i18n="fields.title"></h1>
        <button class="btn btn-48" id="field-add-btn" data-i18n="fields.add"></button></header>
      <div id="fields-list" class="fields-list"></div>
      <dialog id="field-dialog"></dialog>
    </section>`;
  applyTranslations(el);
  await refreshFields(el);

  document.getElementById('field-add-btn')?.addEventListener('click', () => openFieldDialog(el));
}

async function refreshFields(el) {
  const fields = await fieldStore.listFields();
  const list = el.querySelector('#fields-list');
  if (!list) return;
  if (!fields.length) {
    list.innerHTML = '<p data-i18n="fields.no_fields"></p>';
  } else {
    list.innerHTML = fields.map((f) => `
      <div class="field-card" data-id="${f.id}">
        <h3>${f.name}</h3>
        <p>${f.crop} · ${f.area || 0} acres</p>
        <button class="btn btn-48 btn-soil" data-action="soil" data-id="${f.id}" data-i18n="fields.soil_test"></button>
      </div>`).join('');
  }
  applyTranslations(el);
  el.querySelectorAll('[data-action="soil"]').forEach((b) =>
    b.addEventListener('click', () => openSoilDialog(el, Number(b.getAttribute('data-id')))));
}

function openFieldDialog(el) {
  const d = el.querySelector('#field-dialog');
  d.innerHTML = `
    <form id="field-form" method="dialog">
      <h2 data-i18n="fields.add"></h2>
      <input name="name" placeholder="${T('fields.name')}">
      <input name="area" type="number" placeholder="${T('fields.area')}">
      <select name="crop">${Object.keys(CROPS).map((c) => `<option value="${c}">${CROPS[c].name}</option>`).join('')}</select>
      <menu><button value="cancel" data-i18n="cancel"></button><button value="save" data-i18n="save"></button></menu>
    </form>`;
  d.showModal();
  d.querySelector('#field-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    await fieldStore.addField({ name: f.name.value, area: f.area.value, crop: f.crop.value });
    d.close();
    await refreshFields(el);
  });
}

async function openSoilDialog(el, fieldId) {
  const soils = await fieldStore.getSoilTests(fieldId);
  const d = el.querySelector('#field-dialog');
  d.innerHTML = `
    <form id="soil-form" method="dialog">
      <h2 data-i18n="fields.soil_test"></h2>
      <input name="ph" type="number" step="0.1" placeholder="${T('fields.ph')} (5.5–7.0)">
      <input name="nitrogen" type="number" placeholder="${T('fields.nitrogen')}">
      <input name="phosphorus" type="number" placeholder="${T('fields.phosphorus')}">
      <input name="potassium" type="number" placeholder="${T('fields.potassium')}">
      <input name="organic_matter" type="number" step="0.1" placeholder="${T('fields.organic_matter')}">
      <select name="texture">${Object.keys(SOIL_TEXTURES).map((k) => `<option value="${k}">${SOIL_TEXTURES[k]}</option>`).join('')}</select>
      <menu><button value="cancel" data-i18n="cancel"></button><button value="save" data-i18n="save"></button></menu>
    </form>`;
  d.showModal();
  d.querySelector('#soil-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    const result = await fieldStore.saveSoilTest(fieldId, {
      ph: parseFloat(f.ph.value) || 7.0,
      nitrogen: parseFloat(f.nitrogen.value) || 20,
      phosphorus: parseFloat(f.phosphorus.value) || 15,
      potassium: parseFloat(f.potassium.value) || 100,
      organic_matter: parseFloat(f.organic_matter.value) || 2.5,
      texture: f.texture.value,
    });
    d.close();
    // Show the recommendation inline.
    const rec = computeSoilRecommendation(result);
    const list = el.querySelector('#fields-list');
    if (list) {
      list.insertAdjacentHTML('afterbegin', `
        <div class="soil-rec"><h3 data-i18n="fields.soil_recommendation"></h3>
         <p class="overall">${rec.overall}</p><ul>${rec.actions.map((a) => `<li>${a}</li>`).join('')}</ul></div>`);
      applyTranslations(list);
    }
  });
}
const SOIL_TEXTURES = { clay:'Clay', clay_loam:'Clay loam', loam:'Loam', sandy_loam:'Sandy loam', sand:'Sand', silt:'Silt', silty_clay:'Silty clay' };

/* ======================================================================
 * Alerts view (F7)
 * ==================================================================== */
async function renderAlerts(ctx) {
  const el = rootEl();
  if (!el) return;
  el.innerHTML = `
    <section class="view alerts-view">
      <header class="view-header"><h1 data-i18n="alerts.title"></h1></header>
      <div id="alerts-list" class="alerts-list"></div>
    </section>`;
  applyTranslations(el);
  buildSkeleton(el.querySelector('#alerts-list'), 4);
  try {
    const res = await api.predictHazards({ location: LOCATION_DEFAULT.name });
    if (res.offline || !res.data) {
      el.querySelector('#alerts-list').innerHTML = '<p data-i18n="alerts.no_alerts"></p>';
      applyTranslations(el);
      return;
    }
    const alert = res.data.alert || {};
    const sev = severityColor(alert.overall_risk_level || 'green');
    const conn = res.connectivity || mapConnectivity('OFFLINE', '', 0);
    el.querySelector('#alerts-list').innerHTML = `
      <div class="alert-card" style="border-left:5px solid ${sev.color}">
        <div class="alert-head">${sev.icon} ${sev.label} — ${alert.message || T('alerts.no_alerts')}</div>
        <p class="alert-confidence">Confidence ${Math.round((Number(alert.confidence)||0)*100)}%</p>
        <div class="alert-actions">
          ${(alert.recommended_actions ? Object.entries(alert.recommended_actions).map(([haz, acts]) => (acts||[]).map((a) => `<li>${a}</li>`).join('')).join('') : '')}
        </div>
        <div class="conn-badge">${renderConnectivity(conn)}</div>
      </div>`;
    // Schedule a deduplicated local notification for any non-green alert.
    if (alert.overall_risk_level !== 'green') {
      await scheduleHazardAlert(alert, LOCATION_DEFAULT.name);
    }
    applyTranslations(el);
  } catch (e) {
    el.querySelector('#alerts-list').innerHTML = '<p data-i18n="error"></p>';
    applyTranslations(el);
  }
}

/* ======================================================================
 * Settings view
 * ==================================================================== */
function renderSettings(ctx) {
  const el = rootEl();
  if (!el) return;
  el.innerHTML = `
    <section class="view settings-view">
      <header class="view-header"><h1 data-i18n="settings.title"></h1></header>
      <div class="settings-list">
        <label data-i18n="settings.language"></label>
        <select id="setting-lang">
          <option value="en">English</option>
          <option value="ur">اردو</option>
        </select>
        <label><input type="checkbox" id="setting-tts"> <span data-i18n="settings.tts"></span></label>
        <label><input type="checkbox" id="setting-notif" checked> <span data-i18n="settings.notifications"></span></label>
        <label><input type="checkbox" id="setting-debug"> <span data-i18n="settings.debug"></span></label>
      </div>
    </section>`;
  applyTranslations(el);
  document.getElementById('setting-lang').value = getLocale();
  document.getElementById('setting-lang').addEventListener('change', (e) => setLocale(e.target.value));
  document.getElementById('setting-tts').addEventListener('change', (e) => {
    if (e.target.checked) speak(T('app.tagline'), 'ur');
  });
}

function speakDemo() {
  // placeholder removed — speak() is imported from ./src/ui.js
}

/* ======================================================================
 * Boot
 * ==================================================================== */
async function init() {
  // Wire auth → api (bearer token).  initAuth binds the api client into
  // the shared auth singleton so api.setToken stays in sync.
  initAuth(api);
  await auth.init();
  if (typeof auth.getToken === 'function') api.setToken(auth.getToken());
  auth.subscribe((state) => api.setToken(state.token));

  await fieldStore.init();

  // Register the Service Worker (F8 offline) — best effort.
  if ('serviceWorker' in navigator) {
    try { navigator.serviceWorker.register('sw.js'); } catch (e) { console.warn(e); }
  }

  // Android back-button + orientation.
  registerBackHandler(() => (window.location.hash === '#home' ? false : true));
  onOrientationChange(() => { /* re-render current view on rotation */ });
  applyRtl(isRTL());

  // Build router with all feature views.
  const router = new Router({ api, auth, fieldStore, capturePhoto, diagnoseImage, CONFIG });
  router.add({
    '#home': renderHome,
    '#scan': renderScan,
    '#symptoms': renderSymptoms,
    '#fields': renderFields,
    '#alerts': renderAlerts,
    '#settings': renderSettings,
  });
  router.start();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}

// Expose a tiny surface for the dev console / tests.
export { CONFIG, api, auth, fieldStore };
export default { init };
