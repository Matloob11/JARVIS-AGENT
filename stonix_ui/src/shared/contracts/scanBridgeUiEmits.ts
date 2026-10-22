/**
 * Read-only scan of bridge Python for emit_ui / _emit_ui / UI-bound sio.emit.
 * Cursor lane may read src/core; it must not edit those files.
 * Fail-closed: missing files, empty scan, or unresolved UPPER_CASE constants throw.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '../../../..');
const CORE_DIR = join(REPO_ROOT, 'src/core');

/** Known constant → event name when assigned in another file or not locally. */
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

function collectPyStrings(text: string, fileLabel: string): string[] {
  const out = new Set<string>();
  const consts = resolveAssignConstants(text);
  const unresolved = new Set<string>();

  const direct = /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1/g;
  let m: RegExpExecArray | null;
  while ((m = direct.exec(text))) out.add(m[2]!);

  // Ternary both arms — may span a newline; bounded to 160 chars.
  const ternary =
    /(?:emit_ui|_emit_ui)\(\s*(['"])([a-zA-Z0-9_]+)\1\s+if\b[\s\S]{0,160}?\belse\s+(['"])([a-zA-Z0-9_]+)\3/g;
  while ((m = ternary.exec(text))) {
    out.add(m[2]!);
    out.add(m[4]!);
  }

  const byConst = /(?:emit_ui|_emit_ui)\(\s*([A-Z][A-Z0-9_]*)\s*,/g;
  while ((m = byConst.exec(text))) {
    const name = m[1]!;
    const mapped = consts[name];
    if (mapped) out.add(mapped);
    else unresolved.add(name);
  }

  // Every sio.emit literal that targets UI room/sid (or init_state / session_expired).
  const sioAny = /sio\.emit\(\s*(['"])([a-zA-Z0-9_]+)\1([\s\S]{0,200}?)\)/g;
  while ((m = sioAny.exec(text))) {
    const name = m[2]!;
    const tail = m[3] ?? '';
    const toUi =
      /\b(?:to|room)\s*=\s*ROOM_UI\b/.test(tail) ||
      /\bto\s*=\s*sid\b/.test(tail) ||
      name === 'init_state' ||
      name === 'session_expired';
    if (!toUi) continue;
    if (name === 'agent_command' || name === 'agent_vision_frame') continue;
    out.add(name);
  }

  // sio.emit(CONST, …) to UI
  const sioConst =
    /sio\.emit\(\s*([A-Z][A-Z0-9_]*)\s*,([\s\S]{0,200}?)\)/g;
  while ((m = sioConst.exec(text))) {
    const cname = m[1]!;
    const tail = m[2] ?? '';
    const toUi =
      /\b(?:to|room)\s*=\s*ROOM_UI\b/.test(tail) || /\bto\s*=\s*sid\b/.test(tail);
    if (!toUi) continue;
    const mapped = consts[cname];
    if (mapped) out.add(mapped);
    else unresolved.add(cname);
  }

  if (unresolved.size) {
    throw new Error(
      `scanBridgeUiEmits: unresolved emit constants in ${fileLabel}: ${[...unresolved].sort().join(', ')}`,
    );
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
    for (const name of collectPyStrings(readFileSync(file, 'utf8'), file)) out.add(name);
  }
  const list = [...out].sort();
  if (list.length === 0) {
    throw new Error('scanBridgeUiEmits: scan returned no UI emit events (fail-closed)');
  }
  if (!list.includes('reminder_due')) {
    throw new Error('scanBridgeUiEmits: expected reminder_due among bridge UI emits');
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

export function scanSubscribedSocketEventsFromSrc(): string[] {
  const srcRoot = resolve(HERE, '../..');
  const files: string[] = [];
  walkTsFiles(srcRoot, files);
  const out = new Set<string>();
  const re = /(?:useSocketEvent|subscribeSocketEvent)\(\s*['"]([a-zA-Z0-9_]+)['"]/g;
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) out.add(m[1]!);
  }
  return [...out].sort();
}
