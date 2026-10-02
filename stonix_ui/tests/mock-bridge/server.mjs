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

/** @type {Map<string, { count: number, resetAt: number }>} */
const loginRate = new Map();
const RATE_LIMIT_MAX = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

function loadFixture(name) {
  const path = join(FIXTURES_DIR, `${name}.json`);
  return JSON.parse(readFileSync(path, 'utf8'));
}

function mergeSettingsSync(base, payload) {
  if (!payload || typeof payload !== 'object') {
    return { ...base, settings: { ...(base.settings || {}) } };
  }
  const nextSettings = { ...(base.settings || {}), ...payload };
  if (payload.automation && typeof payload.automation === 'object') {
    nextSettings.automation = {
      ...(base.settings?.automation || {}),
      ...payload.automation,
    };
  }
  const out = { ...base, settings: nextSettings };
  if (Object.prototype.hasOwnProperty.call(payload, 'turn_mode')) {
    out.restart_required = ['turn_mode'];
    out.applied =
      typeof base.applied === 'object' && base.applied
        ? { ...base.applied, turn_mode: 'pending_restart' }
        : { turn_mode: 'pending_restart' };
  } else if (base.applied === true) {
    out.applied = true;
  }
  return out;
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
  return {
    ...init,
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

function handleVoiceEnroll(socket, payload, cb) {
  if (!agentOnline) {
    ackUi(socket, cb, 'agent_offline');
    return;
  }
  const action = payload?.action;
  ackUi(socket, cb, 'sent');
  if (action === 'cancel') return;
  if (action === 'start') {
    const base = loadFixture('voice_enroll_progress');
    const target = payload?.target_seconds ?? base.target_seconds ?? 6;
    [33, 66, 100].forEach((percent, index) => {
      setTimeout(() => {
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
    });
    return;
  }
  if (action === 'commit') {
    setTimeout(() => {
      emitToUi('voice_enroll_result', loadFixture('voice_enroll_result'));
    }, 150);
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
        const sync = loadFixture('settings_sync');
        socket.emit('settings_sync', mergeSettingsSync(sync, payload || {}));
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
      socket.emit('settings_sync', {
        settings: { persona },
        applied: true,
      });
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
    case 'stop':
    case 'start':
    case 'pause_agent':
    case 'tasks_refresh':
    case 'feedback':
    case 'sim_lookup':
    case 'open_sim_panel':
    case 'swarm':
    case 'job_cancel':
    case 'open_job_artifact':
    case 'voice_take_over':
    case 'wake_word_toggle':
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
    const statusPayload = {
      ...loadFixture('agent_status'),
      online: agentOnline,
      voice_ready: agentOnline,
      in_room: agentOnline,
    };
    emitToUi('agent_status', statusPayload);
    json(res, 200, { ok: true, online: agentOnline });
    return true;
  }

  if (pathname === '/__mock/reset-login-rate' && method === 'POST') {
    loginRate.clear();
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

  if (pathname === '/__mock/vision-frames' && method === 'GET') {
    json(res, 200, { count: visionFrameCount });
    return true;
  }

  if (pathname === '/__mock/vision-frames/reset' && method === 'POST') {
    visionFrameCount = 0;
    json(res, 200, { ok: true });
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
      ui_clients: uiSockets.length,
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
  socket.emit('settings_sync', loadFixture('settings_sync'));

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
