import type { Config } from '@capacitor/cli';

/**
 * KisanAI — Capacitor configuration.
 *
 * The web assets live in ./www (framework-free: index.html, styles.css,
 * app.js, i18n.js, src/, assets/, sw.js, manifest.json). There is no
 * bundler / build step — `npm run gen:config` bakes environment values into
 * www/config.js, then `npx cap copy android` copies www into the native
 * Android project.
 *
 * IMPORTANT (size): Min SDK 26, target SDK 34. webDir is the source folder so
 * the APK ships only the files we list — no node_modules.
 */
const config: Config = {
  appId: 'com.kisanai.app',
  appName: 'KisanAI',
  webDir: 'www',
  bundledWebRuntime: false,
  // Live-reload during development (desktop browser or `npx cap copy` to device).
  // In production the config is baked by scripts/gen-config.js with the real
  // HTTPS base URL of the Flask backend.
  server: {
    url: process.env.KISANAI_DEV_URL || 'http://10.0.2.2:8080',
    cleartext: false,
  },
  android: {
    // Min SDK 26 (Android 8.0). Keep APK lean for 2–3 GB devices.
    minSdk: 26,
    targetSdk: 34,
  },
};

export default config;
