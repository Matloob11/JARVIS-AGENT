
/* =====================================================================================================
   AGENTS: personas, voice & wake, Voice-ID, System Flow, devices (Agent VPS, laptop, this browser + camera),
   machines (PC, VPS, telemetry), map
   ===================================================================================================== */
const hd = (ic, color, title, sub, act = '') => `<header class="n-hd">${icon(ic, 'n-ic', `color:${color}`)}<div><h3>${title}</h3>${sub ? `<p>${sub}</p>` : ''}</div><div class="n-hd-act">${act}</div></header>`;
const sw = (checked, fk, label, tid, extra = '') => `<button type="button" class="n-sw" role="switch" aria-checked="${checked}" data-active="${checked}" aria-label="${label}" data-fk="${fk}" ${tid ? `data-testid="${tid}"` : ''} ${extra}></button>`;
const row = (title, sub, right) => `<div class="n-row"><div class="n-l"><b>${title}</b>${sub ? `<small>${sub}</small>` : ''}</div><div class="n-r">${right}</div></div>`;
function voiceRoomPill() {
  const v = V.mode;
  if (S.dup) return pill('warn', 'Linked to another device', 'warn');
  if (v === 'offline') return pill('err', 'Unavailable', 'err');
  if (v === 'joining') return pill('off', 'Joining…', 'busy');
  return pill('ok', 'Joined', 'ok');
}
const AG = {
  personas() {
    const card = k => { const on = S.persona === k, p = P[k];
      return `<article class="n-persona edge" data-p="${k}" data-on="${on}" data-testid="persona-card-${k}">
        <canvas class="mini-orb" data-p="${k}" width="92" height="92" aria-hidden="true"></canvas>
        <div><strong>${p.name}</strong><span class="n-sm">Voice ${p.voice}</span><span class="n-sm">${p.trait}</span>
        <div class="pa">${on ? pill('acc', S.personaPending ? 'Switching…' : 'Answering now', S.personaPending ? 'busy' : 'acc') : `<button type="button" class="n-btn sm" data-act="persona" data-v="${k}" data-fk="mk${k}">${icon('swap')}Make ${p.name} answer</button>`}</div></div></article>`; };
    return hd('users', '#80befd', 'Who answers', 'Both share memory, tools and approvals. The voice and the accent colour change.') + `<div class="n-persona-grid">${card('jarvis')}${card('anna')}</div>`;
  },
  voice() {
    const p = P[S.persona];
    return hd('mic-fill', '#80befd', 'Voice &amp; wake', 'The voice room, listening and the wake word.') +
      row('Voice room', 'Identity matloob-browser-7f2a', voiceRoomPill()) +
      row(`${p.name} listening`, S.listening ? 'Hears the room through your mic' : 'Muted. Typed chat keeps working', sw(S.listening, 'ag-lis', p.name + ' listening', 'assistant-agent-listening', `data-muted="${!S.listening}"`)) +
      row('Wake word', `Say "${p.name}" before a command. Enforced: yes`, sw(S.wake, 'ag-wake', 'Wake word', 'agents-wake-toggle')) +
      row('Follow-up window', 'Talk again without the wake word for', `<span class="n-num" style="font-weight:600;color:#fff">${S.followup} s</span>`) +
      `<div class="n-actions"><button type="button" class="n-btn sm" data-act="stop" data-fk="ag-stop" ${V.mode === 'speaking' ? '' : 'disabled'}>${icon('stop')}Stop speaking</button><a class="n-link" href="#/settings/voice">Voice settings${icon('chev-r')}</a></div>`;
  },
  vid() {
    const v = S.vid, pct = Math.round(v.score * 100), th = Math.round(v.th * 100);
    const fast = S.set.turn === 'fast';
    const st = !v.enrolled ? ['none', 'off', 'Not enrolled', 'off'] : fast ? ['monitor', 'info', 'Monitor only (Fast mode)', 'info'] : v.score >= v.th ? ['enforcing', 'ok', 'Enforcing, above threshold', 'ok'] : ['below', 'err', 'Below threshold', 'err'];
    const col = st[0] === 'monitor' ? '#4c97fe' : st[0] === 'below' ? '#f2555a' : '#22c086';
    return hd('vprint', '#80befd', 'Voice-ID', 'Only your voice can give commands.') +
      `<div class="vid-meter" data-testid="voice-id-meter" data-state="${st[0]}">
        ${v.enrolled ? `<div class="vid-top"><b class="n-num">${pct}%<small>match, last turn</small></b><span data-testid="voice-id-meter-label">${pill(st[1], st[2], st[3])}</span></div>
        <div class="n-bar"><i style="--v:${v.score};--c:${col}"></i><b style="--th:${th}%" aria-hidden="true"></b></div>
        <div class="vid-scale"><span>0%</span><span>Threshold ${th}%</span><span>100%</span></div>`
        : `<div class="vid-top"><b style="font-size:17px">No voice print yet</b><span data-testid="voice-id-meter-label">${pill('off', 'Not enrolled', 'off')}</span></div><p class="n-note" style="margin:0">Enroll once so ${P[S.persona].name} can tell your voice from the TV, guests or a phone speaker.</p>`}
      </div>
      <dl class="n-kv" style="margin-top:14px"><dt>Unknown voice</dt><dd>${v.reject === 'silent' ? 'Ignored silently' : 'Polite reply'}</dd><dt>Samples</dt><dd>${v.enrolled ? v.samples : 'none'}</dd></dl>
      <div class="n-actions"><button type="button" class="n-btn sm ${v.enrolled ? '' : 'primary'}" data-act="enroll" data-fk="vid-en" data-testid="voice-id-enroll-link">${icon('vprint')}${v.enrolled ? 'Re-enroll' : 'Enroll'}</button></div>`;
  },
  flow() {
    const unknown = !S.bridge;
    const v = V.mode;
    const voice = unknown ? ['off', 'unknown'] : v === 'offline' ? ['err', 'Unavailable'] : ['agent-absent', 'audio-blocked', 'muted'].includes(v) ? ['warn', VS[v].pill] : v === 'joining' ? ['off', 'Joining…'] : ['speaking', 'thinking', 'listening'].includes(v) ? ['acc', VS[v].pill] : ['ok', v === 'mic-off' ? 'Ready, mic off' : 'Ready'];
    const vision = unknown ? ['off', 'unknown'] : camLive() && S.camShare ? ['acc', 'Live, camera shared'] : ['ok', 'On request'];
    const core = unknown || S.agent === 'unknown' ? ['off', 'unknown'] : S.agent === 'online' ? ['ok', 'Online (VPS)'] : S.agent === 'paused' ? ['warn', 'Paused'] : S.agent === 'restarting' ? ['warn', 'Restarting'] : ['err', 'Offline'];
    const mem = unknown ? ['off', 'unknown'] : D.mems.length ? ['ok', 'Synced 2 min ago'] : ['ok', 'Empty'];
    const run = D.tools.filter(t => t.status === 'running').length, err = D.tools.filter(t => t.status === 'error').length;
    const tools = unknown ? ['off', 'unknown'] : err ? ['warn', `${run} running, ${err} error`] : ['ok', run ? `${run} running` : 'Idle'];
    const node = (ic, name, st, tid) => `<div class="n-node edge" data-s="${st[0]}"><span class="ni">${icon(ic)}</span><b>${name}</b><span data-testid="${tid}">${sh(st[0] === 'acc' ? 'acc' : st[0])}${st[1]}</span></div>`;
    return hd('flow', '#80befd', 'System Flow', 'Voice and vision come in, the core assistant decides, memory and tools act.') +
      `<div class="n-flow">${node('mic-fill', 'Voice', voice, 'flow-voice-status')}${node('eye', 'Vision', vision, 'flow-vision-status')}${node('route', 'Core assistant', core, 'flow-core-status')}${node('db', 'Memory', mem, 'flow-memory-status')}${node('box', 'Tools', tools, 'flow-tools-status')}</div>`;
  },
  agent() {
    const a = AGENT[S.agent], online = S.agent === 'online';
    return hd('server', '#80befd', 'Agent (VPS)', 'The brain that listens and answers. Runs on the VPS.', pill(a.tone, a.label, a.s)) +
      (S.agent === 'restarting' ? `<div class="result warn" data-testid="agent-session-restart-banner" style="margin-bottom:12px">${sh('busy')}<span><span lang="ur-Latn">Agent session restart ho raha hai.</span> Voice and chat continue in a moment.</span></div>` : '') +
      kv([['Session', 's-7f2a'], ['Brain', 'VPS (Gemini Live)'], ['Mode', S.set.turn === 'gated' ? 'Secure gated' : 'Fast'], ['Host', 'jarvis-agent-svc'], ['Version', '2.14.0'], ['Last seen', S.bridge ? '3 s ago' : '2 min ago']]) +
      `<div class="health">${pill(S.bridge ? 'ok' : 'err', 'Bridge', S.bridge ? 'ok' : 'err')}${pill('ok', 'Database', 'ok')}${pill('ok', 'LiveKit 38 ms', 'ok')}${pill(online && !S.agentAbsent ? 'ok' : 'warn', online && !S.agentAbsent ? 'In voice room' : 'Not in voice room', online && !S.agentAbsent ? 'ok' : 'warn')}</div>
      <div class="n-actions">${online ? `<button type="button" class="n-btn sm" data-act="agent-pause" data-fk="ag-pause">${icon('pause')}Pause agent</button>` : `<button type="button" class="n-btn sm primary" data-act="agent-wake" data-fk="ag-wake2" ${S.agent === 'restarting' ? 'disabled' : ''}>${icon('play')}Start / Wake</button>`}
        <button type="button" class="n-btn sm danger" data-act="restart" data-fk="ag-rs" data-testid="terminate-session" ${S.agent === 'restarting' ? 'disabled title="Agent update pending: restart already running"' : S.restartUnsupported ? 'disabled title="Agent update pending: this agent version rejected shutdown_agent"' : ''}>${icon('refresh')}Restart session</button></div>
      ${S.restartUnsupported ? `<p class="n-note warn" data-testid="terminate-unsupported">${sh('warn')} Agent update pending: this agent version does not support Restart session yet. The button comes back after the agent updates.</p>` : ''}`;
  },
  laptop() {
    const l = LAPTOP[S.laptop];
    const stoppable = l.online && S.laptop !== 'read' && S.laptop !== 'panic';
    return hd('laptop', '#bfbdf9', 'Laptop (Desktop Node)', 'Apps, files, screenshots and typing on your PC.', `<span data-testid="laptop-card">${pill(l.tone, l.short, l.s)}</span>`) +
      kv([['State', l.label], ['Control mode', l.mode], ['Locked', l.locked], ['Voice active', l.online ? 'Yes' : 'No'], ['Busy tool', S.laptop === 'full' ? 'pdf_render' : 'none'], ['Version', S.laptop === 'unknown' ? 'unknown' : '1.8.2, manifest ok'], ['Round trip', l.online ? '24 ms' : 'n/a'], ['Last seen', S.laptop === 'stale' ? '41 s ago' : l.online ? '1 s ago' : S.laptop === 'unknown' ? 'never' : '12 min ago']]) +
      `<div class="n-actions">${stoppable ? `<button type="button" class="n-btn sm danger" data-act="desktop-stop" data-fk="lp-stop" data-testid="desktop-stop">${icon('power')}Stop desktop</button>` : ''}</div>
      <p class="n-note">${S.laptop === 'read' || S.laptop === 'panic' ? 'Read-only now. Only the laptop tray can switch back to Full.' : S.laptop === 'unknown' ? 'No report from the laptop yet. It counts once it reports.' : 'Stop switches the laptop to read-only. Only the laptop tray can switch back to Full.'}</p>
      <div class="loc" data-testid="device-location-card">${icon('pin')}<div><b>Lahore, Pakistan</b><span>31.5204, 74.3587, </span><span data-testid="device-location-updated">updated 3 min ago</span></div></div>`;
  },
  browser() {
    const badge = !S.bridge ? ['err', 'Offline', 'err'] : S.camera && S.camState === 'starting' ? ['off', 'Not sending yet', 'off'] : !camLive() ? ['off', 'Camera off', 'off'] : S.dup ? ['warn', 'Not linked, take over', 'warn'] : !S.camShare ? ['warn', 'Local preview only', 'warn'] : S.camLink === 2 ? ['info', 'Fallback (bridge)', 'err'] : ['err', 'Live to agent (LiveKit)', 'err'];
    return hd('globe', '#80befd', 'This browser', 'Your dashboard link, the voice room and the camera.') +
      `<div class="cam-row"><div>
        ${row('Bridge', S.bridge ? 'Base /vortex. Secure link active' : 'The dashboard cannot reach the VPS', S.bridge ? pill('ok', 'Live', 'ok') : pill('err', 'Offline', 'err'))}
        ${row('Voice room', S.dup ? 'Voice is active on another device' : 'matloob-browser-7f2a', S.dup ? `<button type="button" class="n-btn xs primary" data-act="takeover" data-fk="br-to">Take over</button>` : voiceRoomPill())}
        ${row('Audio playback', S.audioBlocked ? 'The browser blocked autoplay' : 'Allowed', S.audioBlocked ? `<button type="button" class="n-btn xs primary" data-act="enable-audio" data-fk="br-ea">Enable audio</button>` : pill('ok', 'Allowed', 'ok'))}
        <div class="n-actions"><button type="button" class="n-btn sm" data-act="reconnect" data-fk="br-rc" title="Reconnect: reloads the dashboard and rejoins voice">${icon('refresh')}Reconnect</button></div>
      </div><div>
        <div class="cam">${camLive() ? '<canvas id="cam-cv" width="320" height="240" aria-label="Camera preview, mirrored"></canvas>'
          : S.camera && S.camState === 'starting' ? `<div class="camera-empty" data-state="starting" role="status">${sh('busy')}<span>Starting camera…</span><small class="n-dim" style="font-size:12.5px">Waiting for the browser's camera permission.</small></div>`
          : !S.camera && S.camState === 'error' ? `<div class="camera-empty" data-state="error" role="alert">${icon('alert')}<span>NotAllowedError</span><small style="font-size:12.5px">Camera permission was denied. Allow it in the browser, then turn the camera on again.</small></div>`
          : `<div class="camera-empty">${icon('camera-off')}<span>Camera off</span><small class="n-dim" style="font-size:12.5px">Starts only when you turn it on.</small></div>`}
          <div class="cam-badge" data-testid="camera-link-badge">${pill(badge[0], badge[1], badge[2])}</div></div>
        ${row('Camera', S.camera ? 'Sends 640×480 at 10 fps while shared' : 'Off', sw(S.camera, 'cam', 'Camera', 'camera-toggle'))}
        ${row('Share with agent', 'Vision uses the frames', sw(S.camShare, 'camsh', 'Share with agent', 'camera-share-switch', S.camera ? '' : 'disabled'))}
      </div></div>`;
  },
  machines() {
    const v = S.vitals;
    const bars = (rows) => `<div class="vit">${rows.map(r => `<div class="vr"><span>${r[0]}</span><div class="n-bar"><i style="--v:${r[1] / 100};--c:${r[2]}"></i></div><b class="n-num">${r[1]}%</b></div>`).join('')}</div>`;
    const pcOn = LAPTOP[S.laptop].online;
    return `<section class="n-card edge" data-testid="vitals-pc">${hd('laptop', '#bfbdf9', 'PC (laptop)', 'MATLOOB-PC')}
        ${pcOn ? bars([['CPU', v.cpu, '#35b992'], ['Memory', v.mem, '#3891fc'], ['Disk (C:)', v.disk, '#f9bc54']]) : '<div class="n-empty" style="padding:12px">Laptop offline. Its last numbers are hidden so they are not mistaken for live data.</div>'}
        <div class="vit-foot"><span data-testid="vitals-pc-updated">${pcOn ? (S.stale ? 'Updated 41 s ago' : 'Updated 2 s ago') : 'No data'}</span>${S.stale && pcOn ? `<span data-testid="vitals-pc-stale" style="color:var(--warn-t)">${sh('warn')} Stale</span>` : ''}</div></section>
      <section class="n-card edge" data-testid="vitals-vps">${hd('server', '#80befd', 'Agent (VPS)', 'Ubuntu 24.04, 4 vCPU')}
        ${bars([['CPU', v.vcpu, '#35b992'], ['Memory', v.vmem, '#3891fc'], ['Disk', v.vdisk, '#f9bc54']])}
        <div class="vit-foot"><span data-testid="vitals-vps-updated">${S.staleVps ? 'Updated 38 s ago' : 'Updated 4 s ago'}</span>${S.staleVps ? `<span data-testid="vitals-vps-stale" style="color:var(--warn-t)">${sh('warn')} Stale</span>` : ''}</div></section>
      <section class="n-card edge" data-testid="telemetry-card">${hd('chart', '#80befd', 'Telemetry', 'Last 24 h of turns')}
        <div class="tele"><div class="n-tile edge"><b data-testid="telemetry-success">98.0%</b><span>Success</span></div><div class="n-tile edge"><b data-testid="telemetry-latency">0.42 s</b><span>Latency</span></div>
        <div class="n-tile edge"><b data-testid="telemetry-confusion">1.6%</b><span>Confusion</span></div><div class="n-tile edge"><b data-testid="telemetry-samples">1,280</b><span>Samples</span></div></div>
        <div class="vit-foot"><span data-testid="telemetry-status">${sh('ok')} Healthy</span><span data-testid="telemetry-source">Source: Agent (VPS)</span></div></section>`;
  },
  map() {
    return hd('pin', '#80befd', 'Map', S.mapFocus ? 'Lahore, Pakistan. 31.5204, 74.3587' : 'Places JARVIS &amp; ANNA show you.', S.mapFocus ? `<a class="n-btn sm" data-testid="map-focus-open-link" href="https://www.openstreetmap.org/?mlat=31.5204&amp;mlon=74.3587#map=13/31.5204/74.3587" target="_blank" rel="noopener">${icon('external')}Open map</a>` : '') +
      (S.mapFocus ? `<div data-testid="map-focus-panel"><div class="map" data-testid="map-focus-iframe" role="img" aria-label="Map of Lahore with a pin on Gulberg">${MAP_SVG}<span class="pulse"></span><span class="pin">${icon('pin')}</span><span class="attr">Offline preview. The product embeds OpenStreetMap here.</span></div></div>`
        : `<div class="n-empty" data-testid="map-focus-empty">${icon('pin')}<b>No map focus yet</b><span>Ask JARVIS to show a place, for example "Lahore ka map dikhao".</span></div>`);
  },
};
const MAP_SVG = (() => {
  let roads = '';
  for (let i = 0; i < 18; i++) roads += `<path d="M${-40 + i * 52} -10 L${10 + i * 52 + (i % 3) * 14} 300" stroke="#16223f" stroke-width="${i % 4 ? 1.2 : 2.4}"/>`;
  for (let i = 0; i < 9; i++) roads += `<path d="M-10 ${18 + i * 34} C 220 ${4 + i * 34}, 520 ${40 + i * 30}, 900 ${10 + i * 36}" stroke="#16223f" stroke-width="${i % 3 ? 1.2 : 2.4}" fill="none"/>`;
  return `<svg viewBox="0 0 800 260" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><rect width="800" height="260" fill="#0a1226"/>
    <path d="M-20 210 C 120 170, 230 240, 380 196 S 640 120, 820 150 L820 260 L-20 260Z" fill="#0c1d44"/><path d="M-20 210 C 120 170, 230 240, 380 196 S 640 120, 820 150" stroke="#1d3b7a" stroke-width="3" fill="none"/>
    <ellipse cx="520" cy="80" rx="70" ry="34" fill="#0d2230"/><ellipse cx="190" cy="70" rx="46" ry="24" fill="#0d2230"/>${roads}
    <path d="M0 120 C 200 110, 420 150, 800 96" stroke="#2a3d6e" stroke-width="5" fill="none"/><path d="M380 0 C 400 90, 380 170, 420 260" stroke="#2a3d6e" stroke-width="4" fill="none"/>
    <g fill="#8190b6" font-family="Segoe UI,Arial" font-size="12"><text x="420" y="112">Gulberg</text><text x="120" y="150">Old City</text><text x="610" y="210">DHA</text><text x="560" y="60">Model Town</text></g></svg>`;
})();
const AG_PARTS = ['personas', 'voice', 'vid', 'flow', 'agent', 'laptop', 'browser', 'machines', 'map'];
function renderAgents() {
  const el = $('#page-agents');
  el.innerHTML = `<h2 class="n-group" data-anchor="personas">JARVIS &amp; ANNA</h2>
    <section class="n-card edge" data-part="personas"></section>
    <div class="n-grid2" data-anchor="voice"><section class="n-card edge" data-part="voice"></section><section class="n-card edge" data-part="vid"></section></div>
    <section class="n-card edge" data-part="flow"></section>
    <h2 class="n-group" data-anchor="devices">Devices</h2>
    <div data-testid="operations-rail"><div class="n-grid2" style="margin-bottom:16px"><section class="n-card edge" data-part="agent" data-testid="agent-card"></section><section class="n-card edge" data-part="laptop"></section></div>
    <section class="n-card edge" data-part="browser" data-testid="browser-card" style="margin-bottom:16px"></section></div>
    <h2 class="n-group" data-anchor="machines">Machines and place</h2>
    <div class="n-grid3" data-part="machines"></div>
    <section class="n-card edge" data-part="map" data-anchor="map" data-testid="map-card"></section>
    <div class="page-end"></div>`;
  AG_PARTS.forEach(p => renderAgentsPart(p, true));
  paintAgentsCanvases();
}
function renderAgentsPart(name, initial) {
  const el = $(`#page-agents [data-part="${name}"]`); if (!el) return;
  if (S.crash && name === 'machines') { el.innerHTML = `<div class="panel-error" data-testid="panel-error" style="grid-column:1/-1"><b>${sh('err')}Machines stopped working</b><span>The rest of the dashboard keeps running.</span><button type="button" class="n-btn sm" data-act="retry-card" data-testid="panel-error-retry">${icon('refresh')}Retry</button></div>`; return; }
  keepFocus(el, () => { el.innerHTML = AG[name](); });
  if (!initial) paintAgentsCanvases();
}
let camT = 0;
function paintAgentsCanvases() {
  if (S.page !== 'agents') return;
  $$('#page-agents canvas.mini-orb').forEach(c => drawMiniOrb(c, c.dataset.p));
  const cam = $('#cam-cv');
  cancelAnimationFrame(camT);
  if (cam) { const loop = t => { if (!$('#cam-cv') || S.page !== 'agents') return; drawCam($('#cam-cv'), t / 1000); if (!reduced() && !STILL) camT = requestAnimationFrame(loop); }; loop(performance.now()); }
}

/* =====================================================================================================
   ACTIVITY: Timeline (alerts + every event), Logs (terminal, all levels, system feed, reasoning),
   Tasks & reminders, Notes (memory), Media (intelligence)
   ===================================================================================================== */
const ACT_TABS = [['timeline', 'Timeline', 'activity'], ['logs', 'Logs', 'terminal'], ['tasks', 'Tasks & reminders', 'cal'], ['notes', 'Notes', 'note'], ['media', 'Media', 'image']];
function renderActivity() {
  const el = $('#page-activity');
  if (!el.firstElementChild) {
    el.innerHTML = `<div class="n-card edge act-page"><div class="n-tabs" role="group" aria-label="Activity views">${ACT_TABS.map(t => `<button type="button" data-at="${t[0]}" data-testid="activity-tab-${t[0]}" aria-pressed="false">${icon(t[2])}${t[1].replace('&', '&amp;')}<span class="cnt" hidden></span></button>`).join('')}</div><div class="act-body n-scroll" id="act-body" tabindex="-1"></div></div>`;
  }
  $$('#page-activity [data-at]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.at === S.actTab)));
  const warn = D.alerts.filter(a => !a.seen && a.sev !== 'info').length, c = $('#page-activity [data-at="timeline"] .cnt');
  c.hidden = !warn; c.textContent = warn;
  renderActivityBody(); requestAnimationFrame(tabStripFades);
}
function renderActivityBody() {
  const el = $('#act-body'); if (!el) return;
  keepFocus(el, () => { el.innerHTML = ACT[S.actTab](); });
  if (S.actTab === 'logs') { renderTerm(); const t = $('#trace-term'); if (t) t.scrollTop = t.scrollHeight; }
}
const TL_FILTERS = [['all', 'All'], ['system', 'System'], ['voice', 'Voice'], ['device', 'Devices'], ['approval', 'Approvals'], ['tool', 'Tool runs'], ['reminder', 'Reminders'], ['media', 'Media']];
const KIND_GROUP = { system: 'system', sync: 'system', persona: 'system', memory: 'system', voice: 'voice', device: 'device', approval: 'approval', tool: 'tool', reminder: 'reminder', media: 'media', alert: 'system' };
const ACT = {
  timeline() {
    const al = D.alerts.slice(0, 10);
    const alerts = al.length ? al.map(a => `<div class="alert-row" data-testid="${a.type === 'log' ? 'log-alert-row' : 'system-alert-row'}" ${a.type === 'log' ? `data-level="${a.level}"` : ''}>${sh(a.sev === 'info' ? 'info' : a.sev)}<span><b>${esc(a.title)}</b>${a.n > 1 ? ` <span class="n-dim">×${a.n}</span>` : ''}${a.cat ? ` <span class="n-dim">${esc(a.cat)}</span>` : ''}</span><span class="meta">${a.type === 'log' ? a.level : a.sev === 'warn' ? 'Warning' : a.sev === 'err' ? 'Error' : 'Info'}, ${rel(nowMs() - a.at)}</span></div>`).join('')
      : `<div class="alert-row" style="grid-template-columns:22px 1fr">${sh('ok')}<span><b>Normal.</b> No alerts.</span></div>`;
    const f = S.tlFilter;
    const evs = D.events.filter(e => f === 'all' || KIND_GROUP[e.kind] === f);
    return `<section class="n-tile edge alerts" data-testid="alerts-card"><div class="logs-hd" style="margin-bottom:6px"><h3 style="font-size:15.5px">Alerts</h3><span class="n-dim" style="font-size:13px">System alerts and WARN+ logs, newest first</span></div>${alerts}</section>
      <div class="n-chips" role="group" aria-label="Filter events" style="margin-bottom:14px">${TL_FILTERS.map(x => `<button type="button" class="n-chip" data-tl="${x[0]}" data-fk="tl${x[0]}" aria-pressed="${f === x[0]}">${x[1]}</button>`).join('')}</div>
      ${evs.length ? `<ol class="n-tl">${evs.map(e => `<li class="${e.k}" data-testid="timeline-row" data-kind="${e.kind}"><span class="dot" aria-hidden="true"></span><span><b>${esc(e.title)}${e.n > 1 ? `<span class="x">×${e.n}</span>` : ''}</b><span>${esc(e.sub)}</span></span><time>${fmtClock(e.at)}</time></li>`).join('')}</ol>`
        : `<div class="n-empty">${icon('activity')}<b>${D.events.length ? 'Nothing for this filter' : 'No events yet'}</b><span>${D.events.length ? 'Pick All to see every event.' : 'Events appear here as JARVIS &amp; ANNA work: voice, devices, approvals and tool runs.'}</span></div>`}`;
  },
  logs() {
    const L = S.logs, wide = layout === 'art' || layout === 'dk';   // >= 1180: logs and reasoning both visible (stacked on narrow columns)
    const tabs = wide ? '' : `<div class="n-seg" role="tablist" data-testid="system-trace-tabs" aria-label="Logs or reasoning" style="margin-bottom:14px"><button type="button" role="tab" data-lt="logs" data-testid="trace-tab-logs" aria-selected="${L.tab === 'logs'}" aria-pressed="${L.tab === 'logs'}" data-active="${L.tab === 'logs'}" data-fk="lt1">Logs</button><button type="button" role="tab" data-lt="reasoning" data-testid="trace-tab-reasoning" aria-selected="${L.tab === 'reasoning'}" aria-pressed="${L.tab === 'reasoning'}" data-active="${L.tab === 'reasoning'}" data-fk="lt2">Reasoning</button></div>`;
    const showLogs = wide || L.tab === 'logs', showReason = wide || L.tab === 'reasoning';
    const all = D.logs.slice(-40);
    return `<div data-testid="layout-zone-trace">${tabs}
      ${showLogs ? `<div data-testid="system-trace-panel">
        <div class="logs-hd"><h3>System Logs</h3>${S.bridge ? pill('ok', 'Live', 'ok') : pill('err', 'Offline', 'err')}</div>
        <div class="toolbar">
          <button type="button" class="n-chip" data-lg="warn" data-fk="lg-warn" aria-pressed="${L.warnOnly}">${sh('warn')}WARN+</button>
          <span style="display:inline-flex;align-items:center;gap:8px;font-size:14px;color:var(--n-t3)"><span id="lb-info">INFO</span>${sw(L.info, 'lg-info', 'Show INFO lines', 'trace-info-toggle', 'aria-labelledby="lb-info"')}</span>
          <input class="n-in grow" style="min-width:140px" placeholder="Filter logs" aria-label="Filter logs" data-testid="trace-log-filter" data-fk="lg-filter" id="lg-filter" value="${esc(L.filter)}">
          <button type="button" class="n-btn sm" data-lg="pause" data-fk="lg-pause" data-testid="trace-log-pause">${icon(L.paused ? 'play' : 'pause')}${L.paused ? `Resume${L.held ? ` (${L.held})` : ''}` : 'Pause'}</button>
          <button type="button" class="n-btn sm" data-lg="clear" data-fk="lg-clear" data-testid="trace-log-clear">${icon('trash')}Clear</button>
        </div>
        ${L.dropped ? `<p class="dropped" data-testid="trace-log-dropped">${sh('warn')}<span lang="ur-Latn">Bridge ne ${L.dropped} purane logs drop kiye</span></p>` : ''}
        <div class="term" id="trace-term" data-testid="trace-log-terminal" role="log" aria-label="System logs"></div>
      </div>
      <div class="n-grid2" style="margin-top:16px">
        <section class="n-tile edge"><div class="logs-hd" style="margin-bottom:10px"><h3 style="font-size:15px">All levels</h3><span class="n-dim" style="font-size:12.5px">Including DEBUG</span></div>
          <div class="term small" data-testid="all-logs-list">${all.length ? all.map(lineHtml).join('') : '<div class="wait">Waiting for bridge events</div>'}</div></section>
        <section class="n-tile edge" data-testid="trace-system-feed"><div class="logs-hd" style="margin-bottom:10px"><h3 style="font-size:15px">System feed</h3><span class="n-dim" style="font-size:12.5px">Telemetry, diagnostics, audit</span></div>
          <div class="feedlist">${D.feed.slice(0, 6).map(f => `<div><span>${f[0]}</span><span>${esc(f[1])}</span></div>`).join('')}</div></section>
      </div>` : ''}
      ${showReason ? reasonHtml() : ''}</div>`;
  },
  tasks() {
    const now = nowMs();
    const rows = D.tasks.map(t => {
      const d = (t.due - now) / 1000, over = d < 0;
      return `<div class="n-task" data-testid="task-row" data-id="${t.id}" data-source="${t.src}"><div><div class="tt"><b>${esc(t.title)}</b>${pill(t.kind === 'Task' ? 'dev' : 'info', t.kind)}${pill(t.status === 'Overdue' ? 'warn' : t.status === 'Running' ? 'acc' : 'off', t.status, t.status === 'Overdue' ? 'warn' : t.status === 'Running' ? 'acc' : '')}</div>
        <div class="tm2"><span data-testid="task-countdown" class="${over ? 'over' : ''}">${over ? 'Overdue ' + dur(-d) : 'In ' + dur(d)}</span>, due ${fmtClock(t.due)}</div>${t.desc ? `<div class="tdesc" data-testid="task-description" lang="ur-Latn">${esc(t.desc)}</div>` : ''}</div>
        <div>${S.taskCancel === t.id ? '' : `<button type="button" class="n-btn sm" data-tk="cancel" data-id="${t.id}" data-fk="tc${t.id}" data-testid="task-cancel-btn">${icon('x')}Cancel</button>`}</div>
        ${S.taskCancel === t.id ? `<div class="cc" data-testid="task-cancel-confirm-panel">${sh('warn')}<span>Cancel "${esc(t.title)}"?</span><button type="button" class="n-btn xs danger" data-tk="yes" data-id="${t.id}" data-fk="ty${t.id}" data-testid="task-cancel-confirm">Yes, cancel</button><button type="button" class="n-btn xs" data-tk="keep" data-fk="tk${t.id}" data-testid="task-cancel-dismiss">Keep</button></div>` : ''}</div>`;
    }).join('');
    const hist = D.notifs.map(n => `<div class="mem" data-testid="reminder-history-row"><p>${esc(n.title)}</p><time>${fmtClock(n.at)}</time><div class="tags">${n.spoken ? pill('info', 'Spoken aloud') : pill('off', 'Screen only')}${n.ack ? pill('ok', 'Delivered', 'ok') : pill('warn', 'Not acknowledged yet', 'warn')}</div></div>`).join('');
    return `<div data-testid="tasks-tab-panel"><div class="toolbar"><span class="grow n-dim" style="font-size:13.5px">${D.tasks.length} item${D.tasks.length === 1 ? '' : 's'}. Times in Pakistan time.</span><button type="button" class="n-btn sm" data-tk="refresh" data-fk="tk-ref" data-testid="tasks-refresh-btn">${icon('refresh')}Refresh</button></div>
      <div data-testid="tasks-list">${rows || `<div class="n-empty" data-testid="tasks-empty">${icon('cal')}<b>No tasks or reminders</b><span>Say "Kal 9 baje yaad dilana" or type it in the chat.</span></div>`}</div>
      <div class="p-sec" style="margin-top:22px"><h4>Reminder history <small>Delivered notifications</small></h4>${hist || '<div class="n-empty" style="padding:8px">No reminders delivered yet.</div>'}</div></div>`;
  },
  notes() {
    const q = S.memQuery.toLowerCase();
    const list = D.mems.filter(m => !q || m.text.toLowerCase().includes(q));
    return `<div data-testid="memory-tab-panel"><div class="toolbar"><input class="n-in grow" id="mem-q" placeholder="Search notes" aria-label="Search notes" data-testid="memory-search" data-fk="mem-q" value="${esc(S.memQuery)}">
        ${S.memClear ? '' : `<button type="button" class="n-btn sm danger" data-mm="clear" data-fk="mm-clear" data-testid="memory-clear-btn" ${D.mems.length ? '' : 'disabled'}>${icon('trash')}Saved short-term memory saaf karo</button>`}</div>
      ${S.memClear ? `<div class="result warn" data-testid="memory-clear-confirm-panel" style="margin-bottom:14px;align-items:center;flex-wrap:wrap">${sh('warn')}<span style="flex:1" lang="ur-Latn">Short-term memory saaf ho jayegi. Pehle backup banta hai.</span><button type="button" class="n-btn xs danger" data-mm="yes" data-fk="mm-yes" data-testid="memory-clear-confirm">Haan, saaf karo</button><button type="button" class="n-btn xs" data-mm="no" data-fk="mm-no" data-testid="memory-clear-cancel">Cancel</button></div>` : ''}
      <p class="n-note" style="margin:0 0 10px" lang="ur-Latn">Live voice conversation par asar nahi hota.</p>
      <div data-testid="memory-list">${list.length ? list.map(m => `<div class="mem" data-testid="memory-row"><p>${esc(m.text)}</p><time>${rel(nowMs() - m.at)}</time><div class="tags">${pill(m.role === 'user' ? 'info' : 'acc', m.role)}${pill('dev', m.src)}</div></div>`).join('')
        : `<div class="n-empty" data-testid="memory-empty">${icon('note')}<b>${D.mems.length ? 'No notes match' : 'No saved notes yet'}</b><span>${D.mems.length ? 'Try another word.' : 'Facts you tell JARVIS &amp; ANNA, like "Ali ka number save karo", show up here.'}</span></div>`}</div></div>`;
  },
  media() {
    if (!D.intel.length) return `<div data-testid="intelligence-panel"><div class="n-empty">${icon('image')}<b>Nothing shared yet</b><span>Screenshots, weather cards, links and recordings that JARVIS &amp; ANNA share appear here.</span></div></div>`;
    const it = D.intel[S.mediaIdx] || D.intel[0];
    const TYPE = { json: 'Data card', image: 'Image', link: 'Link', audio: 'Audio', lan: 'File on the PC network', 'lan-audio': 'Recording on the PC network' };
    let body = '';
    if (S.mediaBad && it.type !== 'json' && it.type !== 'link') body = `<div class="result bad" style="align-items:center">${sh('err')}<span style="flex:1">Media unavailable. The signed link expired and one re-sign failed.</span><button type="button" class="n-btn xs" data-md="retry" data-fk="md-retry">${icon('refresh')}Retry</button></div>`;
    else if (it.type === 'json') body = `<table class="jtable" data-testid="intelligence-json"><tbody>${it.data.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join('')}</tbody></table>`;
    else if (it.type === 'image') body = `<div data-testid="intelligence-image"><button type="button" class="media-img" data-md="lightbox" data-fk="md-img" aria-label="Open the image larger"><img data-testid="intelligence-media-image" src="${it.src}" alt="${esc(it.label)}, ${fmtClock(it.at)}"></button></div>`;
    else if (it.type === 'link') body = `<a class="n-link" data-testid="intelligence-link" href="${esc(it.url)}" target="_blank" rel="noopener" style="font-size:15px">${esc(it.label)}${icon('external')}</a>`;
    else if (it.type === 'audio') body = `<div data-testid="intelligence-audio"><div class="fake-audio" data-testid="intelligence-media-audio"><button type="button" class="n-btn primary" data-md="play" data-fk="md-play" aria-label="Play">${icon('play')}</button><div class="n-bar"><i style="--v:.0"></i></div><span>0:00 / 0:04</span></div></div>`;
    else body = `<div class="lan" data-testid="${it.type === 'lan' ? 'intelligence-lan' : 'intelligence-lan-audio'}"><span>${sh('warn')} <span lang="ur-Latn">Sirf PC network par.</span> This file is on the laptop's local network, so it opens only on the same Wi-Fi.</span><code>${esc(it.url)}</code><div><button type="button" class="n-btn xs" data-md="copy" data-fk="md-copy" data-testid="intelligence-copy-lan">${icon('copy')}Copy URL</button></div></div>`;
    return `<div data-testid="intelligence-panel"><div class="media-feat">
      <div class="media-hd"><div><b>${esc(it.label)}</b><br><span>${TYPE[it.type]}, shared at ${fmtClock(it.at)}</span></div><button type="button" class="n-btn sm" data-md="toggle" data-fk="md-tog" data-testid="intelligence-toggle" aria-expanded="${S.mediaOpen}">${icon(S.mediaOpen ? 'chev-d' : 'chev-r')}${S.mediaOpen ? 'Collapse' : 'Expand'}</button></div>
      ${S.mediaOpen ? body : ''}
      <div class="p-sec" style="margin:4px 0 0"><h4>Earlier <small>Last 20</small></h4><div class="media-hist" data-testid="intelligence-history">${D.intel.map((x, i) => `<button type="button" class="n-chip" data-md="pick" data-i="${i}" data-fk="mh${i}" data-testid="intelligence-history-${x.id}" aria-pressed="${i === S.mediaIdx}">${icon({ json: 'list', image: 'image', link: 'link', audio: 'music', lan: 'download', 'lan-audio': 'music' }[x.type])}${esc(x.label.length > 26 ? x.label.slice(0, 25) + '…' : x.label)}</button>`).join('')}</div></div></div></div>`;
  },
};
function lineHtml(l) {
  return `<div class="ln" data-testid="trace-log-line" data-level="${l.lvl}"><span class="tm">${l.ts}</span><span class="lv" data-l="${l.lvl}">${l.lvl}</span><span class="ct">${esc(l.cat)}</span><span>${esc(l.msg)}${l.n > 1 ? `<span class="x">×${l.n}</span>` : ''}</span></div>`;
}
function renderTerm() {
  const t = $('#trace-term'); if (!t) return;
  const L = S.logs, f = L.filter.toLowerCase();
  const lines = D.logs.filter(l => l.lvl !== 'DEBUG' && (L.info || l.lvl !== 'INFO') && (!L.warnOnly || ['WARNING', 'ERROR', 'CRITICAL'].includes(l.lvl)) && (!f || (l.msg + ' ' + l.cat).toLowerCase().includes(f)));
  t.innerHTML = lines.length ? lines.map(lineHtml).join('') : `<div class="wait">${D.logs.length ? 'No lines match the filter.' : 'Waiting for bridge events'}</div>`;
}
function reasonHtml() {
  if (S.crash) return `<div class="panel-error" data-testid="panel-error" style="margin-top:16px"><b>${sh('err')}Reasoning stopped working</b><span>The rest of the dashboard keeps running.</span><button type="button" class="n-btn sm" data-act="retry-card" data-testid="panel-error-retry">${icon('refresh')}Retry</button></div>`;
  const p = D.plans[S.logs.plan];
  return `<section class="n-tile edge reason" data-testid="trace-reasoning-card" style="margin-top:16px">
    <div class="logs-hd" style="margin-bottom:12px"><h3 style="font-size:15.5px">Reasoning</h3>
      <select class="n-in" style="height:34px;font-size:13.5px" aria-label="Choose a turn" data-testid="trace-reasoning-select" data-fk="rs-sel" id="rs-sel">${D.plans.map((x, i) => `<option value="${i}" ${i === S.logs.plan ? 'selected' : ''}>${x.intent}, ${rel(x.ago * 1000)}</option>`).join('')}</select></div>
    <div class="r-top"><b>${p.intent}</b>${p.amb ? `<span data-testid="trace-ambiguity-badge">${pill('warn', 'Ambiguous', 'warn')}</span>` : ''}${pill('info', 'Source: ' + p.src)}<span class="n-dim" style="font-size:13px">${fmtClock(nowMs() - p.ago * 1000, true)}</span></div>
    <div class="conf" data-testid="trace-confidence-bars">${p.conf.map((c, i) => `<div class="cr"><span>${c[0]}</span><div class="n-bar"><i style="--v:${c[1]};--c:${i ? '#4b5b86' : 'var(--acc-line)'}"></i></div><b>${Math.round(c[1] * 100)}%</b></div>`).join('')}</div>
    <div data-testid="trace-plan-progress" style="font:600 13.5px/19px var(--font-ui);color:#e6ebf7">Step ${p.step[0]}/${p.step[1]} - ${p.step[2]} (${p.step[3]})</div>
    <ol class="steps">${p.steps.map(s => `<li data-testid="trace-reasoning-step">${sh(s[1])}<code>${s[0]}</code><em>${s[2]}</em></li>`).join('')}</ol></section>`;
}

/* =====================================================================================================
   SETTINGS: every section of today's drawer (same testids) + Notifications, SIM lookup, About
   ===================================================================================================== */
const SET_SECS = [['session', 'Session', 'key'], ['connection', 'Connection', 'wifi'], ['persona', 'Persona', 'swap'], ['voice', 'Voice & conversation', 'volume'], ['voice-id', 'Voice-ID & wake', 'vprint'], ['automation', 'Automation', 'laptop'], ['vision', 'Vision', 'eye'], ['confirmations', 'Confirmations', 'shield'], ['notifications', 'Notifications', 'bell'], ['sim', 'SIM lookup', 'sim'], ['about', 'About', 'info']];
function fstat(f) {
  const st = S.fstate[f] || (S.bridge ? null : 'offline');
  if (!st) return '';
  const M = { applied: ['applied', icon('check') + 'Applied'], pending: ['pending', sh('busy') + 'Pending, waiting for the agent'], agent: ['agent', icon('clock') + 'Agent update pending'], error: ['error', icon('alert') + (S.ferr && S.ferr[f] || 'Not applied')], offline: ['error', icon('alert') + 'Bridge offline: changes are disabled'] };
  const m = M[st];
  return `<p class="fstat" data-testid="${st === 'agent' ? 'settings-agent-pending-' : 'settings-status-'}${f}" data-state="${m[0]}">${m[1]}</p>`;
}
const segBtns = (field, opts, cur, tid, label) => `<span class="n-seg" role="group" aria-label="${label}" ${tid ? `data-testid="${tid}"` : ''}>${opts.map(o => `<button type="button" data-set="${field}" data-v="${o[0]}" data-fk="s-${field}-${o[0]}" aria-pressed="${cur === o[0]}" data-active="${cur === o[0]}" ${S.bridge ? '' : 'disabled'}>${o[1]}</button>`).join('')}</span>`;
const fld = (title, sub, ctl, f) => `<div class="field"><div class="fl"><b>${title}</b>${sub ? `<small>${sub}</small>` : ''}</div><div class="fc">${ctl}</div>${f ? fstat(f) : ''}</div>`;
const swc = (field, on, label, tid) => `<button type="button" class="n-sw" role="switch" aria-checked="${on}" aria-label="${label}" data-set="${field}" data-fk="s-${field}" ${tid ? `data-testid="${tid}"` : ''} ${S.bridge ? '' : 'disabled'}></button>`;
const SETS = {
  session: () => hd('key', '#80befd', 'Session', 'Who is signed in on this device.') + fld(`${OWNER.name} (owner)`, 'Password and authenticator', pill('ok', 'Signed in', 'ok')) + fld('Session ends', 'You stay signed in for 6 hours, then sign in again', '<span class="n-num" style="color:#fff;font-weight:600">3:42 AM</span>') +
    fld('Sign out on this device', 'Socket, voice and camera stop', `<button type="button" class="n-btn sm" data-act="logout" data-fk="s-lo" data-testid="settings-logout">${icon('logout')}Sign out</button>`),
  connection: () => hd('wifi', '#80befd', 'Connection', 'How this dashboard reaches the VPS.') +
    fld('Bridge base', 'Every request goes through it', `<input class="n-in n-mono" value="/vortex" readonly aria-label="Bridge base" style="width:120px">`) +
    `<div class="conn-rows">${[['Bridge', S.bridge ? 'Live' : 'Offline', S.bridge ? 'ok' : 'err'], ['Agent (VPS)', AGENT[S.agent].label, AGENT[S.agent].s], ['Laptop', LAPTOP[S.laptop].short, LAPTOP[S.laptop].s], ['Voice', VS[V.mode].compact, ['offline'].includes(V.mode) ? 'err' : ['agent-absent', 'audio-blocked', 'muted'].includes(V.mode) ? 'warn' : 'ok']].map(r => `<div class="n-tile edge"><b>${r[0]}</b><span>${sh(r[2])}${r[1]}</span></div>`).join('')}</div>
    <div class="secure" data-tone="${S.bridge ? 'ok' : 'err'}">${icon(S.bridge ? 'lock' : 'alert')}<span>${S.bridge ? 'Secure link active. Cookie session over TLS.' : 'Bridge offline. Chat messages wait in a queue and are sent once when it is back.'}</span></div>
    <div class="n-actions"><button type="button" class="n-btn sm" data-act="reconnect" data-fk="s-rc" title="Reconnect: reloads the dashboard and rejoins voice">${icon('refresh')}Reconnect</button></div><p class="n-note">Reconnect reloads the dashboard for now; voice rejoins with the mic off.</p>`,
  persona: () => hd('swap', '#80befd', 'Persona', 'Who answers by default.') +
    fld('Who answers', `${P[S.persona].name} uses the ${P[S.persona].voice} voice`, segBtns('persona', [['jarvis', 'JARVIS'], ['anna', 'ANNA']], S.persona, 'settings-persona', 'Persona'), 'persona'),
  voice: () => hd('volume', '#80befd', 'Voice &amp; conversation', 'How JARVIS &amp; ANNA speak and when they answer.') + `<div data-testid="settings-voice-conversation">` +
    fld('Voice', `Used by ${P[S.persona].name}. Default ${P[S.persona].voice}`, `<select class="n-in" data-set="voice" data-fk="s-voice" data-testid="settings-voice-select" aria-label="Voice" style="width:170px" ${S.bridge ? '' : 'disabled'}>${VOICES.map(v => `<option ${v === (S.persona === 'anna' ? S.set.voiceA : S.set.voice) ? 'selected' : ''}>${v}</option>`).join('')}</select>`, 'voice') +
    fld('Language', 'Auto follows what you speak', `<select class="n-in" data-set="lang" data-fk="s-lang" data-testid="settings-language-select" aria-label="Language" style="width:170px" ${S.bridge ? '' : 'disabled'}>${[['auto', 'Auto'], ['ur', 'Urdu (ur)'], ['hi', 'Hindi (hi)'], ['en', 'English (en)']].map(o => `<option value="${o[0]}" ${o[0] === S.set.lang ? 'selected' : ''}>${o[1]}</option>`).join('')}</select>`, 'language') +
    fld(`Turn mode ${S.set.turnRestart ? `<span data-testid="settings-turn-mode-restart-badge" style="margin-left:6px">${pill('warn', 'Restart required', 'warn')}</span>` : ''}`, 'Secure gated checks your voice before every action', segBtns('turn', [['gated', 'Secure gated'], ['fast', 'Fast']], S.set.turn, 'settings-turn-mode', 'Turn mode'), 'turnMode') +
    fld('Follow-up window', 'Talk again without the wake word', `<select class="n-in" data-set="followup" data-fk="s-fu" data-testid="settings-wake-followup-select" aria-label="Follow-up window" style="width:100px" ${S.bridge ? '' : 'disabled'}>${[0, 10, 20, 30].map(s => `<option value="${s}" ${s === S.followup ? 'selected' : ''}>${s} s</option>`).join('')}</select>`, 'wakeFollowupS') +
    fld('Transcript script', 'How your Urdu words are written', segBtns('script', [['roman', 'Roman'], ['native', 'Native']], S.set.script, 'settings-transcript-script', 'Transcript script'), 'transcriptScript') +
    fld('Speak typed replies', 'Auto speaks when your mic is on', segBtns('speak', [['auto', 'Auto'], ['always', 'Always'], ['never', 'Never']], S.set.speak, 'settings-speak-typed-replies', 'Speak typed replies'), 'speakTypedReplies') +
    fld('Quiet hours', 'Saved when you leave the field or press Enter', `<input class="n-in n-mono" data-set="quiet" data-fk="s-quiet" data-testid="settings-quiet-hours" value="${esc(S.set.quiet)}" aria-label="Quiet hours" style="width:140px" ${S.bridge ? '' : 'disabled'}>`, 'quietHours') + `</div>`,
  'voice-id': () => hd('vprint', '#80befd', 'Voice-ID &amp; wake', 'Who may command, and how a command starts.') + `<div data-testid="settings-voice-id-block">` +
    fld('Wake word', `Say "${P[S.persona].name}" first. Enforced: yes`, swc('wakeWord', S.wake, 'Wake word', 'settings-wakeWord'), 'wakeWord') +
    fld('Agent listening', S.listening ? 'Hears the room through your mic' : 'Muted. Typed chat keeps working', `<button type="button" class="n-sw" role="switch" aria-checked="${S.listening}" aria-label="Agent listening" data-set="muted" data-fk="s-muted" data-testid="settings-muted" data-muted="${!S.listening}" ${S.bridge ? '' : 'disabled'}></button>`) +
    fld(`Voice-ID ${S.set.turn === 'fast' ? `<span data-testid="settings-voice-id-monitor-badge" style="margin-left:6px">${pill('info', 'Monitor only in Fast')}</span>` : ''}`, S.vid.enrolled ? 'Commands from other voices are ignored' : 'Disabled until you enroll your voice (below)', swc('voiceId', S.vid.on && S.vid.enrolled, 'Voice-ID', 'settings-voiceId').replace('<button', S.vid.enrolled ? '<button' : '<button disabled title="Enroll your voice first"'), 'voiceId') +
    fld('Unknown voice', 'What happens when someone else speaks', segBtns('reject', [['silent', 'Silent'], ['polite', 'Polite']], S.vid.reject, 'settings-voice-id-reject', 'Unknown voice'), 'voiceIdReject') +
    fld('Threshold', 'Saved when you release the slider', `<input type="range" class="n-range" min="0.2" max="0.9" step="0.01" value="${S.vid.th}" style="--p:${((S.vid.th - .2) / .7 * 100).toFixed(1)}%" data-set="th" data-fk="s-th" data-testid="settings-voice-id-threshold" aria-label="Voice-ID threshold" ${S.bridge ? '' : 'disabled'}><b class="n-num" style="width:40px;text-align:right;color:#fff" id="s-th-v">${S.vid.th.toFixed(2)}</b>`, 'voiceIdThreshold') +
    fld(S.vid.enrolled ? `${sh('ok')} Voice-ID enrolled` : `${sh('off')} Not enrolled`, S.vid.enrolled ? `${S.vid.samples} samples, score 0.91` : 'Record 20 seconds once', `<button type="button" class="n-btn sm" data-act="enroll" data-fk="s-en" data-testid="settings-voice-enroll-link">${icon('vprint')}${S.vid.enrolled ? 'Re-enroll' : 'Enroll'}</button>`) + `</div>`,
  automation: () => hd('laptop', '#bfbdf9', 'Automation', 'What the laptop may do for you.') +
    fld('Desktop control', 'Open apps, click and type on the PC', swc('dc', S.set.dc, 'Desktop control', 'settings-automation-desktop_control'), 'automationDesktopControl') +
    fld('Clipboard helper', 'Read and fill the clipboard', swc('clip', S.set.clip, 'Clipboard helper', 'settings-automation-clipboard_helper'), 'automationClipboardHelper') +
    fld('Speak clipboard tips', 'Say what was copied', swc('clipSpeak', S.set.clipSpeak, 'Speak clipboard tips', 'settings-automation-clipboard_speak'), 'automationClipboardSpeak') +
    fld('Voice may confirm high-risk actions', 'Off: high-risk actions need a tap or a hold', swc('voiceHigh', S.set.voiceHigh, 'Voice may confirm high-risk actions', 'settings-automation-voice_confirm_high'), 'automationVoiceConfirmHigh'),
  vision: () => hd('eye', '#80befd', 'Vision', 'When the camera frames reach the agent.') +
    fld('Camera frames', 'Only while the camera is on', segBtns('vision', [['live', 'Live'], ['ask', 'Sirf poochne par']], S.set.vision, 'settings-vision-mode', 'Vision mode'), 'visionMode'),
  confirmations: () => hd('shield', '#f9c74c', 'Confirmations', 'These tools always ask before they run.') +
    `<div class="guarded" data-testid="settings-guarded-tools-list">${GUARDED.map(g => `<code>${g}</code>`).join('')}</div><p class="n-note" lang="ur-Latn">List .env (JARVIS_CONFIRM_EXTRA/SKIP) se badalti hai.</p>`,
  notifications: () => hd('bell', '#80befd', 'Notifications', 'Reminders and approvals when this tab is in the background.') +
    fld('Desktop notifications', S.notifPerm === 'granted' ? 'On for this browser' : S.notifPerm === 'denied' ? 'Blocked. Allow them in the browser site settings' : 'Off', S.notifPerm === 'default' ? `<button type="button" class="n-btn sm" data-act="notif-on" data-fk="s-nt">Turn on</button>` : pill(S.notifPerm === 'granted' ? 'ok' : 'err', S.notifPerm === 'granted' ? 'On' : 'Blocked', S.notifPerm === 'granted' ? 'ok' : 'err')) +
    fld('Approval alert', 'Tab title flashes and a short chime plays while an approval waits', pill('ok', 'On', 'ok')),
  sim: () => hd('sim', '#80befd', 'SIM lookup', 'Owner and network of a Pakistani number, through an authorized provider.') +
    fld('Provider', 'SIM_LOOKUP_API_URL and SIM_LOOKUP_API_KEY on the VPS', pill('ok', 'Configured', 'ok')) +
    fld('Workspace', 'Every lookup needs a purpose and is audited', `<button type="button" class="n-btn sm" data-act="open-sim" data-fk="s-sim">${icon('external')}Open workspace</button>`),
  about: () => hd('info', '#80befd', 'About', 'Versions on this device.') + kv([['Dashboard', '3.0.0 (neon)'], ['Agent', '2.14.0'], ['Desktop Node', '1.8.2'], ['Build', '2026-10-02']]) +
    (Q.has('debug') ? `<p class="n-note">LiveKit identity: <code class="n-mono">matloob-browser-7f2a</code></p>` : ''),
};
function renderSettings() {
  const el = $('#page-settings');
  if (!el.firstElementChild) {
    el.innerHTML = `<div class="set-wrap"><nav class="set-index" aria-label="Settings sections">${SET_SECS.map(s => `<a href="#/settings/${s[0]}" data-sec="${s[0]}">${icon(s[2])}${s[1].replace('&', '&amp;')}</a>`).join('')}</nav><div class="set-main n-scroll" id="set-main">${SET_SECS.map(s => `<section class="n-card edge" data-anchor="${s[0]}" data-sp="${s[0]}"></section>`).join('')}<div class="page-end"></div></div></div>`;
    $('#set-main').addEventListener('scroll', spy, { passive: true });
    el.addEventListener('scroll', spy, { passive: true });
  }
  SET_SECS.forEach(s => { const c = $(`#page-settings [data-sp="${s[0]}"]`); keepFocus(c, () => { c.innerHTML = SETS[s[0]](); }); });
  spy(); requestAnimationFrame(tabStripFades);
}
function spy() {
  const secs = $$('#page-settings [data-sp]'); if (!secs.length) return;
  const top = $('#page-settings').getBoundingClientRect().top + 90;
  let cur = secs[0].dataset.sp;
  for (const s of secs) if (s.getBoundingClientRect().top <= top) cur = s.dataset.sp;
  const mainEl = $('#set-main');
  if (mainEl.scrollTop + mainEl.clientHeight >= mainEl.scrollHeight - 4 && layout === 'art') cur = secs[secs.length - 1].dataset.sp;
  $$('#page-settings .set-index a').forEach(a => a.setAttribute('aria-current', String(a.dataset.sec === cur)));
}
// a field: optimistic, pending until the agent echoes it (simulated), errors and reverts are shown in place
const WIRE = {"turnMode": "turn_mode", "wakeFollowupS": "wake_followup_s", "transcriptScript": "transcript_script", "speakTypedReplies": "speak_typed_replies", "quietHours": "quiet_hours", "wakeWord": "wake_word", "voiceIdReject": "voice_id_reject", "voiceIdThreshold": "voice_id_threshold", "voiceId": "voice_id", "automationDesktopControl": "desktop_control", "automationClipboardHelper": "clipboard_helper", "automationClipboardSpeak": "clipboard_speak", "automationVoiceConfirmHigh": "voice_confirm_high", "visionMode": "vision_mode"};
function setField(f, v) {
  if (!S.bridge) return;
  const map = { persona: 'persona', voice: 'voice', lang: 'language', turn: 'turnMode', followup: 'wakeFollowupS', script: 'transcriptScript', speak: 'speakTypedReplies', quiet: 'quietHours', wakeWord: 'wakeWord', voiceId: 'voiceId', reject: 'voiceIdReject', th: 'voiceIdThreshold', dc: 'automationDesktopControl', clip: 'automationClipboardHelper', clipSpeak: 'automationClipboardSpeak', voiceHigh: 'automationVoiceConfirmHigh', vision: 'visionMode' };
  const key = map[f];
  if (f === 'persona') { setPersona(v); S.fstate.persona = 'pending'; renderSettings(); setTimeout(() => { S.fstate.persona = 'applied'; renderSettings(); }, 700); return; }
  if (f === 'speak' && v === 'always') { S.fstate[key] = 'pending'; renderSettings(); setTimeout(() => { S.fstate[key] = 'error'; S.ferr = Object.assign(S.ferr || {}, { [key]: 'Not applied: the agent rejected "Always". Value kept at Auto.' }); renderSettings(); toast({ s: 'warn', text: 'Setting sync timeout — value revert ho gaya' }); }, 900); return; }
  if (f === 'voice') { if (S.persona === 'anna') S.set.voiceA = v; else S.set.voice = v; }
  else if (f === 'followup') S.followup = +v;
  else if (f === 'wakeWord') S.wake = v;
  else if (f === 'voiceId') S.vid.on = v;
  else if (f === 'reject') S.vid.reject = v;
  else if (f === 'th') S.vid.th = +v;
  else if (f === 'turn') { S.set.turn = v; S.set.turnRestart = true; }
  else S.set[f] = v;
  S.fstate[key] = 'pending'; renderSettings();
  setTimeout(() => { S.fstate[key] = f === 'followup' ? 'agent' : 'applied'; renderSettings(); }, f === 'followup' ? 1600 : 700);
  addLog('INFO', 'settings', `settings_update ${WIRE[key] || key}=${v}`);
}
