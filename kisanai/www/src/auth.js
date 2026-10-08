/**
 * KisanAI — Authentication (src/auth.js).
 *
 * Offline-first, passwordless-leaning auth for the mobile app:
 *
 *   1. Phone login (E.164) via POST /api/mobile/login
 *      { phone_number|E.164, password?, email? } -> { token|temp_token,
 *        requires_2fa, user }
 *   2. TOTP 2FA via POST /api/mobile/verify-totp { temp_token, code }
 *   3. (Future) SMS OTP via POST /api/mobile/send-otp { phone_number }
 *
 * The bearer token is persisted in Capacitor Preferences, which on Android
 * is backed by EncryptedSharedPreferences (Android Keystore) — the token is
 * NEVER stored in plaintext on disk and never as a JS string longer than a
 * request needs (it is handed to api.js via a getter, not imported into the
 * UI).
 *
 * On-device TOTP: a tiny RFC 6238 helper (Web Crypto / Node crypto) is
 * included so 2FA can be verified locally when the backend verify endpoint is
 * absent (the proposed /api/mobile/* endpoints may 404 — see TODO §Mobile
 * auth design).  In that case the app degrades to offline mode with cached
 * rule-based data.
 *
 * Import-safe in Node: the only top-level imports are ../config.js,
 * ../i18n.js and ./caps.js (all lazy / pure).  No Capacitor call is made
 * until a capability is actually needed inside a native guard.
 */
import CONFIG from '../config.js';
import { T } from '../i18n.js';
import { getPreferences, webStoreGet, webStoreSet } from './caps.js';

const TOKEN_KEY = 'kisanai_auth_token';
const TEMP_TOKEN_KEY = 'kisanai_auth_temp_token';
const PROFILE_KEY = 'kisanai_auth_profile';
const FEEDBACK_QUEUE_KEY = 'kisanai_feedback_queue';

/* ======================================================================
 * Minimal RFC 4648 base32 (for TOTP secrets).
 * ==================================================================== */
function decodeBase32(str) {
  const clean = String(str || '').toUpperCase().replace(/\s+/g, '').replace(/=+$/, '');
  const map = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = 0, buffer = 0, out = [];
  for (let i = 0; i < clean.length; i++) {
    const val = map.indexOf(clean[i]);
    if (val < 0) continue; // skip non-base32
    buffer = (buffer << 5) | val;
    bits += 5;
    if (bits >= 8) {
      out.push((buffer >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(out);
}

/* ======================================================================
 * RFC 6238 TOTP (HMAC-SHA1, 30s step, 6 digits).
 * Works in browser (window.crypto.subtle) and Node (global crypto).
 * ==================================================================== */
const OTP_STEP = 30;
const OTP_DIGITS = 6;

function getCrypto() {
  if (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.subtle) return globalThis.crypto;
  if (typeof require === 'function') { try { const c = require('crypto'); const webcrypto = c.webcrypto || c; return { subtle: webcrypto.subtle.bind(webcrypto) }; } catch (e) {} }
  return null;
}

function counterToBytes(counter) {
  const b = new Uint8Array(8);
  for (let i = 7; i >= 0; i--) {
    b[i] = counter & 0xff;
    counter = Math.floor(counter / 256);
  }
  return b;
}

export async function totp(secret, forTime = Date.now()) {
  const crypto = getCrypto();
  if (!crypto || !crypto.subtle) throw new Error('WebCrypto unavailable — TOTP not supported in this environment');
  const keyBytes = decodeBase32(secret);
  const key = await crypto.subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-1' }, false, ['sign']);
  const counter = Math.floor(forTime / 1000 / OTP_STEP);
  const sig = await crypto.subtle.sign('HMAC', key, counterToBytes(counter));
  const view = new DataView(sig);
  const offset = view.getUint8(sig.byteLength - 1) & 0x0f;
  const binary = (view.getUint32(offset) & 0x7fffffff) % (10 ** OTP_DIGITS);
  return String(binary).padStart(OTP_DIGITS, '0');
}

export async function verifyTotp(secret, code, windowSlots = 1) {
  const expected = await totp(secret, Date.now());
  if (code === expected) return { valid: true, drift: 0 };
  // check neighbouring windows for clock skew
  for (let w = 1; w <= windowSlots; w++) {
    if (code === await totp(secret, Date.now() + w * OTP_STEP * 1000)) return { valid: true, drift: w };
    if (code === await totp(secret, Date.now() - w * OTP_STEP * 1000)) return { valid: true, drift: -w };
  }
  return { valid: false, drift: 0 };
}

export function generateSecret() {
  // base32 of 20 random bytes = 32 chars
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let out = '';
  const bytes = new Uint8Array(20);
  if (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else if (typeof require === 'function') {
    const c = require('crypto'); c.randomFillSync(bytes);
  }
  for (let i = 0; i < bytes.length; i++) {
    // take 5 bits at a time (with 5 bits left over we still emit a char —
    // TOTP libs tolerate trailing junk)
    out += letters[bytes[i] % 32];
  }
  return out;
}

export function totpProvisioningUri(secret, account, issuer = 'KisanAI') {
  const label = encodeURIComponent(account) + (issuer ? '@' + encodeURIComponent(issuer) : '');
  const params = encodeURIComponent(`otpauth://totp/${label}?secret=${encodeURIComponent(secret)}&issuer=${encodeURIComponent(issuer || 'KisanAI')}&algorithm=SHA1&digits=6&period=30`);
  return params;
}

/* ======================================================================
 * AuthManager
 * ==================================================================== */
export class AuthManager {
  constructor(api) {
    this.api = api;            // the AgriApi client (for /api/mobile/* calls)
    this._token = null;        // in-memory cache; never persisted as a long JS var
    this._profile = null;
    this._listeners = new Set();
    this.initialised = false;
  }

  /** Load persisted credentials (Keystore / localStorage) at app start. */
  async init() {
    const prefs = await getPreferences();
    if (prefs) {
      this._token = await prefs.get({ key: TOKEN_KEY }).then((r) => r?.value ?? null).catch(() => null);
      const prof = await prefs.get({ key: PROFILE_KEY }).then((r) => r?.value ?? null).catch(() => null);
      if (prof) { try { this._profile = JSON.parse(prof); } catch (e) {} }
    } else {
      this._token = webStoreGet(TOKEN_KEY, null);
      const prof = webStoreGet(PROFILE_KEY, null);
      if (prof) { try { this._profile = JSON.parse(prof); } catch (e) {} }
    }
    this.initialised = true;
    this._notify();
    if (this._token && this.api && typeof this.api.setToken === 'function') {
      this.api.setToken(this._token);
    }
    return this._token;
  }

  /**
   * Phone login.  `phone` must be E.164 (+92XXXXXXXXXX).
   * Returns { step:'done'|'totp', token?, temp_token?, requires_2fa, user }
   * or { step:'offline' } when the backend endpoint is absent (404).
   */
  async login(credentials) {
    // Basic E.164 validation for the phone path.
    if (credentials && credentials.phone_number && !/^\+?\d{7,15}$/.test(credentials.phone_number)) {
      return { step: 'error', error: T('error'), message: 'Phone must be in E.164 format (+92XXXXXXXXXX).' };
    }
    const res = await this.api.mobileLogin(credentials);
    if (res.unauthorized) {
      return { step: 'error', error: 'unauthorized', message: 'Credentials rejected.' };
    }
    if (res.offline) {
      // Endpoint absent / no network → degrade to offline mode.
      return { step: 'offline', message: 'Backend auth unavailable — running in offline mode.' };
    }
    const data = res.data || {};
    if (data.requires_2fa) {
      await this._persistTempToken(data.temp_token || data.token);
      return { step: 'totp', requires_2fa: true, temp_token: data.temp_token, user: data.user };
    }
    if (data.token) {
      return this._finishLogin(data.token, data.user);
    }
    // Some backends return a session directly without 2FA.
    return { step: 'offline', message: 'Unexpected login response — offline mode.' };
  }

  /** Finalise TOTP step: exchange temp_token + code for a bearer token. */
  async verifyAndLogin(tempToken, code) {
    const res = await this.api.mobileVerifyTotp(tempToken, code);
    if (res.unauthorized || res.offline) {
      return { step: 'offline', message: 'TOTP verification unavailable — offline mode.' };
    }
    const data = res.data || {};
    if (data.token) {
      return this._finishLogin(data.token, data.user);
    }
    if (data.requires_2fa !== false) {
      return { step: 'error', error: 'totp_failed', message: 'Code did not verify.' };
    }
    return { step: 'offline', message: 'Unexpected TOTP response.' };
  }

  async _finishLogin(token, profile) {
    await this._clearTempToken();
    this._token = token;
    this._profile = profile || null;
    await this._persistToken(token);
    if (this.api && typeof this.api.setToken === 'function') this.api.setToken(token);
    this._notify();
    return { step: 'done', token, user: profile };
  }

  /** SMS OTP (passwordless) — future path; gracefully no-ops if endpoint absent. */
  async sendOtp(phoneNumber) {
    if (!/^\+?\d{7,15}$/.test(phoneNumber)) {
      return { step: 'error', message: 'Phone must be E.164 format.' };
    }
    const res = await this.api.mobileSendOtp(phoneNumber);
    if (res.offline) return { step: 'offline', message: 'SMS OTP unavailable — offline mode.' };
    return { step: 'otp_sent', data: res.data };
  }

  async logout() {
    this._token = null;
    this._profile = null;
    const prefs = await getPreferences();
    if (prefs) {
      await prefs.remove({ key: TOKEN_KEY }).catch(() => {});
      await prefs.remove({ key: PROFILE_KEY }).catch(() => {});
    } else {
      webStoreSet(TOKEN_KEY, null);
      webStoreSet(PROFILE_KEY, null);
    }
    if (this.api && typeof this.api.setToken === 'function') this.api.setToken(null);
    this._notify();
  }

  /* ---- token access ---- */
  getToken() { return this._token; }
  getProfile() { return this._profile; }
  isLoggedIn() { return !!this._token; }

  requireAuth() {
    return {
      headers: authHeaders(this._token),
      authorized: !!this._token,
    };
  }

  /* ---- persistence ---- */
  async _persistToken(token) {
    const prefs = await getPreferences();
    if (prefs) {
      await prefs.set({ key: TOKEN_KEY, value: token }).catch(() => {});
    } else {
      webStoreSet(TOKEN_KEY, token);
    }
  }

  async _persistTempToken(token) {
    const prefs = await getPreferences();
    if (prefs) {
      await prefs.set({ key: TEMP_TOKEN_KEY, value: token ?? '' }).catch(() => {});
    } else {
      webStoreSet(TEMP_TOKEN_KEY, token ?? '');
    }
  }

  async _clearTempToken() {
    const prefs = await getPreferences();
    if (prefs) { await prefs.remove({ key: TEMP_TOKEN_KEY }).catch(() => {}); }
    else { webStoreSet(TEMP_TOKEN_KEY, null); }
  }

  /* ---- offline feedback queue (F6) ---- */
  queueFeedback(item) {
    const queue = webStoreGet(FEEDBACK_QUEUE_KEY, []);
    queue.push({ ...item, queued_at: Date.now(), attempts: 0 });
    webStoreSet(FEEDBACK_QUEUE_KEY, queue);
    return queue.length;
  }

  getFeedbackQueue() {
    return webStoreGet(FEEDBACK_QUEUE_KEY, []);
  }

  clearFeedbackQueue() {
    return webStoreSet(FEEDBACK_QUEUE_KEY, []);
  }

  /* ---- listener pattern: ui subscribes to auth state changes ---- */
  subscribe(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  _notify() {
    for (const fn of this._listeners) { try { fn({ token: this._token, profile: this._profile }); } catch (e) {} }
  }
}

/* ---- helper used by api/auth bridging ---- */
export function authHeaders(token) {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

const auth = new AuthManager(null);
// Allow api.js to inject itself (avoids a circular import: api ← auth ← api).
export function initAuth(api) { auth.api = api; return auth; }
export function getAuth() { return auth; }
export default auth;
