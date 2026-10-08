/**
 * scripts/lint.js — cross-platform `node --check` linter.
 *
 * Runs `node --check` on every .js/.mjs under www/ and scripts/.
 * (Replaces the cmd.exe-only `for %F in (...)` from the original lint:full.)
 *
 * Exit code 1 if any file fails to parse.
 */
import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // skip node_modules, assets (fonts/icons may be binary)
      if (entry.name === 'node_modules' || entry.name === 'assets') continue;
      out.push(...walk(full));
    } else if (entry.name.endsWith('.js') || entry.name.endsWith('.mjs')) {
      out.push(full);
    }
  }
  return out;
}

const files = [...walk(path.join(ROOT, 'www')), ...walk(path.join(ROOT, 'scripts'))];
let bad = 0;
for (const f of files) {
  try {
    execFileSync('node', ['--check', f], { stdio: 'pipe' });
    console.log('ok   ' + path.relative(ROOT, f));
  } catch (e) {
    console.error('FAIL ' + path.relative(ROOT, f));
    console.error(e.stderr?.toString().split('\n').slice(0, 3).join('\n'));
    bad += 1;
  }
}
console.log(`\n${files.length - bad}/${files.length} files clean`);
process.exit(bad ? 1 : 0);
