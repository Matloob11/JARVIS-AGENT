
/* ---------------------------------------------------------------- layout
   dk  >= 1180 px wide: the reference's three columns as a fluid grid (real text sizes, no transform scaling)
   tl  1024-1179 px: icon rail + the same main column and right panel
   ph  < 1024 px: phone shell
   art the measured 1672x941 artboard, kept only as the reference frame for the visual diff:
       ?still=1 at exactly 1672x941, ?layout=art, or the prototype panel's "Reference artboard" */
const LAYOUT_Q = ['art', 'dk', 'tl', 'ph'].includes(Q.get('layout')) ? Q.get('layout') : null;
let layoutForce = null;
let hdr = { compact: false, greet: true, tiny: false };
function pickLayout(w, h) {
  const f = layoutForce || LAYOUT_Q;
  if (f) return f;
  if (STILL && w === 1672 && h === 941) return 'art';
  return w >= 1180 ? 'dk' : w >= 1024 ? 'tl' : 'ph';
}
function fit() {
  const w = innerWidth, h = innerHeight;
  const next = pickLayout(w, h);
  const changed = next !== layout;
  if (changed) {
    layout = next;
    html.classList.toggle('fluid', layout !== 'art'); html.classList.toggle('comp', layout === 'dk' || layout === 'tl');
    ['art', 'dk', 'tl', 'ph'].forEach(k => html.classList.toggle(k, layout === k));
    $('.hero-flare').setAttribute('preserveAspectRatio', layout === 'art' ? 'xMidYMid meet' : 'none');
  }
  const vp = $('#viewport');
  if (layout === 'art') {
    let s = Math.max(.5, Math.min(w / 1672, h / 941));
    if (Math.abs(s - 1) < 0.004) s = 1;
    stageScale = s;
    const ox = Math.max(0, (w - 1672 * s) / 2), oy = Math.max(0, (h - 941 * s) / 2);
    const fits = 1672 * s <= w + .5 && 941 * s <= h + .5;
    vp.style.overflow = fits ? 'hidden' : 'auto';
    $('#sizer').style.width = (fits ? 0 : 1672 * s) + 'px'; $('#sizer').style.height = (fits ? 0 : 941 * s) + 'px';
    stage.style.transform = `translate(${ox}px, ${oy}px) scale(${s})`;
    // at 1:1 the stage gets its own compositing layer: Chrome then uses grayscale text antialiasing, as in the
    // reference image. When scaled, the layer is dropped so text is re-rastered crisply at the new size.
    stage.style.willChange = s === 1 ? 'transform' : 'auto';
  } else {
    stageScale = 1;
    stage.style.transform = ''; stage.style.willChange = 'auto';
    vp.style.overflow = 'hidden'; $('#sizer').style.width = $('#sizer').style.height = '0px';
  }
  // header: compact pill labels when the header row is narrow (Electron reserves 140 px for the window buttons)
  const tb = $('.topbar');
  const avail = layout === 'art' ? 1400 : tb.clientWidth - (html.classList.contains('is-electron') && layout !== 'ph' ? 140 : 0);
  const nh = { compact: layout === 'ph' ? w < 600 : layout === 'tl' ? avail < 760 : layout === 'dk' ? avail < 1000 : false,
    greet: layout === 'art' || (layout === 'dk' && avail >= 1120), tiny: layout === 'ph' && w <= 340 };
  if (changed || nh.compact !== hdr.compact || nh.greet !== hdr.greet || nh.tiny !== hdr.tiny) {
    hdr = nh; tb.dataset.greet = hdr.greet ? 'on' : 'off';
    if (typeof renderHeader === 'function' && $('#pill-voice .ping')) renderHeader();
  }
  if (changed) {
    if (layout !== 'ph' && S.page === 'chat') { location.hash = '#/overview'; }
    if (layout !== 'ph') html.classList.remove('typing');
    html.classList.toggle('route-chat', layout === 'ph' && S.page === 'chat');
    renderHeader(); renderVoicePill(); closePop(); renderSheet(); selectTab(S.panelTab, true);
    if (S.page === 'activity') renderActivityBody();
    if (typeof renderConfirm === 'function' && D.queue.length) renderConfirm();
  } else if (layout !== 'art') selectTab(S.panelTab, true);
  resizeCanvases();
  if (S.page === 'agents') paintAgentsCanvases();
  tabStripFades();
}
// Activity sub-tabs scroll sideways on narrow columns: mark which edge has more (CSS fades it)
function tabStripFades() {
  for (const t of $$('.n-tabs, html.fluid .set-index')) {
    const l = t.scrollLeft > 4, r = t.scrollLeft + t.clientWidth < t.scrollWidth - 4;
    const v = l && r ? 'both' : r ? 'right' : l ? 'left' : '';
    if (v) t.dataset.more = v; else delete t.dataset.more;
  }
}
document.addEventListener('scroll', e => { if (e.target.classList && (e.target.classList.contains('n-tabs') || e.target.classList.contains('set-index'))) tabStripFades(); }, true);

/* ---------------------------------------------------------------- toasts (max 3, bottom centre; above the tab bar on phones) */
function toast(o) {
  if (typeof o === 'string') o = { text: o };
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'ntoast';
  el.innerHTML = (o.s ? sh(o.s) : '') + `<span class="tx"${o.lang ? ` lang="${o.lang}"` : ''}>${esc(o.text)}${o.sub ? `<small>${esc(o.sub)}</small>` : ''}</span>`;
  if (o.action) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'n-btn xs'; b.textContent = o.action[0];
    b.onclick = () => { o.action[1](); dismiss(); }; el.append(b);
  }
  const x = document.createElement('button'); x.type = 'button'; x.className = 'x'; x.setAttribute('aria-label', 'Dismiss'); x.innerHTML = icon('x');
  x.onclick = () => dismiss(); el.append(x);
  box.append(el);
  while (box.children.length > 3) box.firstElementChild.remove();
  let gone = false;
  function dismiss() { if (gone) return; gone = true; el.classList.add('out'); setTimeout(() => el.remove(), reduced() ? 0 : 260); }
  if (!STILL) setTimeout(dismiss, o.ms || (o.action ? 8000 : 4200));
  return el;
}

/* ---------------------------------------------------------------- activity feed + logs (client side) */
function addEvent(kind, k, title, sub) {
  const last = D.events[0];
  if (last && last.title === title && last.sub === sub && nowMs() - last.at < 60e3) { last.n = (last.n || 1) + 1; last.at = nowMs(); }
  else D.events.unshift({ kind, k, title, sub, at: nowMs() });
  if (D.events.length > 200) D.events.length = 200;
  renderRecent(true);
  if (S.page === 'activity' && S.actTab === 'timeline') renderActivityBody();
  renderNavMarks();
}
function addLog(lvl, cat, msg) {
  const l = { lvl, cat, msg, ts: tStamp(0), t: 0, fresh: true };
  if (S.logs.paused) { S.logs.held++; D.logsHeld = (D.logsHeld || []).concat(l); }
  else { D.logs.push(l); if (D.logs.length > 300) { D.logs.shift(); S.logs.dropped++; } }
  if (lvl === 'ERROR' || lvl === 'CRITICAL' || lvl === 'WARNING') { D.alerts.unshift({ type: 'log', sev: lvl === 'WARNING' ? 'warn' : 'err', level: lvl, title: msg, cat, at: nowMs() }); D.alerts = D.alerts.slice(0, 20); }
  if (S.page === 'activity' && S.actTab === 'logs') renderActivityBody();
  renderNavMarks();
}
// Recent Activity (Overview): the four newest rows; the nominal frame keeps the replica's DOM untouched
let recentDirty = false;
function renderRecent(fresh) {
  const list = $('#act-list');
  if (!fresh && !recentDirty) return;
  recentDirty = true;
  list.innerHTML = '';
  D.events.slice(0, 4).forEach((e, i) => {
    const li = document.createElement('li');
    li.className = 'act-row ' + e.k + (fresh && i === 0 ? ' new' : '');
    li.dataset.testid = 'activity-row'; li.setAttribute('data-testid', 'activity-row'); li.dataset.kind = e.kind;
    li.innerHTML = '<span class="dot"></span><span class="ttl"></span><span class="sub"></span><span class="tm"></span>';
    li.querySelector('.ttl').textContent = e.title; if (e.n > 1) li.querySelector('.ttl').insertAdjacentHTML('beforeend', `<span class="x">×${e.n}</span>`);
    li.querySelector('.sub').textContent = e.sub; li.querySelector('.tm').textContent = fmtClock(e.at);
    list.append(li);
  });
  if (!D.events.length) list.innerHTML = '<li class="n-empty" style="padding:10px 20px 0 60px;justify-items:start;text-align:left">No events yet. They appear here as JARVIS &amp; ANNA work.</li>';
  $('.act-line').hidden = D.events.length < 2;
}

/* ---------------------------------------------------------------- header: pills, avatar, greeting */
const PILL_GEO = { system: [764, 163, 48], devices: [943, 205, 53], voice: [1163, 153, 53] };
const PILL_NOMINAL = { system: 'System Online', devices: '3 Connected Devices', voice: 'Voice Ready' };
function layoutPills() {
  const ids = ['system', 'devices', 'voice'];
  const nominal = ids.every(k => $('#lbl-' + k).textContent === PILL_NOMINAL[k]);
  if (nominal) { ids.forEach(k => { const p = $('#pill-' + k); p.style.left = ''; p.style.width = ''; }); return; }
  let x = PILL_GEO.system[0]; const widths = {};
  ids.forEach(k => {
    const lbl = $('#lbl-' + k), g = PILL_GEO[k];
    widths[k] = lbl.textContent === PILL_NOMINAL[k] ? g[1] : Math.ceil(g[2] + lbl.offsetWidth + 18);
  });
  const total = widths.system + 16 + widths.devices + 15 + widths.voice;
  x = Math.min(764, 1350 - total);
  ids.forEach((k, i) => { const p = $('#pill-' + k); p.style.left = x + 'px'; p.style.width = widths[k] + 'px'; x += widths[k] + (i ? 15 : 16); });
}
function deviceCount() { return S.bridge ? 1 + (S.agent === 'online' || S.agent === 'paused' ? 1 : 0) + (LAPTOP[S.laptop].online ? 1 : 0) : 0; }
function renderHeader() {
  const compact = hdr.compact;
  // P1 agent
  let a, s;
  if (!S.bridge) { a = ['System Unreachable', 'Unreachable']; s = 'warn'; }
  else if (S.agent === 'unknown') { a = ['System Unknown', 'Unknown']; s = 'off'; }
  else if (S.agent === 'restarting') { a = ['System Restarting', 'Restarting']; s = 'busy'; }
  else if (S.agent === 'paused') { a = ['System Paused', 'Paused']; s = 'warn'; }
  else if (S.agent === 'offline') { a = ['System Offline', 'Offline']; s = 'err'; }
  else { a = ['System Online', 'Online']; s = null; }
  $('#lbl-system').textContent = compact ? a[1] : a[0];
  const dot = $('#dot-agent'); if (s) dot.dataset.s = s; else delete dot.dataset.s;
  const ps = $('#pill-system'); ps.dataset.active = String(S.bridge && S.agent === 'online');
  ps.title = `Agent (VPS), ${S.bridge ? 'last seen 3 s ago' : 'bridge offline'}`;
  // P2 devices
  const n = deviceCount();
  $('#lbl-devices').textContent = S.bridge ? (hdr.tiny ? String(n) : compact ? `${n} Devices` : `${n} Connected Device${n === 1 ? '' : 's'}`) : (compact ? 'Offline' : 'Bridge Offline');
  const pd = $('#pill-devices'); pd.dataset.active = String(S.bridge);
  pd.setAttribute('aria-label', S.bridge ? `${n} Connected Device${n === 1 ? '' : 's'}` : 'Bridge Offline');
  const mk = $('#mark-devices'), lp = LAPTOP[S.laptop];
  const markS = !S.bridge ? 'err' : camLive() ? 'rec' : lp.mark || null;
  if (markS) { mk.hidden = false; mk.dataset.s = markS; } else mk.hidden = true;
  pd.title = S.bridge ? `This browser, Agent (VPS) and laptop. Laptop: ${lp.label}${camLive() ? '. Camera is on' : ''}` : 'Bridge offline: the dashboard cannot reach the VPS';
  // avatar badge + greeting
  const b = $('#avatar-badge'); b.hidden = !S.unread; b.textContent = S.unread > 99 ? '99+' : S.unread;
  $('#account').setAttribute('aria-label', `Account and reminders${S.unread ? `, ${S.unread} unread` : ''}`);
  if (!STILL) { const hr = new Date(nowMs()).getHours(); $('#greet1').textContent = hr < 12 ? 'Good morning,' : hr < 17 ? 'Good afternoon,' : 'Good evening,'; }
  renderVoicePill();
  if (layout === 'art') layoutPills();
  renderNavMarks();
}
function renderNavMarks() {
  if (S.page === 'activity' && S.actTab === 'timeline') D.alerts.forEach(a => { a.seen = true; });
  const warnAlerts = D.alerts.filter(x => !x.seen && x.sev !== 'info').length;
  const nb = $('#nav-activity-badge');
  nb.hidden = !warnAlerts; nb.textContent = warnAlerts; nb.dataset.tone = D.alerts.some(x => !x.seen && x.sev === 'err') ? 'err' : 'warn';
  const agentsBad = S.agent === 'offline' || S.laptop === 'panic';
  $('#nav-agents-dot').hidden = !agentsBad;
  $('#tb-agents-mark').hidden = !agentsBad;
  const am = $('#tb-act-mark'); am.hidden = !warnAlerts; am.textContent = warnAlerts;
  const cm = $('#tb-chat-mark'); cm.hidden = !D.queue.length; cm.textContent = D.queue.length;
}

/* ---------------------------------------------------------------- routing (hash routes, pages stay mounted) */
const TITLES = {
  overview: ['Overview', 'Control, collaborate, and automate with JARVIS & ANNA.'],
  agents: ['Agents', 'Personas, voice and the devices JARVIS & ANNA run on.'],
  activity: ['Activity', 'Events, logs, tasks, notes and media in one place.'],
  settings: ['Settings', 'Session, voice, automation and safety.'],
  chat: ['Chat', 'Talk or type to JARVIS & ANNA.'],
};
function route() {
  const m = location.hash.match(/^#\/(\w+)(?:\/([\w-]+))?/) || [, 'overview', ''];
  let page = TITLES[m[1]] ? m[1] : 'overview', sub = m[2] || '';
  if (page === 'chat' && layout !== 'ph') { page = 'overview'; setTimeout(() => { selectTab('chat'); $('#msg').focus(); }, 0); }
  const prev = S.page;
  S.page = page; S.sub = sub;
  html.classList.toggle('route-chat', page === 'chat');
  const navPage = page === 'chat' ? 'overview' : page;
  $$('.nav-item').forEach(a => { const on = a.dataset.page === navPage && page !== 'chat'; a.classList.toggle('active', on); on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'); });
  $('#nav-overview .ic').classList.toggle('fill', page === 'overview');
  $$('#tabbar button').forEach(b => { const on = b.dataset.tab === page; on ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'); b.dataset.active = String(on); });
  const ov = $('#page-overview');
  ov.hidden = page !== 'overview'; ov.style.display = page === 'overview' ? '' : 'none';
  ['agents', 'activity', 'settings'].forEach(k => { $('#page-' + k).hidden = page !== k; });
  $('#page-title').textContent = TITLES[page][0];
  $('#page-sub').textContent = TITLES[page][1];
  document.title = 'JARVIS & ANNA' + (page === 'overview' ? '' : ' · ' + TITLES[page][0]);
  if (page === 'agents') { if (prev !== 'agents' || !$('#page-agents').childElementCount) renderAgents(); paintAgentsCanvases(); if (sub) scrollToAnchor('#page-agents', sub); }
  if (page === 'activity') { if (sub && ACT_TABS.some(t => t[0] === sub)) S.actTab = sub; renderActivity(); }
  if (page === 'settings') { renderSettings(); if (sub) scrollToAnchor('#page-settings .set-main', sub); }
  if (page === 'overview') { resizeCanvases(); }
  renderNavMarks(); renderSheet(); closePop();
  if (prev !== page && prev && routedOnce) {
    const a = document.activeElement;
    if (a && a !== document.body && a.closest('.nav-item,#tabbar,.n-link,.card-open,a[href^="#/"]')) $('#page-title').focus({ preventScroll: true });
    srSay(TITLES[page][0] + ' page');
  }
  routedOnce = true;
  schedule();
}
let routedOnce = false;
function srSay(msg) { const el = $('#sr'); el.textContent = ''; setTimeout(() => { el.textContent = msg; }, 60); }
function scrollToAnchor(scope, id) {
  requestAnimationFrame(() => { const t = $(`${scope} [data-anchor="${id}"]`); if (t) t.scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' }); });
}
function go(page, sub) { location.hash = '#/' + page + (sub ? '/' + sub : ''); }

/* ---------------------------------------------------------------- popovers (Agent, Devices, Voice, Account) */
let openPopId = null, popTrigger = null;
const POP_POS = { 'pop-agent': [764, 92], 'pop-devices': [902, 92], 'pop-voice': [966, 92], 'pop-account': [1300, 96] };
function openPop(id, trigger) {
  if (openPopId === id) { closePop(); return; }
  closePop();
  const p = $('#' + id); openPopId = id; popTrigger = trigger;
  renderPop(id);
  if (id === 'pop-account' && S.unread) {
    // opening the menu clears the unread count and acknowledges what it shows (notification_ack)
    S.unread = 0; setTimeout(() => { D.notifs.forEach(n => { n.ack = true; }); renderHeader(); if (openPopId === 'pop-account') renderPop('pop-account'); }, STILL ? 0 : 1500); renderHeader();
  }
  p.hidden = false;
  if (layout === 'art') { const [l, t] = POP_POS[id]; p.style.left = l + 'px'; p.style.top = t + 'px'; }
  else if (layout === 'tl' || layout === 'dk') {
    const r = trigger.getBoundingClientRect(), tb = $('.topbar').getBoundingClientRect();
    const w = 350; p.style.left = clamp(r.right - tb.left - w, 8, tb.width - w - 8) + 'px'; p.style.top = (r.bottom - tb.top + 8) + 'px';
  } else { p.style.left = ''; p.style.top = ''; $('#pop-scrim').hidden = false; }
  trigger && trigger.setAttribute('aria-expanded', 'true');
  // focus the dialog itself (Tab reaches its first control); a pointer click does not paint a focus ring on a control
  p.tabIndex = -1; p.focus({ preventScroll: true });
}
function closePop(restore) {
  if (!openPopId) return;
  const p = $('#' + openPopId); p.hidden = true; $('#pop-scrim').hidden = true;
  if (popTrigger) { popTrigger.setAttribute('aria-expanded', 'false'); if (restore) popTrigger.focus({ preventScroll: true }); }
  openPopId = null; popTrigger = null;
}
function renderOpenPop() { if (openPopId) renderPop(openPopId, true); }
function keepFocus(el, fn) {
  const fk = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset.fk : null;
  fn();
  if (fk) { const n = el.querySelector(`[data-fk="${fk}"]`); if (n) n.focus({ preventScroll: true }); }
}
function renderPop(id) {
  const p = $('#' + id);
  keepFocus(p, () => { p.innerHTML = POPS[id](); });
}
const kv = rows => `<dl class="n-kv">${rows.map(r => `<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join('')}</dl>`;
const swRow = (label, sub, checked, fk, tid, extra = '') => `<div class="n-row"><div class="n-l"><b id="lb-${fk}">${label}</b>${sub ? `<small>${sub}</small>` : ''}</div><div class="n-r">${extra}<button type="button" class="n-sw" role="switch" aria-checked="${checked}" data-active="${checked}" aria-labelledby="lb-${fk}" data-fk="${fk}" ${tid ? `data-testid="${tid}"` : ''}></button></div></div>`;
const POPS = {
  'pop-agent'() {
    const a = AGENT[S.agent], online = S.agent === 'online';
    return `<div class="pop-hd"><b>Agent (VPS)</b>${pill(S.bridge ? a.tone : 'warn', S.bridge ? a.label : 'Unreachable', S.bridge ? a.s : 'warn')}</div>
      <div class="pop-sec">${kv([['Session', 's-7f2a'], ['Brain', 'VPS (Gemini Live)'], ['Mode', S.set.turn === 'gated' ? 'Secure gated' : 'Fast'], ['Host', 'jarvis-agent-svc'], ['Version', '2.14.0'], ['Last seen', S.bridge ? '3 s ago' : '2 min ago']])}</div>
      <div class="pop-sec"><b>Health <span class="n-dim" style="font-weight:400;font-size:12.5px">GET /vortex/status, every 30 s</span></b>
        <div class="health" style="margin-top:0">${pill(S.bridge ? 'ok' : 'err', 'Bridge', S.bridge ? 'ok' : 'err')}${pill('ok', 'Database', 'ok')}${pill('ok', 'LiveKit 38 ms', 'ok')}${pill(online && !S.agentAbsent ? 'ok' : 'warn', online && !S.agentAbsent ? 'Agent in room' : 'Agent not in room', online && !S.agentAbsent ? 'ok' : 'warn')}</div></div>
      <div class="pop-foot">${online ? `<button type="button" class="n-btn sm" data-act="agent-pause" data-fk="pause" data-testid="agent-pause">${icon('pause')}Pause agent</button>`
        : `<button type="button" class="n-btn sm primary" data-act="agent-wake" data-fk="wake" data-testid="agent-wake" ${S.wakeBusy ? 'aria-busy="true"' : ''}>${icon('play')}Start / Wake${S.wakeBusy ? '<span class="n-sh" data-s="busy" data-testid="agent-wake-spinner"></span>' : ''}</button>`}
        <a class="n-link" href="#/agents/devices" data-fk="open">Open Agents${icon('chev-r')}</a></div>`;
  },
  'pop-devices'() {
    const lp = LAPTOP[S.laptop];
    return `<div class="pop-hd"><b>Connected devices</b>${S.bridge ? pill('ok', `${deviceCount()} online`, 'ok') : pill('err', 'Bridge offline', 'err')}</div>
      <div class="pop-sec"><b>This browser ${S.bridge ? pill('ok', 'Live', 'ok') : pill('err', 'Offline', 'err')}</b>
        ${kv([['Bridge', '/vortex'], ['Voice room', S.dup ? 'Linked to another device' : V.mode === 'joining' ? 'Joining…' : V.mode === 'offline' ? 'Unavailable' : 'Joined'], ['Camera', camLive() ? (S.camShare ? `${sh('err')}On, sharing with agent` : `${sh('err')}On, local preview only`) : S.camera ? 'Starting…' : 'Off']])}
        <div class="pop-foot"><button type="button" class="n-btn sm" data-act="reconnect" data-fk="rc" data-testid="topbar-reconnect" title="Reconnect: reloads the dashboard and rejoins voice">${icon('refresh')}Reconnect</button>${S.dup ? `<button type="button" class="n-btn sm" data-act="takeover" data-fk="to">Take over</button>` : ''}</div></div>
      <div class="pop-sec"><b>Agent (VPS) ${pill(AGENT[S.agent].tone, AGENT[S.agent].label, AGENT[S.agent].s)}</b><a class="n-link" href="#/agents/devices" data-fk="oa">Open Agents${icon('chev-r')}</a></div>
      <div class="pop-sec" data-testid="chip-laptop"><b>Laptop (Desktop Node) ${pill(lp.tone, lp.short, lp.s)}</b>
        ${kv([['Mode', lp.mode], ['Locked', lp.locked], ['Voice active', lp.online ? 'Yes' : 'No'], ['Busy tool', S.laptop === 'full' ? 'pdf_render' : 'none'], ['Last seen', S.laptop === 'stale' ? '41 s ago' : lp.online ? '1 s ago' : '12 min ago']])}
        ${lp.online && S.laptop !== 'read' && S.laptop !== 'panic' ? `<div class="pop-foot"><button type="button" class="n-btn sm danger" data-act="desktop-stop" data-fk="ds" data-testid="desktop-stop-menu">${icon('power')}Stop desktop</button></div>` : ''}
        <p class="n-note">${S.laptop === 'read' || S.laptop === 'panic' ? 'The laptop is read-only. Only the laptop tray can switch back to Full.' : 'Stop switches the laptop to read-only. Only the laptop tray can switch back to Full.'}</p></div>`;
  },
  'pop-voice'() {
    const line = statusLine(V.mode), p = P[S.persona], vm = S.vid;
    return `<div class="pop-hd"><b>${esc(line.text)}</b>${pill('acc', 'Voice ' + p.voice)}</div>
      <div class="pop-sec">
        ${swRow('Microphone (this browser)', S.micOn ? 'Live in the voice room' : 'Off. Typed chat keeps working', S.micOn, 'mic', 'topbar-mic-toggle')}
        <div class="n-row"><div class="n-l"><b id="lb-lis">${p.name} listening</b><small>${S.listening ? 'Hears the room through your mic' : 'Muted: typed chat keeps working'}</small></div><div class="n-r"><button type="button" class="n-sw" role="switch" aria-checked="${S.listening}" aria-labelledby="lb-lis" data-fk="lis" data-testid="topbar-agent-listening" data-muted="${!S.listening}" title="${p.name} mic se nahi sune${p.fut}; typed chat chalti rahegi"></button></div></div>
        ${swRow('Wake word', `Say "${p.name}" first. Enforced: yes`, S.wake, 'wake', 'chip-wake', S.wakePending ? '<span class="n-sh" data-s="busy" data-testid="chip-wake-spinner"></span>' : '')}
      </div>
      <div class="pop-sec"><b>Voice-ID ${vm.enrolled ? pill(vm.score >= vm.th ? 'ok' : 'err', Math.round(vm.score * 100) + '% / ' + Math.round(vm.th * 100) + '%', vm.score >= vm.th ? 'ok' : 'err') : pill('off', 'Not enrolled', 'off')}</b>
        <div class="pop-foot" style="margin-top:4px">
          <button type="button" class="n-btn sm" data-act="stop" data-fk="stop" data-testid="topbar-stop" title="${p.name} ko chup karao" ${V.mode === 'speaking' ? '' : 'disabled'}>${icon('stop')}Stop speaking</button>
          <button type="button" class="n-btn sm" data-act="enroll" data-fk="en">${icon('vprint')}${vm.enrolled ? 'Re-enroll' : 'Enroll'}</button>
          ${V.mode === 'audio-blocked' ? `<button type="button" class="n-btn sm primary" data-act="enable-audio" data-fk="ea">Enable audio</button>` : ''}
          ${S.dup ? `<button type="button" class="n-btn sm primary" data-act="takeover" data-fk="to">Take over</button>` : ''}
          ${V.mode === 'agent-absent' && !S.restarting ? `<button type="button" class="n-btn sm primary" data-act="agent-wake" data-fk="wk">Wake</button>` : ''}
        </div></div>
      <div class="pop-foot"><a class="n-link" href="#/settings/voice" data-fk="vs">Voice settings${icon('chev-r')}</a></div>`;
  },
  'pop-account'() {
    const rows = D.notifs.slice(0, 20).map(n => `<div class="notif" data-testid="task-notif-row"><b>${esc(n.title)}</b><time>${fmtClock(n.at)}</time>
      <div class="meta">${n.spoken ? `<span data-testid="task-notif-spoken">${icon('volume')}Spoken aloud</span>` : `<span data-testid="task-notif-screen-only">${icon('keyboard')}Screen only</span>`}${n.ack ? `<span>${icon('check')}Delivered</span>` : `<span class="nack">${sh('warn')}Not acknowledged yet</span>`}</div></div>`).join('');
    const perm = S.notifPerm === 'granted' ? `${sh('ok')}Desktop notifications on` : S.notifPerm === 'denied' ? `${sh('err')}Desktop notifications blocked in the browser` : `${sh('off')}Desktop notifications off <button type="button" class="n-btn xs" data-act="notif-on" data-fk="nt">Turn on</button>`;
    return `<div class="pop-hd"><b>${OWNER.name} (owner)</b><span class="n-dim" style="font-size:12.5px">Session ends 3:42 AM</span></div>
      <div class="pop-sec"><b>Reminders <span class="n-dim" style="font-weight:400;font-size:12.5px">Last 20</span></b>${rows || '<div class="n-empty" style="padding:10px 0">No reminders yet.</div>'}</div>
      <div class="pop-sec" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px">${perm}</div>
      <div class="pop-foot"><a class="n-btn sm" href="#/settings" data-fk="st">${icon('settings')}Settings</a><button type="button" class="n-btn sm" data-act="logout" data-fk="lo" data-testid="logout">${icon('logout')}Sign out</button></div>`;
  },
};
