/**
 * KisanAI — Internationalisation (i18n) module.
 *
 * Offline-first: every translation is embedded in this single file so the app
 * works with NO network fetch of locale bundles. Two locales are shipped:
 *   - "en" (English)    — LTR
 *   - "ur" (Urdu)       — RTL  (Noto NastaliqTypeface loaded via CSS @font-face)
 *
 * Usage (browser & Node):
 *   import { T, setLocale, getLocale, isRTL, applyTranslations } from './i18n.js';
 *   T('home.title')            // => "Weather & Crops"
 *   setLocale('ur')            // switches to Urdu, re-renders [data-i18n] nodes
 *
 * The `T()` function is a pure lookup — it is unit-tested in scripts/tests.mjs
 * alongside rainConfidence()/localSkill().
 */

/* ======================================================================
 * Translation tables — flat dotted keys.
 * EN is the source of truth; UR is its mirror. Any key missing from UR
 * transparently falls back to EN so the UI never shows a raw key.
 * ==================================================================== */
export const STRINGS = {
  en: {
    // ---- app chrome ----
    'app.name': 'KisanAI',
    'app.tagline': 'Offline-first weather & crop advisory for smallholder farmers',
    'app.switch_language': 'اردو',
    'app.connecting': 'Connecting…',
    'app.offline_mode': 'Offline mode',

    // ---- connectivity badge ----
    'conn.online': 'Live',
    'conn.online.subtitle': 'Fresh telemetry received',
    'conn.cache': 'Cached',
    'conn.cache.subtitle': '{days}h stale — data may be out of date',
    'conn.offline': 'Offline',
    'conn.offline.subtitle': 'No connection — using cached rules',
    'conn.penalty': 'Confidence reduced by {pct}%',

    // ---- home / F1 ----
    'home.title': 'Weather & Crops',
    'home.location_default': 'Your location',
    'home.rain_confidence': 'Rain confidence',
    'home.local_skill': 'Local skill',
    'home.local_skill.subtitle': 'Field rules vs model agreement',
    'home.decision_card': "Today's action",
    'home.no_rain': 'No rain expected — no irrigation needed',
    'home.rain_expected': 'Rain expected — delay irrigation',
    'home.irrigate_now': 'Irrigate now',
    'home.check_crops': 'Check crops for stress',
    'home.stale_data': 'Weather data is stale',
    'home.openmeteo_fallback': 'Showing Open-Meteo satellite data (free, no key required)',
    'home.day': 'Day',
    'home.high': 'High',
    'home.low': 'Low',
    'home.pop': 'POP',
    'home.empty': 'No forecast available yet',

    // ---- location / F2 ----
    'location.title': 'Select location',
    'location.gps': 'Use my location',
    'location.search': 'Search district…',
    'location.permission_denied': 'Location permission denied',
    'location.permission_denied.subtitle': 'Choose a district from the list instead',
    'location.no_gps': 'GPS unavailable — please select a district',
    'location.saved': 'Location saved',

    // ---- crop scan / F3 ----
    'scan.title': 'Scan crop',
    'scan.camera': 'Take photo',
    'scan.gallery': 'Choose from gallery',
    'scan.hint': 'Place the diseased leaf in the frame',
    'scan.processing': 'Analysing…',
    'scan.not_sure': 'Not sure — retake the photo',
    'scan.not_sure.subtitle': 'Confidence {pct}% — try better lighting and focus',
    'scan.no_disease': 'No disease detected — plant looks healthy',
    'scan.no_doses': 'No specific dose recommended',
    'scan.retry': 'Retake photo',
    'scan.disease': 'Disease',
    'scan.confidence': 'Confidence',
    'scan.urgency': 'Urgency',
    'scan.treatment': 'Treatment',
    'scan.explanation': 'Why',
    'scan.evidence': 'Evidence',
    'scan.suggested_next': 'Next question',

    // ---- symptom checker / F4 ----
    'symptoms.title': 'Symptom checker',
    'symptoms.crop': 'Crop',
    'symptoms.stage': 'Growth stage',
    'symptoms.report': 'Report symptoms',
    'symptoms.none': 'No symptoms selected',
    'symptoms.analyse': 'Analyse',
    'symptoms.offline': 'Offline — using cached rules',
    'symptoms.diagnosis': 'Diagnosis',

    // ---- fields & soil / F5 ----
    'fields.title': 'My fields',
    'fields.add': 'Add field',
    'fields.name': 'Field name',
    'fields.area': 'Area (acres)',
    'fields.crop': 'Crop grown',
    'fields.soil_test': 'Soil test',
    'fields.ph': 'pH',
    'fields.nitrogen': 'Nitrogen (ppm)',
    'fields.phosphorus': 'Phosphorus (ppm)',
    'fields.potassium': 'Potassium (ppm)',
    'fields.organic_matter': 'Organic matter (%)',
    'fields.texture': 'Texture',
    'fields.save': 'Save',
    'fields.cancel': 'Cancel',
    'fields.delete': 'Delete',
    'fields.no_fields': 'No fields added yet',
    'fields.soil_recommendation': 'Soil recommendation',

    // ---- rain feedback / F6 ----
    'rain.title': 'Rain feedback',
    'rain.ask': 'Did it rain on {date}?',
    'rain.yes': 'Yes, it rained',
    'rain.no': 'No rain',
    'rain.skipped': 'Skipped — will ask again tomorrow',
    'rain.queued': 'Feedback queued (offline) — will sync when online',
    'rain.synced': '{n} feedback items synced',

    // ---- alerts / F7 ----
    'alerts.title': 'Alerts',
    'alerts.daily_prompt': "Tap for today's hazard alert",
    'alerts.no_alerts': 'No active alerts — all clear',
    'alerts.severity.high': 'High',
    'alerts.severity.medium': 'Medium',
    'alerts.severity.low': 'Low',
    'alerts.deduped': 'Duplicate alert suppressed ({n})',
    'alerts.open': 'Open',

    // ---- offline / F8 ----
    'offline.data_age': 'Data age',
    'offline.hours': '{n} hours ago',
    'offline.days': '{n} days ago',
    'offline.sw_cache': 'Data cached for offline use',

    // ---- settings ----
    'settings.title': 'Settings',
    'settings.language': 'Language',
    'settings.theme': 'Theme',
    'settings.tts': 'Voice guidance (Urdu)',
    'settings.notifications': 'Notifications',
    'settings.debug': 'Debug mode',

    // ---- common ----
    'cancel': 'Cancel',
    'save': 'Save',
    'delete': 'Delete',
    'ok': 'OK',
    'retry': 'Retry',
    'close': 'Close',
    'loading': 'Loading…',
    'error': 'Error',
    'try_again': 'Try again',
    'no_data': 'No data',
  },
  ur: {
    // ---- app chrome ----
    'app.name': 'کیسان AI',
    'app.tagline': 'آف لائن پہلے مطلوبہ موسم اور فصل کے مشورے',
    'app.switch_language': 'English',
    'app.connecting': 'جڑ رہا ہے…',
    'app.offline_mode': 'آف لائن موڈ',

    // ---- connectivity badge ----
    'conn.online': 'لائیو',
    'conn.online.subtitle': 'تازہ ڈیٹا موصول',
    'conn.cache': 'کیشڈ',
    'conn.cache.subtitle': '{days} گھنٹے پرانا — ڈیٹا تبدیل ہوسکتا ہے',
    'conn.offline': 'آف لائن',
    'conn.offline.subtitle': 'کوئی کنکشن نہیں — قوانین استعمال ہو رہے ہیں',
    'conn.penalty': 'اعتماد {pct}% کم ہو گیا',

    // ---- home / F1 ----
    'home.title': 'موسم اور فصلیں',
    'home.location_default': 'آپ کا مقام',
    'home.rain_confidence': 'بارش کا اعتماد',
    'home.local_skill': ' مقامی صلاحیت',
    'home.local_skill.subtitle': 'فیلڈ قوانین بذرس مڈل کے ساتھ مطابقت',
    'home.decision_card': 'آج کا عمل',
    'home.no_rain': 'بارش کی ایک بھی امکان نہیں — بلا دے مانی ضرورت نہیں',
    'home.rain_expected': 'بارش کی امکان ہے — بال دے مانی تلک نہیں',
    'home.irrigate_now': 'اب بلایاں',
    'home.check_crops': 'فصلوں کی جانچ کریں',
    'home.stale_data': 'موسم کا ڈیٹا پرانا ہے',
    'home.openmeteo_fallback': 'اوپن میٹیو کی سیٹیپ مخاطرات کی نمائش (مفت، کوئی کلید کی ضرورت نہیں)',
    'home.day': 'دن',
    'home.high': 'اعلیٰ',
    'home.low': 'کم',
    'home.pop': 'بارش کا امکان',
    'home.empty': 'ابھی کوئی پیشکش دستیاب نہیں',

    // ---- location / F2 ----
    'location.title': 'مقام منتخب کریں',
    'location.gps': 'میری جگہ استعمال کریں',
    'location.search': 'ضلع تلاش کریں…',
    'location.permission_denied': 'مقام کی اجازت مسترد ہوئی',
    'location.permission_denied.subtitle': 'بجائے اس کے فہرست سے ایک ضلع منتخب کریں',
    'location.no_gps': 'GPS دستیاب نہیں — براہ کرم ایک ضلع منتخب کریں',
    'location.saved': 'مقام محفوظ ہو گیا',

    // ---- crop scan / F3 ----
    'scan.title': 'فصل کی جانچ',
    'scan.camera': 'تصویر لیں',
    'scan.gallery': 'گیلری سے منتخب کریں',
    'scan.hint': 'بیمار پتے کو فریم میں رکھیں',
    'scan.processing': 'تجزیہ جات…',
    'scan.not_sure': 'یقین نہیں — تصویر دوبارہ لیں',
    'scan.not_sure.subtitle': 'اعتماد {pct}% — بہتر روشنی اور فوکس کی کوشش کریں',
    'scan.no_disease': 'کوئی بیماری نہیں — پودا صحت مند ہے',
    'scan.no_doses': 'کوئی خاص دوز نہیں',
    'scan.retry': 'تصویر دوبارہ لیں',
    'scan.disease': 'بیماری',
    'scan.confidence': 'اعتماد',
    'scan.urgency': 'فوریت',
    'scan.treatment': 'علاج',
    'scan.explanation': 'کیوں',
    'scan.evidence': 'ثبوت',
    'scan.suggested_next': 'اگلا سوال',

    // ---- symptom checker / F4 ----
    'symptoms.title': 'علامات چیکر',
    'symptoms.crop': 'فصل',
    'symptoms.stage': 'روایتی مرحلہ',
    'symptoms.report': 'علامات بھیجیں',
    'symptoms.none': 'کوئی علامات منتخب نہیں',
    'symptoms.analyse': 'جانچیں',
    'symptoms.offline': 'آف لائن — محفوظ قوانین استعمال ہو رہے ہیں',
    'symptoms.diagnosis': 'نشخ',

    // ---- fields & soil / F5 ----
    'fields.title': 'میرے کھیت',
    'fields.add': 'کھیت شامل کریں',
    'fields.name': 'کھیت کا نام',
    'fields.area': 'رقبہ (ایکڑ)',
    'fields.crop': 'لگائی گئی فصل',
    'fields.soil_test': 'مٹی کا ٹیسٹ',
    'fields.ph': 'پی ایچ',
    'fields.nitrogen': 'نائٹروجین (ppm)',
    'fields.phosphorus': 'فسفرس (ppm)',
    'fields.potassium': 'پوسفیلیم (ppm)',
    'fields.organic_matter': 'عضوی مادہ (%)',
    'fields.texture': 'سطح',
    'fields.save': 'محفوظ کریں',
    'fields.cancel': 'منسول کریں',
    'fields.delete': 'حذف کریں',
    'fields.no_fields': 'ابھی تک کوئی کھیت نہیں شامل',
    'fields.soil_recommendation': 'مٹی کا تجویزہ',

    // ---- rain feedback / F6 ----
    'rain.title': 'بارش کی فیڈ بیک',
    'rain.ask': '{date} کو بارش ہوئی؟',
    'rain.yes': 'جی ہاں، بارش ہوئی',
    'rain.no': 'بارش نہیں ہوئی',
    'rain.skipped': 'طلاء — کل پھر پوچھیں گے',
    'rain.queued': 'فیڈ بیک قیول کیو — اوپ لائن سنک ہوگا',
    'rain.synced': '{n} فیڈ بیک آئٹم سنک ہو گئے',

    // ---- alerts / F7 ----
    'alerts.title': 'انتباہات',
    'alerts.daily_prompt': 'آج کے حفاظتی انتباہ کے لئے ٹیپ کریں',
    'alerts.no_alerts': 'کوئی فعال انتباہ نہیں — سب محفوظ ہے',
    'alerts.severity.high': 'اعلیٰ',
    'alerts.severity.medium': 'درمیانہ',
    'alerts.severity.low': 'کم',
    'alerts.deduped': 'ایک ہی انتباہ کو دہراؤ ({n})',
    'alerts.open': 'کھیڈیں',

    // ---- offline / F8 ----
    'offline.data_age': 'ڈیٹا کی عمر',
    'offline.hours': '{n} گھنٹے پہلے',
    'offline.days': '{n} دنوں پہلے',
    'offline.sw_cache': 'آف لائن استعمال کے لئے ڈیٹا کیشڈ ہے',

    // ---- settings ----
    'settings.title': 'ترتیبات',
    'settings.language': ' زبان',
    'settings.theme': 'تھیم',
    'settings.tts': 'صوتی رہنمائی (اردو)',
    'settings.notifications': 'نوٹیفکیشن',
    'settings.debug': 'ڈیبگ موڈ',

    // ---- common ----
    'cancel': 'منسوخ',
    'save': 'محفوظ',
    'delete': 'حذف',
    'ok': 'ٹھیک ہے',
    'retry': 'دوبارہ کوشش',
    'close': 'بند',
    'loading': 'لوڈ ہو رہا ہے…',
    'error': 'خرابی',
    'try_again': 'دوبارہ کوشش',
    'no_data': 'کوئی ڈیٹا نہیں',
  },
};

/* ======================================================================
 * State
 * ==================================================================== */
export const RTL_LOCALES = { en: false, ur: true };

let locale = 'en';
if (typeof localStorage !== 'undefined') {
  const saved = localStorage.getItem('kisanai-locale');
  if (saved && Object.prototype.hasOwnProperty.call(STRINGS, saved)) locale = saved;
}

const observers = new Set();

function hasLocale(loc) {
  return Object.prototype.hasOwnProperty.call(STRINGS, loc) && typeof STRINGS[loc] === 'object';
}
function ensureLocale(loc) {
  return hasLocale(loc) ? loc : 'en';
}

/* ======================================================================
 * T — pure string lookup with {placeholder} interpolation.
 * ==================================================================== */
export function interpolate(str, params) {
  if (!params) return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => {
    const v = params[k];
    return v === undefined || v === null ? m : String(v);
  });
}

export function T(key, params) {
  if (typeof key !== 'string') return String(key === undefined ? '' : key);
  const bundle = STRINGS[locale] || STRINGS.en;
  let val = Object.prototype.hasOwnProperty.call(bundle, key)
    ? bundle[key]
    : (STRINGS.en[key] !== undefined ? STRINGS.en[key] : key);
  return interpolate(val, params);
}

export function getLocale() {
  return locale;
}

export function isRTL(loc) {
  return !!RTL_LOCALES[ensureLocale(loc === undefined ? locale : loc)];
}

export function setLocale(next) {
  const target = ensureLocale(next);
  if (target === locale) return false;
  locale = target;
  try { if (typeof localStorage !== 'undefined') localStorage.setItem('kisanai-locale', target); } catch (e) { /* ssr */ }
  applyTranslations();
  return true;
}

export function applyTranslations(root) {
  root = root || (typeof document !== 'undefined' ? document.documentElement : null);
  if (!root) return;
  root.lang = locale;
  root.dir = isRTL() ? 'rtl' : 'ltr';
  root.querySelectorAll('[data-i18n]').forEach((el) => {
    const val = T(el.getAttribute('data-i18n'));
    const attr = el.getAttribute('data-i18n-attr');
    if (attr) {
      el.setAttribute(attr, val);
    } else {
      el.textContent = val;
    }
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', T(el.getAttribute('data-i18n-placeholder')));
  });
  root.querySelectorAll('[data-i18n-lang]').forEach((el) => {
    el.textContent = T('app.switch_language');
  });
  if (typeof document !== 'undefined') {
    document.dispatchEvent(new CustomEvent('kisanai:locale-changed', { detail: { locale, isRTL: isRTL() } }));
  }
  for (const fn of observers) { try { fn(locale); } catch (e) { /* noop */ } }
}

export function observeLocale(fn) {
  observers.add(fn);
  return () => observers.delete(fn);
}

// Auto-apply on DOMContentLoaded in the browser.
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => applyTranslations());
  } else {
    applyTranslations();
  }
}

const api = { T, interpolate, getLocale, isRTL, setLocale, applyTranslations, observeLocale, STRINGS };
export default api;
