import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import {
  normalizeConfirm,
  normalizeMessage,
  normalizePlan,
  normalizePlanProgress,
  normalizeSettings,
  normalizeTasks,
  toEpochMs,
} from '../../src/shared/contracts/events';

const FIXTURES_DIR = join(import.meta.dirname, '../mock-bridge/fixtures');

const EVENT_NORMALIZER: Record<string, (raw: unknown) => unknown> = {
  new_message: normalizeMessage,
  update_message: normalizeMessage,
  new_message_user: normalizeMessage,
  transcription_update: normalizeMessage,
  settings_sync: normalizeSettings,
  confirm_request: normalizeConfirm,
  confirm_resolved: normalizeConfirm,
  reasoning_update: normalizePlan,
  plan_progress: normalizePlanProgress,
  plan_progress_update: normalizePlanProgress,
  task_sync: normalizeTasks,
  task_notification: raw => normalizeTasks({ tasks: [raw] }),
};

function normalizeCmdPayload(type: string | undefined, payload: unknown): void {
  switch (type) {
    case 'settings_update':
      normalizeSettings(payload);
      break;
    case 'chat':
      normalizeMessage(payload);
      break;
    case 'confirm_response':
      normalizeConfirm(payload);
      break;
    default:
      normalizeMessage(payload);
      break;
  }
}

function normalizeInitState(raw: unknown): void {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const messages = data.messages;
  if (Array.isArray(messages)) {
    for (const row of messages) normalizeMessage(row);
  }
  if (data.settings != null) {
    normalizeSettings({ settings: data.settings });
  }
  normalizeTasks({ tasks: data.tasks ?? [] });
  if (data.tool_logs != null) normalizeMessage(data.tool_logs);
}

function normalizeFixtureBasename(basename: string, raw: unknown): void {
  if (basename === 'init_state') {
    normalizeInitState(raw);
    return;
  }
  if (basename === 'messages_page') {
    const data = (raw && typeof raw === 'object' ? raw : {}) as { messages?: unknown[] };
    for (const row of data.messages ?? []) normalizeMessage(row);
    return;
  }
  if (basename.startsWith('cmd_')) {
    const cmd = (raw && typeof raw === 'object' ? raw : {}) as {
      type?: string;
      payload?: unknown;
    };
    normalizeCmdPayload(cmd.type, cmd.payload ?? raw);
    return;
  }
  const normalizer = EVENT_NORMALIZER[basename];
  if (normalizer) {
    normalizer(raw);
    return;
  }
  normalizeMessage(raw);
  normalizeSettings(raw);
  normalizeConfirm(raw);
  normalizePlan(raw);
  normalizeTasks(raw);
}

test.describe('DASH-UI-EVENTS normalizers', () => {
  test('normalizeMessage maps agent→assistant and seconds→ms', () => {
    const msg = normalizeMessage({
      id: '1',
      role: 'agent',
      text: 'Salam',
      timestamp: 1_700_000_000,
    });
    expect(msg.role).toBe('assistant');
    expect(msg.ts).toBe(1_700_000_000_000);
    expect(toEpochMs(1_700_000_000_000)).toBe(1_700_000_000_000);
  });

  test('normalizeConfirm accepts allow|allowed and by|reason', () => {
    const a = normalizeConfirm({ id: 'c1', allowed: true, by: 'timeout' });
    expect(a.allow).toBe(true);
    expect(a.reason).toBe('timeout');
    const b = normalizeConfirm({ id: 'c2', allow: false, reason: 'denied', ttl_s: 60 });
    expect(b.allow).toBe(false);
    expect(b.ttl_ms).toBe(60_000);
  });

  test('normalizePlan accepts plan_steps objects or string plan', () => {
    const fromSteps = normalizePlan({
      plan_steps: [{ step: 1, action: 'search', description: 'Find mosque' }],
    });
    expect(fromSteps.plan).toEqual(['Find mosque']);
    const fromStrings = normalizePlan({ plan: ['a', 'b'] });
    expect(fromStrings.plan).toEqual(['a', 'b']);
  });

  test('normalizeSettings accepts flat or nested', () => {
    const nested = normalizeSettings({ settings: { voice: 'Aoede' }, applied: true });
    expect(nested.settings.voice).toBe('Aoede');
    expect(nested.applied).toBe(true);
    const flat = normalizeSettings({ voice: 'Aoede', language: 'ur' });
    expect(flat.settings.voice).toBe('Aoede');
  });

  test('normalizeTasks accepts tasks|pending', () => {
    expect(normalizeTasks({ tasks: [{ id: 1 }] }).tasks).toHaveLength(1);
    expect(normalizeTasks({ pending: [{ id: 2 }] }).tasks).toHaveLength(1);
  });

  test('every mock-bridge fixture parses through events.ts normalizers', () => {
    const files = readdirSync(FIXTURES_DIR).filter(name => name.endsWith('.json'));
    expect(files.length).toBeGreaterThan(10);
    for (const file of files) {
      const basename = file.replace(/\.json$/, '');
      const text = readFileSync(join(FIXTURES_DIR, file), 'utf8').replace(/^\uFEFF/, '');
      const raw = JSON.parse(text);
      expect(() => normalizeFixtureBasename(basename, raw)).not.toThrow();
    }
  });
});
