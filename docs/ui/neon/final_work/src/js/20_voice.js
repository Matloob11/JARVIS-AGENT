
/* ---------------------------------------------------------------- audio: simulated speech + microphone */
const Speech = {
  active: false, t0: 0, dur: 0, seed: 1,
  start(seconds) { this.active = true; this.t0 = performance.now() / 1000; this.dur = seconds; this.seed = Math.random() * 1000; },
  stop() { this.active = false; },
  level(t) {
    if (!this.active) return 0;
    const e = t - this.t0;
    if (e > this.dur) { this.active = false; return 0; }
    // syllables ~5 Hz, words ~1.6 Hz, phrase pauses ~0.35 Hz; smooth attack at start, decay at the end
    const s = this.seed;
    const syl = Math.max(0, Math.sin(e * TAU * 4.6 + s) * .5 + Math.sin(e * TAU * 7.3 + s * 2) * .3 + .45);
    const word = Math.max(0, Math.sin(e * TAU * 1.55 + s * 3) * .6 + .55);
    const phrase = Math.sin(e * TAU * .33 + s) > -.75 ? 1 : .05;
    const env = clamp(e / .15, 0, 1) * clamp((this.dur - e) / .3, 0, 1);
    return clamp(syl * word * phrase * env * (.8 + .4 * Math.random()), 0, 1);
  }
};
const Mic = {
  on: false, ctx: null, an: null, buf: null, stream: null, level: 0, bands: null,
  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { return false; }
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.an = this.ctx.createAnalyser(); this.an.fftSize = 1024; this.an.smoothingTimeConstant = .6;
    src.connect(this.an);
    this.buf = new Float32Array(this.an.fftSize); this.fbuf = new Uint8Array(this.an.frequencyBinCount);
    this.on = true; return true;
  },
  stop() { if (this.stream) this.stream.getTracks().forEach(t => t.stop()); if (this.ctx) this.ctx.close(); this.on = false; this.level = 0; this.stream = this.ctx = this.an = null; },
  read() {
    if (!this.on) return 0;
    this.an.getFloatTimeDomainData(this.buf);
    let s = 0; for (let i = 0; i < this.buf.length; i++) s += this.buf[i] * this.buf[i];
    const db = 20 * Math.log10(Math.sqrt(s / this.buf.length) + 1e-9);
    this.db = db;
    const L = clamp((db + 56) / 40, 0, 1);
    this.level += (L - this.level) * (L > this.level ? .55 : .14);   // fast attack, slow release
    this.an.getByteFrequencyData(this.fbuf);
    const bands = []; const n = this.fbuf.length;
    for (let i = 0; i < 24; i++) { const a = Math.floor(Math.pow(i / 24, 2) * n * .45) + 2, b = Math.floor(Math.pow((i + 1) / 24, 2) * n * .45) + 3; let m = 0; for (let j = a; j < b; j++) m = Math.max(m, this.fbuf[j]); bands.push(m / 255); }
    this.bands = bands;
    return this.level;
  }
};

/* ---------------------------------------------------------------- the ten voice states (first match wins) */
const VS = {
  offline: { pill: 'Voice Offline', compact: 'Offline', pillIc: 'mic-off', look: { grey: 1, dim: .72, notch: true, glyph: 'alert', tone: 'warn' }, wave: 'off' },
  joining: { pill: 'Joining Voice', compact: 'Joining', pillIc: 'mic-fill', spin: true, look: { grey: 1, dim: .6 }, wave: 'dots' },
  'agent-absent': { pill: 'No Agent', compact: 'No agent', pillIc: 'mic-fill', look: { grey: 1, dim: .6, glyph: 'user-off' }, wave: 'off' },
  'audio-blocked': { pill: 'Audio Blocked', compact: 'Blocked', pillIc: 'speaker-off', look: { grey: 1, dim: .6, glyph: 'speaker-off', tone: 'warn' }, wave: 'off' },
  speaking: { pill: 'Speaking', compact: 'Speaking', pillIc: 'mic-fill', ping: true, look: {}, wave: 'speaking' },
  thinking: { pill: 'Thinking', compact: 'Thinking', pillIc: 'mic-fill', look: { comet: true }, wave: 'thinking' },
  muted: { pill: 'Not Listening', compact: 'Muted', pillIc: 'ear-off', look: { grey: 1, dim: .72, dashed: true, glyph: 'ear-off' }, wave: 'off' },
  listening: { pill: 'Listening', compact: 'Listening', pillIc: 'mic-fill', ping: true, look: {}, wave: 'listening' },
  'mic-off': { pill: 'Voice Ready', compact: 'Ready', pillIc: 'mic-off', look: { dim: .86 }, wave: 'dots' },
  idle: { pill: 'Voice Ready', compact: 'Ready', pillIc: 'mic-fill', look: {}, wave: 'idle' },
};
function deriveVoice() {
  if (S.room === 'unavailable' || S.dup) return 'offline';
  if (S.room === 'joining') return 'joining';
  if (S.agentAbsent || S.restarting || S.agent === 'offline' || S.agent === 'restarting') return 'agent-absent';
  if (S.audioBlocked) return 'audio-blocked';
  if (S.activity === 'speaking') return 'speaking';
  if (S.activity === 'thinking') return 'thinking';
  if (!S.listening) return 'muted';
  if (S.activity === 'listening' && S.micOn) return 'listening';
  if (!S.micOn) return 'mic-off';
  return 'idle';
}
const V = { mode: 'idle', L: 0, prevL: 0, userL: 0, bands: null };
const statusEl = $('#voice-status'), quoteEl = $('#hero-quote');
function statusLine(v) {
  const p = P[S.persona], n = p.name;
  switch (v) {
    case 'offline': return S.dup ? { s: 'err', text: 'Voice is on another device', tone: 'err', act: ['Take over', 'voice-take-over', takeOver] } : { s: 'err', text: 'Voice unavailable', tone: 'err' };
    case 'joining': return { s: 'busy', text: 'Joining voice…', tone: 'mute' };
    case 'agent-absent': return S.restarting || S.agent === 'restarting' ? { s: 'busy', text: 'Agent session restarting…', tone: 'mute' }
      : { s: 'warn', text: `${n} is not in the voice room`, tone: 'warn', act: ['Wake', 'voice-wake-agent', wakeAgent] };
    case 'audio-blocked': return { s: 'warn', text: 'Audio is blocked', tone: 'warn', act: ['Enable audio', 'enable-audio', enableAudio] };
    case 'speaking': return { text: `${n} is speaking`, act: ['Stop', 'hero-stop', stopSpeaking] };
    case 'thinking': return { text: 'Thinking…' };
    case 'muted': return { s: 'warn', text: 'Not listening (muted)', tone: 'warn' };
    case 'listening': return { text: 'Listening to you' };
    case 'mic-off': return { s: 'off', text: 'Mic is off', tone: 'mute' };
    default: return { text: 'Listening for your command' };
  }
}
function helperText(v) {
  const p = P[S.persona];
  switch (v) {
    case 'offline': return S.dup ? 'Voice doosre tab ya device par chal rahi hai. Take over dabayein; chat chalti rahegi.' : 'Voice abhi band hai. Chat aur controls chalte rahenge.';
    case 'joining': return 'Voice room join ho raha hai. Chat chalti rahegi.';
    case 'agent-absent': return S.restarting || S.agent === 'restarting' ? 'Agent session restart ho raha hai. Thodi der mein wapas aa jayega.' : `${p.name} room me nahi. Wake dabayein; chat chalti rahegi.`;
    case 'audio-blocked': return 'Browser ne awaaz rok di hai. Enable audio dabayein.';
    case 'muted': return `${p.name} mic se nahi sune${p.fut}; typed chat chalti rahegi.`;
    case 'mic-off': return 'Mic band hai. Start Listening dabayein ya type karein.';
    default: return null;
  }
}
let lastAnnounced = '', announceT = 0;
function announce(msg) { clearTimeout(announceT); announceT = setTimeout(() => { const el = $('#sr'); el.textContent = ''; setTimeout(() => { el.textContent = msg; }, 40); }, 1200); }

function renderVoice() {
  const v = deriveVoice(), prev = V.mode;
  V.mode = v;
  const cfg = VS[v], line = statusLine(v);
  // status line (exact replica DOM for the reference state)
  statusEl.textContent = '';
  if (line.s) statusEl.insertAdjacentHTML('beforeend', sh(line.s));
  statusEl.append(line.text);
  if (line.tone) statusEl.dataset.tone = line.tone; else delete statusEl.dataset.tone;
  if (line.act) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'act'; b.textContent = line.act[0]; b.dataset.testid = line.act[1];
    b.setAttribute('data-testid', line.act[1]); b.onclick = line.act[2]; statusEl.append(b);
  }
  const user = $('#user-speaking-label');
  user.hidden = v !== 'listening';
  statusEl.classList.toggle('vh', v === 'listening');
  // quote slot: the quote, or the helper line in attention states
  const help = helperText(v);
  quoteEl.textContent = help || QUOTE;
  if (help) { quoteEl.dataset.helper = ''; quoteEl.lang = 'ur-Latn'; } else { delete quoteEl.dataset.helper; quoteEl.removeAttribute('lang'); }
  if (help && S.voiceErr && v === 'offline') { const e = document.createElement('span'); e.className = 'err'; e.textContent = ' Voice: ' + S.voiceErr; quoteEl.append(e); }
  // buttons
  const listen = $('#btn-listen'), mic = $('#btn-micoff'), task = $('#btn-task');
  const off = v === 'offline' || v === 'joining';
  listen.disabled = off; mic.disabled = off;
  listen.setAttribute('aria-pressed', String(S.micOn && S.listening && !off));
  mic.setAttribute('aria-pressed', String(!S.micOn && !off));
  $('#btn-listen-lbl').textContent = v === 'joining' ? 'Joining…' : 'Start Listening';
  task.setAttribute('aria-pressed', String(!!S.task)); task.dataset.active = String(!!S.task);
  // header pill + orb look
  renderVoicePill();
  const look = cfg.look;
  orbLook = { notch: !!look.notch, dashed: !!look.dashed, comet: !!look.comet, pull: V.userL };
  const g = $('#orb-glyph');
  if (look.glyph) { g.hidden = false; g.dataset.tone = look.tone || ''; g.querySelector('use').setAttribute('href', '#i-' + look.glyph); }
  else g.hidden = true;
  $('#user-ring').hidden = v !== 'listening';
  $('#hero').dataset.voice = v;
  $('#orb').setAttribute('aria-label', `${P[S.persona].name} voice orb: ${line.text}`);
  if (prev !== v) {
    if (['offline', 'muted', 'mic-off', 'agent-absent', 'audio-blocked'].includes(v) || ['offline', 'muted', 'mic-off', 'agent-absent', 'audio-blocked'].includes(prev)) announce(line.text);
    renderCaptions();
    if (typeof renderOpenPop === 'function') renderOpenPop();
  }
  schedule();
  if (STILL || reduced()) drawStatic();
}
function renderVoicePill() {
  const v = V.mode, cfg = VS[v], pv = $('#pill-voice');
  const compact = hdr.compact;
  $('#lbl-voice').textContent = compact ? cfg.compact : cfg.pill;
  pv.querySelector('use').setAttribute('href', '#i-' + cfg.pillIc);
  pv.querySelector('.ping').hidden = !cfg.ping;
  const active = ['idle', 'listening', 'thinking', 'speaking', 'mic-off'].includes(v);
  pv.dataset.active = String(active);
  pv.title = `Voice: ${statusLine(v).text}. Voice ${P[S.persona].voice}`;
  if (layout === 'art') layoutPills();
}
function setActivity(a) { if (S.activity !== a) { S.activity = a; renderVoice(); } }

/* ---------------------------------------------------------------- voice actions */
function takeOver() { S.dup = false; renderVoice(); addEvent('voice', 'k-info', 'Voice taken over', 'This browser now has the voice room'); toast({ s: 'ok', text: 'Voice is on this browser now.' }); }
function wakeAgent() {
  if (S.agent === 'online' && !S.agentAbsent) return;
  S.wakeBusy = true; renderOpenPop(); renderAgentsPart('agent');
  toast({ s: 'busy', text: `${P[S.persona].name} ko jaga rahe hain…`, sub: 'start sent to the agent', lang: 'ur-Latn' });
  // the product waits 90 s; the prototype's "Wake times out" state compresses that to 4 s
  if (S.wakeFail) { setTimeout(() => { S.wakeBusy = false; renderOpenPop(); renderAgentsPart('agent'); toast({ s: 'warn', text: 'Agent abhi tak online nahi…', sub: 'No answer 90 s after Start / Wake. Check the VPS, then try again.', lang: 'ur-Latn', ms: 9000 }); }, STILL ? 0 : 4000); return; }
  setTimeout(() => { S.wakeBusy = false; S.agentAbsent = false; S.agent = 'online'; renderAll(); addEvent('system', 'k-ok', 'Agent online', 'Woken from the dashboard'); toast({ s: 'ok', text: `${P[S.persona].name} is back in the voice room.` }); }, STILL ? 0 : 2200);
}
function enableAudio() { S.audioBlocked = false; renderVoice(); toast({ s: 'ok', text: 'Audio enabled.' }); }
function stopSpeaking() { Speech.stop(); setActivity(null); addLog('INFO', 'voice', 'stop sent: speech interrupted by owner'); }
function micOnAction() {
  if (V.mode === 'offline' || V.mode === 'joining') return;
  const was = S.micOn && S.listening;
  S.micOn = true;
  if (!S.listening) { S.listening = true; addLog('INFO', 'voice', 'unmute sent (Start Listening)'); }
  renderVoice(); renderOpenPop();
  if (!was) addEvent('voice', 'k-info', 'Microphone on', 'Voice room joined, mic live');
}
function micOffAction() {
  if (V.mode === 'offline' || V.mode === 'joining') return;
  if (Mic.on) stopRealMic();
  S.micOn = false; if (S.activity === 'listening') S.activity = null;
  renderVoice(); renderOpenPop();
  addEvent('voice', 'k-neutral', 'Microphone off', 'Typed chat keeps working');
}
async function startRealMic() {
  if (Mic.on) return true;
  const ok = await Mic.start();
  if (!ok) { toast({ s: 'warn', text: 'Microphone permission was not granted', sub: 'Allow it in the browser, then try again.' }); return false; }
  S.micOn = true; S.listening = true;
  $('#pc-mic').setAttribute('aria-pressed', 'true');
  renderVoice(); addEvent('voice', 'k-info', 'Microphone test on', 'The orb follows your voice');
  return true;
}
function stopRealMic() {
  Mic.stop();
  $('#pc-mic').setAttribute('aria-pressed', 'false');
  if (S.activity === 'listening') S.activity = null;
  renderVoice();
}
function demoVoice(sec) { if (['offline', 'joining', 'agent-absent', 'audio-blocked'].includes(V.mode)) { toast({ s: 'warn', text: 'Voice is not available in this state', sub: statusLine(V.mode).text }); return; } S.activity = 'speaking'; Speech.start(sec || 5); renderVoice(); schedule(); }

/* ---------------------------------------------------------------- render loop */
/* loop policy: full (speaking, listening, thinking, a voice level or a colour still settling): every frame, but a frame is
   skipped when less than 12.5 ms passed (60 fps on 60/120 Hz, ~72 on 144 Hz); breath (idle, mic off: only the core
   breathes): ~15 fps from a 66 ms timer; stop (static states once settled, tab hidden, orb off-screen, other page,
   reduced motion): no frames, the last frame stays on the canvas */
let lastT = 0, raf = 0, breathT = 0, orbVisible = true, pcLvl = null, userRingEl = null;
function orbOnScreen() { return S.screen === 'app' && S.page === 'overview' && !document.hidden && orbVisible; }
function loopMode() {
  const mode = V.mode, cfg = VS[mode], t = performance.now() / 1000;
  const tgtGrey = cfg.look.grey ? 1 : 0, tgtDim = cfg.look.dim || 1, tgtPal = S.persona === 'anna' ? 1 : 0;
  const settling = Math.abs(palMix - tgtPal) > .001 || Math.abs(greyMix - tgtGrey) > .01 || Math.abs(orbDim - tgtDim) > .005 || V.L > .01 || ripples.some(r => t - r.t0 < r.dur);
  if (settling || Speech.active || Mic.on || S.ptt || mode === 'speaking' || mode === 'thinking' || mode === 'listening') return 'full';
  return mode === 'idle' || mode === 'mic-off' ? 'breath' : 'stop';
}
function frame(now) {
  raf = 0;
  if (!orbOnScreen() || reduced() || STILL) return;
  const t = now / 1000;
  const mode = V.mode, cfg = VS[mode];
  const tgtGrey = cfg.look.grey ? 1 : 0, tgtDim = cfg.look.dim || 1, tgtPal = S.persona === 'anna' ? 1 : 0;
  const lm = loopMode();
  if (lm === 'stop') { drawOrb(t, 0, mode); drawWave(cfg.wave, t, 0); return; }   // static states: one exact frame, then no frames
  if (lm === 'full' && now - lastT < 12.5) { schedule(); return; }
  const dt = Math.min(.1, (now - lastT) / 1000);
  lastT = now;
  const ease = 1 - Math.exp(-4.2 * dt);
  if (Math.abs(palMix - tgtPal) > .001) { palMix += (tgtPal - palMix) * ease; if (Math.abs(palMix - tgtPal) < .01) palMix = tgtPal; }
  if (Math.abs(greyMix - tgtGrey) > .01) { greyMix += (tgtGrey - greyMix) * ease * 1.4; if (Math.abs(greyMix - tgtGrey) < .02) greyMix = tgtGrey; }
  if (Math.abs(orbDim - tgtDim) > .005) { orbDim += (tgtDim - orbDim) * ease * 1.4; if (Math.abs(orbDim - tgtDim) < .01) orbDim = tgtDim; }
  let L = 0, bands = null;
  if (Speech.active) { L = Speech.level(t); if (!Speech.active) setActivity(null); }
  if (Mic.on) {
    const u = Mic.read(); bands = mode === 'listening' ? Mic.bands : bands;
    V.userL += (u - V.userL) * .5;
    if (S.activity !== 'thinking' && S.activity !== 'speaking') setActivity(u > .14 ? 'listening' : null);
    if (mode === 'listening') L = u * .55;
    const pl = pcLvl || (pcLvl = $('#pc-lvl')); if (pl) pl.style.transform = `scaleX(${u.toFixed(3)})`;
    if (EN.open) enLevel(u, Mic.db);
  } else V.userL *= .9;
  orbLook.pull = V.userL;
  V.L += (L - V.L) * (L > V.L ? .6 : .18);
  if (V.L - V.prevL > .035 && V.L > .24) ripples.push({ t0: t, dur: S.persona === 'anna' ? 1.25 : .9, a: .22 + .5 * V.L, inward: mode === 'listening' });
  if (mode === 'listening' && V.userL > .14 && (!ripples.length || t - ripples[ripples.length - 1].t0 > .35)) ripples.push({ t0: t, dur: 1.1, a: .3 + .4 * V.userL, inward: true });
  while (ripples.length > 6) ripples.shift();
  V.prevL = V.L;
  drawOrbFrame(t, V.L, mode);
  drawWave(cfg.wave, t, mode === 'listening' ? V.userL : V.L, bands);
  if (mode === 'listening') (userRingEl || (userRingEl = $('#user-ring'))).style.transform = `scale(${(1 + .05 * V.userL).toFixed(3)})`;
  schedule();
}
function schedule() {
  if (raf || STILL || reduced() || !orbOnScreen()) return;
  if (loopMode() === 'breath') {
    if (!breathT) breathT = setTimeout(() => { breathT = 0; if (!raf && orbOnScreen()) raf = requestAnimationFrame(frame); }, Math.max(0, 66 - (performance.now() - lastT)));
  } else { if (breathT) { clearTimeout(breathT); breathT = 0; } raf = requestAnimationFrame(frame); }
}
document.addEventListener('visibilitychange', schedule);
// the orb stops drawing while it is scrolled out of view (phones, short windows)
if ('IntersectionObserver' in window) new IntersectionObserver(es => { for (const e of es) orbVisible = e.isIntersecting; schedule(); }, { threshold: 0 }).observe(orbCv);
// one still frame per state (?still=1, reduced motion): the state stays readable from the word and the shape
const STILL_L = { speaking: .62, listening: .4 };
function drawStatic() {
  const mode = V.mode, cfg = VS[mode];
  palMix = S.persona === 'anna' ? 1 : 0; greyMix = cfg.look.grey ? 1 : 0; orbDim = cfg.look.dim || 1;
  orbLook.pull = mode === 'listening' ? .5 : 0;
  const L = STILL_L[mode] || 0;
  drawOrb(0, L, mode);
  drawWave(cfg.wave, 0, L, null);
}
function resizeCanvases() {
  measureCanvas(orbCv, 520); measureCanvas(waveCv, 216);
  if (STILL || reduced()) { paintLogos(); drawStatic(); } else { paintLogos(); drawOrb(performance.now() / 1000, V.L, V.mode); drawWave(VS[V.mode].wave, performance.now() / 1000, V.L); schedule(); }
}
