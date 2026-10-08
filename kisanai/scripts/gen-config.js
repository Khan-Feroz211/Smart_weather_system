/**
 * scripts/gen-config.js
 *
 * Build-time config injection for KisanAI.
 *
 * Reads environment variables and writes a single inline statement into
 * www/config.js (appended as window.__KISANAI_CONFIG__). This keeps API URLs
 * OUT of source control / source code — they are injected per-environment at
 * build time, satisfying the "base URL comes from build-time config" rule.
 *
 * Usage:
 *   npm run gen:config                       # one-shot, uses current env
 *   npm run gen:config -- -w                 # watch mode (dev): re-write on .env changes
 *
 * Env:
 *   KISANAI_API_BASE_URL, KISANAI_VISION_ENDPOINT,
 *   KISANAI_AGRI_API_KEY, KISANAI_ENV
 */
const fs = require('fs');
const path = require('path');

const WWW = path.resolve(__dirname, '..', 'www');
const CONFIG_FILE = path.join(WWW, 'config.js');
const ENV_FILE = path.resolve(__dirname, '..', '.env');

function loadEnv() {
  // Load a local .env (if present) — never required, never committed.
  if (fs.existsSync(ENV_FILE)) {
    const text = fs.readFileSync(ENV_FILE, 'utf8');
    for (const line of text.split('\n')) {
      const m = line.match(/^([A-Z0-9_]+)\s*=\s*(.*)$/);
      if (m) {
        // Do not overwrite existing real env vars.
        if (process.env[m[1]] === undefined) process.env[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
      }
    }
  }
}

function buildConfigObj() {
  const apiBase = (process.env.KISANAI_API_BASE_URL || '').trim();
  const vision = (process.env.KISANAI_VISION_ENDPOINT || '').trim();
  const envName = (process.env.KISANAI_ENV || 'production').trim();
  const apiKey = (process.env.KISANAI_AGRI_API_KEY || '').trim();
  const debug = process.env.KISANAI_DEBUG === 'true' || process.env.NODE_ENV === 'development';

  const obj = {};
  if (apiBase) obj.API_BASE_URL = apiBase;
  if (vision) obj.VISION_ENDPOINT = vision;
  if (envName) obj.ENV = envName;
  if (apiKey) obj.AGRI_API_KEY = apiKey;
  obj.DEBUG = debug;
  return obj;
}

function injectLine(cfg) {
  const json = JSON.stringify(cfg, null, 2);
  // One self-contained statement appended to config.js runtime.
  return `\n// --- BEGIN build-time config (auto-generated, do not edit) ---\n` +
    `window.__KISANAI_CONFIG__ = window.__KISANAI_CONFIG__ || ${JSON.stringify(cfg)};\n` +
    `// --- END build-time config ---\n`;
}

function writeConfig() {
  loadEnv();
  const cfg = buildConfigObj();
  const injected = injectLine(cfg);

  let existing = '';
  if (fs.existsSync(CONFIG_FILE)) {
    existing = fs.readFileSync(CONFIG_FILE, 'utf8');
    // Strip any previously injected block so we don't accumulate duplicates.
    existing = existing.replace(/\/\/ --- BEGIN build-time config[\s\S]*?\/\/ --- END build-time config ---\n/, '');
  }
  fs.writeFileSync(CONFIG_FILE, existing + injected, 'utf8');
  console.log(`[gen-config] wrote ${CONFIG_FILE} with:`, JSON.stringify(cfg));
}

function main() {
  const watch = process.argv.includes('-w');
  writeConfig();
  if (watch) {
    let timer = null;
    fs.watch(ENV_FILE, { persistent: false }, () => {
      clearTimeout(timer);
      timer = setTimeout(() => { try { writeConfig(); } catch (e) { console.error(e); } }, 300);
    });
    console.log('[gen-config] watching .env ...');
  }
}

main();
