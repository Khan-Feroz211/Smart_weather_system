/**
 * KisanAI — Offline-first hash router (src/router.js).
 *
 * A dependency-free router that keys on `window.location.hash` so it works
 * inside the Capacitor WebView, a plain browser, and `http-server` — and,
 * crucially, needs NO server-side routing or bundler.  On `hashchange` (and
 * on first load) it renders the matched route into `rootEl`.
 *
 * Routes are declarative:  { '#home': renderHome, '#scan': renderScan, ... }
 * Each render fn is `(ctx) => Promise<void> | void` where ctx gives it the
 * shared singletons (api, auth, storage, etc.) and a `render()` re-entry helper.
 *
 * Import-safe in Node (no Capacitor; only guards `window`).
 */

const DEFAULT_ROUTE = '#home';

export class Router {
  /**
   * @param {Object} ctx  shared singletons { api, auth, storage, camera, ui, i18n, render }
   * @param {HTMLElement} rootEl
   */
  constructor(ctx, rootEl) {
    this.ctx = ctx || {};
    this.rootEl = rootEl || (typeof document !== 'undefined' ? document.getElementById('app') : null);
    this.routes = {};
    this._teardown = null;
  }

  add(route, handler) {
    if (typeof route === 'object' && route !== null) {
      Object.assign(this.routes, route);
    } else {
      this.routes[String(route)] = handler;
    }
    return this;
  }

  match(hash) {
    const h = (hash || '').split('?')[0];
    if (this.routes[h]) return [h, this.routes[h]];
    // parametric: #field/:id
    for (const key of Object.keys(this.routes)) {
      if (key.includes(':')) {
        const pattern = key.replace(/:[^/]+/g, '([^/]+)');
        const re = new RegExp('^' + pattern + '$');
        const m = re.exec(h);
        if (m) return [key, this.routes[key], m.slice(1)];
      }
    }
    return [null, null];
  }

  async render(hash = null) {
    if (typeof window === 'undefined') return;
    const current = hash || (window.location.hash || DEFAULT_ROUTE);
    const [matched, handler, params] = this.match(current);
    if (!handler) {
      // unknown route → fall back to home
      window.location.hash = DEFAULT_ROUTE;
      return;
    }
    this.ctx.params = params || [];
    if (this.rootEl) {
      this.rootEl.classList.add('route-transition');
      try {
        await handler(this.ctx);
      } catch (e) {
        console.error('[router] route render error:', e);
        this._renderError(e);
      }
      this.rootEl.classList.remove('route-transition');
    }
  }

  _renderError(err) {
    if (!this.rootEl) return;
    this.rootEl.innerHTML = `<section class="view error-view"><h2>{{error}}</h2><p>${String(err && err.message ? err.message : err)}</p></section>`;
    if (window.KisanAI18n) window.KisanAI18n.applyTranslations(this.rootEl);
  }

  start(initialHash = null) {
    if (typeof window === 'undefined') return this;
    this._teardown && this._teardown();
    window.addEventListener('hashchange', () => this.render());
    this.render(initialHash);
    return this;
  }

  stop() {
    window.removeEventListener('hashchange', () => this.render());
    this._teardown = null;
  }

  static nav(hash) {
    if (typeof window !== 'undefined') window.location.hash = hash;
  }
}

export const navigate = (hash) => Router.nav(hash);
export default Router;
