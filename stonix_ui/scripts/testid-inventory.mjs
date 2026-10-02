#!/usr/bin/env node
/**
 * Minimal testid inventory (X-74 stub until X-69 full baseline).
 * Lists data-testid literals under src/; --check exits 0 when core voice ids exist.
 */
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const srcDir = path.join(root, 'src');

const REQUIRED = [
  'agent-audio-visualizer',
  'voice-status',
  'voice-toggle',
  'assistant-agent-listening',
  'enable-audio',
  'voice-wake-agent',
  'voice-take-over',
];

async function walk(dir, out = []) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const ent of entries) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) await walk(full, out);
    else if (/\.(tsx?|jsx?)$/.test(ent.name)) out.push(full);
  }
  return out;
}

const files = await walk(srcDir);
const ids = new Set();
const re = /data-testid=["']([^"']+)["']/g;
for (const file of files) {
  const text = await readFile(file, 'utf8');
  let m;
  while ((m = re.exec(text))) ids.add(m[1]);
}

const sorted = [...ids].sort();
console.log(`Found ${sorted.length} data-testid literals in stonix_ui/src`);

const check = process.argv.includes('--check');
if (!check) {
  for (const id of sorted) console.log(id);
  process.exit(0);
}

const missing = REQUIRED.filter(id => !ids.has(id));
if (missing.length) {
  console.error('Missing required testids:', missing.join(', '));
  process.exit(1);
}
console.log('testid inventory check passed (voice core ids present)');
