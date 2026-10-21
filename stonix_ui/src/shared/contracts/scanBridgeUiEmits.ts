/**
 * Read-only scan of bridge Python for emit_ui / _emit_ui / UI-bound sio.emit.
 * Cursor lane may read src/core; it must not edit those files.
 * Fail-closed: missing files or empty scan throws (Vitest fails).
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
/** stonix_ui/src/shared/contracts → repo root */
const REPO_ROOT = resolve(HERE, '../../../..');
const CORE_DIR = join(REPO_ROOT, 'src/core');

/** Known constant → event name (emit_ui(PLAN_PROGRESS_EVENT, …)). */
const KNOWN_EMIT_CONSTANTS: Record<string, string> = {
  PLAN_PROGRESS_EVENT: 'plan_progress',
};

function listBridgePyFiles(): string[] {
  const files = [join(CORE_DIR, 'ui_bridge.py')];
  if (!existsSync(CORE_DIR)) {
    throw new Error(`scanBridgeUiEmits: missing ${CORE_DIR}`);
  }
  for (const ent of readdirSync(CORE_DIR, { withFileTypes: true })) {
    if (ent.isFile() && /^bridge_.*\.py$/.test(ent.name)) {
      files.push(join(CORE_DIR, ent.name));
    }
  }
  return files;
}

function resolveAssignConstants(text: string): Record<string, string> {
  const found: Record<string, string> = { ...KNOWN_EMIT_CONSTANTS };
  const re = /^([A-Z][A-Z0-9_]*)\s*=\s*['"]([a-zA-Z0-9_]+)['"]/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    found[m[1]!] = m[2]!;
  }
  return found;
}

function collectPyStrings(text: string): string[] {
  const out = new Set<string>();
  const consts = resolveAssignConstants(text);

  // emit_ui("evt" / _emit_ui("evt"
  const direct = /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1/g;
  let m: RegExpExecArray | null;
  while ((m = direct.exec(text))) out.add(m[2]!);

  // Ternary both arms — bounded (no runaway [\s\S]*): at most 120 chars between if/else.
  const ternary =
    /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1\s+if\b.{0,120}?\belse\s+(['"])([a-zA-Z0-9_]+)\3/g;
  while ((m = ternary.exec(text))) {
    out.add(m[2]!);
    out.add(m[4]!);
  }

  // emit_ui(CONST, …) / _emit_ui(CONST, …
  const byConst = /(?:emit_ui|_emit_ui)\(\s*([A-Z][A-Z0-9_]*)\s*,/g;
  while ((m = byConst.exec(text))) {
    const mapped = consts[m[1]!];
    if (mapped) out.add(mapped);
  }

  // Per-sid UI bootstrap only (never agent_command / agent_vision_frame).
  const perSid = /sio\.emit\(\s*(['"])(init_state|session_expired)\1/g;
  while ((m = perSid.exec(text))) out.add(m[2]!);

  // Explicit room UI target: sio.emit("evt", ..., to=ROOM_UI) / room=ROOM_UI
  const sioRoomUi =
    /sio\.emit\(\s*(['"])([a-zA-Z0-9_]+)\1[\s\S]{0,160}?(?:to|room)\s*=\s*ROOM_UI/g;
  while ((m = sioRoomUi.exec(text))) {
    const name = m[2]!;
    if (name !== 'agent_command' && name !== 'agent_vision_frame') out.add(name);
  }

  return [...out].sort();
}

export function scanBridgeUiEmits(): string[] {
  const files = listBridgePyFiles();
  const missing = files.filter(f => !existsSync(f));
  if (missing.length) {
    throw new Error(`scanBridgeUiEmits: missing bridge files: ${missing.join(', ')}`);
  }
  const out = new Set<string>();
  for (const file of files) {
    for (const name of collectPyStrings(readFileSync(file, 'utf8'))) out.add(name);
  }
  const list = [...out].sort();
  if (list.length === 0) {
    throw new Error('scanBridgeUiEmits: scan returned no UI emit events (fail-closed)');
  }
  return list;
}

function walkTsFiles(dir: string, acc: string[]): void {
  if (!existsSync(dir)) return;
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === 'node_modules' || ent.name === 'dist') continue;
      walkTsFiles(p, acc);
    } else if (
      /\.(ts|tsx)$/.test(ent.name) &&
      !ent.name.endsWith('.test.ts') &&
      !ent.name.endsWith('.test.tsx')
    ) {
      acc.push(p);
    }
  }
}

/** Live scan of useSocketEvent('…') / subscribeSocketEvent('…') under stonix_ui/src. */
export function scanSubscribedSocketEventsFromSrc(): string[] {
  const srcRoot = resolve(HERE, '../..');
  const files: string[] = [];
  walkTsFiles(srcRoot, files);
  if (files.length === 0) {
    throw new Error('scanSubscribedSocketEventsFromSrc: no ts/tsx under stonix_ui/src');
  }
  const out = new Set<string>();
  const re = /(?:useSocketEvent|subscribeSocketEvent)\(\s*(['"])([a-zA-Z0-9_]+)\1/g;
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) out.add(m[2]!);
  }
  const list = [...out].sort();
  if (list.length === 0) {
    throw new Error('scanSubscribedSocketEventsFromSrc: no subscriptions found (fail-closed)');
  }
  return list;
}

export { CORE_DIR, REPO_ROOT };
