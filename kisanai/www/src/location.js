/**
 * KisanAI — Location (src/location.js).
 *
 * F2: GPS + district picker.
 *
 *   - `getCurrentLocation()` resolves the device GPS coords through Capacitor
 *     Geolocation.  On permission denial / unavailability it returns null so
 *     the UI can fall back to the district picker (no hard-coded "Chakwal").
 *   - `DISTRICTS` is a self-contained, OFFLINE table of Pakistani districts
 *     (province-grouped) with lat/lon, so location resolution works with no
 *     network.  (The backend's OPENMETEO_FALLBACK_LOCATIONS only carries a
 *     handful of cities; the mobile app ships a fuller list.)
 *   - `findDistricts(query)` is a pure, case-insensitive filter (testable).
 *
 * Import-safe in Node: only ../config.js & ./caps.js (lazy) are imported; the
 * geolocation call is guarded behind isNative().
 */
import { getGeolocation } from './caps.js';

export const PROVINCES = [
  'Punjab', 'Sindh', 'Khyber Pakhtunkhwa', 'Balochistan', 'Punjab (Islamabad)', 'Azad Jammu & Kashmir', 'Gilgit-Baltistan',
];

/** Bundled Pakistan district table (lat/lon for offline resolution). */
export const DISTRICTS = [
  // Punjab
  { name: 'Lahore',      province: 'Punjab', lat: 31.5497, lon: 74.3437 },
  { name: 'Faisalabad',  province: 'Punjab', lat: 31.4181, lon: 73.0762 },
  { name: 'Rawalpindi',  province: 'Punjab', lat: 33.6353, lon: 73.0859 },
  { name: 'Gujranwala',  province: 'Punjab', lat: 32.6167, lon: 74.1958 },
  { name: 'Multan',      province: 'Punjab', lat: 30.1790, lon: 71.4833 },
  { name: 'Sialkot',     province: 'Punjab', lat: 32.4960, lon: 74.5311 },
  { name: 'Sargodha',    province: 'Punjab', lat: 32.0836, lon: 72.6683 },
  { name: 'Sheikhupura', province: 'Punjab', lat: 31.7860, lon: 73.9960 },
  { name: 'Bahawalpur',  province: 'Punjab', lat: 29.3916, lon: 68.0700 },
  { name: 'Dera Ghazi Khan', province: 'Punjab', lat: 29.9500, lon: 69.8500 },
  { name: 'Sahiwal',     province: 'Punjab', lat: 30.3225, lon: 72.9500 },
  { name: 'Chakwal',     province: 'Punjab', lat: 30.2300, lon: 72.3300 },
  { name: 'Jhelum',      province: 'Punjab', lat: 32.9360, lon: 73.7300 },
  { name: 'Gujrat',      province: 'Punjab', lat: 32.9020, lon: 73.7300 },
  { name: 'Kasur',       province: 'Punjab', lat: 31.2765, lon: 74.4108 },
  { name: 'Rahim Yar Khan', province: 'Punjab', lat: 28.4180, lon: 70.3090 },
  { name: 'Mardan',      province: 'Punjab', lat: 34.3700, lon: 72.2100 },
  // Sindh
  { name: 'Karachi',     province: 'Sindh', lat: 24.8607, lon: 67.0011 },
  { name: 'Hyderabad',   province: 'Sindh', lat: 25.3980, lon: 68.4090 },
  { name: 'Sukkur',      province: 'Sindh', lat: 24.8722, lon: 68.8600 },
  { name: 'Larkana',     province: 'Sindh', lat: 27.9290, lon: 68.1800 },
  { name: 'Naushahro Fakir', province: 'Sindh', lat: 26.8500, lon: 68.8146 },
  // Khyber Pakhtunkhwa
  { name: 'Peshawar',    province: 'Khyber Pakhtunkhwa', lat: 34.0167, lon: 71.5833 },
  { name: 'Mingora',     province: 'Khyber Pakhtunkhwa', lat: 34.7800, lon: 72.2300 },
  { name: 'Abbottabad',  province: 'Khyber Pakhtunkhwa', lat: 34.2200, lon: 73.1300 },
  { name: 'Swat',        province: 'Khyber Pakhtunkhwa', lat: 34.8833, lon: 72.4500 },
  { name: 'Malakand',    province: 'Khyber Pakhtunkhwa', lat: 34.0000, lon: 71.8500 },
  // Balochistan
  { name: 'Quetta',      province: 'Balochistan', lat: 30.1800, lon: 67.0000 },
  { name: 'Gwadar',      province: 'Balochistan', lat: 25.1300, lon: 62.0200 },
  { name: 'Turbat',      province: 'Balochistan', lat: 27.3500, lon: 65.7500 },
  { name: 'Dera Murad Jamali', province: 'Balochistan', lat: 30.5300, lon: 66.9900 },
  { name: 'Khuzdar',     province: 'Balochistan', lat: 27.8000, lon: 66.9500 },
  // Islamabad
  { name: 'Islamabad',   province: 'Punjab (Islamabad)', lat: 33.6844, lon: 73.0479 },
  // AJK / GB
  { name: 'Muzaffarabad', province: 'Azad Jammu & Kashmir', lat: 34.3700, lon: 73.4600 },
  { name: 'Gilgit',      province: 'Gilgit-Baltistan', lat: 35.9173, lon: 74.2425 },
  { name: 'Skardu',      province: 'Gilgit-Baltistan', lat: 35.3100, lon: 75.5500 },
];

/** Normalise a district name (strip province suffix, lowercase). */
export function districtKey(name) {
  return String(name || '').toLowerCase().trim();
}

/** Pure case-insensitive district search. */
export function findDistricts(query) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return DISTRICTS.slice();
  return DISTRICTS.filter((d) => districtKey(d.name).includes(q) || districtKey(d.province).includes(q));
}

/** Resolve a district name → { name, lat, lon, province } (offline, pure). */
export function resolveDistrict(name) {
  const key = districtKey(name);
  const match = DISTRICTS.find((d) => districtKey(d.name) === key);
  return match || null;
}

/**
 * Best-effort live reverse-geocode of a lat/lon to a district name.
 * Uses the Open-Meteo geocoding (no API key).  Returns null on failure so
 * the caller keeps the raw coordinates.
 */
export async function reverseGeocode(lat, lon) {
  try {
    const url = 'https://nominatim.openstreetmap.org/reverse'
      + `?format=json&lat=${lat}&lon=${lon}&zoom=6&addressdetails=1&email=kisanai@local`;
    const res = await fetch(url, { headers: { 'User-Agent': 'kisanai/1.0' }, signal: AbortSignal.timeout(6000) });
    if (!res.ok) return null;
    const data = await res.json();
    const addr = data.address || {};
    return (addr.city || addr.town || addr.county || addr.state || null) || null;
  } catch (e) {
    return null;
  }
}

/**
 * Get the device's current location via Capacitor Geolocation.
 * Returns { lat, lon, accuracy, source:'gps' } | null.
 * Null on permission denial / GPS unavailable so the UI can open the picker.
 */
export async function getCurrentLocation() {
  const Geolocation = await getGeolocation();
  if (!Geolocation) {
    // Web / Node: no GPS bridge — fall back to the district picker.
    return null;
  }
  try {
    const pos = await Geolocation.getCurrentPosition({
      enableHighAccuracy: true,
      timeoutMs: 10000,
      // Android permission rationale handled natively via Capacitor's
      // permission request flow; we never hard-code a city when denied.
    });
    // Some Capacitor versions resolve coords under `coords`, some flat.
    const coords = pos && pos.coords ? pos.coords : pos;
    if (!coords || coords.latitude == null || coords.longitude == null) return null;
    return {
      lat: coords.latitude,
      lon: coords.longitude,
      accuracy: coords.accuracy,
      source: 'gps',
    };
  } catch (e) {
    // PERMISSION_DENIED / TIMEOUT / NOT_ENABLED → caller opens the picker.
    return null;
  }
}

export const LOCATION_DEFAULT = { name: 'Lahore', lat: 31.5497, lon: 74.3437, province: 'Punjab' };

export default { DISTRICTS, PROVINCES, findDistricts, resolveDistrict, reverseGeocode, getCurrentLocation, LOCATION_DEFAULT };
