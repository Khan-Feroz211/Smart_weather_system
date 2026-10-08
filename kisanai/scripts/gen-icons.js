/**
 * scripts/gen-icons.js
 *
 * Generates the PWA/Capacitor launcher icons (icon-72/192/512.png) as solid
 * colour PNGs using only Node's built-in zlib — no native deps required.
 * Run:  node scripts/gen-icons.js
 */
import fs from 'fs';
import path from 'path';
import zlib from 'zlib';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ICONS_DIR = path.resolve(__dirname, '..', 'www', 'assets', 'icons');

const SIZES = [
  { file: 'icon-72.png',  size: 72,  color: '#2E7D32' },
  { file: 'icon-192.png', size: 192, color: '#2E7D32' },
  { file: 'icon-512.png', size: 512, color: '#2E7D32' },
];

// CRC-32 (PNG chunk CRC) — standard bitwise tableless implementation.
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[i] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const len = Buffer.from([(data.length >>> 24) & 0xff, (data.length >>> 16) & 0xff, (data.length >>> 8) & 0xff, data.length & 0xff]);
  const crc = crc32(Buffer.concat([typeBuf, data]));
  const crcBuf = Buffer.from([(crc >>> 24) & 0xff, (crc >>> 16) & 0xff, (crc >>> 8) & 0xff, crc & 0xff]);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

function writePng(size, color, outPath) {
  const [r, g, b] = [parseInt(color.slice(1, 3), 16), parseInt(color.slice(3, 5), 16), parseInt(color.slice(5, 7), 16)];
  const w = size; const h = size;
  const rowLen = w * 4 + 1;
  const raw = Buffer.alloc(rowLen * h);
  for (let y = 0; y < h; y++) {
    raw[y * rowLen] = 0; // filter: None
    for (let x = 0; x < w; x++) {
      const i = y * rowLen + 1 + x * 4;
      raw[i] = r; raw[i + 1] = g; raw[i + 2] = b; raw[i + 3] = 255;
    }
  }
  const idat = zlib.deflateSync(raw);
  const ihdr = Buffer.from([
    (w >>> 24) & 0xff, (w >>> 16) & 0xff, (w >>> 8) & 0xff, w & 0xff,
    (h >>> 24) & 0xff, (h >>> 16) & 0xff, (h >>> 8) & 0xff, h & 0xff,
    8, 6, 0, 0, 0,
  ]);
  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', idat),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
  fs.writeFileSync(outPath, png);
}

function main() {
  fs.mkdirSync(ICONS_DIR, { recursive: true });
  for (const { file, size, color } of SIZES) {
    const out = path.join(ICONS_DIR, file);
    writePng(size, color, out);
    const bytes = fs.statSync(out).size;
    const buf = fs.readFileSync(out);
    const ok = buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47 && buf[6] === 0x1a && buf[7] === 0x0a;
    console.log(`[gen-icons] ${file} ${size}x${size} ${bytes}B ${ok ? 'OK' : 'BAD'}`);
  }
}

main();
