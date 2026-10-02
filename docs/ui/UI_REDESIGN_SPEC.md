> **Superseded (2 Oct 2026, C-135):** the owner chose the Neon Command Center design from their reference image. Build from `docs/ui/neon/spec_v2.md` and `docs/ui/neon/final_neon.html`. This file stays for history.

# JARVIS & ANNA UI redesign: implementation spec (stonix_ui, Cursor lane)

- **Status:** final design, 2026-10-02, revised after the design review (voice states, short windows, test hook, gates, Reconnect).
- **Owner request (Roman Urdu, translated):** "Change this UI fully. Its name is JARVIS and ANNA. Make it professional with new colours. When the voice speaks, the speaker should vibrate. Look at chat and everything deeply."

**Source of truth:** `final_mockup.html`. It is a single file: open it in Chrome and use the dashed **Prototype controls** pill at the bottom left (it is not product UI).

**Mockup URL parameters**

| Parameter | Effect |
|---|---|
| (none) | The real default after login: voice room joined, **mic off**; a spoken prompt plays after 0.7 s |
| `shot=1` | Hides the prototype pill and freezes clocks; the voice still runs |
| `state=mic-off\|idle\|listening\|thinking\|speaking\|muted\|joining\|agent-absent\|audio-blocked\|offline` | Sets the inputs that produce that voice state (section 5.2) |
| `persona=anna` | Starts as ANNA |
| `m=core\|chat\|ops\|trace` | Phone tab |
| `tab=chat\|logs\|notes\|tools\|tasks` | Chat panel tab |
| `open=settings\|confirm\|sim\|enroll\|enroll-error\|enroll-timeout\|bell\|agent\|laptop\|more\|lightbox\|login\|check\|offline` | Opens a panel, popover, dialog or screen |
| `electron=1` | Electron window frame |
| `rm=1` | Reduced motion |
| `banners=1` | All three voice banners (audio blocked, other tab, agent absent) |
| `empty=1` | Empty chat |
| `crash=1` | Panel error |
| `mapempty=1` | No map focus |
| `unknown=1` | Flow modules "unknown" |
| `scope=1` | Oscilloscope view |
| `laptop=0..6` | Laptop chip state (6 = "unknown", no desktop_status yet) |
| `vid=0..3` | Voice-ID meter state |
| `cam=0..4` | Camera link badge |
| `agent=offline` | Agent offline (also not in the voice room) |
| `media=loading\|error` | Intelligence image: "Loading media…" or "Media unavailable" |
| `syncfail=1` | The next persona switch gets no settings_sync and reverts after 5 s |
| `wakepending=1` | Wake word tile waiting for its setting to be confirmed |
| `demo=0` | No spoken prompt on load |

**Other files**
- `final_rationale.md`: palette, typography and motion, in short.
- `tasks.json`: X-69…X-102, small verifiable tasks.
- Evidence of this revision: `_fix/verify_results.json` (layout, states, loop, motion, perf in both headless modes, axe, Electron, persona, Reconnect), `_fix/shots/*.png`, scripts `_fix/verify.cjs` and `_fix/shoot.cjs`. Earlier evidence: `_judge/final_*`. Review evidence: `_critic/`.

## 0. Ground rules

1. **Lane.** Everything here is in Cursor's lane: `stonix_ui/**` and `assets/**`. Claude does not edit these files. Claude deploys the frontend later (after C-21/C-22), with `VITE_BRIDGE_BASE=/vortex`.
2. **Nothing is removed.**
   - Every panel, control, testid and text anchor that exists today keeps a home (section 9). They may move.
   - Terminate stays visible; it is disabled with "Agent update pending" when unsupported.
3. **No contract change.**
   - Events, commands, REST calls and LiveKit topics are unchanged.
   - `notification_ack` and `desktop_stop` are already contracted (X-67).
4. **Tests are a hard constraint.**
   - Keep every testid and text anchor (`testids.txt`).
   - Change a spec only in the same commit as the UI change that needs it. The three `/STONIX/` specs and `tests/mock-bridge/fixtures/init_state.json:6` change in X-70.
   - The suite runs at Playwright's default "Desktop Chrome" viewport, **1280×720**. The redesign keeps today's behaviour there: logs and reasoning side by side above 1180 px, and the intelligence dock open when it has items (it also opens on every new item). So `trace.spec` and `intelligence.spec` pass unchanged.
   - The mock bridge always answers `/livekit/token` with 503, so e2e runs start in "Voice unavailable". Voice states, motion and frame-rate checks use the **e2e-only test hook** (section 5.6), never real audio.
5. **Reconnect and the socket listeners (D1–D3) are now owned by X-96** (one socket subscription store; it lands before X-83, X-84 and X-88):
   - D1: Reconnect leaves hooks on the dead socket (verified: after Reconnect, `confirm_request` no longer opens the dialog).
   - D2: `socket.off('init_state')` strips every hook's listener.
   - D3: opening Settings sends `request_state` and wipes the trace.

   **Until X-96 lands, Reconnect reloads the page** (section 8), and it is a secondary button everywhere. A half-dead reconnect that silently stops confirmation dialogs must never ship.
6. **New npm packages.** Cursor installs them and logs them in `done_highlight.md`:
   - `@fontsource-variable/golos-text`
   - `@fontsource/b612`
   - `@fontsource/b612-mono`
   - `@fontsource-variable/mona-sans`
   - `@axe-core/playwright` (dev)

   Nothing else: no animation library, no three.js.
7. **Gates are local.** Visual baselines and frame-rate gates run in their own Playwright project (`gates`, new headless Chromium), outside `npm run test:e2e`, so CI stays green (section 12). If the owner wants them in CI, Cursor files a `REQ-CI` for Claude (`.github/**` is Claude's lane).

---

## 1. Design tokens

### 1.1 One CSS-variable source: `src/assets/styles/tokens.css`

Persona accents are registered colours, so they can be interpolated (cross-fade) and `color-mix()` derivatives follow them every frame.

```css
@property --acc     { syntax: '<color>'; inherits: true; initial-value: #8ED8FF; }
@property --acc-2   { syntax: '<color>'; inherits: true; initial-value: #6F7BFF; }
@property --acc-ink { syntax: '<color>'; inherits: true; initial-value: #04111C; }

:root {
  color-scheme: dark;
  /* neutral night (OKLab chroma ~0.015: neither persona is lit inside a blue room) */
  --bg: #05060B;  --bay: #0B0D14;  --raise: #121520;  --raise-2: #191D2A;  --well: #07080E;
  --line: #1D2130; --line-2: #2C3243;
  --t1: #EDF0F7;  --t2: #B3BACB;   --t3: #9098AD;     --t4: #5F6679;      /* t4: non-text only */
  /* status: persona-independent, always shape + word */
  --ok: #3FD69A;  --warn: #F2B544; --danger: #F5603A;
  --data: #8F9BB3;                 /* low-chroma slate: meters, busy, spinners */
  --user: #E6ECF8;                 /* the owner's voice */
  --jarvis: #8ED8FF; --jarvis-2: #6F7BFF; --anna: #D6A6FF; --anna-2: #F45FD0;
  /* persona accent (JARVIS default) */
  --acc: #8ED8FF; --acc-2: #6F7BFF; --acc-ink: #04111C;
  --acc-soft:   color-mix(in oklab, var(--acc) 12%, transparent);
  --acc-soft-2: color-mix(in oklab, var(--acc) 22%, transparent);
  --acc-line:   color-mix(in oklab, var(--acc) 45%, transparent);
  --acc-grad:   linear-gradient(135deg, var(--acc), var(--acc-2));
  /* type */
  --f-ui: "Golos Text Variable", "Segoe UI", system-ui, sans-serif;
  --f-inst: "B612", "Segoe UI", system-ui, sans-serif;
  --f-mono: "B612 Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
  --f-brand: "Mona Sans Variable", "Golos Text Variable", "Segoe UI", sans-serif;
  /* shape + motion */
  --r-bay: 12px; --r-ctl: 8px; --r-dlg: 16px;
  --ease: cubic-bezier(.22,1,.36,1);      /* UI out */
  --ease-std: cubic-bezier(.2,.7,.2,1);   /* standard */
  transition: --acc .7s var(--ease), --acc-2 .7s var(--ease), --acc-ink .7s var(--ease);
}
:root[data-persona="anna"] { --acc: #D6A6FF; --acc-2: #F45FD0; --acc-ink: #1A0526; }
:root.vt-run, :root.no-acc-tr { transition: none !important; }   /* View Transition and instant switches */
```

- **Where `data-persona` lives:** on `<html>`. It is set from `useSettings().persona`, which is the server-synced value, never local-only.
- **The `:root` transition is a fallback with a cost.** While it runs, every element that uses an accent restyles on each frame for 700 ms. Chrome and Electron 35 take the View Transition path instead (section 4), which adds `:root.vt-run` and turns the transition off; the cross-fade only runs where View Transitions are missing.
- **Removals:** the three drifting token sources go away.
  - `index.css:7-18 --command-*` maps to the new variables.
  - The hex values in `shared/theme/commandTheme.ts` become `var()` references.
  - `personaAccent()` returns token class names only.

### 1.2 Tailwind 3.4 mapping (`tailwind.config.js`)

Tailwind's `<alpha-value>` needs channel triplets, and those cannot be interpolated. So:
- colours are plain `var()` values;
- alpha shades are named tokens (`acc-soft`, `acc-line`…) or arbitrary values such as `bg-[color-mix(in_oklab,var(--ok)_13%,var(--bay))]`.

Never use a `/NN` opacity modifier on these colours: Tailwind 3 drops the class silently.

**Before** switching `command.*` to variables, grep for `command-[a-z]+/[0-9]` and replace each hit. Today's `text-command-muted/70` timestamps fail AA anyway; they become `text-ink-3`.

```js
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: { extend: {
    colors: {
      night: 'var(--bg)', console: 'var(--bay)', raise: 'var(--raise)', 'raise-2': 'var(--raise-2)', well: 'var(--well)',
      line: 'var(--line)', 'line-2': 'var(--line-2)',
      ink: { 1: 'var(--t1)', 2: 'var(--t2)', 3: 'var(--t3)', 4: 'var(--t4)' },
      acc: { DEFAULT: 'var(--acc)', 2: 'var(--acc-2)', ink: 'var(--acc-ink)', soft: 'var(--acc-soft)', 'soft-2': 'var(--acc-soft-2)', line: 'var(--acc-line)' },
      ok: 'var(--ok)', warn: 'var(--warn)', danger: 'var(--danger)', data: 'var(--data)', user: 'var(--user)',
      jarvis: { DEFAULT: 'var(--jarvis)', 2: 'var(--jarvis-2)' }, anna: { DEFAULT: 'var(--anna)', 2: 'var(--anna-2)' },
      // migration aliases (keep until every usage is moved; no /NN modifiers on them)
      command: { bg: 'var(--bg)', panel: 'var(--bay)', soft: 'var(--raise)', border: 'var(--line)', text: 'var(--t1)',
                 muted: 'var(--t3)', accent: 'var(--acc)', blue: 'var(--acc)', danger: 'var(--danger)', warning: 'var(--warn)' },
    },
    fontFamily: { ui: ['"Golos Text Variable"', '"Segoe UI"', 'system-ui', 'sans-serif'], inst: ['B612', '"Segoe UI"', 'sans-serif'],
                  mono: ['"B612 Mono"', '"Cascadia Mono"', 'Consolas', 'monospace'], brand: ['"Mona Sans Variable"', '"Golos Text Variable"', 'sans-serif'] },
    fontSize: { 12: ['12px', '1.4'], 12.5: ['12.5px', '1.45'], 13: ['13px', '1.45'], 13.5: ['13.5px', '1.45'], 14.5: ['14.5px', '1.58'], 16: ['16px', '1.4'], 27: ['27px', '1.25'] },
    borderRadius: { bay: '12px', ctl: '8px', dlg: '16px' },
    boxShadow: { pop: '0 18px 50px rgba(0,0,0,.6)', dlg: '0 30px 80px rgba(0,0,0,.6)', drawer: '-24px 0 60px rgba(0,0,0,.5)', toast: '0 16px 40px rgba(0,0,0,.55)' },
    transitionTimingFunction: { out: 'cubic-bezier(.22,1,.36,1)', std: 'cubic-bezier(.2,.7,.2,1)' },
    zIndex: { topbar: '30', rail: '60', menu: '72', scrim: '80', drawer: '90', dialog: '95', lightbox: '110', toast: '120', screen: '200', window: '500' },
  } },
  plugins: [],
};
```

Delete the dead config: the `orbitron`, `outfit` and `rajdhani` families; the `hardware`, `neon-cyan` and `neon-amber` shadows; and the `pulse-slow`, `spin-slow` and `scan` animations. None of them is used.

### 1.3 Contrast (WCAG 2.x, computed)

| Text token | Night | Console | Raised | Raised 2 |
|---|---|---|---|---|
| t1 `#EDF0F7` | 17.8 | 17.0 | 16.0 | 14.7 |
| t2 `#B3BACB` | 10.4 | 10.0 | 9.4 | 8.6 |
| t3 `#9098AD` | 7.0 | 6.7 | 6.3 | 5.8 |
| ok / warn / danger | 10.9 / 11.1 / 6.4 | 10.4 / 10.6 / 6.1 | 9.8 / 9.9 / 5.7 | 9.0 / 9.2 / 5.3 |
| JARVIS ice / ANNA lilac | 12.9 / 10.4 | 12.4 / 9.9 | 11.6 / 9.3 | 10.7 / 8.6 |

**Ink on accent:**
- `#04111C` on ice: 12.2.
- `#04111C` on indigo: 5.4.
- `#1A0526` on lilac: 9.8.
- `#1A0526` on magenta: 6.8.
- `#170606` on vermilion: 6.2.
- `#1C1300` on gold: 10.0.

**Rules:**
- t3 on `--acc-soft-2` is only about 3.8:1. Use t1 or t2 there.
- Indigo and magenta are never small text.

### 1.4 Status shape: `<StatusShape s=… live? />` (new `src/shared/ui/StatusShape.tsx`, replaces `StatusDot`)

| `s` | Shape and colour | Meaning |
|---|---|---|
| `ok` | ● green | online, healthy, applied, delivered |
| `warn` | ▲ gold | stale, paused, caution, medium risk, audio blocked, agent not in the room |
| `err` | ◆ vermilion | offline, error, panic, high risk |
| `off` | ○ hollow t3 | off, not started, mic off, not listening |
| `unknown` | ○ dashed t3 | no data ("unknown" in italics next to it) |
| `busy` | ● slate | running, read-only, info, joining |
| `acc` | ● persona accent | voice active (speaking, thinking) |
| `user` | ● white frost | you are speaking |
| `rec` | ● vermilion | camera frames are being shared (privacy) |

**Rules:**
- Size: 8 px (the triangle is 10×9 and the diamond is a 7 px square rotated 45°).
- **Always** put a word next to it (visible, or visually hidden on the phone chip row).
- `live` adds a 1.8 s ping ring. Only the header Voice chip uses it, while speaking or listening, and never under reduced motion.

---

## 2. Typography

- **Self-hosted** (CSP C-109, Electron offline). Remove the Google `@import` at `index.css:1`.

```ts
// src/main.tsx
import '@fontsource-variable/golos-text';          // "Golos Text Variable", wght 400–900
import '@fontsource/b612/400.css'; import '@fontsource/b612/700.css';
import '@fontsource/b612-mono/400.css'; import '@fontsource/b612-mono/700.css';
import '@fontsource-variable/mona-sans/wdth.css';  // "Mona Sans Variable", wdth 75–125 (brand only)
```

| Role | Family | Settings |
|---|---|---|
| UI, chat, labels | Golos Text | 400 / 500 / 600 / 650 / 700. Chat body 14.5/1.58 |
| Readouts, numbers, clock, annunciator values, dB | B612 | `font-variant-numeric: tabular-nums` |
| Machine text (logs, tool names, args) | B612 Mono | 12–13 px |
| Wordmark, persona name under the speaker, chat author names | Mona Sans | `font-variation-settings: "wdth" 125`, weight 640–650. Wordmark 14.5 px, letter-spacing .07em. Persona name 27/34, .1em. Author 12.5 px, .06em |

- **Scale:** 12 · 12.5 · 13 · 13.5 · 14.5 · 16 · 27. **Nothing below 12 px** (today there are 131 uses of 9–11 px). Badges and counts are 12 px too.
- **Case:** sentence case. Uppercase only for the persona names (proper nouns), the instrument legend under the speaker (OUT / IN / MIC OFF / NOT LISTENING / BLOCKED / NO SIGNAL), log levels and WARN+ / INFO.
- **No** tracked-out eyebrow labels and **no** middle-dot meta strings (use commas). Use `text-wrap: pretty` on alert titles, chat text, notes and hints.

---

## 3. Spacing, radii, shadows, layers

- **Grid:**
  - console gutter 12 px;
  - gap between bays 8 px;
  - bay padding 14 px;
  - section rhythm 13/9 px (module head);
  - controls 34 px high (30 small, 28 xs);
  - phone gutters 16 px.
- **Radii:**
  - bay 12;
  - control 8;
  - dialog 16 (18 as a phone sheet);
  - pills 999;
  - status tiles 8;
  - wordmark capsule 999.
- **Surfaces:**
  - Bays are **opaque** (`--bay` + 1 px `--line`). Glass and blur are **only** for overlays: the scrim `blur(4px)` and the login card `blur(14px)`.
  - No shadow on bays. Shadows are for popovers, dialogs, the drawer and toasts (tokens in 1.2).
- **Z layers:** topbar 30 · rail drawer 60 · menus 72 · scrim 80 · drawer 90 · dialogs 95 · lightbox 110 · toasts 120 · full screens 200 · Electron window buttons 500.

---

## 4. Motion

Animated properties are `transform` and `opacity` only. Nothing animates `width`, `stroke-dashoffset` or colours per frame (the `@property` fallback below is the one documented exception).

| What | Duration and easing | Notes |
|---|---|---|
| Persona switch (preferred) | 760 ms, `cubic-bezier(.2,.7,.1,1)` | `document.startViewTransition(() => flushSync(() => setPersonaLocal(p)))`, then animate `clip-path: circle(0 → r at orbCenter)` on `::view-transition-new(root)`. Add `:root.vt-run` during the transition. CSS: `::view-transition-old(root), ::view-transition-new(root) { animation: none; mix-blend-mode: normal; }` |
| Persona accents (fallback only) | 700 ms, `--ease` | `@property` cross-fade of `--acc`, `--acc-2` and `--acc-ink`. Restyles every accent user each frame for 700 ms, so it runs only where View Transitions are missing |
| Persona name roll | 600 ms transform / 450 ms opacity | JARVIS slides down and away, ANNA slides in |
| Wordmark capsule indicator | 550 ms, `--ease`, `transform` only | Both names share one width (the wider name's, measured on events: fonts loaded, resize, breakpoint), so the indicator only translates |
| Meter fills (machines, Voice-ID, confidence, jobs, enrollment) | 600 ms `--ease-std` on `transform: scaleX()` | `transform-origin: left`; the value is a `--v` 0..1 custom property. Never animate `width` |
| Confirm countdown ring | none | `stroke-dashoffset` is set once a second without a transition (a 16 px repaint) |
| Room light | opacity, at most 30 writes a second | Written only when it changes by more than .01 (section 5.3) |
| Drawer | 320 ms, `--ease` | `translateX`; keep `content-visibility: hidden` until the close finishes (transition-behavior: allow-discrete) |
| Dialogs | 200 ms opacity / 250 ms transform | Rise 10 px and scale .985 → 1 |
| Popovers | 150 ms opacity / 200 ms transform | Drop 4 px |
| Toasts | 300 ms in / 200 ms out | 8 px rise |
| Hold to allow | 1000 ms linear fill (`scaleX`) | Pointer or Space |
| Voice state cross-fade (canvas) | `1 - e^(-6·dt)` | No snapping between states |
| Canvas persona colour | `1 - e^(-4.2·dt)` | Snapped when a View Transition runs |

- **framer-motion** is used only for drawers, dialogs, toasts and message entrances.
  - Wrap the app in `<MotionConfig reducedMotion="user">`.
  - **Never** use it for the orb: today's 42 s and 34 s ring rotations and pulse loops are removed.
- **Reduced motion** (`prefers-reduced-motion: reduce`):
  - no rAF loop; the speaker is drawn once per state, persona or size change;
  - the persona switch is instant (no View Transition);
  - CSS animations are cut to `.001ms`;
  - the room light uses a static opacity per state;
  - the state stays readable through the word, the shape and the readout.

---

## 5. Voice presence (the "vibrating speaker")

### 5.1 Architecture: no React re-render storm

```
LiveKit Room ──► AudioReactiveEngine (plain TS singleton, src/features/voice/audioEngine.ts)
   agent RemoteAudioTrack ─ createAudioAnalyser(track, {fftSize: 1024, smoothingTimeConstant: 0.6, minDecibels: -90, maxDecibels: -22})
   local mic LocalAudioTrack ─ createAudioAnalyser(track, {...same, cloneTrack: true})   (only while the mic publishes)
   per frame:  getFloatTimeDomainData → RMS → dBFS = 20·log10(rms) → level 0..1 = clamp((dBFS + 56) / 40)
               getByteFrequencyData → 24 log bands 80 Hz–5 kHz;  128-sample waveform;  onset flag
   smoothing:  30 ms attack, 120 ms release (k = 1 - e^(-dt/τ));  a timestamped ring of band snapshots (read by age in ms)
   e2e builds only: window.__jarvisTest can replace both inputs (5.6)
          │  (no setState)
          ▼
SpeakerCanvas (plain TS class)  ◄── one requestAnimationFrame loop owns: speaker canvas, equalizer/scope canvas,
                                    room-light opacity (≤ 30 Hz), readout text (8 Hz)
          ▲
SpeakerPresence.tsx (React)  ── props: voiceState, persona, reducedMotion  (the only React state)
```

- **livekit-client 2.22 facts (verified in `node_modules/livekit-client`):**
  - `createAudioAnalyser()` creates a **new `AudioContext` on every call** (there is no shared helper context) and returns `{ analyser, calculateVolume, cleanup }`. Keep one analyser per bound track. Call `cleanup()` on **every unbind** (track unsubscribed, reconnect, mic toggled off, unmount); it closes that context and stops the cloned track. Without it, every reconnect and mic toggle leaks a context.
  - Call `resume()` on each live `analyser.context` from the **Enable audio** click (together with `room.startAudio()`) and from every Mic toggle gesture. livekit also retries `resume()` on the next body click.
  - `calculateVolume()` is the RMS of the **byte frequency bins** (0..1), not dBFS. Do not use it for the level; compute dBFS from the time-domain RMS as above (the mockup's `Mic.read()` is the reference).
- **Delete the 30 fps `setAgentLevel` / `setVizFrame`** in `useAudioLevels.ts:104-108`. `useCommandCenter` stops passing `agentLevel`, `vizFrame` and `spectralDataRef` through the tree.
- **React keeps only coarse flags:** `agentState` (`lk.agent.state`), `isSpeaking` and `isUserSpeaking`.
  - `isSpeaking`: `lk.agent.state === 'speaking'`, or agent RMS above the threshold for at least 120 ms.
  - `isUserSpeaking` starts **locally**: mic dBFS above −45 for 100 ms, released after 400 ms below −50. `ActiveSpeakersChanged` including the local identity confirms it, and it alone enters listening when the mic analyser is unavailable; on its own it arrives hundreds of milliseconds late.
- Keep the hidden `<audio>` attachment (the Chrome remote-audio quirk).
- **Loop policy** (replaces a loop that redrew at 60 fps whenever the orb was visible):

  | Mode | When | Rate |
  |---|---|---|
  | full | speaking, listening, thinking; or while the state cross-fade, the persona colour or a ripple is still settling | every animation frame, but a frame is skipped when less than 12.5 ms passed since the last draw: 60 fps on 60 and 120 Hz screens, 72 on 144 Hz, and 60 Hz vsync jitter never skips |
  | breath | idle and mic off (only the core breathes) | about 15 fps (a 66 ms timer, then rAF) |
  | stop | muted, offline, joining, agent absent, audio blocked once settled; `document.hidden`; orb off-screen (`IntersectionObserver`); phone tab other than Core; reduced motion | no frames; the last frame stays on the canvas |

  Any state, persona, size or view change restarts the loop; it stops again by itself.
- **Sizes:** only from `ResizeObserver`. **Never read layout inside the loop.**

### 5.2 Visual state machine (first match wins)

The derivation reads only the inputs below; nothing sets a state directly (the e2e hook overrides the inputs, section 5.6).

| # | State | Source | `voice-status` | Header chip | Readout | Visual |
|---|---|---|---|---|---|---|
| 1 | offline | `voiceUnavailable` or room `error` | **Voice unavailable** (◆) | ◆ Offline | NO SIGNAL (gold) | Grey, amber notch at 12 o'clock, alert glyph, no motion. The voice error shows in the helper slot |
| 2 | joining | room `idle` (not left on purpose), `connecting`, `reconnecting` | Joining voice… (● slate) | ● Joining | OUT — | Dim (grey .6), no motion. Mic button "Joining…", disabled |
| 3 | agent absent | connected, `agentMissing` (no agent participant after 15 s) | Agent not in room (▲) | ▲ No agent | OUT — | Dim, user-off glyph. The `voice-wake-agent` banner (Wake) shows |
| 4 | audio blocked | connected, `playbackBlocked` (`!room.canPlaybackAudio`) | Audio blocked (▲) | ▲ Blocked | BLOCKED (gold) | Dim, speaker-off glyph. The `enable-audio` banner shows. Wins over speaking: nothing would be heard |
| 5 | speaking | `isSpeaking` (5.1). A typed reply spoken aloud counts, mic on or off | Speaking (● accent) | ● Speaking, ping | OUT −NN dB | Everything driven by the agent bands (5.3) |
| 6 | thinking | `lk.agent.state === 'thinking'` | Thinking (● accent) | ● Thinking | OUT — | A comet orbits the bezel, a faint arc counter-rotates inside, the aurora swirls slowly |
| 7 | muted | agent listening muted (`mute` flag) | Not listening (○) | ▲ Muted | NOT LISTENING | Grey, dashed bezel ring, ear-off glyph, no motion. Speaking and thinking still win: the agent can answer typed messages aloud |
| 8 | listening | `isUserSpeaking` and the mic is on | Listening to you (● white) | ● Listening, ping | IN −NN dB | White ripples travel inward, ticks follow your level, rings pulled in up to 7 %. "You are speaking" swaps into the voice-name slot |
| 9 | mic off | connected, mic not publishing. **The default after login** | Mic off (○) | ○ Mic off | MIC OFF | Like idle: only the core breathes |
| 10 | idle | otherwise | Ready (● green) | ● Ready | OUT — | **Only the core breathes** (5.2 s, ±1.2 %). Rings, ticks, halo and aurora are frozen |

- **Mic controls.** `voice-toggle` (deck), `topbar-mic-toggle`, the composer mic and the More menu's mic item are **disabled while offline or joining** (`voice.spec.ts:25` expects `voice-toggle` disabled while voice is unavailable). The deck label reads "Joining…" while joining, otherwise "Mic on" / "Mic off".
- **Mic off is not a warning.** Its icons are t3, not gold; gold stays for "Not listening" (the owner's explicit mute).
- **Banners and states share their inputs.** `enable-audio` ↔ audio blocked, `voice-wake-agent` ↔ agent absent, `voice-take-over` ↔ voice active in another tab (a room error, so offline), `agent-session-restart-banner` while the agent restarts.
- **`status_change` no longer drives the speaker.** The old "(no LiveKit) `status_change.speaking`" clause could never match: offline wins first, and in e2e the token is always 503. When LiveKit is unavailable nothing plays in this client, so the honest state is "Voice unavailable". `status_change` keeps its other uses unchanged.
- **Readout format:** a real minus sign (U+2212) and integer dB from `level·40 − 56`. Update the text at most 8 times a second. When the bezel radius R is under 80 px the legend shortens: no OUT/IN prefix, "EARS OFF" for "NOT LISTENING".
- **Announcements** follow section 11.7 (only meaningful changes, debounced).

### 5.3 Rendering (one canvas, back to front)

**Canvas geometry**
- The canvas is **136 %** of the `.orb-wrap` square (offset −18 %), so ripples can leave the bezel.
- `R = min(W,H)/2 · 0.735` is the bezel radius, which equals the wrap radius.
- DPR is `min(devicePixelRatio, 2)`.
- `small = R < 92` (phones).
- **Readout gap.** The bottom gap of the bezel is `clamp(asin((hw + 6) / R), .24, .9)` rad on each side, where `hw` is half the width of the widest legend of the current family, measured with canvas `measureText` (B612 12 px plus letter-spacing; no layout read). The conversational family (OUT −88 dB, IN −88 dB, MIC OFF, BLOCKED, NO SIGNAL) keeps one gap on every turn; only "NOT LISTENING" (muted, grey and still) opens it wider. Recompute on resize and when the family changes, never per frame.

**Layers, back to front**

1. **Room light (DOM, not canvas).**
   - A `div` 320 % of the wrap, centred behind it, inside an `isolation: isolate` wrapper, with `will-change: opacity`.
   - Background: `radial-gradient(closest-side, acc 34% → acc 20% @18% → acc-2 11% @40% → acc-2 4% @60% → transparent 74%)`.
   - Opacity: `(.24 + .62·L·E.speaking + .30·U·E.listening + .10·E.thinking) · (1 − .7·E.offline) · (1 − .45·E.muted) · (1 − .5·E.dim)`.
   - Written **at most 30 times a second**, and only when it changes by more than .01. It never drifts.
   - Static values under reduced motion: offline .08, muted .14, dim .12, speaking .62, listening .40, otherwise .26.
2. **Bezel: 120 ticks**, starting at 12 o'clock, skipping the readout gap.
   - Band mapping is mirrored: low frequencies at the top, high at the bottom.
   - Tick value `v`:
     - speaking: `E.speaking · band(bands, m) · clamp(.3 + 1.5L)`;
     - listening: `+ E.listening · U · (.6 + .4·sin(3a − 5t))`;
     - thinking: comet proximity `(1 − d/1.1)²`.
   - Tick geometry: starts at `r0 = R + 7s`, length `(major ? 7 : 4)·s + v·12s` with `s = R/120`. A major tick every 10.
   - **Batched:** bucket the ticks into 6 alpha levels × {minor, major} = at most 12 `stroke()` calls per frame, with `lineCap: 'butt'`.
   - Colours: unlit ticks `#2E3445` (α .72 minor, .95 major); lit ticks `acc` at α `.3 + .7·level`, or white frost mixed in while listening.
3. **Bezel ring.**
   - An arc around the gap, 1 px, `acc` α `.32 + .25·E.speaking`.
   - Dashed `[3,5]` when muted.
   - Offline: an amber arc at 12 o'clock (±.17 rad, 3 px).
4. **Five cone rings** at radii `R·(.47, .57, .67, .77, .87)`.
   - Ring `i` reads the newest band snapshot taken at or before `t − i·lag` (JARVIS 50 ms, ANNA 66 ms). The lag is in milliseconds, so the outward cone has the same timing at 60, 120 and 144 Hz. Energy travels outward like a cone pushing air.
   - Amplitude: `R·(.045 + .018i)·E.speaking·clamp(1.6L + .05)·ringAmp`.
   - Radius at angle θ: `base + pull + amp·(band(hist_i, m(θ + rot_i)) − .22)`.
   - `rot_i` advances **only while speaking**.
   - Points: 120, or 72 on small canvases.
   - Colour: `mix(acc, acc2, i/5)`, α `(.82 − .13i)·lift`.
   - When not speaking, each ring is a plain `arc`. While listening the rings are pulled in by `−R·.07·U·(1 − i/5)`.
5. **Thinking comet.**
   - Head at `2.4t − π/2` on `R·.985`.
   - 16 trail segments batched into 4 alpha groups (2.6 → 1.4 px).
   - A counter-rotating arc on `R·.62` at `−1.6t`.
6. **Ripples.**
   - Outward: on a syllable onset (`L − prevL > .035` and `L > .24`, minimum gap `rippleGap`), radius `.9R → 1.31R`, ease-out cubic, α `.22 + .5L`.
   - Inward, white: while listening, every ≥ .34 s if `U > .14`, radius `1.28R → .5R`.
   - A ripple whose radius `rr` is over `R − 14` is drawn as an arc that skips the readout: from `π/2 + asin((hw + 6)/rr)` to `π/2 − asin((hw + 6)/rr) + 2π`.
   - At most 6 at once (4 on small canvases).
7. **Glass-aurora core.**
   - Radius `Rc = .35R·(1 + .09L + breath − .05U)`, where `breath = .012·sin(2πt/5.2)` in idle and mic off (half of that in thinking).
   - **Body cache** (one offscreen canvas per persona colour, size and DPR). Rebuilt only when its key changes, i.e. during the persona fade or the grey transition. Contents:
     - a tinted dark radial gradient (`acc2` mixed into near-black);
     - glass edge darkening;
     - an `acc2` under-glow;
     - an elliptical specular highlight;
     - a small `hi` spot.
   - **Aurora ribbons** at **half resolution**, in an offscreen canvas. Refreshed at 30 fps when small, otherwise only when the swirl or level changes. Contents:
     - two depth "clouds" drawn as pre-rendered radial sprites (no gradient creation per frame);
     - three bezier ribbons, each 3 widths plus a bright edge, stroked with **cached** linear gradients;
     - an inner glow sprite;
     - a cached radial mask applied with `destination-in`, so the light fades toward the rim.
   - Swirl speed: idle 0, listening .25, thinking .6, speaking `.5 + 1.6L`, times the persona `swirl`.
   - **Membrane rim:**
     - 120 points (80 when small);
     - `r(θ) = Rc + Σ_q w_q·g_q·sin(k_q·θ ± φ_q)·amp + .3·amp·sin(2θ + φ_slow)`;
     - `amp = Rc·.055·L` (speaking only);
     - `g` = 4 band groups (bands 0–5, 6–11, 12–17, 18–23);
     - `φ_q += dt·vel_q·(.6 + L)`.
   - Draw sequence:
     1. clip to the membrane path;
     2. `drawImage(body)`;
     3. `drawImage(ribbons)` with `'screen'`;
     4. restore;
     5. stroke the rim with a linear gradient (hi .78 → acc .22 → acc2 .70, 1.3 px);
     6. add a glow stroke (5 px, α `.05 + .26L + .1U`, skipped below .06).
   - **Vibration:** when `L > .3`, translate the whole core by a random sub-pixel jitter `(rand − .5)·(L − .3)·R·.03·jitter` on both axes.
8. **Glyph overlays (DOM):** ear-off when muted; alert (gold) when offline; user-off when the agent is not in the room; speaker-off (gold) when audio is blocked.

**The dim look** (joining, agent absent, audio blocked): the canvas mixes to grey .6 (offline 1, muted .72), the bezel ring stays solid, the equalizer is grey and still, and nothing moves.

**Persona motion character** (engine parameters, not colours only)

| Parameter | JARVIS (crisper) | ANNA (wider, slower) |
|---|---|---|
| Membrane modes k | 3, 5, 7, 11 | 2, 3, 5, 7 |
| Mode weights | .9, .8, .62, .5 | 1, .85, .55, .38 |
| Phase velocity (rad/s) | 3.4, 5.6, 7.8, 10.2 | 2.0, 3.1, 4.3, 5.6 |
| Slow wobble velocity | 1.5 | .9 |
| Swirl factor | 1.0 | .68 |
| Ribbon width | 1.0 | 1.45 |
| Band blur (neighbour mix) | 0 | .55 |
| Ripple duration / minimum gap | .9 s / .13 s | 1.25 s / .20 s |
| Jitter | 1.0 | .6 |
| Ring amplitude / ring lag | 1.0 / 50 ms | 1.15 / 66 ms |
| Speech colour (canvas) | ice `#8ED8FF`, indigo `#6F7BFF`, hi `#E4F6FF` | lilac `#D6A6FF`, magenta `#F45FD0`, hi `#FFE8FA` |

### 5.4 Under the speaker

Every row under the speaker has a **fixed height**, so no voice state moves the speaker or the deck (verified: the wrap, the helper slot and the deck keep identical rects across all ten states at 1440×900, 1366×768, 1200×800 and 390×844; only the centred identity text changes width).

- **Readout:** in the bezel gap. B612 12 px, letter-spacing .06em (.02em when R < 80), t2; gold when offline or audio blocked. `aria-hidden`; the spoken state is in `voice-status`.
- **Identity (two fixed rows):**
  - the persona name (Mona Sans 27/34, with the roll);
  - the state row: `StatusShape` + **`voice-status`** word + a vertical rule + one slot that holds **either** "Voice Charon"/"Voice Aoede" **or** `user-speaking-label` "● You are speaking". Listening swaps them (visibility and opacity); both stay mounted, so the row never grows. `user-speaking-label` is `aria-hidden` while not listening.
  - `user-speaking-indicator` is a static 1.5 px white ring 12 px outside the wrap at opacity .42 while listening. It has the **same bottom gap as the bezel** (a conic-gradient mask; `--ring-gap = asin((hw + 8)/(R + 12))`, set on resize), so it never crosses the readout. The canvas does the reactive part.
- **Visualizer row** (30 px), inside the **`agent-audio-visualizer`** container:
  - **Equalizer:** 33 mirrored bars, low bands in the middle.
    - Speaking: `band(blur, m)·clamp(1.5L + .1)`.
    - Listening: the user bands, white.
    - Thinking: a cosine highlight sweep.
    - Idle and mic off: .06.
    - Muted, offline and the dim look: grey and static.
    - Batched into 4 alpha groups (`roundRect` in one path per group).
    - Size 236×30 CSS px; below 372 px of presence width it shrinks so the view toggle stays beside it. Redrawn at 30 fps when small.
  - **Oscilloscope** (optional view): 128-sample waveform, from the mic analyser time domain or synthesised from the level; windowed by `sin(πx)`; a 3.5 px glow stroke plus a 1.3 px stroke; centre line and 8 ticks. Dashed flat line when muted, offline or dim.
  - **View toggle:** two 26×24 icon buttons, "Equalizer bars" and "Oscilloscope", with `aria-pressed`. Remember the choice in `localStorage` (try/catch).
- **Helper slot** (one fixed line, two when the presence content box is at least 432 px tall): the helper copy (per state and persona gender, section 10) and the **voice error** (◆ + text). With two lines the error takes the second line; with one line, or when the presence is under 340 px wide, the error replaces the copy (wrapping onto both lines when there are two). Long text ends in an ellipsis; the full text is in the slot's `title`.
- **Deck (desktop):**
  - Mic (`voice-toggle`: "Mic on" / "Mic off" / "Joining…"; min-width so on/off never shifts the row);
  - Listening (`assistant-agent-listening`, `data-muted`);
  - the Voice-ID meter (`voice-id-meter`, `voice-id-meter-label`, `voice-id-enroll-link` when not enrolled). States: pass (ok fill), fail (danger fill), monitor (slate fill), none ("Not enrolled" + Enroll). Below 420 px of presence width it is compact: shield + value (the label and bar are hidden; the label text stays in the DOM; Enroll stays visible).
- **Terminate** (`terminate-session`): a **ghost** button (t2 text, `--line-2` border); it turns danger-coloured only on hover or keyboard focus.
  - desktop: top-right of the presence. When the deck wraps into two rows (presence content under 274 px wide), it joins the deck's second row instead of sitting over the speaker;
  - phone: the far end of the Voice-ID row, with its label, never next to Stop (7.3);
  - disabled with the title "Agent update pending: …" when `shutdown_agent` is rejected.
- **Voice banners** (`enable-audio`, `voice-take-over`, `voice-wake-agent`) and `agent-session-restart-banner` **overlay** the stage below the Terminate row (absolute, 52 px from the top, z above the canvas). They never push the layout (D8) and never cover Terminate.
- **Height budget (container queries on the presence; content-box values from the mockup):**
  - wrap = `clamp(180px, 100cqh − rows, 252px)`, where `rows` is the sum of the fixed rows: 190 px (one-line helper), 208 px (two-line helper), 166 px (no helper), plus 40 px when the deck wraps;
  - below 370 px of content height the helper slot hides (before the speaker goes under 180 px);
  - only below 332 px does the floor drop to 132 px (and the view toggle hides);
  - measured: wrap 243 at 1440×900, 228 at 1366×768, 202 at 1200×800 (intelligence dock open), 190 at 1536×730, 180 at 1280×720, 252 at 1920×1080, with the helper visible at every one of these.

### 5.5 Performance budget (acceptance)

Gates run in the `gates` Playwright project with `channel: 'chromium'` (**new headless**: the real Chrome build, canvas rasterised off the main thread, closest to Chrome and Electron). Playwright 1.60's default `chromium.launch()` is **chromium-headless-shell**, which rasterises the canvas on the main thread and swings widely under CPU throttling; do not gate on it. Gate values are medians of three runs. The mockup ranges below cover four runs on this Windows laptop (two in `_fix/verify_results.json`, two earlier).

| Profile | Gate (new headless) | Mockup, new headless | Mockup, headless shell (default) |
|---|---|---|---|
| Desktop 1440×900 DPR 1, speaking | ≥ 58 fps, ≤ 1.5 ms script per frame | 59.9–60.2 fps, 0.65–1.05 ms | 60.0–60.2 fps, 0.96–1.46 ms |
| Desktop DPR 2, speaking | ≥ 58 fps, ≤ 1.5 ms script per frame | 56.3–61.1 fps (median 60), 0.61–0.75 ms | 59.9–60.3 fps, 1.18–1.65 ms |
| Desktop DPR 2, 4× CPU throttle, speaking | ≥ 50 fps | 54.5–60.3 fps, 3.7–7.5 ms | 19.6–46.9 fps (the main thread is saturated; large run-to-run variance) |
| Phone 390×844, DPR 3, 4× CPU, speaking | ≥ 50 fps | 58.2–60.2 fps, 4.2–7.1 ms | 19.2–52.9 fps |
| Idle and mic off | ≤ 16 draws/s, no style recalc | 10–13 draws/s, 0.33–0.43 ms per draw (4–6 ms script per second), 0 recalcs | 14–15 draws/s, 0.25–0.34 ms per draw |
| Muted, offline, joining, agent absent, audio blocked | 0 draws | 0 draws, about 0.2 ms script per second | 0 draws |
| 144 Hz display (rAF shim) | ≤ 75 draws/s | 68 draws/s | 63 draws/s |
| Layout reads in the loop | 0 | 0 (about 7 layouts/s come from the 8 Hz readout text) | same |

**Corrections to the first version:** it reported 59–60 fps at 4× CPU in "headless Chromium" for desktop and phone. Those numbers came from new headless; Playwright's default (headless shell) gives the lower, noisy figures above. The 4× gate is set at 50 fps so it is stable, and the gates use new headless by configuration.

**Style recalcs while speaking:** about 74 a second in total. The room light now adds at most 19/s (it was a style write on every frame); the 8 Hz readout adds about 14/s and 7 layouts/s. The rest (about 60/s) is Blink ticking the two composited CSS animations (the header Voice chip ping and the System Flow packets) once per main-thread frame while the canvas loop runs; they restyle only those few elements (RecalcStyleDuration 7–13 ms per second unthrottled). In idle, mic off and every static state there are no style recalcs at all.

**Rules that made the numbers:**
- Batch strokes by alpha.
- No full-canvas blits: the canvas halo was removed and the room light is a composited DOM layer.
- Two image draws for the core.
- Cached gradients and sprites.
- Ribbons at half resolution (30 fps when small).
- Equalizer at 30 fps when small.
- The loop policy of 5.1 (15 fps breathing, stopped when nothing moves, about 60 fps on high-refresh screens).
- `content-visibility: hidden` on **inactive phone zones** and **closed overlays** (drawer, dialogs, popovers, lightbox). They stay mounted, so the camera and state survive.

### 5.6 E2E test hook (e2e builds only)

The mock bridge cannot produce LiveKit audio (its `/livekit/token` always returns 503), so voice states, motion and frame rates are tested through a hook that only exists in e2e builds.

```ts
// vite.config.ts: a define, so production builds drop the code and never emit the chunk
define: { __E2E_HOOKS__: JSON.stringify(process.env.VITE_E2E_HOOKS === '1') },
// src/vite-env.d.ts (next to the existing vite/client reference)
declare const __E2E_HOOKS__: boolean;
// src/main.tsx
if (__E2E_HOOKS__) void import('./features/voice/e2eHooks').then(m => m.install());
// playwright.config.ts, webServer[1].env (the preview build): VITE_E2E_HOOKS: '1'
```

`window.__jarvisTest`:

| Member | Effect |
|---|---|
| `setVoiceState(s \| null)` | Sets the inputs that produce state `s` (section 5.2); `null` releases every override |
| `setVoiceInputs({ room, agentAbsent, audioBlocked, activity, micOn, listening })` | Overrides individual inputs of the state machine |
| `feedAgent(level, bands[24])`, `feedAgent('speech')`, `feedAgent(null)` | Replaces the agent analyser input every frame: a constant level and bands, the built-in speech simulator (a port of the mockup's `SpeechSim`), or back to the real analyser. `feedUser(…)` does the same for the mic |
| `bindAgentStream(stream)` | Runs the real `createAudioAnalyser` path on a synthetic `MediaStream` (for example oscillators through `MediaStreamAudioDestinationNode`) |
| `stats()` | `{ loop: 'full' \| 'breath' \| 'stop', draws, roomWrites, commits }`. `commits` counts `DashboardInner` renders (an e2e-only `useEffect` without deps; React's `Profiler` does not report in production builds) |

- Specs that need audio launch Chromium with `--autoplay-policy=no-user-gesture-required`.
- Locally `reuseExistingServer` is on: stop any preview server that was built without the variable, or the hook is missing. Specs fail fast with "e2e hook missing: rebuild with VITE_E2E_HOOKS=1" when `window.__jarvisTest` is undefined.
- Acceptance: after `npm run build` without the variable, `grep -rl __jarvisTest stonix_ui/dist` is empty.

---

## 6. Chat

- **Bay:** 396 px wide at ≥ 1440 (380 at 1280–1439, 372 at 900–1279); full screen on phones.
- **Tabs:**
  - `chat`, `logs`, `notes`, `tools`, `tasks`. These are **buttons** with lowercase text and CSS `text-transform: capitalize`.
  - Use `aria-pressed` and `data-active`. **Do not use `role="tab"`**: the e2e suite uses `getByRole('button', { name: 'notes' | 'tools' | 'tasks' })`, and that name match is a case-insensitive substring, so no other button's name may contain those words.
  - Counts are `aria-hidden` 12 px pills.

**Messages**
- **Day separator:** "Today".
- **Load older:** "Purane messages" (`chat-load-older`) at the top. After loading, "Start of conversation".
- **System line:** centred, icon + text + time (`data-role="system"`), e.g. "Switched to ANNA" (added after `settings_sync`, section 8).
- **Assistant turn** (`chat-msg`, `data-role="assistant"`, `data-persona`):
  - a 30 px avatar with a gradient ring in **that message's persona** colours and the initial (J or A) in Mona Sans;
  - a header with the author name in Mona Sans Expanded 12.5 px **in that persona's colour** (ice or lilac);
  - HH:MM and a source icon (mic, keyboard or bell) with **visually hidden text** ("Voice", "Typed", "Reminder"), never `aria-label` on a span;
  - body 14.5/1.58 with markdown-ish formatting (bold, lists, `code`);
  - a follow-up in the same `turn_id` drops the avatar and header (`cont`).
  - **Past replies keep their persona** after a switch.
- **User message:** right-aligned bubble (Raised 2, `--line-2` border, radius 14/14/4/14). The footer shows the source icon, the time and one status:

  | Status | Display |
  |---|---|
  | Sending | clock |
  | **Delivered** | ✓ ok. Shown when the ack status is `sent` |
  | **Queued (agent offline)** | clock |
  | **Not delivered** | ◆ + **Retry** (`chat-retry`) |
  | Failed | "Failed: reason" |

  `chat-status-hint` stays on the hint element.
- **Tool chips:** `[status icon] tool_name arg duration` plus visually hidden ", done / failed / running". Clicking expands an inline detail with Args and Result (B612 Mono 12 px). Running chips get a slate border.
- **Actions:** copy, 👍 and 👎 (`chat-feedback`, `chat-feedback-up/down`; once per answer, the other one disabled after a choice). On hover devices they show on hover or focus; always shown on touch.
- **Typing:** a 24 px avatar, three accent dots and "JARVIS jawab de raha hai…" or "ANNA jawab de rahi hai…".
- **Streaming:** an accent caret (blinking 1 s steps; static under reduced motion).
- **Empty state** (`chat-empty`, new): a small ring mark, "No conversation yet", "<persona> se kuch bhi poochein, bol kar ya likh kar." and three suggestion pills.
- **Live captions** (`chat-live-captions`, `chat-live-user`, `chat-live-assistant`):
  - a strip above the composer with a faint accent wash;
  - "● You (live)" in white frost and "● JARVIS (live)" in the accent;
  - the last 14 words, the newest word in t2, an ellipsis prefix when longer;
  - `aria-live="off"` (the conversation log is the polite region).
- **Composer:**
  - The `textarea` **is** the field: radius 12, 48 px minimum, grows to 140 px, padding left 104 (Swarm) and right 86 (mic + send). Swarm, mic and Send are absolutely placed inside.
  - Its own `:focus-visible` ring is a 2 px accent outline with a 2 px offset.
  - Enter sends; Shift+Enter adds a line.
  - Swarm: `swarm-mode-toggle`, `data-active`.
  - Mic button mirrors the header mic (and is disabled with it, 5.2).
  - Send: `title="Send command"`, accent background with ink icon.
  - Placeholder "Message JARVIS..." or "Message ANNA...", plus a hint row (hidden on phones).
- **Auto-scroll:** only while the reader is at the bottom. Only a scroll up by the reader releases the pin (see `makeSticky` in the mockup).

### 6.1 Confirmation: one queue, two views (`useConfirmQueue` stays the single source)

**The dialog** (`confirm-dialog`, `role="alertdialog"`)
- It always shows the **queue head** (oldest first) and opens on `confirm_request`.
- **Header:** shield, "Sir, confirm karein", the summary, and a risk tag (◆ High risk / ▲ Medium risk).
- **Body:**
  - a tool line: "Tool `name` on PC (laptop) / Agent (VPS)";
  - the args in B612 Mono;
  - meta: **"Queue: N"**, a ring countdown "Expires in m:ss" (corrected by server time and clamped to the TTL, D11; the ring steps once a second), and 🎤 'Voice: "haan" bolein' when voice approval is allowed;
  - "Voice se cancel hua" notice (`confirm-cancel-notice`) when cancelled by voice.
- **Footer:** **Show in chat** (minimise without deciding) · **Deny** (`confirm-deny`, focused first) · **Allow** or **Hold to allow** (`confirm-allow`).
- **Keys:** Esc = Deny; **Enter never allows**; high risk needs a **1 s hold of the pointer or the Space key** (D9).
- **Hint line** (same text on the chat card), by input type (`matchMedia('(pointer: coarse)')`):

  | | Fine pointer (mouse, keyboard) | Coarse pointer (touch) |
  |---|---|---|
  | High risk | "Hold for 1 second with the mouse or the Space key. Esc denies. Enter never allows." | "Press and hold for 1 second to allow." |
  | Medium risk | "Esc denies. Enter never allows." | "Nothing runs until you tap Allow." |

**The chat card** (`chat-confirm-card`, new) is rendered inside the assistant turn that asked. It has three forms:

| Form | When | Contents |
|---|---|---|
| Full | The dialog is minimised | The same information as the dialog. It shows **"Request n of N"** (never "Queue:") and Deny / Allow buttons that act on the same queue item. Its buttons carry **no** `confirm-*` testids |
| Compact | The dialog is open **for this item** | "◆ Waiting for your answer in the dialog". It repeats **no** summary, tool or args, because the suite's strict `getByText('run_shell')`, `getByText('First queued action')` and `getByText('Queue: 2')` must match exactly one element |
| Record | Resolved | "● Allowed `tool` 09:45", "◆ Denied…", "○ Expired, nothing ran" or "◆ Cancelled by voice" (+ `confirm-cancel-notice`) |

**Queue behaviour**
- New requests while the dialog is closed open the dialog **for the head**.
- Resolving the head opens the next one after 260 ms.
- On the phone the dialog is a bottom sheet. The Chat tab shows ◆ with hidden text ", N confirmations waiting" while anything is pending.

**When the tab is hidden and a confirmation is pending** (keep today's behaviour and add the notification)
- the title flashes "⚠ Confirm: tool" every second;
- one 880 Hz chime per request (gain .12, 380 ms);
- a browser `Notification('Confirm: tool', {body: summary, tag: id})` when permission is granted.

### 6.2 Other tabs (restyle only; behaviour and testids unchanged)

- **logs:** the **system feed** first (telemetry, diagnostics, audit lines in full; the trace bay folds them to one line in short windows), then every level, newest first: time (B612), level (WARN/ERROR coloured), category, text.
- **notes** (`memory-tab-panel`):
  - search (`memory-search`; the input itself carries the focus ring);
  - rows with role and source pills and relative time;
  - two-step clear (`memory-clear-*`);
  - the note "Live voice conversation par asar nahi hota."
- **tools** (`tools-tab-panel`):
  - Jobs (`jobs-list`, `job-row`, `job-progress-bar/fill`, `data-percent`; Cancel; "PC par kholo");
  - tool log filter (`tool-log-filter`: All, Running, Success, Error, Issues);
  - expandable rows (`tool-row`, `data-status`). Put **`aria-expanded` on the inner button only**, not on the row `div` (axe).
- **tasks** (`tasks-tab-panel`):
  - Refresh;
  - rows with kind and status pills;
  - countdown (`task-countdown`; "In 5 min 3 s" / "Overdue 2 min", in minutes and hours, D11);
  - due clock;
  - two-step cancel.

---

## 7. Layout grid and responsive rules

### 7.1 Desktop anatomy

```
┌ topbar 64 ───────────────────────────────────────────────────────────────────────────────┐
│ ◐ [JARVIS & ANNA]  ●Bridge ●Agent▾ ●Laptop▾ │ ●Voice ⎍Wake ⌇Voice-ID   (clock≥1520) [🎤 👂 ■] [🔔 SIM ⟳ ⚙ ⏻ ⋯] │
├ rail 284 ─────┬ stage (1fr) ─────────────────────────────────┬ conversation 396 ───────────┤
│ Alerts        │ System Flow  [Voice]─[Vision]─[Core]─[Memory]─[Tools]   (one 56 px row)  │ chat logs notes tools tasks │
│ Machines      ├ presence (1fr) ───────────────┬ intel 256 ───┤                             │
│  PC (laptop)  │  (room light)  ◎ speaker  [⏻] │ Intelligence │ messages…                   │
│  Agent (VPS)  │  OUT −19 dB  JARVIS            │ featured     │                             │
│ Telemetry     │  ● Speaking │ Voice Charon     │ (scrolls)    │                             │
│ Camera        │  ▁▃▅▇▅▃▁ [bars|wave]           ├──────────────┤ captions                    │
│ Location+Map  │  helper slot (1–2 lines)       │ Recent ▸ chips│ composer                    │
│ (scrolls)     │  [Mic on] [Listening] [Voice-ID]               │                             │
│               ├ System Logs  ●Connected ▲12  WARN+ INFO filter ⏸ 🗑 ─┤                     │
│               │ terminal + feed             │ Reasoning 252  │                             │
└───────────────┴──────────────────────────────┴────────────────┴─────────────────────────────┘
```

- **Grid:**
  - columns `284px minmax(0,1fr) 396px` at ≥ 1440, `272 | 1fr | 380` at 1280–1439, `264 | 1fr | 372` at 1181–1279; gap 8 px; padding `0 12px 12px`;
  - centre rows `minmax(0,1fr) clamp(220px, 31vh, 290px)` when the window is at least 860 px tall, and **`minmax(0,1fr) 180px` below 860 px tall**: on short windows the speaker keeps priority (5.4);
  - stage columns `minmax(0,1fr) 256px` (240 at 1280–1439, 232 at 1181–1279).
- **System Flow:** **one 56 px row at every desktop width**. Below 760 px of stage width the session and long labels hide; below 640 px the links and node icons hide (shape, label and value stay; nodes share the row by content). The summary ("All modules nominal" / "Attention: …") is visually hidden text on desktop and visible on phones.
- **Trace bay:** logs and Reasoning side by side above 1180 px (Reasoning 252 px at ≥ 1440, 224 px below). Below 1440 the header stays one row: "▲ 12" (the word "dropped" is visually hidden) and icon-only Pause and Clear with their names. In a 180 px bay the terminal shows about five lines and the system feed folds to one line (full text in each line's `title` and in the conversation's logs tab).
- **Intelligence dock:** featured item (scrolls) plus a **fixed 46 px history row at its foot** ("Recent" + chips): horizontal scroll, an edge fade only where chips are clipped, labels ellipsized at 150 px with the full label in the `title`. The dock is open when it has items (closed while empty and on tablets), and **every new item opens it** and shows itself; the owner may collapse it to the 46 px tab until the next item.
- **Scrolling:** bays scroll internally. The rail scrolls; **nothing collapses to 1 px** (D4). There is **no page scroll** at heights 720–900; the presence container queries shrink the speaker instead, never below 180 px at those heights.
- **Header tiles:** two lines (12 px label in t3 over a 13 px B612 value in t1) with a `StatusShape`, 42 px high. Wake is a toggle tile with a mini switch and a pending spinner. Voice-ID mini shows the score and a bar with a threshold tick.

### 7.2 Breakpoints

| Width | Changes |
|---|---|
| ≥ 1520 | Everything plus the PKT clock |
| 1440–1519 | No clock. Columns 284 / 1fr / 396, intel 256, Reasoning 252, terminal lines in three columns (category, message, ×N) |
| 1366–1439 | Columns 272 / 1fr / 380, intel 240, Reasoning 224 beside the logs; terminal lines flow (category as a quiet prefix); trace header compact |
| 1280–1365 | Header Voice-ID mini hidden (it lives in the deck). Everything else as 1366–1439. This includes the suite's 1280×720 viewport: trace side by side, dock open |
| 1181–1279 | Columns 264 / 1fr / 372, intel 232 (open when it has items), compact tiles. At 1200×800 the dock is open, the deck wraps into two rows and Terminate joins it |
| ≤ 1180 (900–1180) | Trace tabbed: Logs / Reasoning (`system-trace-tabs`, `trace-tab-*`, tablist/tab/tabpanel with arrow keys), as today |
| 960–1179 (tablet) | Rail becomes a drawer (`ops-drawer-toggle` visible, `ops-drawer-backdrop`); intel dock starts collapsed and opens on new items; Wake, Voice-ID mini, SIM, Reconnect and Log out fold into **More**; no chevrons; columns `1fr 372` |
| 900–959 | Also the voice group (Mic, Listening, Stop) folds into More. This fixes the 44 px header overflow |
| < 900 (phone) | Single zone, bottom tabs (7.3) |
| ≤ 380 | Swarm label hidden (padding-left 52); Wake label visually hidden (icon chip) |

Window heights below 860 px (desktop and tablet): the trace row is 180 px (7.1).

### 7.3 Phone (< 900)

- **Header**, two rows, 16 px gutters:
  1. the brand (24 px mark + capsule), then bell, Settings and More;
  2. **one single-line chip row**: Bridge, Agent, Laptop, Voice, Wake.
     - 32 px pill chips, `flex: 1 1 auto`.
     - Shape + label visible; the value word is visually hidden, so it is still read out.
     - Wake shows a ⚡ (accent when on) + "Wake".
     - Tapping Agent or Laptop opens its popover.
     - Saves about 16 px against two-line tiles; combined with smaller paddings the header is 96 px.
- **Bottom tab bar** (`mobile-tab-bar`): Core, Chat, Ops, Trace, 62 px + safe area, with `aria-current="page"` and `data-active`. Shape marks: ◆ on Chat while a confirmation is pending, ▲ on Ops while alerts exist.
- **Zones** stay **mounted** (absolute, `visibility: hidden` plus `content-visibility: hidden` when inactive), so the camera keeps running across tabs (the getUserMedia count test).
- **Core tab** (order: the speaker and its controls first):
  1. presence: wrap `clamp(150px, min(216px, 58vw, 100svh − 480px), 216px)`, sized by width **and** height; readout, identity, visualizer, a two-line helper slot;
  2. a **3-up labelled dock: Mic · Listening · Stop** (62 px buttons, icon over label);
  3. the **Voice-ID row**: the meter (flex) and **Terminate at its far end** (40 px, ghost, labelled), so a mis-tap on Stop can never start a restart confirmation;
  4. System Flow (5 tiles: icon over label and value; the heading and summary visible);
  5. Intelligence.
- The dock and the Voice-ID row are fully above the tab bar at 390×844, 390×664 (a 390 px phone with browser toolbars) and 360×640 (measured: their bottom at 628, 596 and 572 against a tab-bar top of 782, 602 and 578).
- **Trace lines on phones:** the ×N repeat count stays after the message; the category drops to a second line.
- **Confirm** is a bottom sheet (touch hint, 6.1). **Settings** is a full-width drawer.

### 7.4 Electron (`window.electronAPI?.isElectron`; set `.is-electron` on `<html>`)

- **Reserve:** `padding-right: 160px` on the topbar at **every** width. `WindowControls` (minimise, maximise, close; `window-*` testids) sits at top-right inside that space, not over other controls.
- **Drag region:** the topbar is `-webkit-app-region: drag`; every button, link, input and the persona radiogroup is `no-drag`.
- **Fold order** (measured; 164 px stays free at 960–1920 px, with zero overlaps):

  | Width | Folds into More |
  |---|---|
  | < 1520 | Voice-ID mini (also in the deck), **Log out**, SIM |
  | < 1366 | Reconnect, **Settings** (covers "Settings and Log out fold below 1280") |
  | < 1100 | Voice group |

- **Clock:** hidden in Electron.
- **Window:** keep 1200×800 as the default window (the speaker is 202 px there with the dock open).
- **Other screens:** `offline.html` uses the same tokens (inline CSS) and keeps the texts "VPS unreachable" and "Retry".

---

## 8. Header, menus, overlays (behaviour notes)

- **Persona switch** = the wordmark capsule (`chip-persona` on the container).
  - `role="radiogroup"` with the label "Who answers"; two `role="radio"` buttons with `aria-checked` and roving `tabindex`; arrow keys switch.
  - It sends the same `settings_update {persona}` as today and switches **optimistically**.
  - `chip-persona-spinner` shows **while `settings.isPersonaPending`** (until `settings_sync`), never on a fixed timer; the radiogroup gets `aria-busy`.
  - After `settings_sync`: the chat line "Switched to ANNA" and the toast "ANNA is answering. Voice Aoede."
  - **No `settings_sync` within 5 s:** switch back **instantly** (no View Transition, `:root.no-acc-tr`) and show the existing toast **"Setting sync timeout — value revert ho gaya"** with "JARVIS hi jawab de raha hai." No chat line is added.
  - Titles: "JARVIS answers. Voice Charon" / "Switch to ANNA. Voice Aoede".
- **Wake word tile** (`chip-wake`): an optimistic toggle; `chip-wake-spinner` shows until the setting is confirmed.
- **Agent popover** (`chip-agent-menu`; `role="dialog"`, not "menu", because it holds key/value text):
  - "Agent (VPS)" with the state;
  - session, brain, last seen;
  - Pause agent (`agent-pause`) or Start / Wake (`agent-wake` + `agent-wake-spinner`).
- **Laptop tile and popover** (`chip-laptop`, `chip-laptop-menu`, new; X-67 provides the data):
  - **"unknown"** (dashed ○, italic) until the first `desktop_status` arrives;
  - control mode, locked, voice active, busy tool, brain, last seen;
  - **Stop desktop** (`desktop-stop-menu`, new, danger);
  - a note: "Stop switches the laptop to read-only. Only the laptop tray can switch back to Full."
  - Laptop states:

    | State | Shape and label |
    |---|---|
    | unknown | ○ dashed *unknown* |
    | online | ● Full |
    | stale | ▲ Stale |
    | offline | ○ Offline |
    | locked | ▲ Locked |
    | panic | ◆ Panic |
    | read-only | ● slate Read-only |

  - The rail card shows "PC (laptop)" with a state tag and its own Stop desktop button (`desktop-stop`), plus a hint for read-only or panic.
- **Bell** (`topbar-task-bell`, `task-bell-count` capped at 99+, `task-bell-menu`):
  - "Recent alerts" (last 20) with rows (`task-notif-row`): due time, 🔊 Spoken aloud (`task-notif-spoken`) or 💬 Screen only (`task-notif-screen-only`), and ✓ Delivered or ▲ Not acknowledged yet.
  - **Footer:** desktop notification permission.
    - granted: "● Desktop notifications on";
    - default: "○ Desktop notifications off [Turn on]" (`Notification.requestPermission()`);
    - denied: "◆ Blocked in browser settings".
  - Opening the bell clears the unread count and acknowledges what is shown (`notification_ack`, X-67).
- **More** (`role="menu"`, `menuitem`s, Up/Down arrows): Mic (disabled while voice is unavailable or joining), Listening, Stop, Wake word, Reconnect, SIM lookup, **"Open settings"**, Log out.
  - **No `title` attribute containing "settings" anywhere except the header gear.** `getByTitle('Settings')` is a case-insensitive substring match in strict mode, so the drawer close button uses `title="Close"`.
- **Reconnect** (header `topbar-reconnect`, the More item, the drawer footer). Always a secondary action, never the accent primary.
  - **Until X-96 lands:** leave the LiveKit room, set `sessionStorage['jarvis.reconnected'] = '1'`, then `location.reload()`. After the reload, the first socket connect shows the **"Reconnected"** toast and clears the flag (`status.spec.ts:75` expects it within 20 s). Title: "Reconnect: reloads the dashboard and rejoins voice". The drawer adds the note "Reloads the dashboard. Voice rejoins with the mic off."
  - **After X-96:** Reconnect re-binds every listener in place (no reload) and keeps the toast; the drawer note goes.
- **Settings drawer** (`settings-drawer`): 420 px, every section and field as today (audit 1.7).
  - Quiet hours commit on blur or Enter; the threshold commits on slider release (D12).
  - Esc closes it and focus returns to the opener.
  - The title is "System settings".
  - Opening it no longer wipes the trace (X-96: `request_state` once per connect, `init_state` merges).
- **SIM dialog** (`sim-dialog`): `role="dialog"`, `aria-modal`, focus trap, Esc. Keep the titles "SIM lookup workspace" (opener) and "Close SIM access", and `.sim-search button`.
- **Enrollment** (`voice-enroll-dialog`):
  - prerequisites with shapes: "Voice room joined" (avoid the word "connected", see 11), Mic, Agent listening;
  - the 3 sentences (the first follows the persona: "JARVIS sun raha hai? …" / "ANNA sun rahi hai? …");
  - mic level and progress (fills scale, section 4);
  - outcomes: success (`voice-enroll-success`), **error** (new optional `voice-enroll-error`: "Enrollment failed: too much background noise (SNR 6 dB)…") and timeout (`voice-enroll-timeout`);
  - focus trap and Esc.
- **Lightbox** (`intelligence-lightbox`): `role="dialog"`; Esc must work (today it is bound to a non-focusable div, D9).
- **Toasts:** react-hot-toast with a custom renderer. Bottom centre, at most 3, Raised 2, a 12 px radius, an icon by kind. DESKTOP_* toasts carry Play and Download actions through `/vortex/media` (X-67). Keep the texts the suite expects (11).

---

## 9. Where every current panel goes (nothing removed)

| Today (file) | Feature | New home and changes | Files |
|---|---|---|---|
| `TopBar.tsx` brand "STONIX / Assistant command center" | Brand | Ring mark + **wordmark capsule** (persona switch) | `shared/ui/BrandMark.tsx` (new), `features/status/PersonaSwitch.tsx` (new), `TopBar.tsx` |
| `TopBar.tsx` chips `chip-bridge`, `chip-agent`(+menu), `chip-voice`, `chip-wake`, `chip-persona` | Status | Annunciator tiles (desktop), single-line chips (phone). Persona moves into the wordmark. Same testids and `data-active`; Wake keeps its pending spinner | `TopBar.tsx`, `features/status/Annunciator.tsx` (new), `AgentControls.tsx` |
| X-67 (todo) | Laptop chip, Stop desktop | Tile `chip-laptop` ("unknown" until `desktop_status`) + popover `chip-laptop-menu` + `desktop-stop-menu`; rail card "PC (laptop)" + `desktop-stop` | `features/status/LaptopChip.tsx` (new or from X-67), `OperationsRail.tsx` |
| `AgentControls.tsx` Mic, Listening, Stop | Voice controls | Header voice group; deck (Mic, Listening); phone dock (Mic, Listening, Stop). All mic mirrors are disabled while voice is unavailable or joining | `AgentControls.tsx`, `AssistantCore.tsx` |
| `TopBar.tsx` bell | Reminders | Bell popover + permission footer + ack markers | `TopBar.tsx` → `features/notifications/BellMenu.tsx` (folder from X-67) |
| `TopBar.tsx` SIM, Reconnect, Settings, Logout, ops toggle | System | System group; fold rules 7.2 and 7.4; **More** menu (new). Reconnect is secondary and reloads until X-96 | `TopBar.tsx`, `features/status/MoreMenu.tsx` (new) |
| `OperationsRail.tsx` Camera, Location, Map, Vitals + Telemetry, Alerts | Operations | Operations bay, scrolling: **Alerts** (shape + word, "Normal. No alerts."), **Machines** (PC (laptop) / Agent (VPS), slate meters, ▲ on high values, stale), **Telemetry** (4 tiles + latency sparkline + status), **Camera** (off by default; badge states incl. "Not linked" + Take over), **Location** + **Map focus** (OSM iframe kept; `map-focus-empty` "No map focus yet. Ask <persona> to show a place.") | `OperationsRail.tsx`, `CameraPanel.tsx`, `cameraState.ts`, `location/MapFocus.tsx`, `telemetry/TelemetryCard.tsx` |
| `FlowMap.tsx` | System Flow | One 56 px bus row at the top of the stage (below the speaker on phones): 5 nodes (Voice, Vision, Core assistant, Memory, Tools) with shapes, links with packets only while voice or tools move data (not on phone). **"unknown"** in italics with a dashed ○. "System Flow" heading **visible at every width** | `FlowMap.tsx`, `flowMapStatus.ts` |
| `AssistantCore.tsx` orb, rings, 32 bars, footer | Voice core | **SpeakerPresence** (section 5) + identity + visualizer + helper slot + deck + Terminate; banners overlay | `AssistantCore.tsx`, `features/voice/presence/{SpeakerPresence.tsx, speakerCanvas.ts, visualizer.ts}` (new), `features/voice/audioEngine.ts` (new), `features/voice/e2eHooks.ts` (new, e2e builds only) |
| `IntelligencePanel.tsx` | Intelligence / media | Dock right of the presence (256 / 240 / 232 px; open when it has items, opens on every new item; 46 px tab when collapsed). Featured types: JSON table (`intelligence-json`), link, image (`intelligence-image` → `intelligence-media-image`, click → lightbox), audio (`intelligence-audio` → `intelligence-media-audio`), LAN-only (`intelligence-lan`, `intelligence-lan-audio`, "Sirf PC network par" + Copy URL `intelligence-copy-lan`), **"Loading media…"** and **"Media unavailable"** as 16:9 placeholders (spinner / ◆ + reason + Retry); history chips (`intelligence-history-<id>`) in a fixed row at the foot | `IntelligencePanel.tsx` |
| `SystemTrace.tsx` | System Logs + Reasoning | Trace bay under the stage (180 px in windows under 860 px tall): WARN+ (fixed), INFO toggle, **Filter logs** (the input carries the ring), Pause/Resume (with count), Clear, ▲ dropped notice, terminal (`tabindex=0`, role log; time, level, body with category, message and ×N), system feed, Reasoning card (select with ellipsis and title, intent, ambiguous and source badges, confidence bars, step progress, steps). Side by side above 1180 px, tabbed at 1180 px and below | `SystemTrace.tsx` |
| `ChatPanel.tsx` + tabs | Conversation | Section 6 (the logs tab gains the full system feed) | `ChatPanel.tsx`, `chat/useChat.ts` (Delivered state), `confirm/ConfirmCard.tsx` (new) |
| `ConfirmDialog.tsx` + `useConfirmQueue.ts` | Confirm | Section 6.1 (dialog + chat card, one queue) | same files + `ConfirmCard.tsx` |
| `SettingsPanel.tsx` | Settings | Drawer (section 8) | `SettingsPanel.tsx`, `settings/useSettings.ts` |
| `SimAccessDialog.tsx` | SIM | Dialog (section 8) | `SimAccessDialog.tsx` |
| `VoiceEnrollDialog.tsx` | Voice-ID enroll | Dialog (section 8) | `VoiceEnrollDialog.tsx` |
| `LoginScreen.tsx` | Login | **Aurora card**: glows (indigo left-top, magenta right-bottom), glass card (blur 14), mark 52 px, "JARVIS & ANNA" in Mona Sans 26, password (show/hide), TOTP (B612 20 px, .55em), "Recovery code use karein", error, gradient **Sign in** (ice → indigo, ink `#060812`, 5.7:1 worst case), foot "Socket, voice and camera start only after you sign in." | `LoginScreen.tsx` |
| `App.tsx` "Session check…" | Session check | Breathing mark + track ("Reconnecting…" while the interim Reconnect reloads) | `App.tsx` |
| `electron/offline.html` | Offline page | Card with the mark, "VPS unreachable", text, Retry | `electron/offline.html` |
| `shared/ui/WindowControls.tsx` | Window buttons | Inside the reserved 160 px; `.is-electron` on `<html>` | `WindowControls.tsx`, `App.tsx` |
| `voice/LiveKitProvider.tsx` banners | Voice banners | Overlay notices below the Terminate row, from the same inputs as the voice state (not in the page flow) | `LiveKitProvider.tsx`, `AssistantCore.tsx` |
| `MobileTabBar.tsx` | Phone tabs | Section 7.3 | `MobileTabBar.tsx` |
| `shared/ui/ErrorBoundary.tsx` | Panel error | Styled fallback in place of the panel body: ◆ "<Panel> stopped working", "This panel hit an error. The rest of the dashboard keeps running.", **Retry** (`panel-error`, `panel-error-retry`) | `ErrorBoundary.tsx` |
| `toast/provider.tsx` | Toasts | Custom renderer (section 8) | `toast/provider.tsx` |

**New testids:** `chat-confirm-card`, `chat-empty`, `chip-laptop-menu`, `desktop-stop-menu`; optional `voice-enroll-error`.

**Moved testid:** `chip-persona` moves to the wordmark radiogroup container.

Everything else in `testids.txt` (180 static plus the dynamic ones, including `chip-wake-spinner` and `chip-persona-spinner`) stays on an element with the same job. The mockup has every id of the first version plus `chip-wake-spinner`.

---

## 10. Branding changes

| Where | Today | New |
|---|---|---|
| `src/features/auth/LoginScreen.tsx:78-79` | "STONIX", "Owner session — password + authenticator" | "JARVIS & ANNA" (wordmark), "Owner session: password and authenticator" |
| `src/features/command-center/components/TopBar.tsx:139-140` | "STONIX", "Assistant command center" | Ring mark + wordmark capsule |
| `src/features/confirm/ConfirmDialog.tsx:15` | `BASE_TITLE = 'STONIX Command'` | `'JARVIS & ANNA'`. `document.title` is **"JARVIS & ANNA"** (D10); the flash stays "⚠ Confirm: <tool>" |
| `index.html:36` | "JARVIS & ANNA // Neural Interface" | "JARVIS & ANNA". Add `<meta name="theme-color" content="#05060B">` and `<link rel="icon" type="image/svg+xml" href="/favicon/favicon.svg">` |
| `public/favicon/*` (stained glass) | 16, 32, 180, 192, 512 PNG | The ring mark (ice → indigo left arc, lilac → magenta right arc, white core) on `#05060B` with an 8/32 radius. **Same file names** (the Electron tray uses `favicon-32x32.png`). Add `favicon.svg` and keep the master at `assets/brand/jarvis-anna-mark.svg`. Render the PNGs with a script (`stonix_ui/scripts/render-favicons.mjs`, Playwright screenshot of the SVG) |
| `electron/main.cjs:278-289` | Tray "Show JARVIS", "Quit JARVIS", tooltip "JARVIS-AGENT" | "Show JARVIS & ANNA", "Quit JARVIS & ANNA", tooltip "JARVIS & ANNA" |
| `electron/main.cjs:308` | Window title "JARVIS & ANNA Neural Interface" | "JARVIS & ANNA" (`electron.spec.ts` still matches `/JARVIS & ANNA/`) |
| `electron/offline.html:6, :52` | "JARVIS — VPS unreachable" | "JARVIS & ANNA: VPS unreachable"; keep "VPS unreachable" and "Retry" |
| `src/shared/ui/WindowControls.tsx:26` | "JARVIS backend band ho gaya (code N)" | "JARVIS & ANNA backend band ho gaya (code N)" |
| `stonix_ui/README.md:1` | "# STONIX dashboard (stonix_ui)" | "# JARVIS & ANNA dashboard (stonix_ui)" |
| `tests/e2e/auth.spec.ts:37`, `dashboard.spec.ts:11`, `voice.spec.ts:11` | `getByText(/STONIX/i)` | `await expect(page).toHaveTitle(/JARVIS & ANNA/)` + `await expect(page.getByRole('radiogroup', { name: 'Who answers' })).toBeVisible()` |
| `tests/mock-bridge/fixtures/init_state.json:6` | "Mock bridge ready. STONIX dashboard online." | "Mock bridge ready. JARVIS & ANNA dashboard online." |

**Unchanged:**
- The folder name `stonix_ui` (deploy, CI and lanes depend on it). The check for leftovers is a **whole-word** grep: `grep -rwi stonix` (it ignores `stonix_ui` in `README.md:10` and the `stonixRoot` variable in `tests/electron/electron.spec.ts:6`).
- The internal `command-*` prefix may stay as aliases.

**Persona-gender copy table** (`src/shared/persona.ts`, new; every persona string goes through it)

| Key | JARVIS | ANNA | Used in |
|---|---|---|---|
| `name`, `initial`, `voice` | JARVIS, J, Charon | ANNA, A, Aoede | Everywhere |
| `g` (continuous) | raha | rahi | "Boliye, main sun **raha** hoon." · "Ek lamha, soch raha hoon." · "JARVIS jawab de raha hai…" · "JARVIS sun raha hai" |
| `fut` (future) | ga | gi | "JARVIS mic se nahi sune**ga**; typed chat chalti rahegi." · "yaad dila doon**ga**" |

**Helper copy by voice state** (the helper slot, 5.4). `<NAME>`, `<g>` and `<fut>` come from the table above.

| State | JARVIS | ANNA |
|---|---|---|
| offline | Voice abhi band hai. Chat aur controls chalte rahenge. | same |
| joining | Voice room join ho raha hai. Chat chalti rahegi. | same |
| agent absent | JARVIS room me nahi. Wake dabayein; chat chalti rahegi. | ANNA room me nahi. Wake dabayein; chat chalti rahegi. |
| audio blocked | Browser ne awaaz rok di hai. Upar Enable audio dabayein. | same |
| speaking | Beech mein bolein ya Stop dabayein, jawab ruk jayega. | same |
| thinking | Ek lamha, soch raha hoon. | Ek lamha, soch rahi hoon. |
| muted | JARVIS mic se nahi sunega; typed chat chalti rahegi. | ANNA mic se nahi sunegi; typed chat chalti rahegi. |
| listening | Boliye, main sun raha hoon. | Boliye, main sun rahi hoon. |
| mic off | Mic band hai. Mic on dabayein ya type karein. | same |
| idle, wake word on | "JARVIS" bolein ya neeche type karein. | "ANNA" bolein ya neeche type karein. |
| idle, wake word off | Boliye, JARVIS sun raha hai. | Boliye, ANNA sun rahi hai. |

- Voice error (offline, in the helper slot): "Voice: <error>".
- Stop title: `<NAME> ko chup karao`.
- Mic-off copy replaces today's AssistantCore line "Mic off — room linked hai. Mic on dabao jab bolna ho…" (nothing is lost: the Listening control and the mute meaning are in the muted copy and the Listening title).

**Files that today ignore the persona:** `useChat.ts:109`, `AgentControls.tsx:106,260`, `AssistantCore.tsx:239`, `VoiceEnrollDialog.tsx:11`.

---

## 11. Accessibility rules (each one was verified in the mockup)

1. **Text size:** no text below 12 px anywhere, including badges, counts and log levels.
2. **Focus:**
   - A visible focus ring on **every** focusable element: `:focus-visible { outline: 2px solid var(--acc); outline-offset: 2px }`.
   - Inputs and textareas get the ring **on the element itself**: the composer textarea, **Filter logs**, memory search, SIM inputs, settings inputs, login fields.
   - Never `outline: none` without a replacement.
3. **No `aria-label` on role-less elements** (spans, divs). Icons are `aria-hidden`; meaning goes in visually hidden text (`.sr-only`) or on the button's own name. This fixes Command's 9 `aria-prohibited-attr` violations.
4. **ARIA roles:**
   - Popovers with mixed content are `role="dialog"` with a label. Only More is `role="menu"` with `menuitem`s.
   - The trace view switch (≤ 1180 px and phones) is `role="tablist"` / `tab` / `tabpanel` with arrow keys. Above 1180 px there are no tabs: Reasoning is a named `region`.
   - Chat tabs are buttons with `aria-pressed` (e2e names).
5. **Scrollable regions** have `tabindex="0"`, **`role="region"`** and a name (a name on a role-less div is prohibited: axe reports it as `aria-prohibited-attr`): the rail, the conversation's logs, notes, tools and tasks panes. The conversation and the terminal are `role="log"`; Reasoning is a `region` or a `tabpanel` (rule 4).
6. **Status:** never colour alone (section 1.4). Every shape has a word next to it, visible or hidden.
7. **Live regions:**
   - the conversation (`role="log"`, `aria-live="polite"`);
   - toasts (`role="status"`);
   - one hidden polite announcer for voice changes that matter, **debounced 1.2 s** and never repeated: into offline ("JARVIS: voice unavailable"), muted ("JARVIS: not listening"), mic off ("Mic off"), agent absent ("Agent not in the voice room") and audio blocked ("Agent audio blocked"), and back out of them ("Voice back", "JARVIS: listening again", "Mic on"). The listening / thinking / speaking cycle of every turn is **not** announced (`voice-status` still shows it);
   - captions `aria-live="off"`.
8. **Dialogs:** focus trap, initial focus (Deny for confirm), Esc, and focus returns to the opener. The confirm dialog cannot be closed by clicking outside. The hold hint has a touch variant (6.1).
9. **Keyboard:**
   - The persona radiogroup uses arrow keys.
   - Hold to allow works with Space; Enter never allows.
   - Menus use arrow keys and Esc.
10. **Uniqueness the strict e2e locators need:**
    - "System Flow" appears exactly once in the DOM.
    - **"connected"** (substring, case-insensitive; `confirm.spec.ts:39`) appears in exactly one element: the trace header "Connected". Render closed dialogs **conditionally** (or avoid "connected" in their text). New copy avoids it too ("Voice room joined", "Agent not in room").
    - The `title` "Settings" is unique (see section 8).
    - `confirm-allow` and `confirm-deny` exist only in the dialog.
    - "Queue: N" appears only in the dialog.
    - No button name other than the chat tabs contains "notes", "tools" or "tasks".
11. **Contrast:** WCAG AA for all text (table in 1.3). Placeholder text uses t3 (6.3:1 on Raised).
12. **Reduced motion:** section 4.

---

## 12. Acceptance checks (put these in the repo as e2e specs; numbers from the mockup)

**Projects.** `playwright.config.ts` gets two projects:
- `chromium` (today's: Desktop Chrome, 1280×720, default headless shell) with `grepInvert: /@gate/`. `npm run test:e2e` becomes `playwright test --project=chromium`; CI keeps running it unchanged.
- `gates` (`use: { ...devices['Desktop Chrome'], channel: 'chromium' }`, `grep: /@gate/`) for the visual and frame-rate gates. New scripts: `test:perf` and `test:visual` (both `--project=gates`). Both specs also skip under `process.env.CI`.

Run against the mock bridge (`npm --prefix stonix_ui run test:e2e`; gates with `test:perf` / `test:visual`). Voice states come from the hook (5.6).

1. **Visual baselines** (`tests/e2e/visual.spec.ts`, tagged `@gate`, **Windows-local only**: `test.skip(process.platform !== 'win32')`; the baselines are committed with Playwright's `-gates-win32` suffix and never run in CI):
   - Emulate `reducedMotion: 'reduce'` so the speaker is static.
   - Mask the clock, relative times and the camera.
   - Capture `toHaveScreenshot` at **1440×900** (dashboard JARVIS speaking, dashboard ANNA, settings open, confirm open, login) and at **390×844** (Core, Chat, Ops, Trace, confirm sheet).

   ```ts
   test.use({ viewport: { width: 1440, height: 900 } });
   test('dashboard 1440 @gate', async ({ page }) => {
     test.skip(process.platform !== 'win32' || !!process.env.CI, 'Visual baselines are Windows-local (X-95)');
     await page.emulateMedia({ reducedMotion: 'reduce' });
     await loginViaRecovery(page);
     await page.evaluate(() => window.__jarvisTest!.setVoiceState('speaking'));
     await expect(page).toHaveTitle(/JARVIS & ANNA/);
     await expect(page).toHaveScreenshot('dash-1440.png', { mask: [page.locator('.clock'), page.locator('time')], maxDiffPixelRatio: 0.01 });
   });
   ```

2. **Layout** (`layout.spec.ts`, default project):
   - no horizontal scroll (`scrollWidth ≤ clientWidth + 1`) at **360, 390, 768, 900, 960, 1024, 1200, 1280, 1366, 1440 and 1920** px wide;
   - no page-level vertical scroll at heights **720, 800 and 900** for widths ≥ 900; the composer is fully visible; nothing in the header overflows or overlaps;
   - **speaker priority:** the orb wrap is **≥ 180 px and the helper slot is visible at 1200×800, 1366×768 and 1536×730** (mockup: 202, 228, 190), and ≥ 180 px at 1280×720;
   - **phone dock:** at **390×844, 390×664 and 360×640** the Mic / Listening / Stop dock and the Voice-ID row with Terminate are fully above the tab bar;
   - `trace.spec` and `intelligence.spec` run unchanged at the default 1280×720.
3. **Electron** (`tests/electron/electron.spec.ts` + emulation in e2e): with `window.electronAPI = { isElectron: true, … }` injected by `addInitScript` at **1200×800** (also 1280, 1366, 1440 and 960), no header control's rect intersects the `window-*` buttons, and at least 150 px stay free at the right (mockup: 164).
4. **axe** (`@axe-core/playwright`, tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`): **0 violations**, and no `aria-prohibited-attr` among the "incomplete" results, on:
   - the dashboard in every voice state of 5.2 (through the hook), with ANNA listening and with reduced motion;
   - settings, confirm, SIM, enroll (incl. error), bell, Agent/Laptop popovers (incl. "unknown"), More (1024), lightbox, media loading and unavailable;
   - login, offline page, tablet drawer and tablet trace tabs;
   - each chat tab and phone tab, and the phone confirm sheet.
   (Mockup: 0 violations and 0 prohibited-attribute warnings across 36 states.)
5. **Text size:** a DOM walk finds no visible text node under 12 px.
6. **Frame rate** (`tests/e2e/perf.spec.ts`, `@gate`, `gates` project, local only). Speaking is driven by the hook: `setVoiceState('speaking')` + `feedAgent('speech')`.

   ```ts
   const cdp = await page.context().newCDPSession(page);
   await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
   const fps = await page.evaluate(ms => new Promise<number>(r => { let n = 0; const t0 = performance.now();
     const f = (t: number) => { n++; t - t0 < ms ? requestAnimationFrame(f) : r(n / (ms / 1000)); }; requestAnimationFrame(f); }), 3000);
   expect(fps).toBeGreaterThanOrEqual(50);   // 390x844, deviceScaleFactor 3, isMobile; median of three runs
   ```

   Also ≥ 58 fps at 1440×900 DPR 1 and DPR 2 unthrottled and ≥ 50 at DPR 2 with 4× throttle. Script time (CDP `Performance.getMetrics` `ScriptDuration`) ≤ 1.5 ms per frame on desktop. Idle and mic off ≤ 16 draws a second (`stats().draws`).
7. **Motion** (`presence.spec.ts`, default project, through the hook):
   - Sample the speaker canvas (draw it at 96×96, mean absolute RGBA difference between frames 100 ms apart): **speaking with `feedAgent('speech')` ≥ 5× idle** (mockup, three runs in both headless modes: idle 0.09–0.10, speaking 13.0–17.9, that is 138–197 times idle).
   - **Speaking with `feedAgent(0)` (silence) ≤ 2× idle** (mockup: 0.025–0.027, stiller than idle breathing): the motion follows the level, never the state alone.
   - **Loop:** `stats().loop` is `'stop'` and `draws` does not change over 1 s in muted, offline, joining, agent absent and audio blocked; idle and mic off stay ≤ 16 draws a second; speaking is above 30.
   - Under reduced motion the difference is **0**, and `document.getAnimations()` has no infinite animation running.
8. **Confirm:** Deny is focused first; Enter never allows; a 400 ms Space press does not allow; a 1 s Space hold allows (high risk); Esc denies. "Show in chat" minimises to the full chat card. A second request shows "Queue: 2" and the dialog stays on the head. With `hasTouch` the hint reads "Press and hold for 1 second to allow."
9. **Persona:**
   - switching by click and by arrow keys re-themes the page;
   - the spinner shows while pending and hides after `settings_sync`; the toast and the "Switched to …" line come after it;
   - with `settings_sync` withheld, the persona reverts after 5 s with "Setting sync timeout — value revert ho gaya";
   - in ANNA no JARVIS accent colour remains outside chat history, and the reverse;
   - the placeholder becomes "Message ANNA...";
   - past replies keep their persona name and colour;
   - with reduced motion the switch is instant.
10. **Existing suite:** all 23 e2e specs and the Electron spec stay green in the default project at 1280×720, with only the `/STONIX/` updates of X-70. This includes `voice.spec` (voice-toggle disabled while voice is unavailable), `trace.spec` (reasoning beside the logs at 1280; tabs at 1100), `intelligence.spec` (the dock open) and `status.spec` "Reconnect shows success toast" (the interim reload keeps the toast).
11. **No layout shift** (`presence.spec.ts`): the rects of the orb wrap, the helper slot and the deck, and the identity row's top and height, are identical in idle, listening, thinking and speaking, and in mic off before and during a spoken reply, at 1440×900, 1366×768, 1200×800 and 390×844.
12. **Reconnect** (`reconnect.spec.ts`, X-96): after `topbar-reconnect`, a `confirm_request` opens the dialog, `new_message` renders and `agent_status` offline flips the chip; opening Settings emits no `request_state` and keeps a WARNING line in the terminal; the mock records one `request_state` per connect.

---

## 13. Hand-off text for `plan.md` section 11 (Claude pastes it; Claude also adds the X-69…X-102 rows and task blocks, as for X-64…X-68)

```
- [2026-10-02 HH:MM] claude → cursor: **UI redesign (user ka faisla: "UI full change, naam JARVIS & ANNA, professional, voice bole to speaker vibrate kare").** Design + spec + tasks scratchpad me tayyar: final mockup (single HTML), spec.md, tasks.json (review ke baad revised). Tasks X-69..X-102 (P3, sab stonix_ui/** + assets/**). Koi contract change nahi; har testid aur text anchor rehta hai; /STONIX/ wale 3 specs + mock fixture X-70 ke saath badlenge. Order: X-69 tokens/fonts → X-96 socket subscription store (D1–D3: Reconnect ke baad hooks dead socket par, init_state listener strip, Settings kholne par trace wipe; X-83, X-84, X-88 se pehle) → X-70..X-73 brand, copy, status shapes → X-74 audio engine + e2e test hook (window.__jarvisTest, sirf VITE_E2E_HOOKS=1 builds me) → X-75, X-97, X-98 speaker canvas, 10-state voice machine (default after login = mic off), reduced motion + presence.spec → X-76, X-77 → X-67 ke baad X-78, X-99, X-100 header, X-79, X-85, X-92 → baaki panels (X-84, X-101, X-102 confirm) → X-94 a11y → X-95 gates. X-96 tak Reconnect page reload karta hai (secondary button; 'Reconnected' toast reload ke baad). Perf/visual gates alag Playwright project 'gates' (new headless) me, sirf local; npm run test:e2e = --project=chromium, is liye CI green rehta hai. Agar CI me gates chahiye to Cursor REQ-CI likhe (.github Claude ka lane).
```
