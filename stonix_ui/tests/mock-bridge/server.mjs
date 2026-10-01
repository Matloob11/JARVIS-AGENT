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
let ownerSessionActive = false;

/** @type {Map<string, { count: number, resetAt: number }>} */
const loginRate = new Map();
const RATE_LIMIT_MAX = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

function loadFixture(name) {
  const path = join(FIXTURES_DIR, `${name}.json`);
  return JSON.parse(readFileSync(path, 'utf8'));
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

function emitToUi(event, payload) {
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
  socket.emit('new_message', userMsg);
  setTimeout(() => {
    const assistantTemplate = loadFixture('new_message');
    socket.emit('new_message', {
      ...assistantTemplate,
      id: randomUUID(),
      turn_id: String(id),
      ts: Math.floor(Date.now() / 1000),
      timestamp: Math.floor(Date.now() / 1000),
    });
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
  if (action === 'start' || action === 'commit') {
    const base = loadFixture('voice_enroll_progress');
    const target = payload?.target_seconds ?? base.target_seconds ?? 6;
    [33, 66, 100].forEach((percent, index) => {
      setTimeout(() => {
        socket.emit('voice_enroll_progress', {
          ...base,
          percent,
          seconds: Math.round((target * percent) / 100),
          target_seconds: target,
        });
      }, (index + 1) * 200);
    });
    setTimeout(() => {
      socket.emit('voice_enroll_result', loadFixture('voice_enroll_result'));
    }, 900);
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

  if (!agentOnline && type !== 'request_state') {
    ackUi(socket, cb, 'agent_offline');
    return;
  }

  switch (type) {
    case 'chat':
      handleChat(socket, payload, cb);
      break;
    case 'settings_update': {
      ackUi(socket, cb, 'sent');
      const sync = loadFixture('settings_sync');
      socket.emit('settings_sync', {
        ...sync,
        settings: { ...sync.settings, ...(payload || {}) },
      });
      break;
    }
    case 'confirm_response': {
      ackUi(socket, cb, 'sent');
      const resolved = loadFixture('confirm_resolved');
      socket.emit('confirm_resolved', {
        ...resolved,
        id: payload?.id != null ? String(payload.id) : resolved.id,
        allow: Boolean(payload?.allow),
      });
      break;
    }
    case 'clear_memory': {
      ackUi(socket, cb, 'sent');
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
      socket.emit('init_state', loadFixture('init_state'));
      break;
    case 'stop':
    case 'start':
    case 'pause_agent':
    case 'shutdown_agent':
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
    json(res, 200, page);
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
  socket.emit('init_state', loadFixture('init_state'));

  socket.on('ui_command', (cmd, cb) => {
    try {
      handleUiCommand(socket, cmd || {}, cb);
    } catch (err) {
      console.error('[mock-bridge] ui_command', err);
      ackUi(socket, cb, 'rejected', 'handler_error');
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
