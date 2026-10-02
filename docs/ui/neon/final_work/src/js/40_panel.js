
/* =====================================================================================================
   RIGHT PANEL: chat, approvals (pinned confirm card + queue + history), tools (jobs + tool log), composer
   ===================================================================================================== */
const chat = $('#chat-scroll');
let msgSeq = 100;
function atBottom() { return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40; }
function scrollChat(force) { if (force || atBottom()) chat.scrollTop = chat.scrollHeight; }
function fbHtml() {
  return `<span class="fb" data-testid="chat-feedback"><button type="button" data-fb="up" data-testid="chat-feedback-up" aria-label="Good answer" aria-pressed="false">${icon('thumb')}</button><button type="button" data-fb="down" data-testid="chat-feedback-down" aria-label="Bad answer" aria-pressed="false">${icon('thumb', 'n-ic', 'transform:rotate(180deg)')}</button></span>`;
}
function addChat(role, text, o = {}) {
  const m = document.createElement('div');
  const stick = atBottom() || o.force;
  m.dataset.id = 'm' + (++msgSeq); m.setAttribute('data-testid', 'chat-msg');
  if (role === 'sys') {
    m.className = 'msg sys' + (o.noAnim ? '' : ' enter'); m.dataset.role = 'system';
    m.innerHTML = sh('acc') + `<span></span>`; m.lastChild.textContent = text;
  } else if (role === 'day') {
    m.className = 'chat-day'; m.removeAttribute('data-testid'); m.textContent = text;
  } else {
    const per = o.persona || S.persona;
    m.className = (o.noAnim ? 'msg ' : 'msg enter ') + (role === 'user' ? 'user' : 'ai');
    m.dataset.role = role === 'user' ? 'user' : 'assistant';
    if (role !== 'user') m.dataset.persona = per;
    const time = fmtClock(o.at || nowMs());
    if (role === 'user') m.innerHTML = `<div class="av" aria-hidden="true"><span>${OWNER.initial}</span></div><div class="bubble"><span class="txt"></span><span class="vh"> (${SRC_WORD[o.src || 'typed']})</span><span class="time">${srcIcon(o.src)}${time}</span></div>`;
    else m.innerHTML = `<div class="av" aria-hidden="true"><canvas width="44" height="44"></canvas></div><div class="bubble"><span class="who">${P[per].name}</span><span class="txt"></span><span class="time">${time}</span>${o.typing ? '' : fbHtml()}</div>`;
    m.querySelector('.txt').textContent = text;
    if (o.extra) m.querySelector('.txt').append(o.extra);
    if (o.failed) m.append(failHint(m, o.failed === true ? 'queued' : o.failed, o.reason));
    if (o.group && role !== 'user') m.classList.add('grouped');
  }
  if (o.prepend) chat.prepend(m); else chat.insertBefore(m, $('#chat-empty'));
  if (role === 'ai') paintLogos(true);
  $('#chat-empty').hidden = true;
  if (!o.prepend && !$('#pane-chat').hidden) scrollChat(stick);
  return m;
}
// user message source: mic (spoken), keyboard (typed), bell (from a reminder); the word is also in hidden text
const SRC_WORD = { typed: 'typed', spoken: 'spoken', bell: 'from a reminder' };
const srcIcon = src => `<svg class="n-ic src" aria-hidden="true"><use href="#i-${{ spoken: 'mic-fill', bell: 'bell' }[src] || 'keyboard'}"/></svg>`;
// delivery states under a user message: queued (agent offline), not delivered (Retry), failed (reason)
function failHint(m, state = 'queued', reason = '') {
  const h = document.createElement('div'); h.className = 'hint-row'; h.setAttribute('data-testid', 'chat-status-hint'); h.dataset.state = state;
  const txt = { queued: 'Queued (agent offline)', undelivered: 'Not delivered —', failed: 'Failed — ' + (reason || 'unknown error') }[state];
  h.innerHTML = `${sh(state === 'failed' ? 'err' : 'warn')}<span></span>${state === 'failed' ? '' : `<button type="button" class="n-btn xs" data-testid="chat-retry">${icon('refresh')}Retry</button>`}`;
  h.querySelector('span:not(.n-sh)').textContent = txt;
  if (state === 'failed') return h;
  h.querySelector('button').onclick = () => {
    if (!S.bridge || S.agent !== 'online') { toast({ s: 'warn', text: 'Agent offline', sub: 'The message stays queued and is sent once when the agent is back.' }); return; }
    h.remove(); reply('Done. WhatsApp par Ali Office ko "Meeting 5 baje hai" bhej diya.');
  };
  return h;
}
function typingRow() {
  const p = P[S.persona];
  const m = addChat('ai', '', { typing: true });
  m.classList.add('typing-row');
  const t = m.querySelector('.txt');
  t.innerHTML = '<span class="typing" aria-hidden="true"><i></i><i></i><i></i></span><span lang="ur-Latn"></span>';
  t.lastChild.textContent = `${p.name} jawab de ${p.g} hai…`;
  return m;
}
function reply(text, extra, speakSec, o = {}) {
  setActivity('thinking');
  const m = typingRow();
  setTimeout(() => {
    m.classList.remove('typing-row');
    const t = m.querySelector('.txt'); t.textContent = text; if (extra) t.append(extra);
    m.querySelector('.bubble').insertAdjacentHTML('beforeend', fbHtml());
    scrollChat();
    if (o.after) o.after();
    const speak = S.set.speak === 'always' || (S.set.speak === 'auto' && (S.micOn || o.voice));
    if (speak && !['offline', 'joining', 'agent-absent', 'audio-blocked'].includes(V.mode)) { Speech.start(speakSec || clamp(text.length / 14, 2.2, 7)); setActivity('speaking'); }
    else setActivity(null);
  }, STILL ? 0 : 900);
}
chat.addEventListener('click', e => {
  const b = e.target.closest('[data-fb]'); if (!b) return;
  const box = b.parentElement; if (box.dataset.set) return;
  box.dataset.set = b.dataset.fb; b.setAttribute('aria-pressed', 'true');
  box.querySelectorAll('button').forEach(x => { if (x !== b) x.disabled = true; });
  addLog('INFO', 'chat', `feedback ${b.dataset.fb} on ${b.closest('.msg').dataset.id}`);
  toast({ s: 'ok', text: 'Thanks. Feedback saved.' });
});
function processTable() {
  const rows = [['chrome', '41.2'], ['Code', '18.7'], ['python', '12.4'], ['Teams', '9.8'], ['explorer', '4.1']];
  const t = document.createElement('table');
  t.innerHTML = '<tr><td style="color:#8f9dc2">Name</td><td style="color:#8f9dc2">CPU s</td></tr>' + rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
  return t;
}
// live captions (above the composer) while somebody speaks
const CAPS = { user: 'Haan, top processes dikhao', ai: 'Top five processes by CPU right now are chrome, Code, python…' };
function renderCaptions() {
  const v = V.mode, c = $('#captions');
  const show = (v === 'speaking' || v === 'listening') && S.panelTab === 'chat';
  c.hidden = !show;
  $('#cap-user').hidden = v !== 'listening';
  $('#cap-asst').hidden = v !== 'speaking';
  $('#cap-asst b').textContent = P[S.persona].name + ' (live)';
  $('#cap-user span').textContent = CAPS.user; $('#cap-asst span').textContent = CAPS.ai;
}
// "Long chat" demo: history above with "Purane messages"
function loadLongChat() {
  const older = D.older.slice().reverse();
  older.forEach(o => {
    if (o.role === 'day') addChat('day', o.text, { prepend: true, noAnim: true });
    else addChat(o.role, o.text, { prepend: true, noAnim: true, persona: o.persona, at: o.at, src: o.src, failed: o.failed, reason: o.reason, group: o.group });
  });
  const b = document.createElement('div'); b.className = 'chat-older';
  b.innerHTML = `<button type="button" class="n-btn xs" data-testid="chat-load-older">${icon('refresh')}Purane messages</button>`;
  b.querySelector('button').onclick = () => { b.innerHTML = '<span class="chat-day" style="margin:0">Start of conversation</span>'; };
  chat.prepend(b);
  paintLogos(true);
}

/* ---------------------------------------------------------------- tabs (buttons with aria-pressed, as the e2e suite expects) */
function selectTab(name, quiet) {
  S.panelTab = name;
  const ids = { chat: 'tab-chat', approvals: 'tab-appr', tools: 'tab-tools' };
  $$('.tab').forEach(t => t.setAttribute('aria-pressed', String(t.id === ids[name])));
  const ul = $('#tab-ul'), tab = $('#' + ids[name]);
  if (layout === 'art') {
    // the underline moves on transform only; the Chat position is the replica's own geometry
    const dx = tab.offsetLeft - 8, sx = tab.offsetWidth / 123;
    ul.style.left = ''; ul.style.width = '';
    ul.style.transform = dx || sx !== 1 ? `translateX(${dx}px) scaleX(${sx.toFixed(4)})` : '';
  } else { ul.style.transform = ''; ul.style.left = tab.offsetLeft + 'px'; ul.style.width = tab.offsetWidth + 'px'; }
  ul.style.transition = quiet || reduced() ? 'none' : 'transform .35s cubic-bezier(.2,.8,.2,1)';
  $('#pane-chat').hidden = name !== 'chat'; $('#pane-appr').hidden = name !== 'approvals'; $('#pane-tools').hidden = name !== 'tools';
  if (name === 'approvals') renderApprovals();
  if (name === 'tools') renderTools();
  renderCaptions();
}
$$('.tab').forEach(t => t.addEventListener('click', () => selectTab({ 'tab-chat': 'chat', 'tab-appr': 'approvals', 'tab-tools': 'tools' }[t.id])));
$('.tabs').addEventListener('keydown', e => {
  const ids = ['tab-chat', 'tab-appr', 'tab-tools'], names = ['chat', 'approvals', 'tools']; const i = ids.indexOf(document.activeElement.id);
  if (i < 0 || (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft')) return;
  const n = (i + (e.key === 'ArrowRight' ? 1 : 2)) % 3; $('#' + ids[n]).focus(); selectTab(names[n]);
});

/* ---------------------------------------------------------------- approvals: one queue, the head is the pinned card */
let cfSeq = 1;
const CF_DEMOS = {
  medium: () => ({ tool: 'send_whatsapp', label: 'WhatsApp', risk: 'medium', q: 'Send this WhatsApp message to Ali Office?', code: 'To: Ali Office\nMeeting 5 baje hai. Agenda: weekly report.', where: 'PC (laptop)', voice: false, ttl: 90 }),
  high: () => ({ tool: 'file_delete', label: 'Delete files', risk: 'high', q: 'Delete 214 files in Downloads\\old on your laptop?', code: 'Remove-Item "C:\\Users\\Matloob\\Downloads\\old\\*" -Recurse', where: 'PC (laptop)', voice: false, ttl: 90 }),
  voice: () => ({ tool: 'email_send', label: 'Email', risk: 'medium', q: 'Send the weekly report to sana@office.pk?', code: 'Subject: Weekly report\nAttachment: weekly_report.pdf (1.2 MB)', where: 'Agent (VPS)', voice: true, ttl: 90 }),
  long: () => ({ tool: 'run_cmd_command', label: 'PowerShell', risk: 'high', q: 'Run this PowerShell script on your laptop?', where: 'PC (laptop)', voice: false, ttl: 90,
    code: 'Get-ChildItem C:\\Users\\Matloob\\Downloads -Recurse -File |\n  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |\n  Remove-Item -Force -Verbose\nClear-RecycleBin -Force\nRestart-Computer -Force' }),
};
const NOMINAL_TEXT = 'Get-Process | Sort-Object CPU -Descending |\nSelect-Object -First 5';
// a new request: queued, announced once (status node), toast + chime (+ vibration on phones); the phone sheet re-opens
function pushConfirm(c, quiet) {
  c.left = c.left || c.ttl; D.queue.push(c);
  addLog('INFO', 'confirm', `Confirmation requested: ${c.tool} (${c.risk})`);
  sheetLater = false;
  if (document.hidden) titleFlash(c);
  renderConfirm(true); renderApprovals(); renderNavMarks();
  if (quiet) return c;
  chime();
  if (layout === 'ph' && navigator.vibrate) try { navigator.vibrate([120, 60, 120]); } catch (e) {}
  const n = D.queue.length;
  // phones: the bottom sheet that just opened is the visible notice (a toast at the top would cover its title)
  if (!html.classList.contains('sheet')) toast({ s: c.risk === 'high' ? 'err' : 'warn', text: `Approval needed: ${c.label}`, sub: n > 1 ? `Queue: ${n}. Oldest first.` : `${c.tool}, expires in ${mmss(c.left)}`,
    action: ['Review', () => { if (layout === 'ph') { sheetLater = false; renderSheet(true); } else { if (D.queue[0] === c) $('#cf-deny').focus(); else { selectTab('approvals'); } } }], ms: 8000 });
  if (D.queue[0] === c) cfAnnounce(c); else cfSay(`Another approval is waiting: ${c.label}. Queue ${n}.`);
  return c;
}
function addConfirm(kind) { return pushConfirm(Object.assign(CF_DEMOS[kind](), { id: 'c' + (++cfSeq) })); }
let flashT = 0;
function chime() {
  try { const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain(); o.frequency.value = 880; g.gain.setValueAtTime(.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(.15, a.currentTime + .02); g.gain.exponentialRampToValueAtTime(.0001, a.currentTime + .35); o.connect(g).connect(a.destination); o.start(); o.stop(a.currentTime + .4); setTimeout(() => a.close(), 600); } catch (e) { /* audio not allowed yet */ }
  if ('Notification' in window && Notification.permission === 'granted') try { new Notification('Confirm: ' + (D.queue[D.queue.length - 1] || {}).tool); } catch (e) {}
}
function titleFlash(c) { chime(); clearInterval(flashT); let on = false; flashT = setInterval(() => { document.title = (on = !on) ? `⚠ Confirm: ${c.tool}` : 'JARVIS & ANNA'; if (!document.hidden) { clearInterval(flashT); document.title = 'JARVIS & ANNA'; } }, 1000); }
// screen reader: one assertive message per new head request, and the countdown only at 60 s and 10 s
function cfSay(msg) { const el = $('#cf-announce'); el.textContent = ''; setTimeout(() => { el.textContent = msg; }, 50); }
function cfAnnounce(c) { if (!c || c.announced) return; c.announced = true; cfSay(`Approval needed: ${c.label}, ${c.risk} risk, on ${c.where}. Expires in ${mmss(c.left)}.`); }
function cfNeedsMeta(c) { return D.queue.length > 1 || c.risk === 'high' || c.voice || c.left < 60 || !c.nominal; }
// commands are shown as written: lines kept, wrapped only at spaces (a token is never split), scroll for the rest
function codeHtml(text) {
  return text.split('\n').map(l => `<span class="ln">${l.split(/(\s+)/).map(p => !p ? '' : /^\s+$/.test(p) ? esc(p) : `<span class="tk">${esc(p)}</span>`).join('') || ' '}</span>`).join('');
}
// write a text only when it changed (the countdown ticks every second; nothing else in the card should churn)
const setText = (el, t) => { if (el.firstChild && el.firstChild === el.lastChild && el.firstChild.nodeType === 3) { if (el.firstChild.nodeValue !== t) el.firstChild.nodeValue = t; } else if (el.textContent !== t) el.textContent = t; };
function renderConfirm(animateIn) {
  const cf = $('#confirm'), c = D.queue[0], code = $('#cf-code');
  if (!c) {
    if (!cf.hidden) cf.hidden = true;
    sheetLater = false; layoutFeed(); renderSheet(); renderBadge(); return;
  }
  const wasHidden = cf.hidden, fresh = cf.dataset.id !== c.id || code.dataset.layout !== (layout === 'art' ? 'art' : 'fluid');
  cf.hidden = false;
  cf.dataset.risk = c.risk; cf.dataset.id = c.id;
  cf.querySelector('.cf-hd > .ic').style.color = c.risk === 'high' ? '#ff6b70' : '#f9c74c';
  setText($('#cf-q'), c.q);
  if (fresh) {
    // the reference frame keeps its measured markup; everything else is tokenised
    const text = c.nominal ? NOMINAL_TEXT : c.code;
    if (c.nominal && layout === 'art') { code.innerHTML = NOMINAL_CODE; code.dataset.long = 'false'; }
    else { code.innerHTML = codeHtml(text); code.dataset.long = 'true'; }
    code.dataset.layout = layout === 'art' ? 'art' : 'fluid';
    c.lines = text.split('\n').length;
    code.style.whiteSpace = code.style.wordBreak = code.style.overflow = '';
    code.tabIndex = 0; code.setAttribute('role', 'region');
    code.setAttribute('aria-label', `Command to run, ${c.lines} line${c.lines === 1 ? '' : 's'}`);
    code.scrollTop = 0; code.scrollLeft = 0;
    c.expanded = !!c.expanded;
  }
  cf.dataset.expanded = String(!!c.expanded);
  renderConfirmTick(c);
  const allow = $('#cf-allow');
  setText(allow.querySelector('.lbl'), c.risk === 'high' ? 'Hold to approve' : 'Approve & Run');
  allow.setAttribute('aria-label', c.risk === 'high' ? 'Hold to approve (hold to allow). Press and hold for 1 second, or hold Space.' : 'Approve & Run');
  allow.title = c.risk === 'high' ? 'Press and hold for 1 second, or hold Space' : '';
  $('#cf-allow .lbl').style.left = c.risk === 'high' ? '47px' : '';
  renderSheet();                       // phones: the sheet must be open before the command is measured
  layoutConfirm(c);
  if ((animateIn || wasHidden) && !reduced() && !STILL) cf.animate([{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' });
  renderSheet(); renderBadge();
}
// the parts that change every second: only text nodes whose text changed are written
function renderConfirmTick(c) {
  const exp = c.left < 60;
  setText($('#cf-pill-lbl'), exp ? `Expires in ${mmss(c.left)}` : c.risk === 'high' ? 'High risk' : 'Requires approval');
  const meta = $('#cf-meta'), show = cfNeedsMeta(c), cf = $('#confirm');
  if (meta.hidden !== !show) meta.hidden = !show;
  if (cf.dataset.tall !== String(show)) cf.dataset.tall = String(show);
  if (!meta.firstElementChild) meta.innerHTML = '<span data-k="tool"></span><span data-k="where"></span><span data-k="queue"></span><span data-k="lines"></span><span data-k="voice"></span><span data-k="left" aria-live="off">Expires in <span class="n-num">0:00</span></span>';
  const part = (k, html, on) => { const el = meta.querySelector(`[data-k="${k}"]`); if (el.hidden !== !on) el.hidden = !on; if (on && k !== 'left' && el._h !== html) { el.innerHTML = html; el._h = html; } };
  part('tool', `Tool <b>${esc(c.tool)}</b>`, true);
  part('where', `On <b>${esc(c.where)}</b>`, true);
  part('queue', `Queue: ${D.queue.length}`, true);
  part('lines', `${c.lines} lines`, !c.nominal && c.lines > 1);
  part('voice', `${icon('mic-fill', 'n-ic', 'width:13px;height:13px;display:inline-block;vertical-align:-2px')} Voice: "haan" bolein`, !!c.voice);
  part('left', '', !exp);
  const tn = meta.querySelector('[data-k="left"] .n-num').firstChild;    // the countdown: one text node, written only when it changes
  if (tn.nodeValue !== mmss(c.left)) tn.nodeValue = mmss(c.left);
  if (c.left === 60 && !c.said60) { c.said60 = true; cfSay(`Approval for ${c.label} expires in 1 minute.`); }
  if (c.left === 10 && !c.said10) { c.said10 = true; cfSay(`Approval for ${c.label} expires in 10 seconds.`); }
}
// long commands: the code block scrolls (about 160 px), "Show full command (N lines)" expands it, and a high-risk
// approval is enabled only after the whole command was seen (scrolled to the end or expanded)
function layoutConfirm(c) {
  c = c || D.queue[0]; if (!c) return;
  const cf = $('#confirm'), code = $('#cf-code'), row = $('#cf-exp-row'), btn = $('#cf-expand');
  code.style.maxHeight = c.expanded ? 'none' : '';
  if (layout === 'art') {
    code.style.height = 'auto';
    const nat = Math.ceil(code.scrollHeight);
    code.style.height = '';
    const h = c.nominal ? 80 : Math.max(56, Math.min(c.expanded ? Math.min(nat, 360) : 160, nat));
    cf.style.setProperty('--cfh', h + 'px');
    code.style.height = h + 'px';
  } else { cf.style.removeProperty('--cfh'); code.style.height = ''; }
  if (!code.offsetParent || !code.clientHeight) { layoutFeed(); return; }   // not on screen: measured when it shows
  // the expander's wording first: it decides whether the gate hint next to it wraps, i.e. the room left for the code
  setText(btn, c.expanded ? 'Show less' : `Show full command (${c.lines} lines)`); btn.setAttribute('aria-expanded', String(!!c.expanded));
  const over = code.scrollHeight > code.clientHeight + 2 || code.scrollWidth > code.clientWidth + 2;
  // expanding never counts as "seen" by itself: on a short window the expanded card can still clip the command, and
  // then the hold unlocks only when the block was scrolled to the end (or the window grew until it all shows)
  c.over = over;
  const showRow = !c.nominal && (over || c.expanded);
  row.hidden = !showRow;
  if (!c.over) c.seenAll = true;
  const gated = c.risk === 'high' && !c.seenAll;
  $('#cf-gate').hidden = !gated;
  const allow = $('#cf-allow');
  if (gated) { allow.setAttribute('aria-disabled', 'true'); allow.setAttribute('aria-describedby', 'cf-gate'); }
  else { allow.removeAttribute('aria-disabled'); allow.removeAttribute('aria-describedby'); }
  if (layout === 'art') {
    const h = parseFloat(cf.style.getPropertyValue('--cfh')) || 80;
    cf.style.setProperty('--cfx', (h - 80 + (showRow ? 36 : 0)) + 'px');
  } else cf.style.removeProperty('--cfx');
  layoutFeed();
}
$('#cf-code').addEventListener('scroll', () => {
  const c = D.queue[0], code = $('#cf-code'); if (!c || c.seenAll) return;
  const atEnd = code.scrollTop + code.clientHeight >= code.scrollHeight - 3 && code.scrollLeft + code.clientWidth >= code.scrollWidth - 3;
  const atBottom = code.scrollTop + code.clientHeight >= code.scrollHeight - 3;
  if (atEnd || (atBottom && code.scrollWidth <= code.clientWidth + 2)) { c.seenAll = true; layoutConfirm(c); cfSay('Whole command seen. Hold to approve is enabled.'); }
}, { passive: true });
$('#cf-expand').addEventListener('click', () => {
  const c = D.queue[0]; if (!c) return;
  const seen = c.seenAll;
  c.expanded = !c.expanded;
  renderConfirm();
  if (!c.expanded) $('#cf-code').scrollTop = 0;
  if (c.risk === 'high' && !seen) cfSay(c.seenAll ? 'Whole command shown. Hold to approve is enabled.' : 'Command expanded. Scroll it to the end to enable approval.');
});
// window or panel resized: the command block may now show everything (or need the expander); re-measure once per frame
if ('ResizeObserver' in window) {
  let rq = 0;
  new ResizeObserver(() => {
    if (rq) return;
    rq = requestAnimationFrame(() => {
      rq = 0; const c = D.queue[0];
      if (!c || layout === 'art' || $('#confirm').hidden) return;
      const was = c.seenAll; layoutConfirm(c);
      if (!was && c.seenAll && c.risk === 'high') cfSay('Whole command shown. Hold to approve is enabled.');
    });
  }).observe($('#cf-code'));
}
function renderBadge() {
  const n = D.queue.length, b = $('#appr-badge');
  b.textContent = n; b.hidden = !n; b.setAttribute('aria-label', n + ' waiting');
  renderNavMarks();
}
function layoutFeed() {
  // artboard: the confirm card is pinned above every tab while something is pending (fluid layouts use the flex column)
  const cf = $('#confirm');
  if (layout !== 'art') { $('#feed').style.top = ''; return; }
  $('#feed').style.top = cf.hidden ? '84px' : (88 + cf.offsetHeight + 29) + 'px';
}
function resolveHead(state, via) {
  const c = D.queue[0]; if (!c) return;
  const cf = $('#confirm');
  D.queue.shift();
  D.history.unshift({ state, tool: c.tool, what: c.nominal ? 'Get-Process | Sort-Object CPU …' : c.code.split('\n')[0], at: nowMs() });
  D.history = D.history.slice(0, 20);
  const tl = D.tools.find(t => t.id === 't1' && c.nominal);
  addLog('INFO', 'confirm', `${c.tool} ${state} (${via})`);
  const words = { allowed: ['k-ok', 'Command approved'], denied: ['k-err', 'Command denied'], expired: ['k-neutral', 'Approval expired, nothing ran'], cancelled: ['k-neutral', 'Cancelled by voice'] }[state];
  addEvent('approval', words[0], words[1], c.tool);
  cf.classList.add('resolving');
  if (!D.queue.length) sheetLater = false;
  setTimeout(() => {
    cf.classList.remove('resolving');
    if (D.queue.length) setTimeout(() => { renderConfirm(true); cfAnnounce(D.queue[0]); }, 260); else renderConfirm();
    renderApprovals();
  }, reduced() || STILL ? 0 : 360);
  renderBadge();
  if (state === 'allowed') {
    if (tl) { tl.status = 'running'; tl.result = 'Running on the laptop'; }
    if (c.nominal) {
      setTimeout(() => reply('Done. These are the top 5 processes by CPU right now:', processTable(), 4.2, { after() { if (tl) { tl.status = 'success'; tl.dur = '0.6 s'; tl.result = '5 rows returned'; if (S.panelTab === 'tools') renderTools(); } } }), 500);
      setTimeout(() => addEvent('tool', 'k-info', 'Command finished', 'Top 5 processes returned'), 1600);
    } else { setTimeout(() => reply(`Done. ${c.label} ho gaya.`), 500); toast({ s: 'busy', text: `${c.tool} chal raha hai`, lang: 'ur-Latn' }); }
  } else if (state === 'denied') {
    if (tl) { tl.status = 'error'; tl.result = 'Denied by owner, nothing ran'; }
    setTimeout(() => reply('Okay, I won’t run it. Nothing was changed on your laptop.', null, 2.6), 400);
  } else if (state === 'expired') { if (tl) { tl.status = 'error'; tl.result = 'Approval expired, nothing ran'; } toast({ s: 'off', text: 'Approval expired, nothing ran', sub: c.tool }); }
  else if (state === 'cancelled') { toast({ s: 'warn', text: 'Voice se cancel hua', sub: c.tool, lang: 'ur-Latn' }); }
  if (S.panelTab === 'tools') renderTools();
}
// Approve & Run (hold 1 s for high risk: pointer, mouse or Space), Deny, Esc = Deny, Enter never allows
let holdT = 0;
function holdStart(e) {
  const c = D.queue[0]; if (!c) return;
  if (c.risk !== 'high') return;
  if (e.type === 'keydown' && (e.key !== ' ' || e.repeat)) return;
  e.preventDefault();
  const b = $('#cf-allow');
  if (b.getAttribute('aria-disabled') === 'true') { toast({ s: 'warn', text: 'Read the whole command first', sub: 'Scroll the command to the end or press Show full command.' }); return; }
  if (b.dataset.holding === 'true') return;           // pointerdown + mousedown of the same press
  b.dataset.holding = 'true';
  clearTimeout(holdT); holdT = setTimeout(() => { b.dataset.holding = 'false'; resolveHead('allowed', 'hold'); }, reduced() ? 400 : 1000);
}
function holdEnd() { const b = $('#cf-allow'); if (b.dataset.holding === 'true') { b.dataset.holding = 'false'; clearTimeout(holdT); toast({ s: 'warn', text: 'Keep holding for 1 second to approve' }); } }
$('#cf-allow').addEventListener('click', e => { const c = D.queue[0]; if (!c) return; if (c.risk === 'high') { e.preventDefault(); return; } resolveHead('allowed', 'click'); });
['pointerdown', 'mousedown', 'touchstart'].forEach(ev => $('#cf-allow').addEventListener(ev, holdStart, { passive: false }));
['pointerup', 'pointerleave', 'pointercancel', 'mouseup', 'touchend', 'touchcancel'].forEach(ev => $('#cf-allow').addEventListener(ev, holdEnd));
$('#cf-allow').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); } else holdStart(e); });
$('#cf-allow').addEventListener('keyup', e => { if (e.key === ' ') holdEnd(); });
$('#cf-deny').addEventListener('click', () => resolveHead('denied', 'click'));
$('#cf-later').addEventListener('click', () => { sheetLater = true; renderSheet(); if (S.page !== 'chat') toast({ s: 'warn', text: 'Approval is waiting in Chat', sub: 'Open Chat to decide.' }); });
$('#cf-bar').addEventListener('click', () => { sheetLater = false; renderSheet(true); });
$('#confirm').addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); resolveHead('denied', 'escape'); } });
setInterval(() => {
  if (STILL || S.screen !== 'app') return;
  const c = D.queue[0]; if (!c) return;
  c.left = Math.max(0, c.left - 1);
  if (c.left === 0) { resolveHead('expired', 'timeout'); return; }
  renderConfirmTick(c);
  if (c.left === 59) layoutFeed();
  for (const q of D.queue.slice(1)) q.left = Math.max(1, q.left - 1);
  renderSheetBar();
}, 1000);
// phone: the card docks as a bottom sheet with a scrim and a focus trap (Deny focused first). "Decide later" collapses it
// to a 48 px bar in the Chat tab; a new request or an empty queue resets that choice.
let sheetLater = false, sheetWasOpen = false;
function renderSheet(focus) {
  const on = layout === 'ph' && D.queue.length > 0 && S.screen === 'app' && !sheetLater;
  html.classList.toggle('sheet', on);
  $('#sheet-scrim').hidden = !on;
  $('#confirm').setAttribute('aria-modal', String(on));
  if (on && (!sheetWasOpen || focus)) setTimeout(() => $('#cf-deny').focus({ preventScroll: true }), 60);
  if (on && !sheetWasOpen && D.queue[0]) setTimeout(() => layoutConfirm(), 0);
  sheetWasOpen = on;
  renderSheetBar();
}
function renderSheetBar() {
  const bar = $('#cf-bar'), n = D.queue.length, c = D.queue[0];
  const show = layout === 'ph' && n > 0 && !html.classList.contains('sheet');
  bar.hidden = !show;
  if (!show) return;
  bar.dataset.risk = c.risk;
  setText($('#cf-bar-t'), `${n} approval${n === 1 ? '' : 's'} waiting · ${c.label}`);
  bar.setAttribute('aria-label', `${n} approval${n === 1 ? '' : 's'} waiting: ${c.label}, expires in ${mmss(c.left)}. Review.`);
}
$('#sheet-scrim').addEventListener('click', () => $('#cf-deny').focus());


function renderApprovals() {
  if (S.panelTab !== 'approvals') return;
  const el = $('#appr-list');
  const rest = D.queue.slice(1);
  const hist = D.history.map(h => {
    const w = { allowed: ['ok', 'Allowed'], denied: ['err', 'Denied'], expired: ['off', 'Expired, nothing ran'], cancelled: ['err', 'Cancelled by voice'] }[h.state];
    return `<div class="p-card" data-testid="approvals-history-row"><div class="p-top"><b>${esc(h.tool)}</b>${pill(w[0] === 'ok' ? 'ok' : w[0] === 'err' ? 'err' : 'off', w[1], w[0])}</div><div class="p-sub"><code>${esc(h.what)}</code></div><div class="p-sub">${fmtClock(h.at)}</div></div>`;
  }).join('');
  el.innerHTML = `<div data-testid="approvals-panel">
    ${D.queue.length ? `<div class="p-sec"><h4>Waiting <small>${D.queue.length === 1 ? 'Only the request above' : `${D.queue.length} requests, oldest first`}</small></h4>
      ${rest.map((c, i) => `<div class="p-card" data-testid="approvals-queue-row"><div class="p-top"><b>Request ${i + 2} of ${D.queue.length}</b>${pill(c.risk === 'high' ? 'err' : 'warn', c.risk === 'high' ? 'High risk' : 'Medium risk', c.risk === 'high' ? 'err' : 'warn')}</div><div class="p-sub">${esc(c.q)}</div><div class="p-sub">Tool <code>${esc(c.tool)}</code>, expires in ${mmss(c.left)}</div></div>`).join('')}</div>`
      : `<div class="n-empty">${icon('shield-check')}<b>No approvals waiting</b><span>Guarded tools always ask first.</span><a class="n-link" href="#/settings/confirmations">See guarded tools${icon('chev-r')}</a></div>`}
    <div class="p-sec"><h4>Decisions <small>Last 20</small></h4>${hist || '<div class="n-empty" style="padding:8px">No decisions yet.</div>'}</div>
    ${!D.queue.length ? `<button type="button" class="n-btn sm" id="appr-again" style="margin-bottom:14px">${icon('plus')}Ask again (demo)</button>` : ''}</div>`;
  const again = $('#appr-again'); if (again) again.onclick = () => { pushConfirm({ id: 'c' + (++cfSeq), tool: 'run_cmd_command', label: 'PowerShell', risk: 'medium', q: 'Run this PowerShell command on your laptop?', nominal: true, where: 'PC (laptop)', voice: false, ttl: 600, left: 600 }); addEvent('approval', 'k-warn', 'Approval requested', 'PowerShell · Get-Process'); };
}

/* ---------------------------------------------------------------- tools tab: workspaces, jobs, tool log */
const TOOL_ST = { waiting: ['warn', 'Waiting'], running: ['acc', 'Running'], success: ['ok', 'Success'], error: ['err', 'Error'] };
function renderTools() {
  if (S.panelTab !== 'tools') return;
  const el = $('#tools-list');
  const f = S.toolFilter;
  const tools = D.tools.filter(t => f === 'all' || (f === 'issues' ? t.status === 'error' || t.status === 'waiting' : t.status === f));
  keepFocus(el, () => {
    el.innerHTML = `<div data-testid="tools-tab-panel">
    <div class="p-sec"><h4>Workspaces</h4><button type="button" class="n-btn sm" data-act="open-sim" data-fk="sim" title="SIM lookup workspace">${icon('sim')}SIM lookup</button></div>
    <div class="p-sec"><h4>Jobs <small>${D.jobs.filter(j => j.status === 'running').length} running</small></h4><div data-testid="jobs-list">
      ${D.jobs.length ? D.jobs.map(j => `<div class="p-card" data-testid="job-row" data-id="${j.id}" data-status="${j.status === 'success' ? 'done' : j.status === 'error' ? (j.msg === 'Cancelled by owner' ? 'cancelled' : 'error') : j.status}"><div class="p-top"><b>${esc(j.title)}</b>${pill(TOOL_ST[j.status][0], TOOL_ST[j.status][1], TOOL_ST[j.status][0])}</div>
        <div class="p-sub"><code>${j.tool}</code> ${esc(j.msg)}</div>
        <div class="n-bar" data-testid="job-progress-bar"><i data-testid="job-progress-fill" data-percent="${j.pct}" style="--v:${j.pct / 100};--c:${j.status === 'success' ? '#22c086' : 'var(--acc-line)'}"></i></div>
        <div class="p-act">${j.status === 'running' ? `<button type="button" class="n-btn xs" data-job-cancel="${j.id}" data-fk="jc${j.id}" data-testid="job-cancel">${icon('x')}Cancel</button>` : ''}${j.artifact ? `<button type="button" class="n-btn xs" data-job-open="${j.id}" data-fk="jo${j.id}" data-testid="job-open-artifact">${icon('external')}PC par kholo</button>` : ''}</div></div>`).join('')
      : `<div class="n-empty" data-testid="jobs-empty" style="padding:10px">${icon('cal')}<span>No jobs running. Long tasks show their progress here.</span></div>`}</div></div>
    <div class="p-sec"><h4>Tool log</h4>
      <div class="n-seg" data-testid="tool-log-filter" role="group" aria-label="Filter tool runs" style="margin-bottom:10px">${[['all', 'All'], ['running', 'Running'], ['success', 'Success'], ['error', 'Error'], ['issues', 'Issues']].map(x => `<button type="button" data-tf="${x[0]}" data-fk="tf${x[0]}" aria-pressed="${f === x[0]}" data-active="${f === x[0]}">${x[1]}</button>`).join('')}</div>
      <div data-testid="tool-log-list">${tools.length ? tools.map(t => `<div class="toolrow" data-testid="tool-row" data-status="${t.status}" data-open="${!!t.open}"><button type="button" data-tool="${t.id}" data-fk="tr${t.id}" aria-expanded="${!!t.open}"><b>${esc(t.name)}</b>${pill(TOOL_ST[t.status][0], TOOL_ST[t.status][1], TOOL_ST[t.status][0])}${icon('chev-r', 'n-ic chev')}</button>
        <div class="tdet"><span>${rel(nowMs() - t.at)}${t.dur ? ', ' + t.dur : ''}</span><div><span>Args</span> ${esc(t.args)}</div><div><span>Result</span> ${esc(t.result)}</div>
        ${t.media ? `<div class="n-actions"><button type="button" class="n-btn xs" data-act="${t.media === 'image' ? 'open-lb' : 'play-audio'}">${icon(t.media === 'image' ? 'image' : 'play')}${t.media === 'image' ? 'View' : 'Play'}</button><a class="n-btn xs" href="#" data-act="download">${icon('download')}Download</a></div>` : ''}</div></div>`).join('')
        : `<div class="n-empty" data-testid="tool-log-empty" style="padding:10px">${icon('box')}<span>${D.tools.length ? 'No tool runs match this filter.' : 'No tool runs yet. Tools JARVIS & ANNA use show up here.'}</span></div>`}</div></div></div>`;
  });
}
$('#tools-list').addEventListener('click', e => {
  const t = e.target.closest('[data-tool]'); if (t) { const r = D.tools.find(x => x.id === t.dataset.tool); r.open = !r.open; renderTools(); return; }
  const f = e.target.closest('[data-tf]'); if (f) { S.toolFilter = f.dataset.tf; renderTools(); return; }
  const c = e.target.closest('[data-job-cancel]'); if (c) { const j = D.jobs.find(x => x.id === c.dataset.jobCancel); j.status = 'error'; j.msg = 'Cancelled by owner'; renderTools(); toast({ s: 'off', text: `${j.title} cancelled` }); return; }
  const o = e.target.closest('[data-job-open]'); if (o) { toast({ s: 'ok', text: 'Opened on the PC', sub: 'Pictures\\JARVIS\\screenshot_2138.png' }); }
});
setInterval(() => {
  if (STILL) return;
  let ch = false;
  D.jobs.forEach(j => { if (j.status === 'running') { j.pct = Math.min(100, j.pct + (j.id === 'j1' ? 2 : 3)); ch = true; if (j.pct >= 100) { j.status = 'success'; j.msg = 'Done'; addEvent('tool', 'k-ok', `${j.tool} finished`, j.title); } } });
  if (ch && S.panelTab === 'tools') renderTools();
}, 2500);

/* ---------------------------------------------------------------- composer */
function setTaskMode(on) {
  S.task = on;
  $('#btn-task').setAttribute('aria-pressed', String(on)); $('#btn-task').dataset.active = String(on);
  $('#composer').classList.toggle('task', on);
  const n = P[S.persona].name;
  $('#msg').placeholder = on ? 'Describe a task for ' + n + '...' : 'Message ' + n + '...';
  if (on) { if (layout === 'ph' && S.page !== 'chat') go('chat'); selectTab('chat'); setTimeout(() => $('#msg').focus(), 30); }
}
$('#btn-task').addEventListener('click', () => setTaskMode(!S.task));
$('#chip-x').addEventListener('click', () => setTaskMode(false));
$('#composer').addEventListener('submit', e => {
  e.preventDefault();
  const v = $('#msg').value.trim();
  if (!v) return;
  selectTab('chat');
  const offline = !S.bridge || S.agent !== 'online';
  addChat('user', v, { force: true, failed: offline });
  $('#msg').value = '';
  if (offline) { addLog('WARNING', 'chat', 'message queued: agent offline'); return; }
  if (S.task) {
    addEvent('tool', 'k-info', 'Task started', v.slice(0, 34));
    reply('On it. I split this into steps and started the first one. I’ll report back here when it’s done.', null, 4);
    setTaskMode(false);
  } else if (/process|cpu/i.test(v)) {
    reply('I’ll need your approval to run a PowerShell command on your laptop.', null, 3, { after() { addConfirm('medium'); } });
  } else reply(/time|waqt/i.test(v) ? 'It’s ' + fmtClock(nowMs()) + ' right now.' : 'Got it. I’m on it; you can keep talking or type the next thing.', null);
});
$('#msg').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit(); } });
$('#attach').addEventListener('click', () => toast({ s: 'off', text: 'Attachments are not available yet' }));
// composer mic = push-to-talk on the same voice-room mic as Start Listening (no separate local mic test):
// hold (pointer, Space or Enter) to talk without the wake word; release sends the turn
let pttT0 = 0;
function pttStart(e) {
  if (e && e.type === 'keydown' && (e.repeat || (e.key !== ' ' && e.key !== 'Enter'))) return;
  if (e) e.preventDefault();
  if (S.ptt) return;
  if (['offline', 'joining', 'agent-absent', 'audio-blocked'].includes(V.mode)) { toast({ s: 'warn', text: 'Voice is not available right now', sub: statusLine(V.mode).text }); return; }
  S.ptt = true; S.pttMicWasOn = S.micOn; S.pttWasListening = S.listening; pttT0 = performance.now();
  S.micOn = true; S.listening = true; S.activity = 'listening';
  $('#use-mic').setAttribute('aria-pressed', 'true'); $('#use-mic-lbl').textContent = 'Listening…';
  renderVoice();
}
function pttEnd() {
  if (!S.ptt) return;
  const held = performance.now() - pttT0;
  S.ptt = false; S.micOn = S.pttMicWasOn; S.listening = S.pttWasListening; if (S.activity === 'listening') S.activity = null;
  $('#use-mic').setAttribute('aria-pressed', 'false'); $('#use-mic-lbl').textContent = 'Use microphone';
  renderVoice();
  if (held < 350) { toast({ s: 'off', text: 'Hold to talk', sub: 'Keep the microphone button pressed while you speak.' }); return; }
  addChat('user', CAPS.user, { force: true, src: 'spoken' });
  reply('Theek hai. Yeh rahe top processes jo abhi sab se zyada CPU le rahe hain.', processTable(), 3.6, { voice: true });
}
['pointerdown', 'keydown'].forEach(ev => $('#use-mic').addEventListener(ev, pttStart));
['pointerup', 'pointerleave', 'pointercancel', 'keyup', 'blur'].forEach(ev => $('#use-mic').addEventListener(ev, pttEnd));
$('#use-mic').addEventListener('click', e => e.preventDefault());
// phone: the tab bar hides while you type, so the composer sits right above the keyboard
$('#msg').addEventListener('focus', () => { if (layout === 'ph') html.classList.add('typing'); });
$('#msg').addEventListener('blur', () => html.classList.remove('typing'));

/* ---------------------------------------------------------------- hero buttons, persona switch, orb */
$('#btn-listen').addEventListener('click', micOnAction);
$('#btn-micoff').addEventListener('click', micOffAction);
$('#orb').addEventListener('click', e => {
  const r = e.currentTarget.getBoundingClientRect(), k = r.width / 520;
  if (Math.hypot((e.clientX - r.left) / k - 260.6, (e.clientY - r.top) / k - 260.7) < 150) demoVoice();
});
$('#orb').addEventListener('mousemove', e => {
  const r = e.currentTarget.getBoundingClientRect(), k = r.width / 520;
  e.currentTarget.style.cursor = Math.hypot((e.clientX - r.left) / k - 260.6, (e.clientY - r.top) / k - 260.7) < 150 ? 'pointer' : 'default';
});
function setPersona(p, opts = {}) {
  if (p === S.persona && !opts.force) return;
  const prev = S.persona;
  S.persona = p; S.personaPending = true;
  html.dataset.persona = p;
  const name = P[p].name;
  const nm = $('#hero-name');
  if (reduced() || STILL) nm.textContent = name;
  else nm.animate([{ transform: 'translateY(0)', opacity: 1 }, { transform: 'translateY(-14px)', opacity: 0 }], { duration: 220, easing: 'ease-in' }).onfinish = () => {
    nm.textContent = P[S.persona].name;
    nm.animate([{ transform: 'translateY(14px)', opacity: 0 }, { transform: 'translateY(0)', opacity: 1 }], { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' });
  };
  $$('#persona-switch b').forEach((b, i) => { const on = (i === 1) === (p === 'anna'); b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; if (b === document.activeElement || (on && document.activeElement && document.activeElement.closest('#persona-switch'))) b.focus({ preventScroll: true }); });
  $('#persona-switch').title = p === 'anna' ? 'ANNA answers. Voice Aoede. Switch to JARVIS: Voice Charon' : 'JARVIS answers. Voice Charon. Switch to ANNA: Voice Aoede';
  $('#msg').placeholder = (S.task ? 'Describe a task for ' : 'Message ') + name + '...';
  $('#msg').setAttribute('aria-label', 'Message ' + name);
  $('#en-s1').textContent = `${name} sun ${P[p].g} hai? Aaj ka kaam shuru karte hain.`;
  $('#chip-persona-spinner').hidden = false; $('#persona-switch').setAttribute('aria-busy', 'true');
  renderVoice();
  if (S.page === 'agents') renderAgentsPart('personas');
  if (S.page === 'settings') renderSettings();
  if (STILL) confirmPersona(prev);
  else setTimeout(() => (S.personaFail ? revertPersona(prev) : confirmPersona(prev)), S.personaFail ? 5000 : 650);
  schedule();
}
function confirmPersona(prev) {
  S.personaPending = false; $('#chip-persona-spinner').hidden = true; $('#persona-switch').removeAttribute('aria-busy');
  const p = P[S.persona];
  if (STILL) return;
  addChat('sys', 'Switched to ' + p.name);
  addEvent('persona', 'k-info', p.name + ' is answering', 'Voice ' + p.voice);
  toast({ s: 'acc', text: `${p.name} is answering. Voice ${p.voice}.` });
  if (S.page === 'settings') renderSettings();
}
function revertPersona(prev) {
  S.personaFail = false;
  S.persona = prev; html.dataset.persona = prev; $('#hero-name').textContent = P[prev].name;
  $$('#persona-switch b').forEach((b, i) => b.setAttribute('aria-checked', String((i === 1) === (prev === 'anna'))));
  $('#msg').placeholder = 'Message ' + P[prev].name + '...';
  S.personaPending = false; $('#chip-persona-spinner').hidden = true; $('#persona-switch').removeAttribute('aria-busy');
  renderVoice(); if (S.page === 'agents') renderAgentsPart('personas');
  toast({ s: 'warn', text: 'Setting sync timeout — value revert ho gaya', sub: `${P[prev].name} hi jawab de ${P[prev].g} hai.`, lang: 'ur-Latn' });
}
$('#persona-switch').addEventListener('click', e => {
  const b = e.target.closest('b');
  setPersona(b ? (b.textContent.toLowerCase() === 'anna' ? 'anna' : 'jarvis') : P[S.persona].other);
});
$('#persona-switch').addEventListener('keydown', e => {
  if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); setPersona(P[S.persona].other); }
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setPersona(P[S.persona].other); }
});

/* ---------------------------------------------------------------- quick stats (live vitals; reference values in still mode) */
function setVital(k, v) {
  const bar = $(`[data-bar="${k}"]`), val = $(`[data-v="${k}"]`);
  bar.style.width = v + '%'; val.textContent = v + '%';
  const warn = (k === 'cpu' && v >= 85) || (k !== 'cpu' && v >= 90);
  val.closest('.tile').dataset.warn = String(warn);
}
let qsSource = 'pc';
function renderQS() {
  const qs = $('#qs');
  // the laptop is the source; when it is offline the tiles show the Agent (VPS) and say so
  const src = LAPTOP[S.laptop].online ? 'pc' : 'vps';
  if (src !== qsSource) {
    qsSource = src; const v = S.vitals;
    if (src === 'vps') { setVital('cpu', v.vcpu); setVital('mem', v.vmem); setVital('disk', v.vdisk); $('#tile-disk .lab').textContent = 'Disk (VPS)'; }
    else { setVital('cpu', v.cpu); setVital('mem', v.mem); setVital('disk', v.disk); $('#tile-disk .lab').textContent = 'Disk (C:)'; }
  }
  $('#qs-error').hidden = !S.crash;
  $('#qs-src').hidden = LAPTOP[S.laptop].online;
  $$('#qs .tile .stale').forEach(e => e.hidden = !S.stale);
  const n = deviceCount();
  const dv = $('[data-v="dev"]'); if (dv.textContent !== String(n)) { dv.textContent = n; $('[data-bar="dev"]').style.width = (n / 3 * 27.2).toFixed(1) + '%'; }
  qs.querySelector('.card-sub').textContent = LAPTOP[S.laptop].online ? 'Live system status' : 'Laptop offline: showing Agent (VPS)';
}
if (!STILL) setInterval(() => {
  if (S.stale) return;
  const v = S.vitals;
  v.cpu = clamp(Math.round(v.cpu + (Math.random() - .5) * 8), 6, 64);
  v.mem = clamp(Math.round(v.mem + (Math.random() - .5) * 2), 55, 72);
  v.vcpu = clamp(Math.round(v.vcpu + (Math.random() - .5) * 6), 12, 70);
  if (qsSource === 'vps') { setVital('cpu', v.vcpu); setVital('mem', v.vmem); } else { setVital('cpu', v.cpu); setVital('mem', v.mem); }
  if (S.page === 'agents') renderAgentsPart('machines');
}, 4000);
