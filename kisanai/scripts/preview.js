/**
 * scripts/preview.js — serve www/ in-process and fetch each asset, so we can
 * "see how it looks" from Node (loopback is sandboxed cross-process in this
 * env, but in-process http works). Prints a render preview of the Home screen
 * using the REAL pure-logic functions (rainConfidence / mapConnectivity /
 * buildDecisionCard) so the output matches what the Android WebView renders.
 */
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { T, setLocale } from '../www/i18n.js';
import {
  rainConfidence, rainConfidenceForDays, localSkill,
  mapConnectivity, buildDecisionCard, normalizeOmDay,
} from '../www/src/features/home.js';
import CONFIG from '../www/config.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WWW = path.resolve(__dirname, '..', 'www');
const TYPE = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript',
  '.json': 'application/json', '.png': 'image/png', '.ttf': 'font/ttf',
  '.ico': 'image/x-icon', '.svg': 'image/svg+xml',
};

const ASSETS = [
  'index.html', 'styles.css', 'app.js', 'i18n.js', 'config.js',
  'sw.js', 'manifest.json', 'src/features/home.js', 'src/api.js',
  'src/ui.js', 'src/caps.js', 'assets/icons/icon-192.png',
];

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = urlPath === '/' ? 'index.html' : urlPath.replace(/^\//, '');
  const fp = path.join(WWW, file);
  if (!fs.existsSync(fp) || fs.statSync(fp).isDirectory()) { res.statusCode = 404; return res.end('not found'); }
  res.setHeader('Content-Type', TYPE[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(fp).pipe(res);
});

server.listen(0, '127.0.0.1', async () => {
  const { port } = server.address();
  const base = `http://127.0.0.1:${port}`;

  console.log('================ PWA ASSEMBLY PROBE ================');
  console.log(`Serving www/ on ${base}\n`);
  let ok = 0;
  for (const a of ASSETS) {
    await new Promise((resolve) => {
      http.get(base + '/' + a, (r) => {
        let len = 0; r.on('data', (c) => len += c.length);
        r.on('end', () => {
          const mark = r.statusCode === 200 ? 'OK ' : `!! ${r.statusCode}`;
          if (r.statusCode === 200) ok += 1;
          console.log(`${mark}  ${a.padEnd(28)} ${len} bytes`);
          resolve();
        });
      }).on('error', () => { console.log('!!  (error) ' + a); resolve(); });
    });
  }
  console.log(`\n${ok}/${ASSETS.length} assets served (HTTP 200)\n`);

  // ---- Home screen render preview (uses the REAL pure functions) ----
  console.log('================ HOME SCREEN PREVIEW (EN) ================');
  const conn = mapConnectivity('CACHE', '[SIMULATED DATA | CACHE, 3.2h stale]', 3.2);
  console.log(`[connectivity badge]  ${conn.state}  ·  −${conn.penaltyPct}%  ·  ${conn.subtitle}`);

  const days = [
    normalizeOmDay('2025-04-14', 26, 0, 45, 5, 0),   // moderate
    normalizeOmDay('2025-04-15', 24, 0, 20, 6, 61),   // low + wmo rain
    normalizeOmDay('2025-04-16', 22, 0, 10, 7, 0),
    normalizeOmDay('2025-04-17', 20, 5, 55, 9, 80),   // high rain
    normalizeOmDay('2025-04-18', 18, 0, 15, 8, 0),
  ];
  const fc = rainConfidenceForDays(days);
  const card = buildDecisionCard(fc.today, days[0], null);
  console.log(`[decision card]       "${card.title}"`);
  console.log(`                      ${card.body}`);
  console.log(`                      action: ${card.action}  (urgency: ${card.urgency})`);
  console.log(`[today rain]          ${Math.round(fc.today.confidence * 100)}% — ${fc.today.label} — rainExpected=${fc.today.rainExpected}`);

  console.log('\n[5-day strip]');
  for (let i = 0; i < fc.days.length; i++) {
    const d = fc.days[i];
    const bar = '#'.repeat(Math.round((d.rainConfidence.confidence || 0) * 20));
    console.log(`  ${d.date}  H${d.high}°  pop=${Math.round((d.rainConfidence.confidence || 0) * 100)}%  [${bar.padEnd(20)}] ${d.rainConfidence.rainExpected ? '☔' : '☀'}`);
  }

  // ---- Urdu preview ----
  setLocale('ur');
  console.log('\n================ HOME SCREEN PREVIEW (UR / RTL) ================');
  const connUr = mapConnectivity('OFFLINE', '[… | OFFLINE — weather-dependent sections with withdrawn]', 0);
  console.log(`[نقص ربط]              ${connUr.state}  ·  −${connUr.penaltyPct}%  ·  ${connUr.subtitle}`);
  console.log(`[عنوان]               "${T('home.title')}"`);
  console.log(`[فیصلہ]               ${card.action}`);
  setLocale('en');

  console.log('\n================ API CONFIG ================');
  console.log(`API_BASE_URL : ${CONFIG.API_BASE_URL}`);
  console.log(`VISION       : ${CONFIG.VISION_ENDPOINT}`);
  console.log(`TIMEOUT      : ${CONFIG.REQUEST_TIMEOUT_MS}ms  retries=${CONFIG.RETRY_ATTEMPTS}  backoff=${CONFIG.RETRY_BACKOFF_BASE_MS}ms`);
  console.log(`MAX_IMAGE_PX : ${CONFIG.MAX_IMAGE_PX}  MAX_BYTES: ${CONFIG.MAX_IMAGE_BYTES}`);

  server.close();
  console.log('\nPreview complete.');
}
);
