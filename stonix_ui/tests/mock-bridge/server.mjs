import http from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Server } from 'socket.io';
import {
  TEST_PASSWORD,
  TEST_RECOVERY,
  verifyTotp,
} from './scenarios.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(__dirname, 'fixtures');
const PORT = Number(process.env.MOCK_BRIDGE_PORT || 5199);
const SESSION_COOKIE = 'mock-session';
const SESSION_TTL_S = 3600;

/** @type {import('socket.io').Socket[]} */
const uiSockets = [];
/** @type {{ ts: number, type?: string, payload?: unknown, raw: unknown }[]} */
const receivedUiCommands = [];
let agentOnline = true;
let agentPaused = false;
let agentRestarting = false;
/** @type {string | null} */
let pendingShutdownConfirmId = null;
let suppressSettingsSync = false;
let ownerSessionActive = false;
/** @type {Record<string, unknown>[]} */
const persistedChatMessages = [];
/** @type {Record<string, unknown>[]} */
const persistedMemories = [];
/** @type {number} */
let visionFrameCount = 0;
let mockDesktopOnline = true;
/** @type {Record<string, unknown> | null} */
let mockDesktopStatus = null;
const REFERENCE_FIXTURE_PATH = join(__dirname, '../visual/reference_fixture.json');

/** @type {Map<string, { count: number, resetAt: number }>} */
const loginRate = new Map();
const RATE_LIMIT_MAX = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

function loadFixture(name) {
  const path = join(FIXTURES_DIR, `${name}.json`);
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** Gemini Live voices + OpenAI-style aliases (mirrors src/core/voices.py). */
const GEMINI_VOICES = [
  'Achernar', 'Achird', 'Algenib', 'Algieba', 'Alnilam', 'Aoede', 'Autonoe', 'Callirrhoe',
  'Charon', 'Despina', 'Enceladus', 'Erinome', 'Fenrir', 'Gacrux', 'Iapetus', 'Kore',
  'Laomedeia', 'Leda', 'Orus', 'Puck', 'Pulcherrima', 'Rasalgethi', 'Sadachbia', 'Sadaltager',
  'Schedar', 'Sulafat', 'Umbriel', 'Vindemiatrix', 'Zephyr', 'Zubenelgenubi',
];
const VOICE_ALIASES = {
  alloy: 'Zephyr', echo: 'Charon', shimmer: 'Aoede', ash: 'Orus', ballad: 'Algieba',
  coral: 'Leda', sage: 'Sadaltager', verse: 'Puck',
};
const PERSONA_DEFAULT_VOICE = { jarvis: 'Charon', anna: 'Aoede' };
const UNSUPPORTED_FIELDS = new Set([
  'language', 'transcript_script', 'quiet_hours', 'confirm_level', 'style', 'emotional_intelligence',
  'voice_id', 'voice_id_threshold', 'voice_id_reject', 'wake_followup_s',
]);
const LANGUAGES = ['auto', 'ur', 'hi', 'en'];

function resolveMockVoice(name) {
  if (typeof name !== 'string') return null;
  const key = name.trim().toLowerCase();
  const byLower = Object.fromEntries(GEMINI_VOICES.map(v => [v.toLowerCase(), v]));
  return byLower[key] || VOICE_ALIASES[key] || null;
}

function voicesHelp() {
  const aliases = Object.entries(VOICE_ALIASES).map(([a, v]) => `${a}=${v}`).join(', ');
  return `Valid voices: ${GEMINI_VOICES.join(', ')} (aliases: ${aliases})`;
}

/** @type {Record<string, unknown>} */
let liveSettings = {};
/** @type {Set<string>} */
let liveSaved = new Set();
let liveUpdatedTs = 0;
let runningTurnMode = 'gated';

function resetLiveSettingsFromFixture() {
  const sync = loadFixture('settings_sync');
  const base = { ...(sync.settings || {}) };
  const savedList = Array.isArray(base.saved) ? base.saved.map(String) : [];
  const updated = typeof base.updated_ts === 'number' ? base.updated_ts : 0;
  delete base.saved;
  delete base.updated_ts;
  if (typeof base.wake_word_active === 'boolean' && base.wake_word == null) {
    base.wake_word = base.wake_word_active;
  }
  liveSettings = base;
  liveSaved = new Set(savedList);
  liveUpdatedTs = updated;
  runningTurnMode = String(liveSettings.turn_mode || 'gated');
}

resetLiveSettingsFromFixture();

function buildSettingsSync(applied = {}, errors = {}) {
  const persona = liveSettings.persona === 'anna' ? 'anna' : 'jarvis';
  const values = {
    ...liveSettings,
    persona,
    wake_word: Boolean(liveSettings.wake_word ?? liveSettings.wake_word_active ?? true),
    wake_word_active: Boolean(liveSettings.wake_word ?? liveSettings.wake_word_active ?? true),
    muted: Boolean(liveSettings.muted),
    voice_id_enrolled: Boolean(liveSettings.voice_id_enrolled),
    updated_ts: liveUpdatedTs,
    saved: [...liveSaved].filter(k => k !== 'saved' && k !== 'updated_ts'),
  };
  const pending =
    String(values.turn_mode) !== runningTurnMode ? ['turn_mode'] : [];
  const gated = runningTurnMode === 'gated';
  return {
    settings: values,
    applied: { ...applied },
    errors: { ...errors },
    pending_restart: pending,
    restart_required: [...pending],
    enforced: {
      wake_word: gated && Boolean(values.wake_word),
      voice_id: gated && Boolean(values.voice_id) && Boolean(values.voice_id_enrolled),
    },
    available_voices: [...GEMINI_VOICES],
    available_languages: [...LANGUAGES],
    guarded_tools: loadFixture('settings_sync').guarded_tools || [],
    ts: Date.now() / 1000,
  };
}

/**
 * Apply a settings_update like the real agent (K-61 v39): validate, set applied/
 * unsupported/pending_restart, update saved + updated_ts, emit wake_word_update when needed.
 */
function applySettingsUpdate(payload, socket) {
  const patch = payload && typeof payload === 'object' ? { ...payload } : {};
  delete patch.saved;
  delete patch.updated_ts;

  /** @type {Record<string, string>} */
  const applied = {};
  /** @type {Record<string, string>} */
  const errors = {};
  let wakeChanged = false;
  const prevWake = Boolean(liveSettings.wake_word ?? liveSettings.wake_word_active ?? true);

  // Persona before voice: persona without voice drops saved voice to persona default.
  if (Object.prototype.hasOwnProperty.call(patch, 'persona')) {
    const raw = String(patch.persona || '').toLowerCase();
    if (raw !== 'jarvis' && raw !== 'anna') {
      errors.persona = "persona must be 'jarvis' or 'anna'";
    } else {
      liveSettings.persona = raw;
      liveSaved.add('persona');
      applied.persona = 'applied';
      if (!Object.prototype.hasOwnProperty.call(patch, 'voice')) {
        liveSettings.voice = PERSONA_DEFAULT_VOICE[raw];
        liveSaved.delete('voice');
      }
    }
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'voice')) {
    const resolved = resolveMockVoice(patch.voice);
    if (!resolved) {
      errors.voice = `Voice '${String(patch.voice).slice(0, 40)}' nahi mili. ${voicesHelp()}`;
    } else {
      liveSettings.voice = resolved;
      liveSaved.add('voice');
      applied.voice = 'applied';
    }
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'wake_word')
      || Object.prototype.hasOwnProperty.call(patch, 'wake_word_active')) {
    const next = Object.prototype.hasOwnProperty.call(patch, 'wake_word')
      ? Boolean(patch.wake_word)
      : Boolean(patch.wake_word_active);
    liveSettings.wake_word = next;
    liveSettings.wake_word_active = next;
    liveSaved.add('wake_word');
    applied.wake_word = 'applied';
    if (next !== prevWake) wakeChanged = true;
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'muted')) {
    liveSettings.muted = Boolean(patch.muted);
    applied.muted = 'applied';
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'speak_typed_replies')) {
    const v = String(patch.speak_typed_replies || '').toLowerCase();
    if (v === 'auto' || v === 'always' || v === 'never') {
      liveSettings.speak_typed_replies = v;
      liveSaved.add('speak_typed_replies');
      applied.speak_typed_replies = 'applied';
    } else {
      errors.speak_typed_replies = 'speak_typed_replies must be auto|always|never';
    }
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'turn_mode')) {
    const v = String(patch.turn_mode || '').toLowerCase();
    if (v === 'fast' || v === 'gated') {
      liveSettings.turn_mode = v;
      liveSaved.add('turn_mode');
      applied.turn_mode = v === runningTurnMode ? 'applied' : 'pending_restart';
    } else {
      errors.turn_mode = "turn_mode must be 'fast' or 'gated'";
    }
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'automation') && patch.automation
      && typeof patch.automation === 'object') {
    liveSettings.automation = {
      ...(liveSettings.automation && typeof liveSettings.automation === 'object'
        ? liveSettings.automation
        : {}),
      ...patch.automation,
    };
    applied.automation = 'applied';
  }

  if (Object.prototype.hasOwnProperty.call(patch, 'vision_mode')) {
    const v = String(patch.vision_mode || '').toLowerCase();
    if (v === 'live' || v === 'on_request') {
      liveSettings.vision_mode = v;
      liveSaved.add('vision_mode');
      applied.vision_mode = 'applied';
    }
  }

  for (const key of Object.keys(patch)) {
    if (['persona', 'voice', 'wake_word', 'wake_word_active', 'muted', 'speak_typed_replies',
      'turn_mode', 'automation', 'vision_mode'].includes(key)) {
      continue;
    }
    if (UNSUPPORTED_FIELDS.has(key)) {
      liveSettings[key] = patch[key];
      liveSaved.add(key);
      applied[key] = 'unsupported';
      continue;
    }
    // Unknown keys: still report unsupported (saved in live for visibility).
    liveSettings[key] = patch[key];
    applied[key] = 'unsupported';
  }

  if (Object.keys(applied).length || Object.keys(errors).length) {
    liveUpdatedTs = Date.now() / 1000;
  }

  const sync = buildSettingsSync(applied, errors);
  if (socket && wakeChanged) {
    socket.emit('wake_word_update', Boolean(liveSettings.wake_word));
  }
  return sync;
}

/** @deprecated use applySettingsUpdate; kept for direct merge in older call sites */
function mergeSettingsSync(_base, payload) {
  return applySettingsUpdate(payload || {}, null);
}

function parseCookies(header) {
  /** @type {Record<string, string>} */
  const out = {};
  if (!header) return out;
  for (const part of header.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq);
    const val = trimmed.slice(eq + 1);
    out[key] = decodeURIComponent(val);
  }
  return out;
}

function hasSession(req) {
  if (!ownerSessionActive) return false;
  const cookies = parseCookies(req.headers.cookie);
  return cookies.jarvis_session === SESSION_COOKIE;
}

function setSessionCookie(res) {
  res.setHeader(
    'Set-Cookie',
    `jarvis_session=${SESSION_COOKIE}; Path=/vortex; HttpOnly; SameSite=Strict`,
  );
}

function clearSessionCookie(res) {
  res.setHeader(
    'Set-Cookie',
    'jarvis_session=; Path=/vortex; HttpOnly; SameSite=Strict; Max-Age=0',
  );
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on('error', reject);
  });
}

function clientIp(req) {
  return req.socket.remoteAddress || 'unknown';
}

function checkLoginRate(ip) {
  const now = Date.now();
  let row = loginRate.get(ip);
  if (!row || now > row.resetAt) {
    row = { count: 0, resetAt: now + RATE_LIMIT_WINDOW_MS };
    loginRate.set(ip, row);
  }
  row.count += 1;
  if (row.count > RATE_LIMIT_MAX) {
    const retryAfter = Math.ceil((row.resetAt - now) / 1000);
    return retryAfter > 0 ? retryAfter : 60;
  }
  return null;
}

function buildInitState() {
  const init = loadFixture('init_state');
  const mergedMemories = [...persistedMemories];
  for (const row of init.memories || []) {
    const id = row?.id != null ? String(row.id) : '';
    if (id && !mergedMemories.some(item => String(item?.id) === id)) {
      mergedMemories.push(row);
    }
  }
  const now = Math.floor(Date.now() / 1000);
  const loc = init.location && typeof init.location === 'object' ? init.location : {};
  const city = typeof loc.city === 'string' && loc.city ? loc.city : 'Karachi';
  const lat = typeof loc.lat === 'number' ? loc.lat : 24.8607;
  const lng = typeof loc.lng === 'number' ? loc.lng : 67.0011;
  return {
    ...init,
    location: { ...loc, city, lat, lng, ts: now - 90 },
    map_focus: {
      ...(init.map_focus && typeof init.map_focus === 'object' ? init.map_focus : {
        query: city,
        display_name: `${city}, Pakistan`,
        lat,
        lng,
        zoom: 12,
      }),
      ts: now - 90,
    },
    messages: [...(init.messages || []), ...persistedChatMessages],
    memories: mergedMemories.slice(0, 50),
  };
}

function rememberMemorySync(payload) {
  if (!payload || typeof payload !== 'object') return;
  const id = payload.id != null ? String(payload.id) : '';
  if (!id) return;
  const without = persistedMemories.filter(row => String(row?.id) !== id);
  persistedMemories.length = 0;
  persistedMemories.push(payload, ...without);
  if (persistedMemories.length > 200) {
    persistedMemories.splice(200);
  }
}

function applyReferenceScenario() {
  /** @type {Record<string, unknown>} */
  let fixture;
  try {
    fixture = JSON.parse(readFileSync(REFERENCE_FIXTURE_PATH, 'utf8'));
  } catch {
    return { ok: false, error: 'reference_fixture_missing' };
  }
  const sock = fixture.socket_events_from_mock;
  if (!sock || typeof sock !== 'object') {
    return { ok: false, error: 'invalid_fixture' };
  }
  const serverTime = Math.floor(Date.parse('2026-10-02T21:42:10+05:00') / 1000);
  agentOnline = true;
  agentPaused = false;
  agentRestarting = false;
  mockDesktopOnline = true;
  persistedChatMessages.length = 0;

  const init = buildInitState();
  const initPayload = {
    ...init,
    agent_status: { ...(init.agent_status || {}), ...(sock.agent_status || {}) },
    desktop_status: sock.desktop_status,
    vitals_pc: {
      cpu: sock.vitals_pc?.cpu ?? 21,
      ram: sock.vitals_pc?.ram ?? 63,
      disk: sock.vitals_pc?.disk ?? 82,
      disk_label: sock.vitals_pc?.disk_label ?? 'Disk (C:)',
      host: sock.vitals_pc?.host ?? 'MATLOOB-PC',
      ts: serverTime,
    },
    server_time: serverTime,
    messages: [],
    confirmations: [],
  };
  emitToUi('init_state', initPayload);
  emitToUi('agent_status', {
    ...loadFixture('agent_status'),
    ...(sock.agent_status || {}),
  });
  if (sock.desktop_status) {
    emitToUi('desktop_status', sock.desktop_status);
  }

  for (const row of sock.messages || []) {
    const base =
      row.role === 'user' ? loadFixture('new_message_user') : loadFixture('new_message');
    const ts = Math.floor(Date.parse(String(row.ts)) / 1000);
    const msg = {
      ...base,
      ...row,
      ts,
      timestamp: ts,
      final: true,
      is_final: true,
    };
    persistedChatMessages.push(msg);
    emitToUi('new_message', msg);
  }

  const cr = sock.confirm_request;
  if (cr) {
    emitToUi('confirm_request', {
      id: cr.id ?? 'ref-confirm-1',
      tool: cr.tool ?? 'run_cmd_command',
      risk: cr.risk ?? 'medium',
      voice_allowed: cr.voice_allowed ?? false,
      summary: cr.summary ?? 'Run this PowerShell command on your laptop?',
      args_preview:
        cr.args_preview ??
        'Get-Process | Sort-Object CPU -Descending |\nSelect-Object -First 5',
      ttl_ms: cr.ttl_ms ?? 600000,
      expires_at: serverTime + Math.floor((cr.ttl_ms ?? 600000) / 1000),
    });
  }

  return { ok: true, server_time: serverTime };
}

function emitToUi(event, payload) {
  if (event === 'memory_sync') {
    rememberMemorySync(payload);
  }
  if (event === 'memory_cleared') {
    persistedMemories.length = 0;
  }
  for (const socket of uiSockets) {
    socket.emit(event, payload);
  }
}

function ackUi(socket, cb, status, reason) {
  if (typeof cb === 'function') {
    cb(reason ? { status, reason } : { status });
  }
}

function handleChat(socket, payload, cb) {
  if (!agentOnline) {
    ackUi(socket, cb, 'agent_offline');
    return;
  }
  ackUi(socket, cb, 'sent');
  const id =
    (payload && typeof payload === 'object' && payload.id) ||
    (typeof payload === 'string' ? randomUUID() : randomUUID());
  const text =
    (payload && typeof payload === 'object' && payload.text) ||
    (typeof payload === 'string' ? payload : '');
  const userTemplate = loadFixture('new_message_user');
  const userMsg = {
    ...userTemplate,
    id: String(id),
    text: String(text),
    ts: Math.floor(Date.now() / 1000),
    timestamp: Math.floor(Date.now() / 1000),
  };
  persistedChatMessages.push(userMsg);
  socket.emit('new_message', userMsg);
  setTimeout(() => {
    const assistantTemplate = loadFixture('new_message');
    const assistantMsg = {
      ...assistantTemplate,
      id: randomUUID(),
      turn_id: String(id),
      ts: Math.floor(Date.now() / 1000),
      timestamp: Math.floor(Date.now() / 1000),
    };
    persistedChatMessages.push(assistantMsg);
    socket.emit('new_message', assistantMsg);
  }, 300);
}

/** @type {ReturnType<typeof setTimeout>[]} */
let voiceEnrollTimers = [];

function clearVoiceEnrollTimers() {
  for (const id of voiceEnrollTimers) clearTimeout(id);
  voiceEnrollTimers = [];
}

function handleVoiceEnroll(socket, payload, cb) {
  if (!agentOnline) {
    ackUi(socket, cb, 'agent_offline');
    return;
  }
  const action = payload?.action;
  ackUi(socket, cb, 'sent');
  if (action === 'cancel') {
    clearVoiceEnrollTimers();
    return;
  }
  if (action === 'start') {
    clearVoiceEnrollTimers();
    const base = loadFixture('voice_enroll_progress');
    const target = payload?.target_seconds ?? base.target_seconds ?? 6;
    [33, 66, 100].forEach((percent, index) => {
      const id = setTimeout(() => {
        const collected = Math.round((target * percent) / 100);
        emitToUi('voice_enroll_progress', {
          ...base,
          percent,
          seconds: collected,
          collected_s: collected,
          target_seconds: target,
          target_s: target,
        });
      }, (index + 1) * 200);
      voiceEnrollTimers.push(id);
    });
    return;
  }
  if (action === 'commit') {
    clearVoiceEnrollTimers();
    const id = setTimeout(() => {
      const result = loadFixture('voice_enroll_result');
      socket.emit('voice_enroll_result', result);
      emitToUi('voice_enroll_result', result);
    }, 150);
    voiceEnrollTimers.push(id);
  }
}

/**
 * @param {import('socket.io').Socket} socket
 * @param {{ type?: string, payload?: unknown }} cmd
 * @param {(res: { status: string, reason?: string }) => void} cb
 */
function handleUiCommand(socket, cmd, cb) {
  receivedUiCommands.push({
    ts: Date.now(),
    type: cmd?.type,
    payload: cmd?.payload,
    raw: cmd,
  });

  const type = cmd?.type;
  const payload = cmd?.payload ?? {};

  if (!type) {
    ackUi(socket, cb, 'rejected', 'missing_type');
    return;
  }

  if (!agentOnline && type !== 'request_state' && type !== 'start') {
    ackUi(socket, cb, 'agent_offline');
    return;
  }

  switch (type) {
    case 'chat':
      handleChat(socket, payload, cb);
      break;
    case 'settings_update': {
      ackUi(socket, cb, 'sent');
      if (!suppressSettingsSync) {
        socket.emit('settings_sync', applySettingsUpdate(payload || {}, socket));
      }
      break;
    }
    case 'confirm_response': {
      ackUi(socket, cb, 'sent');
      const resolved = loadFixture('confirm_resolved');
      const confirmId = payload?.id != null ? String(payload.id) : resolved.id;
      const allow = Boolean(payload?.allow);
      socket.emit('confirm_resolved', {
        ...resolved,
        id: confirmId,
        allow,
      });
      if (allow && pendingShutdownConfirmId && confirmId === pendingShutdownConfirmId) {
        pendingShutdownConfirmId = null;
        agentOnline = false;
        emitToUi('agent_status', {
          ...loadFixture('agent_status'),
          online: false,
          restarting: true,
          voice_ready: false,
          in_room: false,
        });
        setTimeout(() => {
          agentOnline = true;
          emitToUi('agent_status', {
            ...loadFixture('agent_status'),
            online: true,
            restarting: false,
            voice_ready: true,
            in_room: true,
          });
        }, 1200);
      }
      break;
    }
    case 'clear_memory': {
      ackUi(socket, cb, 'sent');
      persistedMemories.length = 0;
      socket.emit('memory_cleared', loadFixture('memory_cleared'));
      break;
    }
    case 'task_cancel': {
      ackUi(socket, cb, 'sent');
      socket.emit('task_sync', loadFixture('task_sync'));
      break;
    }
    case 'voice_enroll':
      handleVoiceEnroll(socket, payload, cb);
      break;
    case 'mute': {
      ackUi(socket, cb, 'sent');
      socket.emit('mute_update', { muted: true });
      break;
    }
    case 'unmute': {
      ackUi(socket, cb, 'sent');
      socket.emit('mute_update', { muted: false });
      break;
    }
    case 'persona_change': {
      ackUi(socket, cb, 'sent');
      const persona = payload === 'anna' || payload?.persona === 'anna' ? 'anna' : 'jarvis';
      socket.emit(
        'settings_sync',
        applySettingsUpdate({ persona }, socket),
      );
      break;
    }
    case 'request_state':
      ackUi(socket, cb, 'sent');
      socket.emit('init_state', buildInitState());
      break;
    case 'shutdown_agent': {
      ackUi(socket, cb, 'sent');
      pendingShutdownConfirmId = `mock-shutdown-${Date.now()}`;
      setTimeout(() => {
        socket.emit('confirm_request', {
          id: pendingShutdownConfirmId,
          tool: 'shutdown_agent',
          summary: 'Restart JARVIS agent session?',
          risk: 'high',
          ttl_ms: 120_000,
        });
      }, 40);
      break;
    }
    case 'wake_word_toggle': {
      ackUi(socket, cb, 'sent');
      const current = Boolean(liveSettings.wake_word ?? liveSettings.wake_word_active ?? true);
      const next =
        typeof payload === 'boolean'
          ? payload
          : payload && typeof payload === 'object' && typeof payload.active === 'boolean'
            ? payload.active
            : !current;
      socket.emit('settings_sync', applySettingsUpdate({ wake_word: next }, socket));
      break;
    }
    case 'stop':
    case 'tasks_refresh':
    case 'feedback':
    case 'sim_lookup':
    case 'open_sim_panel':
    case 'swarm':
    case 'job_cancel':
    case 'open_job_artifact':
    case 'voice_take_over':
      ackUi(socket, cb, 'sent');
      break;
    case 'pause_agent': {
      ackUi(socket, cb, 'sent');
      agentOnline = false;
      agentPaused = true;
      agentRestarting = false;
      emitToUi('agent_status', {
        ...loadFixture('agent_status'),
        online: false,
        paused: true,
        restarting: false,
        in_room: false,
        voice_ready: false,
        since: Math.floor(Date.now() / 1000),
      });
      break;
    }
    case 'start': {
      ackUi(socket, cb, 'sent');
      agentPaused = false;
      agentRestarting = true;
      agentOnline = false;
      emitToUi('agent_status', {
        ...loadFixture('agent_status'),
        online: false,
        paused: false,
        restarting: true,
        in_room: false,
        voice_ready: false,
        since: Math.floor(Date.now() / 1000),
      });
      setTimeout(() => {
        agentOnline = true;
        agentRestarting = false;
        emitToUi('agent_status', {
          ...loadFixture('agent_status'),
          online: true,
          paused: false,
          restarting: false,
          in_room: true,
          voice_ready: true,
          since: Math.floor(Date.now() / 1000),
          last_heartbeat: Math.floor(Date.now() / 1000),
        });
      }, 200);
      break;
    }
    case 'desktop_stop': {
      ackUi(socket, cb, 'sent');
      mockDesktopOnline = true;
      mockDesktopStatus = {
        online: true,
        state: 'online',
        mode: 'read',
        locked: false,
        voice_active: false,
        busy_tool: null,
        last_seen: Math.floor(Date.now() / 1000),
        rtt_ms_p50: 42,
        version: 'mock-node',
      };
      emitToUi('desktop_status', mockDesktopStatus);
      break;
    }
    case 'notification_ack':
      ackUi(socket, cb, 'sent');
      break;
    default:
      ackUi(socket, cb, 'rejected', 'unknown_type');
  }
}

async function handleMockControl(req, res, pathname, method) {
  if (pathname === '/__mock/emit' && method === 'POST') {
    const body = await readBody(req);
    const event = body?.event;
    const payload = body?.payload ?? {};
    if (!event || typeof event !== 'string') {
      json(res, 400, { error: 'event_required' });
      return true;
    }
    emitToUi(event, payload);
    json(res, 200, { ok: true, event });
    return true;
  }

  if (pathname === '/__mock/agent' && method === 'POST') {
    const body = await readBody(req);
    agentOnline = body?.online !== false;
    if (typeof body?.paused === 'boolean') agentPaused = body.paused;
    else if (!agentOnline) agentPaused = Boolean(body?.paused);
    else agentPaused = false;
    if (typeof body?.restarting === 'boolean') agentRestarting = body.restarting;
    else agentRestarting = false;
    const statusPayload = {
      ...loadFixture('agent_status'),
      online: agentOnline,
      paused: agentPaused,
      restarting: agentRestarting,
      voice_ready: agentOnline && !agentPaused,
      in_room: agentOnline && !agentPaused,
    };
    emitToUi('agent_status', statusPayload);
    json(res, 200, { ok: true, online: agentOnline, paused: agentPaused });
    return true;
  }

  if (pathname === '/__mock/reset-login-rate' && method === 'POST') {
    loginRate.clear();
    mockDesktopOnline = true;
    mockDesktopStatus = null;
    json(res, 200, { ok: true });
    return true;
  }

  if (pathname === '/__mock/memories/reset' && method === 'POST') {
    persistedMemories.length = 0;
    json(res, 200, { ok: true });
    return true;
  }

  if (pathname === '/__mock/chat/reset' && method === 'POST') {
    persistedChatMessages.length = 0;
    json(res, 200, { ok: true });
    return true;
  }

  if (pathname === '/__mock/expire' && method === 'POST') {
    ownerSessionActive = false;
    const body = await readBody(req);
    const reason = body?.reason || loadFixture('session_expired').reason || 'session_expired';
    const payload = { ...loadFixture('session_expired'), reason };
    for (const socket of [...uiSockets]) {
      socket.emit('session_expired', payload);
      socket.disconnect(true);
    }
    uiSockets.length = 0;
    json(res, 200, { ok: true });
    return true;
  }

  if (pathname === '/__mock/emits' && method === 'GET') {
    json(res, 200, { ui_commands: receivedUiCommands });
    return true;
  }

  if (pathname === '/__mock/suppress-settings-sync' && method === 'POST') {
    const body = await readBody(req);
    suppressSettingsSync = body?.suppress === true;
    json(res, 200, { ok: true, suppress: suppressSettingsSync });
    return true;
  }

  if (pathname === '/__mock/reset-settings' && method === 'POST') {
    resetLiveSettingsFromFixture();
    suppressSettingsSync = false;
    const sync = buildSettingsSync();
    emitToUi('settings_sync', sync);
    json(res, 200, { ok: true, settings: sync.settings });
    return true;
  }

  if (pathname === '/__mock/vision-frames' && method === 'GET') {
    json(res, 200, { count: visionFrameCount });
    return true;
  }

  if (pathname === '/__mock/vision-frames/reset' && method === 'POST') {
    visionFrameCount = 0;
    json(res, 200, { ok: true });
    return true;
  }

  if (pathname === '/__mock/scenario/reference' && method === 'POST') {
    const result = applyReferenceScenario();
    json(res, result.ok ? 200 : 500, result);
    return true;
  }

  return false;
}

async function handleVortex(req, res, subPath, method, url) {
  if (subPath === '/health' && method === 'GET') {
    json(res, 200, { ok: true });
    return;
  }

  if (subPath === '/status' && method === 'GET') {
    json(res, 200, {
      ok: true,
      bridge: 'mock',
      agent_online: agentOnline,
      ui_clients: Math.max(1, uiSockets.length),
      desktop: mockDesktopStatus ?? { online: mockDesktopOnline },
    });
    return;
  }

  if (subPath === '/auth/login' && method === 'POST') {
    const ip = clientIp(req);
    const retryAfter = checkLoginRate(ip);
    if (retryAfter != null) {
      res.setHeader('Retry-After', String(retryAfter));
      json(res, 429, { error: 'rate_limited', retry_after: retryAfter });
      return;
    }
    const body = await readBody(req);
    const password = body?.password;
    if (password !== TEST_PASSWORD) {
      json(res, 401, { error: 'invalid_credentials' });
      return;
    }
    const recovery = body?.recovery_code;
    const totp = body?.totp;
    const okRecovery = recovery === TEST_RECOVERY;
    const okTotp = totp != null && verifyTotp(totp);
    if (!okRecovery && !okTotp) {
      json(res, 401, { error: 'invalid_credentials' });
      return;
    }
    const expires_at = Math.floor(Date.now() / 1000) + SESSION_TTL_S;
    ownerSessionActive = true;
    setSessionCookie(res);
    json(res, 200, {
      ok: true,
      user: 'owner',
      session_id: SESSION_COOKIE,
      expires_at,
    });
    return;
  }

  if (subPath === '/auth/me' && method === 'GET') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    json(res, 200, {
      ok: true,
      user: 'owner',
      session_id: SESSION_COOKIE,
      expires_at: Math.floor(Date.now() / 1000) + SESSION_TTL_S,
    });
    return;
  }

  if (subPath === '/auth/logout' && method === 'POST') {
    ownerSessionActive = false;
    clearSessionCookie(res);
    json(res, 200, { ok: true });
    return;
  }

  if (subPath === '/livekit/token' && method === 'GET') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    json(res, 503, { error: 'livekit_unavailable' });
    return;
  }

  if (subPath === '/messages' && method === 'GET') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    const page = loadFixture('messages_page');
    json(res, 200, {
      ...page,
      messages: [...(page.messages || []), ...persistedChatMessages],
    });
    return;
  }

  if (subPath === '/media/file' && method === 'GET') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    json(res, 200, {
      ok: true,
      area: url.searchParams.get('area'),
      path: url.searchParams.get('path'),
      relay: true,
    });
    return;
  }

  const mediaMatch = subPath.match(/^\/media\/([^/]+)(\/sign)?$/);
  if (mediaMatch && method === 'GET') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    const id = mediaMatch[1];
    if (mediaMatch[2]) {
      json(res, 200, { ok: true, id, url: `/vortex/media/${id}`, expires_at: null });
      return;
    }
    json(res, 200, { ok: true, id, content_type: 'application/octet-stream', bytes: 0 });
    return;
  }

  if (subPath === '/feedback' && method === 'POST') {
    if (!hasSession(req)) {
      json(res, 401, { error: 'unauthorized' });
      return;
    }
    json(res, 200, { ok: true });
    return;
  }

  json(res, 404, { error: 'not_found' });
}

async function handleRequest(req, res) {
  const method = req.method || 'GET';
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`);
  const pathname = url.pathname;

  if (pathname.startsWith('/__mock/')) {
    const handled = await handleMockControl(req, res, pathname, method);
    if (handled) return;
    json(res, 404, { error: 'not_found' });
    return;
  }

  if (!pathname.startsWith('/vortex')) {
    json(res, 404, { error: 'not_found' });
    return;
  }

  const subPath = pathname.slice('/vortex'.length) || '/';
  await handleVortex(req, res, subPath, method, url);
}

const server = http.createServer((req, res) => {
  handleRequest(req, res).catch(err => {
    console.error('[mock-bridge]', err);
    if (!res.headersSent) json(res, 500, { error: 'internal' });
  });
});

const io = new Server(server, {
  path: '/vortex/socket.io',
  cors: { origin: true, credentials: true },
});

io.use((socket, next) => {
  if (!ownerSessionActive) {
    return next(new Error('unauthorized'));
  }
  const cookies = parseCookies(socket.handshake.headers.cookie);
  if (cookies.jarvis_session !== SESSION_COOKIE) {
    return next(new Error('unauthorized'));
  }
  return next();
});

io.on('connection', socket => {
  uiSockets.push(socket);
  socket.emit('init_state', buildInitState());
  socket.emit('settings_sync', buildSettingsSync());

  socket.on('ui_command', (cmd, cb) => {
    try {
      handleUiCommand(socket, cmd || {}, cb);
    } catch (err) {
      console.error('[mock-bridge] ui_command', err);
      ackUi(socket, cb, 'rejected', 'handler_error');
    }
  });

  socket.on('vision_frame', payload => {
    if (payload && typeof payload.frame === 'string' && payload.frame.startsWith('data:image/')) {
      visionFrameCount += 1;
    }
  });

  socket.on('disconnect', () => {
    const idx = uiSockets.indexOf(socket);
    if (idx >= 0) uiSockets.splice(idx, 1);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[mock-bridge] http://127.0.0.1:${PORT}/vortex/health`);
});
