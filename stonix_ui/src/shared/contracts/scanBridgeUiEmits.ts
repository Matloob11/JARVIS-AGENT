/**
 * Read-only scan of bridge Python for emit_ui / _emit_ui / per-sid init_state & session_expired.
 * Cursor lane may read src/core; it must not edit those files.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
/** stonix_ui/src/shared/contracts → repo root */
const REPO_ROOT = resolve(HERE, '../../../..');

const BRIDGE_FILES = [
  join(REPO_ROOT, 'src/core/ui_bridge.py'),
  join(REPO_ROOT, 'src/core/bridge_desktop.py'),
  join(REPO_ROOT, 'src/core/bridge_rooms.py'),
] as const;

/** Known constant → event name (emit_ui(PLAN_PROGRESS_EVENT, …)). */
const KNOWN_EMIT_CONSTANTS: Record<string, string> = {
  PLAN_PROGRESS_EVENT: 'plan_progress',
};

function collectPyStrings(text: string): string[] {
  const out = new Set<string>();

  // emit_ui("evt" / _emit_ui("evt" — room 'ui' only (not sio.emit to agent)
  const direct = /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1/g;
  let m: RegExpExecArray | null;
  while ((m = direct.exec(text))) out.add(m[2]!);

  // Ternary both arms: emit_ui("new_message" if … else "update_message", …)
  const ternary =
    /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1\s+if\b[\s\S]*?\belse\s+(['"])([a-zA-Z0-9_]+)\3/g;
  while ((m = ternary.exec(text))) {
    out.add(m[2]!);
    out.add(m[4]!);
  }

  // emit_ui(CONST, …)
  const byConst = /(?:emit_ui|_emit_ui)\(\s*([A-Z][A-Z0-9_]*)\s*,/g;
  while ((m = byConst.exec(text))) {
    const mapped = KNOWN_EMIT_CONSTANTS[m[1]!];
    if (mapped) out.add(mapped);
  }

  // Per-sid: sio.emit('init_state' / "session_expired"
  const perSid = /sio\.emit\(\s*(['"])(init_state|session_expired)\1/g;
  while ((m = perSid.exec(text))) out.add(m[2]!);

  return [...out].sort();
}

export function scanBridgeUiEmits(): string[] {
  const out = new Set<string>();
  for (const file of BRIDGE_FILES) {
    if (!existsSync(file)) continue;
    for (const name of collectPyStrings(readFileSync(file, 'utf8'))) out.add(name);
  }
  return [...out].sort();
}

function walkTsFiles(dir: string, acc: string[]): void {
  if (!existsSync(dir)) return;
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === 'node_modules' || ent.name === 'dist') continue;
      walkTsFiles(p, acc);
    } else if (/\.(ts|tsx)$/.test(ent.name) && !ent.name.endsWith('.test.ts') && !ent.name.endsWith('.test.tsx')) {
      acc.push(p);
    }
  }
}

/** Live scan of useSocketEvent('…') / subscribeSocketEvent('…') under stonix_ui/src. */
export function scanSubscribedSocketEventsFromSrc(): string[] {
  const srcRoot = resolve(HERE, '../..');
  const files: string[] = [];
  walkTsFiles(srcRoot, files);
  const out = new Set<string>();
  const re = /(?:useSocketEvent|subscribeSocketEvent)\(\s*(['"])([a-zA-Z0-9_]+)\1/g;
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) out.add(m[2]!);
  }
  return [...out].sort();
}

export { BRIDGE_FILES, REPO_ROOT };
