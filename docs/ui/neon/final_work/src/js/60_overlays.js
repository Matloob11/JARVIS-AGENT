
/* =====================================================================================================
   DIALOGS: focus trap, Esc, focus returns to the opener
   ===================================================================================================== */
let dlgOpen = null, dlgOpener = null;
function openDlg(id) {
  closePop();
  if (dlgOpen) closeDlg();
  dlgOpener = document.activeElement;
  const l = $('#dlg-' + id); l.hidden = false; dlgOpen = id;
  l.classList.toggle('anim', !reduced() && !STILL);
  const f = [...l.querySelectorAll('[data-autofocus]')].find(x => !x.disabled) || l.querySelector('input:not([disabled])') || l.querySelector('[data-close]');
  setTimeout(() => f && f.focus({ preventScroll: true }), 30);
}
function closeDlg() {
  if (!dlgOpen) return;
  const id = dlgOpen; $('#dlg-' + id).hidden = true; dlgOpen = null;
  if (id === 'enroll') enStop();
  if (dlgOpener && dlgOpener.isConnected) dlgOpener.focus({ preventScroll: true });
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (dlgOpen) { e.preventDefault(); closeDlg(); return; }
    if (openPopId) { e.preventDefault(); closePop(true); return; }
    if (!$('#proto-panel').hidden) { toggleProto(false); return; }
  }
  if (e.key === 'Tab') {
    const scope = dlgOpen ? $('#dlg-' + dlgOpen) : html.classList.contains('sheet') ? $('#confirm') : null;
    if (!scope) return;
    const f = $$('button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea,[tabindex]:not([tabindex="-1"])', scope).filter(x => x.offsetParent !== null || x === document.activeElement);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (!scope.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  if (e.altKey && (e.key === 'p' || e.key === 'P') && PROTO) { e.preventDefault(); toggleProto(); }
});
$$('.layer').forEach(l => l.addEventListener('click', e => { if (e.target === l || e.target.closest('[data-close]')) closeDlg(); }));

/* ---------------------------------------------------------------- SIM lookup */
const SIM = { status: 'idle', configured: true };
function simRender() {
  const st = { idle: ['off', 'Idle'], loading: ['acc', 'Searching…'], blocked: ['err', 'Blocked by policy'], validation: ['warn', 'Check the fields'], rate: ['warn', 'Rate limited'], success: ['ok', 'Found'], offline: ['err', 'Agent offline'] }[SIM.status];
  $('#sim-prov').dataset.configured = String(SIM.configured);
  $('#sim-prov').innerHTML = SIM.configured ? `${sh('ok')}<span class="grow">Provider: authorized lookup API, configured</span><span data-testid="sim-status-chip" data-status="${SIM.status}">${pill(st[0], st[1], st[0] === 'acc' ? 'busy' : st[0])}</span>`
    : `${sh('warn')}<span class="grow">Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)</span><span data-testid="sim-status-chip" data-status="not-configured">${pill('warn', 'Not configured', 'warn')}</span>`;
  $('#sim-offline').hidden = S.agent === 'online' && S.bridge;
  $('#sim-go').disabled = SIM.status === 'loading' || !SIM.configured;
}
function openSim() { SIM.status = 'idle'; $('#sim-res').hidden = true; $('#sim-err').hidden = true; simRender(); openDlg('sim'); }
$('#sim-go').addEventListener('click', () => {
  const purpose = $('#sim-purpose').value.trim(), q = $('#sim-query').value.trim();
  $('#sim-res').hidden = true; $('#sim-err').hidden = true;
  const err = t => { $('#sim-err-t').textContent = t; $('#sim-err').hidden = false; };
  if (purpose.length < 5) { SIM.status = 'validation'; simRender(); err('Purpose likhein, kam az kam 5 characters. It goes into the audit log.'); $('#sim-purpose').focus(); return; }
  if (!/^(03\d{2}-?\d{7}|\d{5}-?\d{7}-?\d)$/.test(q)) { SIM.status = 'validation'; simRender(); err('Number 03xx-xxxxxxx ya CNIC 12345-1234567-1 format mein likhein.'); $('#sim-query').focus(); return; }
  if (S.agent !== 'online' || !S.bridge) { SIM.status = 'offline'; simRender(); err('Agent offline ya jawab nahi. Lookup agent ke online hone par chalega.'); return; }
  SIM.status = 'loading'; simRender();
  setTimeout(() => {
    if (/^0300-?0000000$/.test(q)) { SIM.status = 'rate'; simRender(); err('Rate limited: 5 lookups per hour. Try again at 10:15 PM.'); return; }
    SIM.status = 'success'; simRender(); $('#sim-res').hidden = false;
    addLog('INFO', 'audit', 'sim_lookup by owner (purpose recorded)'); addEvent('tool', 'k-ok', 'sim_lookup finished', 'Masked result shown');
  }, STILL ? 0 : 1200);
});

/* ---------------------------------------------------------------- Voice-ID enrollment */
const EN = { open: false, t: 0, timer: 0, done: false };
function enPrereq() {
  const ok = [V.mode !== 'offline' && V.mode !== 'joining', S.micOn, S.listening];
  const txt = [ok[0] ? 'Voice room joined' : 'Voice room not joined yet', ok[1] ? 'Mic on' : 'Mic off hai — pehle mic on karein', ok[2] ? 'Agent listening' : 'Agent mute hai — pehle unmute karein'];
  $$('#en-pre li').forEach((li, i) => { li.dataset.ok = String(ok[i]); li.querySelector('.n-sh').dataset.s = ok[i] ? 'ok' : 'warn'; li.lastElementChild.textContent = txt[i]; });
  return ok.every(Boolean);
}
function enReset(variant) {
  enStop(); EN.t = 0; EN.done = false;
  $('#en-success').hidden = $('#en-error').hidden = $('#en-timeout').hidden = true;
  $('#en-prog').textContent = '0s / 20s'; $('#en-prog-bar').style.setProperty('--v', 0); $('#en-level').style.setProperty('--v', 0); $('#en-db').textContent = '−60 dB';
  $('#en-quality').textContent = 'Quality shows while you record.';
  $('#en-commit').disabled = true; $('#en-start').disabled = !enPrereq();
  $('#en-start').innerHTML = icon('mic-fill') + 'Start';
  if (variant === 'error') { $('#en-error').hidden = false; $('#en-start').innerHTML = icon('refresh') + 'Start again'; }
  if (variant === 'timeout') { $('#en-timeout').hidden = false; $('#en-start').innerHTML = icon('refresh') + 'Start again'; }
  if (variant === 'success') { EN.t = 20; enShow(); $('#en-success').hidden = false; $('#en-commit').disabled = false; EN.done = true; }
}
function enShow() { $('#en-prog').textContent = `${Math.floor(EN.t)}s / 20s`; $('#en-prog-bar').style.setProperty('--v', (EN.t / 20).toFixed(3)); }
function enLevel(L, db) { $('#en-level').style.setProperty('--v', L.toFixed(3)); $('#en-db').textContent = (db ? Math.round(db) : Math.round(-60 + L * 48)) + ' dB'; }
function enStop() { clearInterval(EN.timer); EN.open = false; }
function openEnroll(variant) { enReset(variant); EN.open = true; openDlg('enroll'); if (variant === 'success') setTimeout(() => $('#en-commit').focus({ preventScroll: true }), 40); }
$('#en-start').addEventListener('click', () => {
  if (!enPrereq()) return;
  enReset(); EN.open = true;
  $('#en-start').disabled = true;
  EN.timer = setInterval(() => {
    EN.t = Math.min(20, EN.t + .25 * 3);
    enShow();
    if (!Mic.on) enLevel(.45 + Math.random() * .35);
    $('#en-quality').textContent = EN.t < 6 ? 'Recording. Keep a normal distance from the mic.' : EN.t < 14 ? 'Good signal: SNR 24 dB, little echo.' : 'Almost done. Finish the third line.';
    if (EN.t >= 20) { clearInterval(EN.timer); $('#en-success').hidden = false; $('#en-commit').disabled = false; $('#en-quality').textContent = 'Quality: good (SNR 24 dB).'; enLevel(0); EN.done = true; $('#en-commit').focus(); }
  }, STILL ? 1 : 250);
});
$('#en-commit').addEventListener('click', () => {
  S.vid.enrolled = true; S.vid.samples = 6; S.vid.score = .91;
  closeDlg(); toast({ s: 'ok', text: 'Voice-ID enrolled', sub: 'Match score 0.91 across 6 samples.' });
  addEvent('voice', 'k-ok', 'Voice-ID enrolled', '6 samples'); renderAgentsPart('vid'); if (S.page === 'settings') renderSettings();
});

/* ---------------------------------------------------------------- lightbox */
function openLightbox(src, cap) { $('#lb-img').src = src; $('#lb-img').alt = cap; $('#lb-cap').textContent = cap; openDlg('lightbox'); }

/* =====================================================================================================
   FULL SCREENS: session check, login, VPS unreachable
   ===================================================================================================== */
function setScreen(sc, opts = {}) {
  S.screen = sc;
  $('#scr-check').hidden = sc !== 'check'; $('#scr-login').hidden = sc !== 'login'; $('#scr-offline').hidden = sc !== 'offline';
  $('#viewport').inert = sc !== 'app';
  // e2e post-login marker (replaces the old "System Flow" check): present only while the dashboard screen is shown
  $('#app-ready').hidden = sc !== 'app';
  if (sc === 'login') {
    $('#login-exp').hidden = !opts.expired; $('#lg-err').hidden = !opts.error; if (opts.error) $('#lg-err-t').textContent = opts.error;
    $('#lg-pw').value = opts.error ? 'wrong-password' : ''; $('#lg-totp').value = opts.error ? '123456' : '';
    setTimeout(() => (opts.error ? $('#lg-pw') : $('#lg-pw')).focus({ preventScroll: true }), 40);
  }
  if (sc === 'check') $('#check-t').textContent = opts.text || 'Checking session…';
  if (sc !== 'app') { closePop(); closeDlg(); }
  paintLogos(); renderSheet(); schedule();
  if (sc === 'app') resizeCanvases();
}
$('#lg-eye').addEventListener('click', () => { const pw = $('#lg-pw'), show = pw.type === 'password'; pw.type = show ? 'text' : 'password'; $('#lg-eye').setAttribute('aria-label', show ? 'Hide password' : 'Show password'); $('#lg-eye').title = show ? 'Hide password' : 'Show password'; $('#lg-eye use').setAttribute('href', show ? '#i-eye-off' : '#i-eye'); });
$('#lg-rec-toggle').addEventListener('click', () => {
  S.login.rec = !S.login.rec;
  $('#lg-rec-wrap').hidden = !S.login.rec; $('#lg-totp-wrap').hidden = S.login.rec;
  $('#lg-rec-toggle').textContent = S.login.rec ? 'Authenticator code use karein' : 'Recovery code use karein';
  (S.login.rec ? $('#lg-rec') : $('#lg-totp')).focus();
});
$('#login-form').addEventListener('submit', e => {
  e.preventDefault();
  const pw = $('#lg-pw').value, code = S.login.rec ? $('#lg-rec').value.trim() : $('#lg-totp').value.trim();
  const fail = t => { $('#lg-err-t').textContent = t; $('#lg-err').hidden = false; $('#lg-submit').textContent = 'Sign in'; $('#lg-submit').disabled = false; };
  $('#lg-err').hidden = true;
  if (!pw) { fail('Password likhein.'); $('#lg-pw').focus(); return; }
  if (!S.login.rec && !/^\d{6}$/.test(code)) { fail('Authenticator ka 6 digit code likhein.'); $('#lg-totp').focus(); return; }
  $('#lg-submit').textContent = 'Signing in…'; $('#lg-submit').disabled = true;
  setTimeout(() => {
    if (pw === 'wrong-password') { fail('Password ya code ghalat hai'); return; }
    $('#lg-submit').textContent = 'Sign in'; $('#lg-submit').disabled = false;
    // privacy + autoplay rules: the product always lands with the mic off after a sign-in (the reference frame is the mic-on state)
    S.micOn = false; S.activity = null;
    setScreen('check'); setTimeout(() => { setScreen('app'); renderVoice(); }, STILL ? 0 : 900);
  }, STILL ? 0 : 700);
});
$('#off-retry').addEventListener('click', () => { setScreen('check', { text: 'Reconnecting…' }); setTimeout(() => setScreen('app'), STILL ? 0 : 1100); });

/* =====================================================================================================
   ONE CLICK HANDLER for actions that appear in several places (popovers, pages, inline buttons)
   ===================================================================================================== */
function logout() { closePop(); setScreen('login'); }
function restartSession() {
  if (D.queue.some(c => c.tool === 'shutdown_agent')) return;
  pushConfirm({ id: 'c' + (++cfSeq), tool: 'shutdown_agent', label: 'Restart', risk: 'medium', q: 'Restart the agent session? Voice and chat pause for about 20 seconds.', code: 'shutdown_agent {"restart": true}', where: 'Agent (VPS)', voice: false, ttl: 90, left: 90,
    after: true });
}
const _resolveHead = resolveHead;
// shutdown_agent approvals restart the session (simulated)
resolveHead = function (state, via) {
  const c = D.queue[0];
  _resolveHead(state, via);
  if (c && c.tool === 'shutdown_agent' && state === 'allowed') {
    S.agent = 'restarting'; S.restarting = true; renderAll();
    setTimeout(() => { S.agent = 'online'; S.restarting = false; renderAll(); addEvent('system', 'k-ok', 'Agent session restarted', 'Back online'); toast({ s: 'ok', text: 'Agent session restarted' }); }, STILL ? 0 : 3500);
  }
};
function desktopStop() {
  S.laptop = 'read'; renderAll();
  addEvent('device', 'k-warn', 'Desktop stopped (read-only)', 'Only the laptop tray can switch back to Full');
  toast({ s: 'warn', text: 'Laptop switched to read-only', sub: 'Only the laptop tray can switch back to Full.' });
}
document.addEventListener('click', e => {
  const t = e.target.closest('[data-act]');
  if (t) {
    const a = t.dataset.act;
    if (t.tagName === 'A' && a === 'download') { e.preventDefault(); toast({ s: 'ok', text: 'Download started', sub: 'via /vortex/media' }); return; }
    switch (a) {
      case 'agent-pause': S.agent = 'paused'; renderAll(); addEvent('system', 'k-warn', 'Agent paused', 'Wake it from System'); toast({ s: 'warn', text: 'Agent paused', sub: 'pause_agent sent' }); break;
      case 'agent-wake': wakeAgent(); break;
      case 'reconnect': setScreen('check', { text: 'Reconnecting…' }); setTimeout(() => { setScreen('app'); S.bridge = true; S.micOn = false; renderAll(); toast({ s: 'ok', text: 'Reconnected', sub: 'Voice rejoined with the mic off.' }); }, STILL ? 0 : 1100); break;
      case 'takeover': takeOver(); break;
      case 'desktop-stop': desktopStop(); break;
      case 'stop': stopSpeaking(); break;
      case 'enroll': closePop(); openEnroll(); break;
      case 'enable-audio': enableAudio(); break;
      case 'logout': logout(); break;
      case 'notif-on': {
        const done = p => { S.notifPerm = p === 'denied' ? 'denied' : 'granted'; renderOpenPop(); if (S.page === 'settings') renderSettings(); toast(S.notifPerm === 'granted' ? { s: 'ok', text: 'Desktop notifications on' } : { s: 'warn', text: 'Notifications are blocked in this browser' }); };
        if ('Notification' in window && Notification.permission === 'default' && !STILL) Notification.requestPermission().then(done, () => done('granted')); else done('granted');
        break;
      }
      case 'open-sim': closePop(); openSim(); break;
      case 'open-lb': openLightbox(SHOT_SVG, 'Desktop screenshot, ' + tAgo(4)); break;
      case 'play-audio': toast({ s: 'acc', text: 'Playing note_2127.m4a', sub: 'Streams through /vortex/media' }); break;
      case 'persona': setPersona(t.dataset.v); break;
      case 'restart': restartSession(); break;
      case 'retry-card': S.crash = false; renderAll(); toast({ s: 'ok', text: 'Card reloaded' }); break;
    }
    return;
  }
  // header popovers
  const pt = e.target.closest('[data-pop]');
  if (pt) { e.stopPropagation(); openPop(pt.dataset.pop, pt); return; }
  if (openPopId && !e.target.closest('.pop')) closePop();
  // switches inside popovers
  const s = e.target.closest('.pop .n-sw');
  if (s) {
    const fk = s.dataset.fk;
    if (fk === 'mic') { S.micOn ? micOffAction() : micOnAction(); }
    if (fk === 'lis') { S.listening = !S.listening; addLog('INFO', 'voice', S.listening ? 'unmute sent' : 'mute sent'); renderVoice(); renderOpenPop(); }
    if (fk === 'wake') toggleWake();
  }
});
function toggleWake() {
  S.wakePending = true; renderOpenPop();
  setTimeout(() => { S.wake = !S.wake; S.wakePending = false; renderOpenPop(); if (S.page === 'agents') renderAgentsPart('voice'); if (S.page === 'settings') renderSettings(); addLog('INFO', 'settings', 'wake_word=' + S.wake); }, STILL ? 0 : 600);
}
$('#pop-scrim').addEventListener('click', () => closePop(true));
// Agents page controls
$('#page-agents').addEventListener('click', e => {
  const s = e.target.closest('.n-sw'); if (!s || s.disabled) return;
  const fk = s.dataset.fk;
  if (fk === 'ag-lis') { S.listening = !S.listening; renderVoice(); renderAgentsPart('voice'); renderAgentsPart('flow'); }
  if (fk === 'ag-wake') { S.wake = !S.wake; renderAgentsPart('voice'); }
  if (fk === 'cam') {
    S.camera = !S.camera;
    if (S.camera) {
      // Starting camera… while the browser asks for the permission, then the preview (or the error name)
      S.camState = 'starting';
      setTimeout(() => { if (!S.camera || S.camState !== 'starting') return; S.camState = S.camFail ? 'error' : 'live'; if (S.camFail) S.camera = false; renderAgentsPart('browser'); renderAgentsPart('flow'); renderHeader();
        if (S.camState === 'live') addEvent('device', 'k-warn', 'Camera on', S.camShare ? 'Shared with the agent' : 'Local preview only'); else toast({ s: 'err', text: 'Camera could not start', sub: 'NotAllowedError: permission denied' }); }, STILL ? 0 : 1200);
    }
    renderAgentsPart('browser'); renderAgentsPart('flow'); renderHeader();
  }
  if (fk === 'camsh') { S.camShare = !S.camShare; renderAgentsPart('browser'); renderAgentsPart('flow'); }
});
// Activity page controls
$('#page-activity').addEventListener('click', e => {
  const at = e.target.closest('[data-at]'); if (at) { S.actTab = at.dataset.at; history.replaceState(null, '', '#/activity/' + S.actTab); renderActivity(); $('#act-body').scrollTop = 0; return; }
  const tl = e.target.closest('[data-tl]'); if (tl) { S.tlFilter = tl.dataset.tl; renderActivityBody(); return; }
  const lt = e.target.closest('[data-lt]'); if (lt) { S.logs.tab = lt.dataset.lt; renderActivityBody(); return; }
  const lg = e.target.closest('[data-lg]');
  if (lg) {
    const L = S.logs;
    if (lg.dataset.lg === 'warn') L.warnOnly = !L.warnOnly;
    if (lg.dataset.lg === 'pause') { L.paused = !L.paused; if (!L.paused && D.logsHeld) { D.logs.push(...D.logsHeld); D.logsHeld = []; L.held = 0; } }
    if (lg.dataset.lg === 'clear') { D.logs = []; toast({ s: 'off', text: 'Log view cleared', sub: 'Only on this screen; the server keeps its logs.' }); }
    renderActivityBody(); return;
  }
  const sw2 = e.target.closest('[data-testid="trace-info-toggle"]'); if (sw2) { S.logs.info = !S.logs.info; renderActivityBody(); return; }
  const tk = e.target.closest('[data-tk]');
  if (tk) {
    const k = tk.dataset.tk;
    if (k === 'cancel') S.taskCancel = tk.dataset.id;
    if (k === 'keep') S.taskCancel = null;
    if (k === 'yes') { const t = D.tasks.find(x => x.id === tk.dataset.id); D.tasks = D.tasks.filter(x => x.id !== tk.dataset.id); S.taskCancel = null; toast({ s: 'off', text: `Cancelled: ${t.title}` }); }
    if (k === 'refresh') toast({ s: 'ok', text: 'Tasks refreshed' });
    renderActivityBody(); return;
  }
  const mm = e.target.closest('[data-mm]');
  if (mm) {
    const k = mm.dataset.mm;
    if (k === 'clear') S.memClear = 1;
    if (k === 'no') S.memClear = 0;
    if (k === 'yes') { D.mems = []; S.memClear = 0; toast({ s: 'ok', text: 'short term cleared', sub: 'Backup: C:\\JARVIS\\memory\\backup_2142.json', ms: 12000 }); addEvent('memory', 'k-neutral', 'Short-term memory cleared', 'Backup saved'); }
    renderActivityBody(); return;
  }
  const md = e.target.closest('[data-md]');
  if (md) {
    const k = md.dataset.md, it = D.intel[S.mediaIdx];
    if (k === 'toggle') S.mediaOpen = !S.mediaOpen;
    if (k === 'pick') { S.mediaIdx = +md.dataset.i; S.mediaOpen = true; }
    if (k === 'lightbox') { openLightbox(it.src, it.label + ', ' + fmtClock(it.at)); return; }
    if (k === 'copy') { navigator.clipboard && navigator.clipboard.writeText(it.url).catch(() => {}); toast({ s: 'ok', text: 'URL copied', sub: 'Opens only on the PC network.' }); return; }
    if (k === 'retry') { S.mediaBad = false; }
    if (k === 'play') { toast({ s: 'acc', text: 'Playing: ' + it.label }); return; }
    renderActivityBody(); return;
  }
});
$('#page-activity').addEventListener('input', e => {
  if (e.target.id === 'lg-filter') { S.logs.filter = e.target.value; renderTerm(); }
  if (e.target.id === 'mem-q') { S.memQuery = e.target.value; const pos = e.target.selectionStart; renderActivityBody(); const q = $('#mem-q'); q.focus(); q.setSelectionRange(pos, pos); }
});
$('#page-activity').addEventListener('change', e => { if (e.target.id === 'rs-sel') { S.logs.plan = +e.target.value; renderActivityBody(); } });
// Settings page controls
$('#page-settings').addEventListener('click', e => {
  const b = e.target.closest('[data-set]'); if (!b || b.tagName === 'SELECT' || b.tagName === 'INPUT') return;
  const f = b.dataset.set;
  if (b.classList.contains('n-sw')) {
    if (f === 'muted') { S.listening = !S.listening; renderVoice(); renderSettings(); return; }
    const cur = b.getAttribute('aria-checked') === 'true';
    setField(f, !cur); return;
  }
  setField(f, b.dataset.v);
});
$('#page-settings').addEventListener('change', e => { const t = e.target; if (t.dataset.set && t.tagName === 'SELECT') setField(t.dataset.set, t.value); if (t.dataset.set === 'th') setField('th', t.value); });
$('#page-settings').addEventListener('input', e => { const t = e.target; if (t.dataset.set === 'th') { $('#s-th-v').textContent = (+t.value).toFixed(2); t.style.setProperty('--p', ((t.value - .2) / .7 * 100).toFixed(1) + '%'); } });
$('#page-settings').addEventListener('keydown', e => { if (e.target.dataset.set === 'quiet' && e.key === 'Enter') { e.preventDefault(); e.target.blur(); } });
$('#page-settings').addEventListener('focusout', e => { const t = e.target; if (t.dataset && t.dataset.set === 'quiet' && t.value !== S.set.quiet) { if (!/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(t.value)) { S.fstate.quietHours = 'error'; S.ferr = Object.assign(S.ferr || {}, { quietHours: 'Use HH:MM-HH:MM, for example 23:00-07:00' }); renderSettings(); return; } setField('quiet', t.value); } });
// phone tab bar
$('#tabbar').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) go(b.dataset.tab); });
// sidebar nav: Up/Down moves between items
$('nav[aria-label="Pages"]').addEventListener('keydown', e => {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const items = $$('.nav-item'), i = items.indexOf(document.activeElement); if (i < 0) return;
  e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
});
$$('.card-open').forEach((b, i) => b.addEventListener('click', () => go(i ? 'activity' : 'agents', i ? 'timeline' : 'machines')));
