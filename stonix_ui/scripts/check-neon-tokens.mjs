/**
 * UI-H10: neon CSS must not repeat a tokens.css hex, and outer glow
 * stays on active, hover, focus, or status selectors.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const styles = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/assets/styles');
const tokenText = fs.readFileSync(path.join(styles, 'tokens.css'), 'utf8');
const rootOnly = tokenText.split("html[data-persona='anna']")[0];

function expand(hex) {
  const value = hex.toLowerCase();
  if (value.length === 4) {
    return `#${value[1]}${value[1]}${value[2]}${value[2]}${value[3]}${value[3]}`;
  }
  return value;
}

const tokenHex = new Set();
for (const match of rootOnly.matchAll(/(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\b/g)) {
  const hex = match[2];
  if (hex.length !== 4 && hex.length !== 7) continue;
  tokenHex.add(expand(hex));
}

const active = /active|hover|focus|pressed|aria-pressed|aria-current|data-s|data-tone|\.k-ok|\.k-info|\.k-warn|\.k-err|\.k-device|is-on|is-live|:checked|dot-online|nav-dot|n-flow--/i;
const blockRe = /([^{}]+)\{([^{}]*)\}/gs;
const hexRe = /#[0-9a-fA-F]{3,8}\b/g;
const glowRe = /(?<!inset\s)0\s+0\s+([0-9.]+)px|var\(--glow-|rgba\(var\(--acc-glow\)/g;

const problems = [];
const neonDir = path.join(styles, 'neon');
for (const name of fs.readdirSync(neonDir).filter((file) => file.endsWith('.css'))) {
  const text = fs.readFileSync(path.join(neonDir, name), 'utf8');
  let match;
  while ((match = blockRe.exec(text))) {
    const selector = match[1];
    const body = match[2];
    if (!selector.includes('data-persona')) {
      for (const hex of body.match(hexRe) || []) {
        if ((hex.length === 4 || hex.length === 7) && tokenHex.has(expand(hex))) {
          problems.push(`${name}: raw ${hex} duplicates a token in ${selector.trim().slice(0, 80)}`);
        }
      }
    }
    if (active.test(selector)) continue;
    const shadow = body.match(/box-shadow\s*:\s*([^;}]+)/);
    if (!shadow) continue;
    glowRe.lastIndex = 0;
    const glow = glowRe.exec(shadow[1]);
    if (!glow) continue;
    const blur = glow[1] === undefined ? 8 : Number(glow[1]);
    if (blur >= 5) {
      problems.push(`${name}: idle glow on ${selector.trim().slice(0, 100)}`);
    }
  }
}

if (problems.length) {
  console.error(problems.slice(0, 40).join('\n'));
  console.error(`UI-H10 check failed (${problems.length})`);
  process.exit(1);
}
console.log(`UI-H10 tokens ok (${tokenHex.size} token colours, neon glow limited to active states)`);
