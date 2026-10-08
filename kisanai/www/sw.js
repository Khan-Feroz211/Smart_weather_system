/**
 * KisanAI — Service Worker (sw.js).
 *
 * F8: Offline.
 *   - App-shell cache-first (HTML/CSS/JS/assets/fonts/icons) so the UI loads
 *     with zero bars of network.
 *   - API network-first with stale-while-revalidate fallback: live data is
 *     tried first; on failure the last cached response is served AND tagged
 *     with a `x-data-age-hours` header so the connectivity badge can render
 *     "X hours ago" (mirrors backend data_age_hours).
 *   - "offline ready" badge so the app can show OFFLINE state.
 *
 * Install/activate run fully offline-capable.  No external deps.
 */
const VERSION = 'kisanai-v1';
const SHELL_CACHE = VERSION + '-shell';
const API_CACHE = VERSION + '-api';
const DATA_TTL_MS = 4 * 60 * 60 * 1000; // 4h for weather/hazard data

const SHELL_ASSETS = [
  './',
  './index.html',
  './styles.css',
  './i18n.js',
  './config.js',
  './app.js',
  './manifest.json',
  './src/api.js',
  './src/auth.js',
  './src/caps.js',
  './src/camera.js',
  './src/location.js',
  './src/notifications.js',
  './src/router.js',
  './src/storage.js',
  './src/ui.js',
  './src/crops.js',
  './src/features/home.js',
  // fonts & icons (404 if absent — pre-cache tolerates missing entries)
  './assets/fonts/NotoNastaliq-Regular.ttf',
  './assets/icons/icon-192.png',
  './assets/icons/icon-512.png',
  './assets/icons/icon-72.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => caches.open(SHELL_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.map((k) => (k.indexOf(VERSION) === 0 ? null : caches.delete(k)))
    ))
  );
  self.clients.claim();
});

function isApi(url) {
  return url.pathname.startsWith('/api/');
}

function ageFromResponse(resp) {
  // Prefer a real data-age header from the server; else use the response
  // timestamp / cached entry time.
  const header = resp.headers.get('x-data-age-hours');
  if (header) return parseFloat(header);
  const date = resp.headers.get('date');
  if (date) return (Date.now() - new Date(date).getTime()) / 3600000;
  return 0;
}

self.addEventListener('fetch', (e) => {
  const { request } = e;
  // Only handle GET; ignore POST/PUT/DELETE (mutations go through api.js).
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (isApi(url)) {
    // Network-first with stale-while-revalidate fallback.
    e.respondWith(
      fetch(request).then((networkResp) => {
        const copy = networkResp.clone();
        // Cache the fresh response with a data-age header = 0.
        const headers = new Headers(copy.headers);
        headers.set('x-data-age-hours', '0');
        headers.set('x-fresh', 'true');
        caches.open(API_CACHE).then((cache) => cache.put(request, new Response(copy.body, { status: copy.status, statusText: copy.statusText, headers })));
        return networkResp;
      }).catch(() => {
        // Offline: serve the last cached response, tagged with its age.
        return caches.match(request).then((cached) => {
          if (!cached) {
            // No cache → synthesize an OFFLINE envelope for the client.
            const offlineBody = JSON.stringify({
              offline: true,
              offlineReason: 'no network and no cached response',
              connectivity_state: 'OFFLINE',
              data_age_hours: 0,
            });
            return new Response(offlineBody, {
              status: 200,
              headers: { 'Content-Type': 'application/json', 'x-data-age-hours': '0', 'x-fresh': 'false' },
            });
          }
          // Tag cached response with computed age so the badge can label it.
          const ageHours = ageFromResponse(cached);
          const headers = new Headers(cached.headers);
          headers.set('x-data-age-hours', String(ageHours));
          headers.set('x-fresh', 'false');
          return new Response(cached.body, { status: cached.status, statusText: cached.statusText, headers });
        });
      })
    );
    return;
  }

  // Static assets: cache-first.
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((networkResp) => {
          if (networkResp && networkResp.status === 200) {
            const copy = networkResp.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return networkResp;
        }).catch(() => caches.match('./index.html'));
      })
    );
  }
});

/* Ping handler: the page can ask the SW for the current connectivity state. */
self.addEventListener('message', (e) => {
  const data = e.data || {};
  if (data.type === 'GET_CONNECTIVITY') {
    const clients = self.clients || {};
    e.source && e.source.postMessage({ type: 'CONNECTIVITY', online: data.online !== false });
  }
});
