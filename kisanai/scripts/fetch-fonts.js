/**
 * scripts/fetch-fonts.js
 *
 * Downloads the Noto Nastaliq Urdu typeface into www/assets/fonts so the app
 * can render Urdu text correctly offline (kisanai/TODO.md §UX requires
 * "Noto Nastaliq font + line-height").
 *
 * Run once at build time (or via `npm run fetch-fonts`):
 *   node scripts/fetch-fonts.js
 *
 * Falls back gracefully: if the network is unavailable the font @font-face in
 * styles.css simply renders with the system font — the app still works.
 */
import fs from 'fs';
import path from 'path';
import https from 'https';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FONTS_DIR = path.resolve(__dirname, '..', 'www', 'assets', 'fonts');

// Candidate TTF sources for Noto Nastaliq (Urdu), tried in order — the first
// 200 wins.  If all are unavailable (offline / renamed repo path) the
// @font-face in styles.css degrades to the system Urdu font (shipped by every
// modern Android device).  See README "fetch-fonts".
const FONT_CANDIDATES = [
  'https://github.com/google/fonts/raw/main/ofl/notonastaliq/notonastaliq-regular.ttf',
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notonastaliq/notonastaliq-regular.ttf',
  'https://github.com/google/fonts/raw/main/ofl/notonastaliq/NotoNastaliq-Regular.ttf',
];
const OUT_FILE = path.join(FONTS_DIR, 'NotoNastaliq-Regular.ttf');

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'kisanai-build' }, timeout: 8000 }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) return download(res.headers.location, dest).then(resolve).catch(reject);
      if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode}`));
      const file = fs.createWriteStream(dest);
      res.pipe(file);
      file.on('finish', () => file.close(() => resolve()));
      file.on('error', (e) => { try { fs.unlinkSync(dest); } catch (e2) {} reject(e); });
    });
    req.on('timeout', () => { req.destroy(new Error('request timed out')); });
    req.on('error', reject);
  });
}

async function main() {
  fs.mkdirSync(FONTS_DIR, { recursive: true });
  if (fs.existsSync(OUT_FILE) && fs.statSync(OUT_FILE).size > 1000) {
    console.log('[fetch-fonts] Noto Nastaliq already present, skipping.');
    return;
  }
  let saved = false;
  for (const url of FONT_CANDIDATES) {
    try {
      console.log('[fetch-fonts] trying', url);
      await download(url, OUT_FILE);
      console.log('[fetch-fonts] saved', OUT_FILE, '(', fs.statSync(OUT_FILE).size, 'bytes )');
      saved = true;
      break;
    } catch (e) {
      console.warn('[fetch-fonts] ', url, '->', e.message);
    }
  }
  if (!saved) {
    console.warn('[fetch-fonts] all sources unavailable — the app will fall back to the system Urdu font. Re-run when online.');
  }
}

main();
