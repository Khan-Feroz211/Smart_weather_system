/**
 * KisanAI — UI primitives (src/ui.js).
 *
 * UX requirements (kisanai/TODO.md §"UX"):
 *   - RTL logical CSS (dir flips automatically; styles.css uses logical props)
 *   - 48dp minimum touch targets (48 * density dpi)
 *   - Noto Nastaliq font + line-height for Urdu
 *   - TTS voice guidance (Urdu) via Web Speech API
 *   - back button (Android hardware back) → history.pop() / close
 *   - rotation / orientation handling
 *   - skeleton placeholders while data loads
 *
 * Pure, testable helpers:
 *   - dp(n, density)        — convert dp → px at a screen density
 *   - is48dpTarget(wPx,hPx) — does a px box clear 48dp at density?
 *   - spineSkeleton(n)      — returns an array of n placeholder {w,h} for SSR/test
 */

export const DP48_PX = { width: 48, height: 48 }; // base 160dpi reference

/** dp → px at a screen density.  density = dpi / 160 (so 1.0 == 160dpi). */
export function dp(n, density = 1) {
  return Math.ceil(Number(n) * (Number(density) || 1));
}
/** dp → px given raw dpi (Android formula: px = dp * dpi / 160). */
export function dpToPx(n, dpi = 160) {
  return Math.ceil((Number(n) * Number(dpi)) / 160);
}

/** True if a px box clears 48dp at the given density (width or height). */
export function is48dpTarget(widthPx, heightPx, density = 1) {
  const minPx = dpToPx(48, (Number(density) || 1) * 160);
  return (Number(widthPx) || 0) >= minPx || (Number(heightPx) || 0) >= minPx;
}
/** Grow a px box to at least minDp (48dp) at the given density. */
export function ensureTouchTarget(widthPx, heightPx, density = 1, minDp = 48) {
  const minPx = dpToPx(minDp, (Number(density) || 1) * 160);
  return { width: Math.max(widthPx, minPx), height: Math.max(heightPx, minPx) };
}

/** Spine-style skeleton placeholders: array of {w,h,key} for rendering. */
export function spineSkeleton(n, width = 100, height = 12, gap = 8) {
  const arr = [];
  for (let i = 0; i < n; i++) arr.push({ key: `skel-${i}`, w: width, h: height, gap });
  return arr;
}

/**
 * TTS (Web Speech API).  Prefers an Urdu voice when locale='ur'.
 * Returns a promise that resolves when speech ends.
 */
export function speak(text, locale = 'en') {
  return new Promise((resolve, reject) => {
    if (typeof window === 'undefined' || !window.speechSynthesis) return resolve(false);
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = locale === 'ur' ? 'ur-PK' : 'en-US';
    const voices = window.speechSynthesis.getVoices
      ? window.speechSynthesis.getVoices()
      : [];
    if (locale === 'ur') {
      const ur = voices.find((v) => (v.lang || '').startsWith('ur'));
      if (ur) utter.voice = ur;
    }
    utter.onend = () => resolve(true);
    utter.onerror = () => resolve(false);
    window.speechSynthesis.speak(utter);
  });
}

/** Apply a logical-rtl tweak + font class to the document root. */
export function applyRtl(isRtl) {
  if (typeof document !== 'undefined') {
    document.documentElement.dir = isRtl ? 'rtl' : 'ltr';
    document.documentElement.classList.toggle('font-nasto', isRtl);
  }
}

/** Skeleton placeholder DOM builder (used while fetching home / diagnosis). */
export function buildSkeleton(container, lines = 3, className = 'skeleton') {
  if (!container || typeof document === 'undefined') return;
  container.innerHTML = '';
  for (let i = 0; i < lines; i++) {
    const div = document.createElement('div');
    div.className = className;
    div.style.width = i === lines - 1 ? '60%' : '100%';
    div.setAttribute('aria-label', 'loading');
    container.appendChild(div);
  }
}

/**
 * Register the Android hardware back-button.  Default Capacitor behaviour
 * already pops the WebView history stack (which matches our hash router), so
 * `onBack` is an OPTIONAL hook for the app to intercept (e.g. close a modal).
 * When the hook returns false we let the default history.pop() happen.
 */
export function registerBackHandler(onBack) {
  if (typeof window === 'undefined') return () => {};
  const handler = (e) => {
    const handled = onBack ? onBack() : false;
    if (!handled && typeof history !== 'undefined') {
      try { history.back(); } catch (err) { /* noop */ }
    } else if (handled) {
      e && e.preventDefault && e.preventDefault();
    }
  };
  window.addEventListener('hashchange', handler);
  // Best-effort: also hook Capacitor's hardware backButton for a cleaner
  // Android experience (requires @capacitor/app, imported lazily so the
  // module still loads in a plain browser / Node tests).
  (async () => {
    try {
      const mod = await import('@capacitor/app');
      if (mod.App && mod.App.addListener) mod.App.addListener('backButton', handler);
    } catch (e) { /* @capacitor/app not installed → hash history handles it */ }
  })();
  return () => window.removeEventListener('hashchange', handler);
}


/** Re-render on orientation change (rotation).  Returns unsubscribe fn. */
export function onOrientationChange(fn) {
  if (typeof window === 'undefined') return () => {};
  const handler = () => { fn({
    angle: screen.orientation?.angle ?? (window.orientation || 0),
    type: screen.orientation?.type || 'landscape',
  }); };
  window.addEventListener('orientationchange', handler);
  window.addEventListener('resize', handler);
  return () => { window.removeEventListener('orientationchange', handler); window.removeEventListener('resize', handler); };
}

export default {
  dp, dpToPx, ensureTouchTarget, is48dpTarget,
  spineSkeleton, buildSkeleton, speak, applyRtl,
  registerBackHandler, onOrientationChange,
};
