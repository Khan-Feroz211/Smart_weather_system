/**
 * KisanAI — build-time configuration (the single source of environment config).
 *
 * Per the spec, the API base URL must NOT be hardcoded in source. Instead it
 * is injected at build time by scripts/gen-config.js, which reads environment
 * variables and writes `window.__KISANAI_CONFIG__` into www/config.js.
 *
 * This loader reads that injected object (or sensible dev defaults) and exports
 * a frozen `CONFIG` singleton. src/api.js is the ONLY module that uses it.
 *
 * Env vars (read by scripts/gen-config.js):
 *   KISANAI_API_BASE_URL   – HTTPS base URL of the Flask backend, e.g.
 *                            https://api.kisanai.example.com
 *   KISANAI_VISION_ENDPOINT– optional deep-learning vision endpoint override
 *                            (defaults to ${API_BASE_URL}/api/agri/disease-diagnose)
 *   KISANAI_AGRI_API_KEY   – X-API-Key for the Agri endpoints (dev only;
 *                            production uses the bearer token from /api/mobile/login)
 *   KISANAI_ENV            – "production" | "staging" | "development"
 */
(function (global) {
  'use strict';

  // Injected by scripts/gen-config.js into window.__KISANAI_CONFIG__.
  var injected = (typeof window !== 'undefined' && window.__KISANAI_CONFIG__) || {};

  // Dev fallback: when opened straight from the filesystem / localhost, point
  // at the local Flask dev server so the app is usable out of the box.
  var isLocal = (function () {
    try {
      var h = window.location.hostname;
      return h === 'localhost' || h === '127.0.0.1' || h === '0.0.0.0';
    } catch (e) {
      return false;
    }
  })();

  var DEFAULT_BASE = isLocal ? 'http://127.0.0.1:8000' : 'https://api.kisanai.example.com';

  var CONFIG = {
    API_BASE_URL: (injected.API_BASE_URL || DEFAULT_BASE).replace(/\/+$/, ''),
    // Vision endpoint: prefer MY_VISION_ENDPOINT if provided, else the backend
    // disease-diagnose route.
    VISION_ENDPOINT: injected.VISION_ENDPOINT
      ? injected.VISION_ENDPOINT.replace(/\/+$/, '')
      : (injected.API_BASE_URL || DEFAULT_BASE).replace(/\/+$/, '') + '/api/agri/disease-diagnose',
    AGRI_API_KEY: injected.AGRI_API_KEY || 'agri_dev_key_2024',
    ENV: injected.ENV || (isLocal ? 'development' : 'production'),
    // Network behaviour. Tuned for weak/intermittent networks.
    REQUEST_TIMEOUT_MS: 10000,          // 10s hard timeout per the spec
    RETRY_ATTEMPTS: 2,                  // +1 for an initial retry
    RETRY_BACKOFF_BASE_MS: 800,         // exponential backoff base
    // Image capture limits (Feature 3).
    MAX_IMAGE_PX: 800,
    MAX_IMAGE_BYTES: 2 * 1024 * 1024,   // match backend MAX_CONTENT_LENGTH (2 MB)
    DEBUG: injected.DEBUG === true || injected.ENV === 'development',
  };

  // Deep freeze so no module can mutate the shared config at runtime.
  function deepFreeze(obj) {
    if (obj && typeof obj === 'object' && !Object.isFrozen(obj)) {
      Object.keys(obj).forEach(function (k) { deepFreeze(obj[k]); });
      Object.freeze(obj);
    }
    return obj;
  }

  var frozen = deepFreeze(CONFIG);
  if (typeof window !== 'undefined') {
    window.__KISANAI_CONFIG__ = frozen;
  }
  // CommonJS + ESM-ish: expose globally and via export.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = frozen;
  }
  globalThis.CONFIG = frozen;
  return frozen;
})(typeof globalThis !== 'undefined' ? globalThis : this);

export default (typeof globalThis.CONFIG !== 'undefined' ? globalThis.CONFIG : (typeof module !== 'undefined' ? module.exports : globalThis.CONFIG));
