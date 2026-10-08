/**
 * KisanAI — Network module (src/api.js).
 *
 * THE single network entry point for the app.  Every backend call goes through
 * here so timeouts, retries, auth headers and connectivity degrade are central.
 *
 * Behaviour (from kisanai/TODO.md):
 *   - 10s hard timeout per attempt (CONFIG.REQUEST_TIMEOUT_MS).
 *   - Exponential backoff + jitter (CONFIG.RETRY_ATTEMPTS retries,
 *     CONFIG.RETRY_BACKOFF_BASE_MS base).
 *   - Bearer token injected from auth.js (Authorization: Bearer <token>).
 *     Falls back to X-API-Key (dev) when no bearer token is present.
 *   - On ANY network failure / non-5xx the caller gets a structured
 *     { offline: true, ... } envelope so the UI can degrade gracefully to the
 *     cached rule-based path (the whole point of Phase 6 graceful degradation).
 *
 * Verified backend contracts (trust CODE, not README — see TODO.md §2/§3):
 *   GET  /api/agri/status
 *   POST/GET /api/agri/analyze   (JSON body or multipart image)
 *   POST /api/agri/disease-diagnose
 *   POST /api/agri/feedback
 *   POST /api/agri/sync-feedback
 *   GET  /api/agri/openmeteo
 *   GET  /api/hazards/predict      (GET-only — README mismatch, code is GET)
 *   GET  /api/hazards/alerts
 *   GET  /api/hazards/dashboard
 *
 * Import-safe in plain Node: this module only imports ../config.js and
 * ../src/features/home.js (both pure).  Capacitor / Preferences / SQLite are
 * NOT imported here — auth.js owns the token and hands it to api via setToken().
 */
import CONFIG from '../config.js';
import { mapConnectivity } from './features/home.js';

const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

/* ======================================================================
 * Low-level HTTP with timeout + exponential backoff.
 * ==================================================================== */
function timeoutSignal(ms, label) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(new Error(`request timeout (${label})`)), ms);
  controller._clear = () => clearTimeout(id);
  return controller;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * GET/POST with retry.  Returns the raw Response.
 * Throws on exhausted retries / timeout so callers can map to offline mode.
 */
export async function http(method, url, opts = {}) {
  const attempts = 1 + CONFIG.RETRY_ATTEMPTS;
  let lastErr;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    const ctrl = timeoutSignal(CONFIG.REQUEST_TIMEOUT_MS, `${method} ${url}`);
    try {
      const res = await fetch(url, { ...opts, method, signal: ctrl.signal });
      lastErr = null;
      // Retry transient server errors; pass through 4xx auth/validation errors.
      if (res.ok || !RETRYABLE_STATUS.has(res.status)) {
        return res;
      }
      lastErr = new HttpError(res.status, `${method} ${url} -> ${res.status}`, res);
    } catch (e) {
      lastErr = e;
      if (e && e.name === 'AbortError') lastErr = new HttpError(408, `timeout ${url}`);
    } finally {
      ctrl._clear();
    }
    if (attempt < attempts) {
      const backoff = CONFIG.RETRY_BACKOFF_BASE_MS * Math.pow(2, attempt - 1);
      const jitter = Math.random() * 200;
      await sleep(backoff + jitter);
    }
  }
  throw lastErr || new HttpError(0, `request failed ${url}`);
}

export class HttpError extends Error {
  constructor(status, message, response = null) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
    this.response = response; // raw Response (null for network errors)
  }
}

/* ======================================================================
 * AgriApi — high-level client bound to the AgriAdvisor / Hazards contracts.
 * ==================================================================== */
export class AgriApi {
  constructor(baseUrl = CONFIG.API_BASE_URL) {
    this.baseUrl = String(baseUrl || '').replace(/\/+$/, '');
    this._token = null;
    this._apiKey = CONFIG.AGRI_API_KEY;
    this._online = true;
    this._statusCache = null;
    this._statusCacheTtl = 15000;
    this._statusFetchedAt = 0;
  }

  /* ---- auth wiring: a token getter is injected by auth.js ---- */
  /** @param {()|null} fn synchronous getter returning a bearer token (or null) */
  setTokenProvider(fn) { this._tokenProvider = fn || null; }
  setToken(token) {
    this._token = token ? String(token) : null;
    this._tokenProvider = null;
  }
  getToken() {
    if (this._tokenProvider) { try { return this._tokenProvider(); } catch (e) { return this._token; } }
    return this._token;
  }

  /* ---- connectivity state (mutated by online/offline events) ---- */
  setOnline(online) { this._online = online; }
  isOnline() { return this._online; }

  authHeaders() {
    const token = this.getToken();
    const h = { 'Content-Type': 'application/json' };
    if (token) {
      h['Authorization'] = `Bearer ${token}`;
    } else if (this._apiKey) {
      // dev fallback: X-API-Key
      h['X-API-Key'] = this._apiKey;
    }
    return h;
  }

  path(p) { return `${this.baseUrl}${p.startsWith('/') ? p : '/' + p}`; }

  /* ---- low-level wrapper that produces an offline-safe envelope ---- */
  async _call(fn) {
    // Fast local short-circuit when the device is known-offline.
    if (!this._online) {
      return { offline: true, offlineReason: 'navigator.offline', data: null };
    }
    try {
      return { offline: false, data: await fn() };
    } catch (e) {
      // Treat 401 specially: bubble it up so auth can trigger re-login.
      if (e instanceof HttpError && e.status === 401) {
        this._dispatch('auth:unauthorized', { status: 401 });
        return { offline: false, unauthorized: true, data: null };
      }
      const offline = !(e instanceof HttpError) || e.status === 0 || RETRYABLE_STATUS.has(e.status);
      if (offline) this._online = false;
      return { offline, offlineReason: e.message || 'network_error', data: null };
    }
  }

  _dispatch(name, detail) {
    if (typeof window !== 'undefined' && window.dispatchEvent) {
      window.dispatchEvent(new CustomEvent(name, { detail }));
    }
  }

  /* =================================================================
   * GET /api/agri/status
   * ================================================================= */
  async getStatus() {
    const now = Date.now();
    if (this._statusCache && now - this._statusFetchedAt < this._statusCacheTtl) {
      return this._statusCache;
    }
    const res = await this._call(() =>
      http('GET', this.path('/api/agri/status')).then((r) => r.json())
    );
    if (!res.offline) { this._statusCache = res.data; this._statusFetchedAt = now; }
    return res;
  }

  /* =================================================================
   * POST /api/agri/analyze  (JSON body)
   *   { crop, symptoms{}, site{}, stage, location, connectivity_state,
   *     data_age_hours, compact, top_k }
   * ================================================================= */
  async analyze(payload, opts = {}) {
    const body = payload;
    const headers = { ...this.authHeaders() };
    const res = await this._call(() =>
      http('POST', this.path('/api/agri/analyze'), {
        headers,
        body: JSON.stringify(body),
      }).then((r) => r.json())
    );
    if (!res.offline) res.connectivity = mapConnectivityFromAnalyze(res.data);
    return res;
  }

  /* =================================================================
   * POST /api/agri/disease-diagnose  (multipart image OR base64 JSON)
   *   multipart: { image: File }
   *   json:      { image: "<base64>", crop?, top_k? }
   *   Returns predictions[].confidence (top-1). <0.7 => "Not sure, retake".
   * ================================================================= */
  async diagnoseImage(image, opts = {}) {
    const crop = opts.crop || 'wheat';
    const top_k = opts.top_k || 3;
    const headers = {};
    let body;
    if (typeof image === 'string') {
      // base64 data URL or raw base64
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify({ image, crop, top_k });
    } else if (typeof FormData !== 'undefined' && image instanceof FormData) {
      body = image; // multipart 'image' field already set
    } else if (image && typeof image.arrayBuffer === 'function') {
      const fd = new FormData();
      fd.append('image', image, 'capture.jpg');
      fd.append('crop', crop);
      fd.append('top_k', String(top_k));
      body = fd;
      // let the browser set the multipart boundary
      delete headers['Content-Type'];
    } else {
      // raw base64 string
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify({ image: image, crop, top_k });
    }
    const res = await this._call(() =>
      http('POST', this.path('/api/agri/disease-diagnose'), { headers, body }).then((r) => r.json())
    );
    return res;
  }

  /* =================================================================
   * POST /api/agri/feedback
   *   { crop, predicted_disease, actual_disease, is_simulated, is_offline }
   *   -> { status, message, note }
   * Offline submissions are queued (F6 rain feedback loop).
   * ================================================================= */
  async submitFeedback(feedback) {
    const isOffline = !!feedback.is_offline;
    const body = {
      crop: feedback.crop || 'wheat',
      predicted_disease: feedback.predicted_disease || 'unknown',
      actual_disease: feedback.actual_disease || 'unknown',
      is_simulated: !!feedback.is_simulated,
      is_offline: isOffline,
    };
    const res = await this._call(() =>
      http('POST', this.path('/api/agri/feedback'), {
        headers: this.authHeaders(),
        body: JSON.stringify(body),
      }).then((r) => r.json())
    );
    // If the network call failed, queue locally for later sync (Gap 1e / F6).
    if (res.offline) {
      this._queueOfflineFeedback(body);
    }
    return res;
  }

  _queueOfflineFeedback(item) {
    try {
      const key = '__kisanai_feedback_queue__';
      const raw = (typeof localStorage !== 'undefined' && localStorage.getItem(key)) || '[]';
      const queue = JSON.parse(raw);
      queue.push({ ...item, queued_at: Date.now(), attempts: 0 });
      localStorage.setItem(key, JSON.stringify(queue));
    } catch (e) { /* ignore storage errors */ }
  }

  /* =================================================================
   * POST /api/agri/sync-feedback  -> { synced_count }
   * Drains the offline feedback queue with backoff retry.
   * ================================================================= */
  async syncFeedback() {
    const key = '__kisanai_feedback_queue__';
    let queue = [];
    try {
      const raw = typeof localStorage !== 'undefined' && localStorage.getItem(key);
      if (raw) queue = JSON.parse(raw);
    } catch (e) { /* ignore */ }
    if (queue.length === 0) return { synced_count: 0 };

    const remaining = [];
    let synced = 0;
    for (const item of queue) {
      let sent = false;
      try {
        const res = await http('POST', this.path('/api/agri/feedback'), {
          headers: this.authHeaders(),
          body: JSON.stringify(item),
        });
        if (res.ok) { sent = true; synced += 1; }
      } catch (e) {
        // network/timeout — keep for next attempt
      }
      if (!sent) {
        item.attempts = (item.attempts || 0) + 1;
        remaining.push(item);
      }
    }
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(key, JSON.stringify(remaining));
    } catch (e) { /* ignore */ }
    return { synced_count: synced };
  }

  /* =================================================================
   * GET /api/agri/openmeteo?location=…&datasets=…
   *   -> { location, geocoding, satellite, flood, climate, seasonal, attribution }
   * ================================================================= */
  async fetchOpenMeteo(location, datasets = 'all') {
    const params = new URLSearchParams({ location: location || 'Lahore', datasets });
    const res = await this._call(() =>
      http('GET', `${this.path('/api/agri/openmeteo')}?${params.toString()}`).then((r) => r.json())
    );
    return res;
  }

  /* =================================================================
   * GET /api/hazards/predict  (GET-only — code truth, README mismatch)
   *   ?location=…&lat=…&lon=…&connectivity_state=…
   *   -> { success, location, alert{…}, offline_sms }
   * ================================================================= */
  async predictHazards(opts = {}) {
    const params = new URLSearchParams();
    if (opts.location) params.set('location', opts.location);
    if (opts.lat != null) params.set('lat', String(opts.lat));
    if (opts.lon != null) params.set('lon', String(opts.lon));
    if (opts.connectivity_state) params.set('connectivity_state', opts.connectivity_state);
    const res = await this._call(() =>
      http('GET', `${this.path('/api/hazards/predict')}?${params.toString()}`).then((r) => r.json())
    );
    if (!res.offline) {
      const alert = res.data && res.data.alert;
      if (alert) res.connectivity = mapConnectivityFromHazards(alert, opts.connectivity_state);
    }
    return res;
  }

  /* =================================================================
   * GET /api/hazards/alerts  -> { success, alerts[], count }
   * ================================================================= */
  async getAlerts() {
    return this._call(() =>
      http('GET', this.path('/api/hazards/alerts')).then((r) => r.json())
    );
  }

  /* =================================================================
   * GET /api/hazards/dashboard  -> { success, dashboard }
   * ================================================================= */
  async getDashboard() {
    return this._call(() =>
      http('GET', this.path('/api/hazards/dashboard')).then((r) => r.json())
    );
  }

  /* =================================================================
   * Mobile auth token endpoints (proposed — backend change #1).
   * These are OPTIONAL: if the backend returns 404 the app degrades to
   * offline mode with cached rule-based data (per TODO §Mobile auth design).
   * ================================================================= */
  async mobileLogin(credentials) {
    const res = await this._call(() =>
      http('POST', this.path('/api/mobile/login'), {
        headers: this.authHeaders(),
        body: JSON.stringify(credentials),
      }).then((r) => r.json())
    );
    return res;
  }

  async mobileVerifyTotp(tempToken, code) {
    return this._call(() =>
      http('POST', this.path('/api/mobile/verify-totp'), {
        headers: this.authHeaders(),
        body: JSON.stringify({ temp_token: tempToken, code }),
      }).then((r) => r.json())
    );
  }

  async mobileSendOtp(phoneNumber) {
    return this._call(() =>
      http('POST', this.path('/api/mobile/send-otp'), {
        headers: this.authHeaders(),
        body: JSON.stringify({ phone_number: phoneNumber }),
      }).then((r) => r.json())
    );
  }
}

/* ======================================================================
 * Connectivity normalisers (pure — re-exported for tests).
 *
 * The backend returns connectivity in two shapes:
 *   analyze  -> connectivity_state (ONLINE/CACHE/OFFLINE) + status_header
 *   hazards  -> alert.degradation_info.mode (online/degraded/cache_only/offline)
 * ==================================================================== */
export function mapConnectivityFromAnalyze(result) {
  if (!result) return mapConnectivity('OFFLINE', '', 0);
  return mapConnectivity(
    result.connectivity_state,
    result.status_header,
    result.data_age_hours
  );
}

export function mapConnectivityFromHazards(alert, queryState) {
  if (!alert) return mapConnectivity('OFFLINE', '', 0);
  const deg = alert.degradation_info || {};
  let state = 'ONLINE';
  const mode = String(deg.mode || '').toLowerCase();
  if (mode === 'offline' || queryState === 'OFFLINE') state = 'OFFLINE';
  else if (mode === 'cache_only' || queryState === 'CACHE') state = 'CACHE';
  else state = 'ONLINE';
  const header = mode ? `[${mode}] ${alert.message || ''}` : '';
  return mapConnectivity(state, header, deg.data_age_hours);
}

export const api = new AgriApi();
export default api;
