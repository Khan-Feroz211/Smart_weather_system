/**
 * KisanAI — Capacitor capability shim (src/caps.js).
 *
 * Centralises every Capacitor plugin import so that:
 *  1. Plugins are only imported when running inside the native Capacitor
 *     WebView (`window.Capacitor`), OR explicitly requested for testing.
 *  2. In a plain browser / Node (smoke tests) the module loads with zero
 *     Capacitor involvement — every loader resolves to { native:false }.
 *
 * All native storage (Preferences / SQLite) goes through this shim so the
 * rest of the app never touches a plugin directly.
 */

let _caps = null; // cached capability object

export function isNative() {
  return typeof window !== 'undefined' && !!globalThis.Capacitor;
}

/**
 * Lazy-load the Capacitor plugins actually used by KisanAI.
 * Safe to call in Node / browser: returns { native:false } where absent.
 */
export async function loadCaps(force = false) {
  if (_caps && !force) return _caps;
  _caps = { native: isNative() };

  if (!_caps.native) {
    // Web / Node fallback: no native plugins.  Storage falls back to
    // localStorage (encrypted only where the platform offers it).
    return _caps;
  }

  try {
    const [{ Preferences }, { Geolocation }, { Camera }, { LocalNotifications }, { CapacitorSQLite, SQLiteConnection }] = await Promise.all([
      import('@capacitor/preferences'),
      import('@capacitor/geolocation'),
      import('@capacitor/camera'),
      import('@capacitor/local-notifications'),
      import('@capacitor-community/sqlite'),
    ]);
    _caps.Preferences = Preferences;
    _caps.Geolocation = Geolocation;
    _caps.Camera = Camera;
    _caps.LocalNotifications = LocalNotifications;
    _caps.CapacitorSQLite = CapacitorSQLite;
    _caps.SQLiteConnection = SQLiteConnection;
  } catch (e) {
    console.warn('[caps] native plugin import failed:', e);
    _caps.native = false;
  }
  return _caps;
}

export async function getPreferences() {
  const caps = await loadCaps();
  return caps.native ? caps.Preferences : null;
}

export async function getSQLite() {
  const caps = await loadCaps();
  // v7 API: new SQLiteConnection(CapacitorSQLite); conn.createConnection(name,{version}); conn.open()
  // storage.js destructures these.
  return caps.native ? { SQLiteConnection: caps.SQLiteConnection, CapacitorSQLite: caps.CapacitorSQLite } : null;
}

export async function getGeolocation() {
  const caps = await loadCaps();
  return caps.native ? caps.Geolocation : null;
}

export async function getCamera() {
  const caps = await loadCaps();
  return caps.native ? caps.Camera : null;
}

export async function getLocalNotifications() {
  const caps = await loadCaps();
  return caps.native ? caps.LocalNotifications : null;
}

/* ---- localStorage-backed fallback stores (web / Node) ---- */
export function webStoreGet(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) { return fallback; }
}

export function webStoreSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; }
  catch (e) { return false; }
}

export default { isNative, loadCaps, getPreferences, getSQLite, getGeolocation, getCamera, getLocalNotifications, webStoreGet, webStoreSet };
