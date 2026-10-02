'use strict';
/* =====================================================================================================
   JARVIS & ANNA: full product mockup in the reference's visual language.
   ?still=1  deterministic reference frame (visual diff; no animation, fixed clock 9:42 PM, no prototype handle)
   ?proto=0  hide the prototype controls      ?reduce=1  force reduced motion
   ===================================================================================================== */
const Q = new URLSearchParams(location.search);
const STILL = Q.has('still');
const PROTO = !STILL && Q.get('proto') !== '0';
const mqReduce = matchMedia('(prefers-reduced-motion: reduce)');
let reduceForce = Q.get('reduce') === '1';
const reduced = () => mqReduce.matches || reduceForce;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const html = document.documentElement;
const icon = (id, cls = 'n-ic', st = '') => `<svg class="${cls}" aria-hidden="true"${st ? ` style="${st}"` : ''}><use href="#i-${id}"/></svg>`;
const sh = s => `<span class="n-sh" data-s="${s}" aria-hidden="true"></span>`;
const pill = (tone, text, s) => `<span class="n-pill" data-tone="${tone}">${s ? sh(s) : ''}${text}</span>`;
const wait = ms => new Promise(r => setTimeout(r, ms));
// the camera counts as on (red dot, badge, System Flow) only once the stream really runs
const camLive = () => S.camera && S.camState === 'live';

/* ---------------------------------------------------------------- clock: still = the reference's 9:42 PM, live = now */
const ANCHOR = STILL ? new Date(2026, 9, 2, 21, 42, 10).getTime() : Date.now();
const PERF0 = performance.now();
const nowMs = () => ANCHOR + (STILL ? 0 : performance.now() - PERF0);
function fmtClock(ms, sec) {
  const d = new Date(ms); let h = d.getHours(); const m = d.getMinutes(), ap = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return h + ':' + String(m).padStart(2, '0') + (sec ? ':' + String(d.getSeconds()).padStart(2, '0') : '') + ' ' + ap;
}
const tAgo = min => fmtClock(nowMs() - min * 60000);
const tStamp = msAgo => { const d = new Date(nowMs() - msAgo); return [d.getHours(), d.getMinutes(), d.getSeconds()].map(v => String(v).padStart(2, '0')).join(':'); };
function rel(msAgo) {
  const s = Math.max(0, Math.round(msAgo / 1000));
  if (s < 60) return s + ' s ago';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}
function dur(sec) {
  sec = Math.max(0, Math.round(Math.abs(sec)));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${s} s` : `${s} s`;
}
const mmss = s => `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, '0')}`;

/* ---------------------------------------------------------------- personas: copy follows grammatical gender */
const P = {
  jarvis: { key: 'jarvis', name: 'JARVIS', voice: 'Charon', g: 'raha', fut: 'ga', trait: 'Calm and precise', other: 'anna' },
  anna: { key: 'anna', name: 'ANNA', voice: 'Aoede', g: 'rahi', fut: 'gi', trait: 'Warm and quick', other: 'jarvis' },
};
const OWNER = { name: 'Matloob', initial: 'M' };
const QUOTE = '“Ideas become reality, faster.”';

/* ---------------------------------------------------------------- state (one store; every view reads it) */
function seedState() {
  return {
    screen: 'app', login: { err: '', expired: false, rec: false, busy: false, show: false },
    page: 'overview', sub: '',
    persona: 'jarvis', personaPending: false, personaFail: false,
    // voice inputs (the visual state is derived from them, first match wins)
    room: 'ok', dup: false, agentAbsent: false, restarting: false, audioBlocked: false,
    activity: null, micOn: true, listening: true, wake: true, wakePending: false, voiceErr: '',
    // system
    agent: 'online', bridge: true, laptop: 'full', camera: false, camShare: true, camLink: 1,
    // panels
    panelTab: 'chat', toolFilter: 'all', actTab: 'timeline', tlFilter: 'all', setSec: 'session',
    logs: { warnOnly: false, info: true, filter: '', paused: false, held: 0, dropped: 0, plan: 0, tab: 'logs' },
    memQuery: '', memClear: 0, taskCancel: null,
    data: 'normal', crash: false, stale: false, staleVps: false, mediaIdx: 0, mediaOpen: true, mediaBad: false, mapFocus: true,
    camState: 'live', restartUnsupported: false, wakeFail: false, ptt: false,
    vid: { mode: 'enforcing', score: .82, th: .45, enrolled: true, samples: 6, reject: 'silent', on: true },
    unread: 0, notifPerm: 'default', followup: 20,
    set: { voice: 'Charon', voiceA: 'Aoede', lang: 'ur', turn: 'gated', turnRestart: false, script: 'roman', speak: 'auto', quiet: '23:00-07:00',
      dc: true, clip: true, clipSpeak: false, voiceHigh: false, vision: 'ask' },
    fstate: {},
    vitals: { cpu: 21, mem: 63, disk: 82, vcpu: 34, vmem: 48, vdisk: 57 },
    task: false,
  };
}
let S = seedState();

/* ---------------------------------------------------------------- data (all simulated) */
const VOICES = ['Zephyr', 'Puck', 'Charon', 'Kore', 'Fenrir', 'Leda', 'Orus', 'Aoede', 'Callirrhoe', 'Autonoe', 'Enceladus', 'Iapetus', 'Umbriel', 'Algieba', 'Despina', 'Erinome', 'Algenib', 'Rasalgethi', 'Laomedeia', 'Achernar', 'Alnilam', 'Schedar', 'Gacrux', 'Pulcherrima', 'Achird', 'Zubenelgenubi', 'Vindemiatrix', 'Sadachbia', 'Sadaltager', 'Sulafat'];
const NOMINAL_CODE = 'Get-Process | Sort-Object CPU -Descending |\n<span style="letter-spacing:-.58px;position:relative;top:-1px">Select-Object -First 5</span>';
const SHOT_SVG = 'data:image/svg+xml;utf8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1a3a"/><stop offset="1" stop-color="#1b0f33"/></linearGradient><linearGradient id="b" x1="0" x2="1"><stop offset="0" stop-color="#2d66fc"/><stop offset="1" stop-color="#7b53f9"/></linearGradient></defs><rect width="960" height="540" fill="url(#g)"/><rect x="0" y="508" width="960" height="32" fill="#0a0f1f"/><g fill="#e6ebf7" font-family="Segoe UI,Arial" font-size="14"><text x="24" y="529">Start</text><text x="880" y="529">9:38 PM</text></g><rect x="70" y="60" width="520" height="330" rx="10" fill="#0f1730" stroke="#2a3654"/><rect x="70" y="60" width="520" height="34" rx="10" fill="#17213b"/><g fill="#c0cae2" font-family="Consolas,monospace" font-size="15"><text x="92" y="128">PS C:\\Users\\Matloob&gt; Get-Process | Sort CPU</text><text x="92" y="160">chrome      41.2</text><text x="92" y="186">Code        18.7</text><text x="92" y="212">python      12.4</text><text x="92" y="238">Teams        9.8</text><text x="92" y="264">explorer     4.1</text></g><rect x="620" y="60" width="270" height="200" rx="10" fill="#101a35" stroke="#2a3654"/><circle cx="755" cy="160" r="62" fill="none" stroke="url(#b)" stroke-width="16"/><g fill="#94a0be" font-family="Segoe UI,Arial" font-size="13"><text x="640" y="290">Weekly report.pdf</text><text x="640" y="312">Downloads</text></g></svg>`);

function seedData() {
  const empty = S.data === 'empty';
  const now = nowMs();
  const D = {
    // activity feed (newest first). [kind, level, title, sub, msAgo, n]
    events: empty ? [] : [
      { kind: 'system', k: 'k-ok', title: 'System online', sub: 'All systems operational', at: now - 1 * 60e3 },
      { kind: 'voice', k: 'k-info', title: 'Voice service ready', sub: 'Microphone initialized', at: now - 2 * 60e3 },
      { kind: 'device', k: 'k-device', title: 'Laptop connected', sub: 'Windows 11 • 3 devices online', at: now - 4 * 60e3 },
      { kind: 'system', k: 'k-neutral', title: 'Agent sync complete', sub: 'JARVIS & ANNA ready', at: now - 5 * 60e3 },
      { kind: 'tool', k: 'k-err', title: 'send_whatsapp failed', sub: 'Timed out waiting for WhatsApp Desktop', at: now - 6 * 60e3 },
      { kind: 'approval', k: 'k-ok', title: 'Command approved', sub: 'open_app chrome', at: now - 11 * 60e3 },
      { kind: 'media', k: 'k-info', title: 'Map: Lahore', sub: 'Shown on Agents › Map', at: now - 18 * 60e3 },
      { kind: 'reminder', k: 'k-info', title: 'Reminder: Gym bag pack karo', sub: 'Spoken aloud', at: now - 28 * 60e3 },
      { kind: 'persona', k: 'k-info', title: 'JARVIS is answering', sub: 'Voice Charon', at: now - 29 * 60e3 },
      { kind: 'voice', k: 'k-warn', title: 'Voice ignored', sub: 'Awaaz match nahi hui (41% < 45%)', at: now - 41 * 60e3, n: 2 },
      { kind: 'approval', k: 'k-neutral', title: 'Approval expired, nothing ran', sub: 'run_cmd_command', at: now - 44 * 60e3 },
      { kind: 'memory', k: 'k-neutral', title: 'Short-term memory cleared', sub: 'Backup saved', at: now - 70 * 60e3 },
    ],
    alerts: empty ? [] : [
      { type: 'sys', sev: 'warn', title: 'Gemini session resumed', n: 3, at: now - 2 * 60e3 },
      { type: 'log', sev: 'err', level: 'ERROR', title: 'send_whatsapp timed out after 20 s', cat: 'tools', at: now - 6 * 60e3 },
      { type: 'sys', sev: 'info', title: 'Laptop clock drift 4 s', n: 1, at: now - 18 * 60e3 },
    ].map(a => ({ ...a, seen: true })),
    logs: empty ? [] : [
      { t: 330e3, lvl: 'INFO', cat: 'bridge', msg: 'Socket linked, session s-7f2a' },
      { t: 318e3, lvl: 'INFO', cat: 'voice', msg: 'Agent joined room jarvis-main' },
      { t: 300e3, lvl: 'WARNING', cat: 'voice', msg: 'Gemini session resumed', n: 3 },
      { t: 250e3, lvl: 'INFO', cat: 'desktop', msg: 'Desktop Node online (full control, manifest ok)' },
      { t: 240e3, lvl: 'DEBUG', cat: 'memory', msg: 'Extractor saved 1 fact' },
      { t: 190e3, lvl: 'INFO', cat: 'tools', msg: 'get_weather started (city=Karachi)' },
      { t: 189e3, lvl: 'INFO', cat: 'tools', msg: 'get_weather finished in 0.9 s' },
      { t: 150e3, lvl: 'ERROR', cat: 'tools', msg: 'send_whatsapp timed out after 20 s' },
      { t: 120e3, lvl: 'WARNING', cat: 'vision', msg: 'Camera not shared; vision on request only' },
      { t: 70e3, lvl: 'WARNING', cat: 'memory', msg: 'Vector store slow: 840 ms query' },
      { t: 12e3, lvl: 'INFO', cat: 'confirm', msg: 'Confirmation requested: run_cmd_command (medium)' },
      { t: 8e3, lvl: 'INFO', cat: 'voice', msg: 'Speaking (Charon), 42 tokens' },
    ].map(l => ({ ...l, ts: tStamp(l.t) })),
    feed: [['telemetry', 'success 98.0%, latency 0.42 s over 1,280 samples'], ['diagnostics', 'LiveKit RTT 38 ms, jitter 4 ms, packet loss 0.1%'], ['audit', 'run_cmd_command requested by owner (typed)'],
      ['telemetry', 'confusion 1.6% (21 of 1,280 turns)'], ['diagnostics', 'Bridge queue 0, 2 UI clients'], ['audit', 'settings_update persona=jarvis by owner']],
    plans: [
      { intent: 'show_top_processes', src: 'chat', amb: false, ago: 12, conf: [['run_cmd_command', .88], ['open_task_manager', .54], ['chat', .10]], step: [2, 3, 'run_cmd_command', 'awaiting confirmation'],
        steps: [['classify request', 'ok', 'done'], ['run_cmd_command', 'acc', 'awaiting you'], ['summarize output', 'off', 'pending']] },
      { intent: 'get_weather', src: 'voice', amb: false, ago: 190, conf: [['get_weather', .96], ['web_search', .31], ['chat', .08]], step: [2, 2, 'speak_summary', 'done'],
        steps: [['get_weather', 'ok', 'done'], ['speak_summary', 'ok', 'done']] },
      { intent: 'send_whatsapp_message', src: 'chat', amb: false, ago: 380, conf: [['send_whatsapp', .91], ['open_app', .22], ['chat', .05]], step: [1, 2, 'send_whatsapp', 'error'],
        steps: [['send_whatsapp', 'err', 'timed out'], ['confirm_delivery', 'off', 'skipped']] },
      { intent: 'open_youtube_and_play', src: 'voice', amb: true, ago: 660, conf: [['open_app', .91], ['search_youtube', .42], ['chat', .12]], step: [3, 3, 'play_media', 'done'],
        steps: [['open_app chrome', 'ok', 'done'], ['search_youtube', 'ok', 'done'], ['play_media', 'ok', 'done']] },
      { intent: 'create_reminder', src: 'chat', amb: false, ago: 1800, conf: [['create_reminder', .97], ['tasks_list', .20], ['chat', .04]], step: [1, 1, 'create_reminder', 'done'],
        steps: [['create_reminder', 'ok', 'done']] },
    ],
    mems: empty ? [] : [
      { text: 'Owner replies Roman Urdu mein pasand karte hain.', role: 'user', src: 'fact', at: now - 2 * 864e5 },
      { text: 'Dentist appointment kal subah 9 baje.', role: 'user', src: 'extractor', at: now - 32 * 60e3 },
      { text: 'Coding ke waqt lo-fi playlists sunte hain.', role: 'assistant', src: 'vector', at: now - 3600e3 },
      { text: 'Ali ka WhatsApp contact "Ali Office" ke naam se saved hai.', role: 'user', src: 'fact', at: now - 5 * 864e5 },
      { text: 'Weekly report Friday 5 PM tak bhejni hoti hai.', role: 'user', src: 'extractor', at: now - 7 * 864e5 },
      { text: 'Office printer: HP LaserJet, teesri manzil.', role: 'user', src: 'fact', at: now - 21 * 864e5 },
    ],
    jobs: empty ? [] : [
      { id: 'j1', title: 'Weekly report PDF', tool: 'pdf_render', status: 'running', pct: 64, msg: 'Page 7 of 11' },
      { id: 'j2', title: 'Download lo-fi playlist', tool: 'yt_download', status: 'running', pct: 31, msg: '12.4 MB of 40 MB' },
      { id: 'j3', title: 'Desktop screenshot', tool: 'screen_capture', status: 'success', pct: 100, msg: 'Saved to Pictures\\JARVIS', artifact: true },
    ],
    tools: empty ? [] : [
      { id: 't1', name: 'run_cmd_command', status: 'waiting', at: now - 12e3, dur: '', args: 'command = Get-Process | Sort-Object CPU -Descending | Select-Object -First 5', result: 'Waiting for your approval' },
      { id: 't2', name: 'pdf_render', status: 'running', at: now - 120e3, dur: '64%', args: '{"doc": "weekly_report.md"}', result: 'Page 7 of 11' },
      { id: 't3', name: 'get_weather', status: 'success', at: now - 190e3, dur: '0.9 s', args: '{"city": "Karachi"}', result: '31°C, haze, RH 68%' },
      { id: 't4', name: 'send_whatsapp', status: 'error', at: now - 380e3, dur: '20.0 s', args: '{"to": "Ali Office", "text": "Meeting 5 baje hai"}', result: 'Timed out waiting for WhatsApp Desktop' },
      { id: 't5', name: 'DESKTOP_SCREENSHOT', status: 'success', at: now - 240e3, dur: '1.2 s', args: '{"display": 1}', result: 'screenshot_2138.png (1.4 MB)', media: 'image' },
      { id: 't6', name: 'play_media', status: 'success', at: now - 660e3, dur: '0.3 s', args: '{}', result: 'playing' },
      { id: 't7', name: 'DESKTOP_RECORD_AUDIO', status: 'success', at: now - 900e3, dur: '30.0 s', args: '{"seconds": 30}', result: 'note_2127.m4a (480 KB)', media: 'audio' },
    ],
    tasks: empty ? [] : [
      { id: 'k1', kind: 'Reminder', status: 'Overdue', title: 'Gym bag pack karo', desc: 'Shoes, towel aur bottle bhi rakhna', due: now - 14 * 60e3, src: 'reminders' },
      { id: 'k2', kind: 'Task', status: 'Running', title: 'Weekly report PDF bhejo', desc: 'weekly_report.md se PDF banao aur sana@office.pk ko email karo', due: now + 316 * 60e3, src: 'scheduler' },
      { id: 'k3', kind: 'Reminder', status: 'Scheduled', title: 'Ammi ko call karna', desc: '', due: now + 526 * 60e3, src: 'reminders' },
      { id: 'k4', kind: 'Reminder', status: 'Scheduled', title: 'Dentist appointment', desc: 'Dr. Saima, DHA Phase 5, 9:00 AM', due: now + 1396 * 60e3, src: 'reminders' },
    ],
    notifs: empty ? [] : [
      { id: 'n1', title: 'Gym bag pack karo', at: now - 28 * 60e3, spoken: true, ack: false },
      { id: 'n2', title: 'Standup call', at: now - 102 * 60e3, spoken: false, ack: true },
      { id: 'n3', title: 'Paani piyo', at: now - 117 * 60e3, spoken: false, ack: true },
    ],
    intel: empty ? [] : [
      { id: 'i1', type: 'json', label: 'Weather, Karachi', at: now - 190e3, data: [['Temperature', '31°C'], ['Feels like', '35°C'], ['Humidity', '68%'], ['Wind', '14 km/h SW'], ['Condition', 'Haze'], ['Source', 'open-meteo']] },
      { id: 'i2', type: 'image', label: 'Desktop screenshot', at: now - 240e3, src: SHOT_SVG },
      { id: 'i3', type: 'link', label: 'lofi hip hop radio, beats to relax/study to', at: now - 660e3, url: 'https://www.youtube.com/watch?v=jfKfPfyJRdk' },
      { id: 'i4', type: 'audio', label: 'Voice note: reminder chime', at: now - 1800e3 },
      { id: 'i5', type: 'lan', label: 'Weekly report draft (PDF)', at: now - 2220e3, url: 'http://192.168.1.20:8765/media/weekly_report.pdf' },
      { id: 'i6', type: 'lan-audio', label: 'Call recording, PC only', at: now - 2640e3, url: 'http://192.168.1.20:8765/media/call_0858.m4a' },
    ],
    queue: empty ? [] : [{ id: 'c1', tool: 'run_cmd_command', label: 'PowerShell', risk: 'medium', q: 'Run this PowerShell command on your laptop?', code: null, nominal: true, where: 'PC (laptop)', voice: false, ttl: 600, left: 600 }],
    history: empty ? [] : [
      { state: 'allowed', tool: 'open_app', what: 'chrome', at: now - 11 * 60e3 },
      { state: 'denied', tool: 'send_whatsapp', what: 'Ali Office: Meeting 5 baje hai', at: now - 9 * 60e3 },
      { state: 'expired', tool: 'run_cmd_command', what: 'Clear-RecycleBin -Force', at: now - 44 * 60e3 },
      { state: 'cancelled', tool: 'file_delete', what: 'Downloads\\old (214 files)', at: now - 63 * 60e3 },
    ],
    older: [
      { role: 'day', text: 'Earlier today' },
      { role: 'user', text: 'Kal subah 9 baje dentist ka reminder laga do', at: now - 30 * 60e3, src: 'typed' },
      { role: 'ai', persona: 'anna', text: 'Ho gaya. Dentist ka reminder kal 9:00 AM ke liye set hai. Main waqt par yaad dila doongi.', at: now - 30 * 60e3 },
      { role: 'sys', text: 'Switched to JARVIS', at: now - 29 * 60e3 },
      { role: 'user', text: 'WhatsApp par Ali ko bolo meeting 5 baje hai', at: now - 9 * 60e3, src: 'typed', failed: 'queued' },
      { role: 'user', text: 'Kal ka weather bhi bata dena', at: now - 8 * 60e3, src: 'spoken', failed: 'undelivered' },
      { role: 'user', text: 'Paani peene ka reminder har 2 ghante', at: now - 7 * 60e3, src: 'bell', failed: 'failed', reason: 'reminder service timed out (20 s)' },
      { role: 'ai', persona: 'jarvis', text: 'Reminder service abhi jawab nahi de rahi. Main dobara koshish karunga.', at: now - 7 * 60e3 },
      { role: 'ai', persona: 'jarvis', text: 'Agar phir bhi na hua to Tasks mein manually add kar dein.', at: now - 7 * 60e3, group: true },
      { role: 'user', text: 'Aaj Karachi ka mausam kaisa hai?', at: now - 3 * 60e3, src: 'spoken' },
      { role: 'ai', persona: 'jarvis', text: 'Karachi mein abhi 31°C hai aur humidity 68% hai. Shaam tak halki samandari hawa rahegi.', at: now - 3 * 60e3 },
    ],
  };
  return D;
}
let D = seedData();
const GUARDED = ['run_cmd_command', 'file_delete', 'send_whatsapp', 'shutdown_agent', 'sim_lookup', 'desktop_type', 'email_send'];
const LAPTOP = {
  full: { label: 'Full control', short: 'Full', s: 'ok', tone: 'ok', mode: 'Full', locked: 'No', online: true },
  stale: { label: 'Stale, last report 41 s ago', short: 'Stale', s: 'warn', tone: 'warn', mode: 'Full', locked: 'No', online: true, mark: 'warn' },
  offline: { label: 'Offline', short: 'Offline', s: 'off', tone: 'off', mode: 'Off', locked: 'n/a', online: false },
  locked: { label: 'Screen locked', short: 'Locked', s: 'warn', tone: 'warn', mode: 'Read', locked: 'Yes', online: true, mark: 'warn' },
  panic: { label: 'Panic stop', short: 'Panic', s: 'err', tone: 'err', mode: 'Off', locked: 'n/a', online: true, mark: 'err' },
  read: { label: 'Read-only', short: 'Read-only', s: 'mute', tone: 'off', mode: 'Read', locked: 'No', online: true, mark: 'read' },
  unknown: { label: 'unknown', short: 'unknown', s: 'off', tone: 'off', mode: 'unknown', locked: 'unknown', online: false },
};
const AGENT = {
  online: { label: 'Online', s: 'ok', tone: 'ok' }, paused: { label: 'Paused', s: 'warn', tone: 'warn' }, restarting: { label: 'Restarting', s: 'busy', tone: 'off' },
  offline: { label: 'Offline', s: 'err', tone: 'err' }, unknown: { label: 'Unknown', s: 'off', tone: 'off' },
};
