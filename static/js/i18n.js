(function () {
    'use strict';

    const state = { locale: localStorage.getItem('sws-locale') || 'en', strings: {} };

    function translate(key) {
        // Fast path: direct flat key (supports dotted keys like "map.wind_particles")
        if (Object.prototype.hasOwnProperty.call(state.strings, key)) {
            return state.strings[key];
        }
        // Fallback: nested path resolution ("map.wind_loading" -> strings.map.wind_loading)
        const parts = key.split('.');
        let current = state.strings;
        for (let i = 0; i < parts.length; i++) {
            if (current && typeof current === 'object' && parts[i] in current) {
                current = current[parts[i]];
            } else {
                return key;
            }
        }
        return typeof current === 'string' ? current : key;
    }

    function applyTranslations() {
        document.documentElement.lang = state.locale;
        document.documentElement.dir = state.locale === 'ur' ? 'rtl' : 'ltr';
        document.querySelectorAll('[data-i18n]').forEach((element) => {
            const value = translate(element.dataset.i18n);
            if (element.dataset.i18nAttr) {
                element.setAttribute(element.dataset.i18nAttr, value);
            } else {
                element.textContent = value;
            }
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
            element.setAttribute('placeholder', translate(element.dataset.i18nPlaceholder));
        });
        document.querySelectorAll('[data-i18n-lang]').forEach((element) => {
            element.textContent = translate('switch_language');
        });
        document.dispatchEvent(new CustomEvent('sws:locale-changed', {
            detail: { locale: state.locale, translate }
        }));
    }

    async function load(locale) {
        const response = await fetch(`/static/i18n/${locale}.json`, { cache: 'no-cache' });
        if (!response.ok) throw new Error(`Translation load failed: ${response.status}`);
        state.strings = await response.json();
        applyTranslations();
    }

    async function setLocale(locale) {
        const next = locale === 'ur' ? 'ur' : 'en';
        try {
            await load(next);
            state.locale = next;
            localStorage.setItem('sws-locale', next);
            applyTranslations();
        } catch (error) {
            console.error('Unable to switch language', error);
        }
    }

    window.SWSI18n = {
        get locale() { return state.locale; },
        t: translate,
        setLocale
    };

    document.addEventListener('DOMContentLoaded', async () => {
        document.querySelectorAll('[data-i18n-lang]').forEach((button) => {
            button.addEventListener('click', () => setLocale(state.locale === 'ur' ? 'en' : 'ur'));
        });
        await load(state.locale);
    });
})();
