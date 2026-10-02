
/* =====================================================================================================
   RENDER ALL (state changes from the prototype panel and system events)
   ===================================================================================================== */
function renderAll() {
  renderHeader(); renderVoice(); renderQS(); renderBadge(); renderOpenPop();
  if (S.page === 'agents') { AG_PARTS.forEach(p => renderAgentsPart(p, true)); paintAgentsCanvases(); }
  if (S.page === 'activity') renderActivity();
  if (S.page === 'settings') renderSettings();
  if (S.panelTab === 'approvals') renderApprovals();
  if (S.panelTab === 'tools') renderTools();
  if (dlgOpen === 'sim') simRender();
  if (dlgOpen === 'enroll') enPrereq();
  renderNavMarks();
}

/* =====================================================================================================
   PROTOTYPE CONTROLS (mockup only; not part of the product)
   ===================================================================================================== */
function toggleProto(force) {
  const p = $('#proto-panel'), open = force === undefined ? p.hidden : force;
  if (open) $('#proto').dataset.handle = 'on';
  p.hidden = !open; $('#proto-handle').setAttribute('aria-expanded', String(open));
  if (open) syncProto();
}
$('#proto-handle').addEventListener('click', () => toggleProto());
function syncProto() {
  const cur = { screen: S.screen === 'login' ? ($('#lg-err').hidden ? ($('#login-exp').hidden ? 'login' : 'expired') : 'login-error') : S.screen, page: S.page, persona: S.persona, voice: S.dup ? 'dup' : S.restarting ? 'restarting' : V.mode, agent: S.agent, bridge: S.bridge ? 'on' : 'off', laptop: S.laptop, data: S.data,
    cam: !S.camera ? (S.camState === 'error' ? 'error' : 'off') : S.camState, layout: layoutForce === 'art' ? 'art' : 'auto' };
  $$('#proto-panel [data-pc]').forEach(g => g.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === cur[g.dataset.pc]))));
  $('#pc-crash').setAttribute('aria-pressed', String(S.crash)); $('#pc-stale').setAttribute('aria-pressed', String(S.stale)); $('#pc-media').setAttribute('aria-pressed', String(S.mediaBad));
  $('#pc-stale-vps').setAttribute('aria-pressed', String(S.staleVps)); $('#pc-vid-none').setAttribute('aria-pressed', String(!S.vid.enrolled)); $('#pc-rs-unsup').setAttribute('aria-pressed', String(S.restartUnsupported));
  $('#pc-rm').checked = reduceForce;
}
// a voice state is reached through its inputs (the product never sets a state directly)
function setVoiceState(v) {
  Object.assign(S, { room: 'ok', dup: false, agentAbsent: false, restarting: false, audioBlocked: false, activity: null, micOn: true, listening: true });
  if (S.agent === 'offline' || S.agent === 'restarting') S.agent = 'online';
  Speech.stop();
  switch (v) {
    case 'offline': S.room = 'unavailable'; S.voiceErr = 'token request failed (503)'; break;
    case 'dup': S.dup = true; break;
    case 'joining': S.room = 'joining'; break;
    case 'agent-absent': S.agentAbsent = true; break;
    case 'restarting': S.restarting = true; break;
    case 'audio-blocked': S.audioBlocked = true; break;
    case 'speaking': S.activity = 'speaking'; if (!STILL) Speech.start(30); break;
    case 'thinking': S.activity = 'thinking'; break;
    case 'muted': S.listening = false; break;
    case 'listening': S.activity = 'listening'; break;
    case 'mic-off': S.micOn = false; break;
  }
  if (v !== 'offline') S.voiceErr = '';
  renderAll(); schedule();
}
$('#proto-panel').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  const g = b.closest('[data-pc]');
  if (g) {
    const v = b.dataset.v, k = g.dataset.pc;
    if (k === 'screen') { if (v === 'app') setScreen('app'); else if (v === 'login-error') setScreen('login', { error: 'Password ya code ghalat hai' }); else if (v === 'login-rate') setScreen('login', { error: 'Bohat zyada koshishein. 0:42 baad try karein.' });
      else if (v === 'login-503') setScreen('login', { error: 'Login abhi set nahi hua (owner enroll karein)' }); else if (v === 'login-net') setScreen('login', { error: 'Bridge offline' }); else if (v === 'expired') setScreen('login', { expired: true }); else setScreen(v); }
    if (k === 'cam') { S.camera = v === 'starting' || v === 'live'; S.camState = v; /* a failed start leaves the switch off */ if (S.page !== 'agents') go('agents', 'devices'); renderAgentsPart('browser'); renderAgentsPart('flow'); renderHeader(); paintAgentsCanvases(); }
    if (k === 'layout') { layoutForce = v === 'art' ? 'art' : null; fit(); }
    if (k === 'page') { if (v === 'chat' && layout !== 'ph') toast({ s: 'off', text: 'Chat is its own tab on phones', sub: 'On wider screens it is the right panel.' }); go(v); }
    if (k === 'persona') { if (v === 'fail') { S.personaFail = true; setPersona(P[S.persona].other); } else setPersona(v); }
    if (k === 'voice') setVoiceState(v);
    if (k === 'agent') { S.agent = v; S.restarting = v === 'restarting'; renderAll(); }
    if (k === 'bridge') { S.bridge = v === 'on'; renderAll(); if (!S.bridge) addEvent('system', 'k-err', 'Bridge offline', 'Reconnecting every 2 s'); }
    if (k === 'laptop') { S.laptop = v; renderAll(); const l = LAPTOP[v]; addEvent('device', v === 'full' ? 'k-device' : v === 'panic' ? 'k-err' : 'k-warn', `Laptop ${l.short.toLowerCase()}`, l.label); }
    if (k === 'data') setData(v);
    syncProto(); return;
  }
  switch (b.id) {
    case 'pc-speak': demoVoice(5); break;
    case 'pc-mic': Mic.on ? stopRealMic() : startRealMic(); break;
    case 'pc-cf-med': addConfirm('medium'); break;
    case 'pc-cf-high': addConfirm('high'); break;
    case 'pc-cf-voice': addConfirm('voice'); break;
    case 'pc-cf-long': addConfirm('long'); break;
    case 'pc-stale-vps': S.staleVps = !S.staleVps; renderAll(); if (S.page !== 'agents') go('agents', 'machines'); break;
    case 'pc-vid-none': S.vid.enrolled = !S.vid.enrolled; renderAll(); break;
    case 'pc-rs-unsup': S.restartUnsupported = !S.restartUnsupported; renderAll(); if (S.page !== 'agents') go('agents', 'devices'); break;
    case 'pc-chat-states': { selectTab('chat'); if (layout === 'ph') go('chat');
      addChat('user', 'WhatsApp par Ali ko bolo meeting 5 baje hai', { force: true, failed: 'queued' });
      addChat('user', 'Kal ka weather bhi bata dena', { force: true, src: 'spoken', failed: 'undelivered' });
      addChat('user', 'Paani peene ka reminder har 2 ghante', { force: true, src: 'bell', failed: 'failed', reason: 'reminder service timed out (20 s)' });
      addChat('ai', 'Reminder service abhi jawab nahi de rahi. Main dobara koshish karunga.', { force: true });
      addChat('ai', 'Agar phir bhi na hua to Tasks mein manually add kar dein.', { force: true, group: true }); break; }
    case 'pc-ev-notallowed': toast({ s: 'warn', text: 'Command abhi allowed nahi', sub: 'This tool is switched off in Settings › Automation.', lang: 'ur-Latn' }); addLog('WARNING', 'tools', 'command rejected: desktop_control is off'); break;
    case 'pc-ev-waketimeout': S.wakeFail = true; S.agent = 'offline'; renderAll(); wakeAgent(); S.wakeFail = false; break;
    case 'pc-ev-backend': html.classList.add('is-electron'); $('#pc-electron').checked = true; toast({ s: 'err', text: 'JARVIS & ANNA backend band ho gaya (code 1)', sub: 'The desktop app restarts it; the dashboard keeps the last state.', lang: 'ur-Latn', ms: 9000 }); break;
    case 'pc-cf-exp': if (D.queue[0]) { D.queue[0].left = 1; } break;
    case 'pc-cf-cancel': if (D.queue[0]) { $('#cf-cancel').textContent = 'Voice se cancel hua'; $('#cf-cancel').hidden = false; setTimeout(() => { $('#cf-cancel').hidden = true; $('#cf-cancel').textContent = ''; resolveHead('cancelled', 'voice'); }, STILL ? 0 : 1400); } break;
    case 'pc-cf-clear': D.queue = []; renderConfirm(); renderApprovals(); break;
    case 'pc-crash': S.crash = !S.crash; renderAll(); break;
    case 'pc-stale': S.stale = !S.stale; renderAll(); break;
    case 'pc-media': S.mediaBad = !S.mediaBad; renderAll(); break;
    case 'pc-ev-reminder': {
      const n = { id: 'n' + Date.now(), title: 'Call home now', at: nowMs(), spoken: true, ack: true };
      D.notifs.unshift(n); S.unread++; renderHeader();
      toast({ s: 'info', text: 'Call home now', sub: `Due ${fmtClock(nowMs())}. Spoken aloud.`, ms: 10000 });
      // the browser notification (Settings › Notifications) when the tab is in the background and permission is granted
      if ('Notification' in window && Notification.permission === 'granted') try { new Notification('Task reminder', { body: `Call home now · Due ${fmtClock(nowMs())}` }); } catch (e) {}
      addEvent('reminder', 'k-info', 'Reminder: Call home now', 'Spoken aloud'); break;
    }
    case 'pc-ev-reject': toast({ s: 'warn', text: 'Awaaz match nahi hui (41% < 45%) - ignore kiya' }); addLog('WARNING', 'voice', 'turn_rejected: voice_id 0.41 < 0.45'); addEvent('voice', 'k-warn', 'Voice ignored', 'Awaaz match nahi hui (41% < 45%)'); break;
    case 'pc-ev-image': {
      D.intel.unshift({ id: 'i' + Date.now(), type: 'image', label: 'Desktop screenshot', at: nowMs(), src: SHOT_SVG }); S.mediaIdx = 0;
      const chip = document.createElement('button'); chip.type = 'button'; chip.className = 'media-chip'; chip.dataset.testid = 'chat-media-chip'; chip.setAttribute('data-testid', 'chat-media-chip');
      chip.innerHTML = icon('image') + 'Desktop screenshot: View'; chip.onclick = () => go('activity', 'media');
      addChat('ai', 'Screenshot le liya. Yeh raha.', { extra: document.createElement('br') }).querySelector('.txt').append(chip);
      toast({ s: 'acc', text: `${P[S.persona].name} shared an image`, action: ['View', () => go('activity', 'media')] });
      addEvent('media', 'k-info', `${P[S.persona].name} shared an image`, 'Desktop screenshot'); break;
    }
    case 'pc-ev-alert': D.alerts.unshift({ type: 'sys', sev: 'err', title: 'Vector store unreachable', n: 1, at: nowMs() }); addEvent('alert', 'k-err', 'Vector store unreachable', 'Error, memory answers may be stale'); toast({ s: 'err', text: 'Vector store unreachable', action: ['Open', () => go('activity', 'timeline')] }); renderAll(); break;
    case 'pc-ev-tool': D.tools.unshift({ id: 't' + Date.now(), name: 'email_send', status: 'error', at: nowMs(), dur: '4.1 s', args: '{"to": "sana@office.pk"}', result: 'SMTP 535: authentication failed' }); addEvent('tool', 'k-err', 'email_send failed', 'SMTP 535: authentication failed'); addLog('ERROR', 'tools', 'email_send failed: SMTP 535'); toast({ s: 'err', text: 'email_send failed', sub: 'SMTP 535: authentication failed' }); if (S.panelTab === 'tools') renderTools(); break;
    case 'pc-ev-desktop': toast({ s: 'ok', text: 'DESKTOP_RECORD_AUDIO finished', sub: 'note_2127.m4a, 480 KB', action: ['Play', () => toast({ s: 'acc', text: 'Playing note_2127.m4a' })] }); break;
    case 'pc-open-sim': openSim(); break;
    case 'pc-open-enroll': openEnroll(); break;
    case 'pc-open-enroll-err': openEnroll('error'); break;
    case 'pc-open-lb': openLightbox(SHOT_SVG, 'Desktop screenshot, ' + tAgo(4)); break;
    case 'pc-open-voice': openPop('pop-voice', $('#pill-voice')); break;
    case 'pc-open-account': openPop('pop-account', $('#account')); break;
    case 'pc-reset': resetAll(); break;
  }
  syncProto();
});
$('#pc-rm').addEventListener('change', e => { setReduced(e.target.checked); });
$('#pc-electron').addEventListener('change', e => { html.classList.toggle('is-electron', e.target.checked); });
$$('#winctl button').forEach(b => b.addEventListener('click', () => toast({ s: 'off', text: b.title + ' (Electron window)', sub: 'Mockup only: the desktop app handles this.' })));
function setReduced(on) { reduceForce = on; html.classList.toggle('reduce-motion', on); cancelAnimationFrame(raf); raf = 0; resizeCanvases(); paintAgentsCanvases(); schedule(); }
mqReduce.addEventListener('change', () => { resizeCanvases(); schedule(); });
function setData(v) {
  const prevData = S.data;
  S.data = v === 'long' ? 'normal' : v;
  D = seedData();
  // rebuild the chat: empty, the two reference messages, or the long history
  $$('#chat-scroll > :not(#chat-empty)').forEach(n => { if (!n.dataset.static) n.remove(); });
  $$('#chat-scroll [data-static]').forEach(n => { n.hidden = v === 'empty'; });
  $('#chat-empty').hidden = v !== 'empty';
  if (v === 'long') loadLongChat();
  recentDirty = true; renderRecent(true);
  renderConfirm(); renderAll();
  if (v === 'long') scrollChat(true); else chat.scrollTop = 0;
}
function resetAll() {
  Speech.stop(); if (Mic.on) stopRealMic();
  S = Object.assign(seedState(), { page: S.page });
  html.dataset.persona = 'jarvis'; $('#hero-name').textContent = 'JARVIS'; $$('#persona-switch b').forEach((b, i) => b.setAttribute('aria-checked', String(i === 0)));
  $('#msg').placeholder = 'Message JARVIS...';
  setReduced(false); setData('normal'); setScreen('app'); selectTab('chat', true); go('overview');
  toast({ s: 'ok', text: 'Prototype reset to the reference state' });
}

/* =====================================================================================================
   TEST HOOKS (screenshots, e2e): window.__neon
   ===================================================================================================== */
window.__neon = {
  get S() { return S; }, get D() { return D; },
  voice: setVoiceState, go, persona: p => setPersona(p, { silent: true }), tab: selectTab, data: setData, screen: setScreen,
  open(n, v) { if (n === 'sim') openSim(); if (n === 'enroll') openEnroll(v); if (n === 'lightbox') openLightbox(SHOT_SVG, 'Desktop screenshot, ' + tAgo(4)); if (n.startsWith('pop-')) openPop(n, n === 'pop-account' ? $('#account') : $('#' + { 'pop-agent': 'pill-system', 'pop-devices': 'pill-devices', 'pop-voice': 'pill-voice' }[n])); },
  close() { closeDlg(); closePop(); },
  set(patch) { Object.assign(S, patch); renderAll(); },
  confirm: addConfirm, toast, act(id) { $('#' + id).click(); }, render: renderAll, reduce: setReduced, proto: toggleProto,
  speak: demoVoice, state: () => V.mode,
  orb: { fast: (t, L, m) => drawOrbFast(t, L, m), exact: (t, L, m) => drawOrb(t, L, m), draws: () => orbDraws, loop: () => loopMode(), V: () => V, setL: l => { V.L = l; } },
};
window.__jarvisTest = { setLevel: l => { V.L = l; }, speak: demoVoice, persona: setPersona, state: () => V.mode };

/* =====================================================================================================
   BOOT
   ===================================================================================================== */
addEventListener('resize', fit);
addEventListener('hashchange', route);
$$('#chat-scroll .msg').forEach(m => { m.dataset.static = '1'; });
// the reference's typed message gets its source mark too (keyboard; the word is in hidden text)
$$('#chat-scroll .msg.user .time').forEach(t => { t.insertAdjacentHTML('afterbegin', srcIcon('typed')); t.insertAdjacentHTML('beforebegin', '<span class="vh"> (typed)</span>'); });
// persona radios: roving tabindex (the checked name is the tab stop; arrows switch)
$('#persona-switch').removeAttribute('tabindex');
$$('#persona-switch b').forEach((b, i) => { b.tabIndex = i === 0 ? 0 : -1; });
// the reference breaks its two bubbles by hand; a space before each break lets them re-wrap in the fluid layouts
$$('#chat-scroll .bubble .txt br').forEach(br => br.before(' '));
buildWave(); fit(); route(); renderVoice(); renderHeader(); renderBadge(); renderQS(); renderConfirm(); renderSheet();
if (!STILL) {
  // live clock: the seeded rows and bubbles follow the time you open the mockup
  renderRecent(true);
  $$('#chat-scroll .msg[data-static] .time').forEach(t => { t.textContent = tAgo(0); });
}
// prototype controls: the handle stays hidden (it would sit in the reference's sidebar) until Alt+P, ?proto=1,
// or five quick taps on the logo (touch screens)
if (PROTO) { $('#proto').hidden = false; $('#proto').dataset.handle = Q.get('proto') === '1' ? 'on' : 'off'; }
{ let taps = [], t0 = 0; const tap = () => { const n = performance.now(); taps = taps.filter(x => n - x < 1600); taps.push(n); if (taps.length >= 5 && PROTO) { taps = []; toggleProto(true); } };
  ['#logo', '#logo-sm'].forEach(sel => $(sel).addEventListener('click', tap)); }
if (reduceForce) setReduced(true);
const scr = Q.get('screen'); if (scr && scr !== 'app') setScreen(scr);
if (Q.has('electron')) html.classList.add('is-electron');
document.fonts.ready.then(() => {
  paintLogos(); resizeCanvases();
  if (layout === 'art') layoutPills();
  requestAnimationFrame(() => { window.__replicaReady = true; window.__neonReady = true; schedule(); });
});
