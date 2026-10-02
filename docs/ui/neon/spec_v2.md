# JARVIS & ANNA "Neon Command Center": implementation spec v2

| | |
|---|---|
| **Task** | C-135 (Claude): UI spec v2. Implementation: Cursor, tasks X-69 … X-109 (`tasks_v2.json`, same folder) |
| **Owner ask** (Roman Urdu, translated) | "I want to build exactly this kind of UI, exactly like this. Look at it deeply and with focus and work out how it will be done." |
| **Reference** | `JARVIS & ANNA Neon Command Center.png` (repo root, 1672×941 RGB, one page: "Overview") |
| **Lane** | Cursor only: `stonix_ui/**`, `assets/**`. Claude deploys the frontend after X-95 (and after C-21/C-22), with `VITE_BRIDGE_BASE=/vortex` |
| **Contracts** | No change. One contracted call the UI never used before, `GET /vortex/status` (K-43 C-STATUS-1: `ui_clients`, bridge/DB/LiveKit health, `agent.host`), is now read |
| **Supersedes** | The look, layout and copy of `docs/ui/UI_REDESIGN_SPEC.md` ("Mission Control") and `docs/ui/jarvis_anna_mockup.html`. These parts of the old spec stay valid where this spec says so: the audio engine (X-74, done), the e2e hook (§5.6 there, extended in §8.11 here), the performance gates (§5.5 there, §21.4 here), the confirm safety rules, the persona copy table and the accessibility rules. `docs/ui/UI_AUDIT.md` stays the inventory of today's UI (features, events, testids, defects D1–D13) |
| **Date** | 2026-10-02 |

**Bundle.** When C-135 closes, Claude copies the 38 files listed in `bundle_manifest.json` (about 5 MB, with SHA-256) to `docs/ui/neon/`, keeping their relative paths; every relative link below assumes that folder. The work folders (crops, fits, review screenshots) stay in the scratchpad, and every other prototype state is reproducible from `final_neon.html` (Alt+P).

| File | What it is | Use it for |
|---|---|---|
| `JARVIS & ANNA Neon Command Center.png` (repo root) | The owner's reference | The acceptance target (§21.1). X-109 copies it byte for byte to `stonix_ui/tests/visual/reference/neon-command-center.png` |
| `final_neon.html` | **The product prototype**, one offline file: every page, state, size and persona. `Alt+P` (or `?proto=1`, or five taps on the logo) opens the prototype controls; `?layout=dk\|tl\|ph\|art`, `?reduce=1`, `?still=1` (frozen reference data) | Anything this spec does not pin down: copy, spacing, DOM order, ARIA, state behaviour |
| `final_work/src/` | The prototype's sources: `neon_base.css` and `neon.css` (look, primitives, pages, overlays), `fluid.css` (**the product layout**), `icons.svg.html`, `js/00_core … 70_proto_boot.js` | Porting (§1) |
| `orb_data.json` | The fitted data of the orb, logo ring, waveform and mini orbs (`OP`, `RINGS`, `PARTICLES`, `SWIRL`, `LOGO*`, `WAVE`, `MINI`), extracted from the prototype | `src/features/voice/orb/orbData.ts` (created in X-70 for `LogoRing`; the orb X-75, the waveform X-76 and the mini orbs X-107 read it) |
| `icons.svg` | All 75 icon symbols of the prototype in one sprite | `src/shared/ui/icons/Sprite.tsx` (X-69) |
| `visual_gate/regions.json`, `visual_gate/reference_fixture.json` | The V-1 gate's regions, container boxes, 96 ink targets, thresholds and baselines; the data and state that reproduce the reference picture | X-109 |
| `visual_gate/validation/` | Two prototype frames at 1672×941 (fluid layout and artboard) with their `diff.py` metrics (re-verified: identical) | X-109 validates its TypeScript port on them |
| `overview_replica.html`, `replica_log.md` | The calibrated 1672×941 artboard (29 diff iterations: MAE 4.84, SSIM 0.927) | Visual reference only. **Never port its absolute positions** |
| `forensics.md`, `forensics.json` | Pixel measurements of the reference: 73 element boxes, 96 text ink boxes, colours, radii, glows, orb layers, waveform bars, fonts | Raw numbers |
| `mapping.md` | Every current feature (F1–F113), event, command and testid with its new home; the e2e impact per spec | The exhaustive "nothing removed" checklist (§17, §18) |
| `replica_work/diff.py` | Reference implementation of the visual diff (MAE, SSIM, ΔE, region stats, container edges, ink boxes) | X-109 ports it to TypeScript |
| `shots/` | Four prototype screens: `d_01_overview` and `d_22_anna_overview` (1672×941), `k_01_overview` (1280×595, the owner's laptop window), `m_01_overview` (390×844 with the confirm sheet). The scratchpad keeps 213 (`d_*` 1672×941, `w_*` 1536×730, `k_*`, `e_*` 1200×800, `t_*` 1024×768, `p_*` 768×1024, `m_*`, `s_*` 320×640) | What the states look like; any other state comes from `final_neon.html` |
| `fix_r1/gates_ABC.json`, `fix_r1/perf2_gpu.json` | The prototype's viewport-gate and performance measurements | The numbers behind §21.3 and §21.4 |

**Precedence:** this spec, then `final_neon.html`, then `forensics.md`, then `mapping.md`. If two of them disagree, the earlier one wins; tell Claude in `plan.md` section 11.

---

## 0. Decisions on one page

1. **The reference is the acceptance target.** At 1672×941 with the reference data, the product's Overview must pass the visual-diff gate V-1 (§21.1). Only the owner's name and the documented differences of §22 change.
2. **The shell is the reference on every page:** sidebar (ring logo, "JARVIS & ANNA", "Your AI Command Center", nav Overview / Agents / Activity / Settings, silk wave, "A SMARTER TOMORROW TOGETHER"), page header (title, subtitle, three status pills, avatar with greeting), main column and a **persistent right panel** (Chat / Approvals / Tools, composer). Hash routes `#/overview` (default), `#/agents`, `#/activity`, `#/settings` (phones also `#/chat`). Pages stay mounted.
3. **Fluid grids, never the artboard.** Reference pixels are maxima: `--vu = min(1px, 100vh / 941)`, `--hu = min(1px, 100vw / 1672)`, sizes `clamp(floor, k · unit, reference)`. No transform scaling. Layouts: **dk** ≥ 1180 px wide, **tl** 1024–1179 px (icon rail), **ph** < 1024 px (bottom tabs).
4. **Nominal = the reference.** Live data fills only the places the reference has. Extras (badges, warning shapes, meta lines, inline actions) appear only off-nominal.
5. **Owner:** "Good evening," / "Matloob", header avatar "M", chat avatar "M" (UI constant `OWNER`; greeting by local time).
6. **Pills:** "System Online" = the agent (`chip-agent`, Agent popover). "3 Connected Devices" = bridge link + device count (`chip-bridge`, Devices popover with Reconnect, the Laptop row and Stop desktop). "Voice Ready" = voice state (`chip-voice`, Voice popover: mic, listening, wake word, stop, Voice-ID). The avatar block opens Account & reminders (the old bell: `topbar-task-bell`). **There is no "More" menu**: the popovers hold every header control.
7. **Hero:** the eyebrow "JARVIS & ANNA" **is the persona switch** (radiogroup "Who answers", `chip-persona`). The big name is the answering persona. Waveform and status line show the voice state. **Start Listening** = `voice-toggle` (mic on, also unmutes), **Run Task** = `swarm-mode-toggle`, **Mic Off** = `hero-mic-off`. The quote slot carries the helper line in attention states. Voice banners become inline actions on the status line (no layout push, D8).
8. **Approvals:** the "Confirm Command" card is the confirmation UI (`confirm-dialog`), pinned at the top of the right panel while anything is pending; on phones it is a bottom sheet with "Decide later". Every real request also shows one meta line under the buttons (tool, where, `Queue: N`, expiry): it is an existing feature (nothing removed) and a safety affordance (§10.2). The Approvals tab lists the waiting queue and the last 20 decisions; its badge is the pending count.
9. **Two personas, one design.** JARVIS = the reference blues; ANNA = the same layer stack in magenta/violet (§2.2). Data colours (CPU green, Memory blue, Disk amber, Devices violet, status colours, the amber confirm card) never follow the persona.
10. **The orb is the reference orb**, ported from the prototype with its fitted data (`orb_data.json`), driven by the X-74 `audioEngine` (agent level and bands, mic level). Ten voice states. One rAF loop that stops when nothing moves. Reduced motion draws one static frame.
11. **Mic off after sign-in and after Reconnect** (privacy, browser autoplay). The product lands in state 9 "Mic is off"; Start Listening gives the reference picture (state 10, "Listening for your command").
12. **Type:** Segoe UI first (the measured match; the owner runs Windows), plus three self-hosted Google Fonts via Fontsource: **Noto Sans** (UI on other systems and every medium-weight label), **Montserrat** (brand words), **JetBrains Mono** (code, terminal).
13. **Nothing is removed.** Every feature of `UI_AUDIT.md` has a home (§17, `mapping.md` F1–F113); every testid survives (inventory guard, X-69); an e2e spec changes only in the commit that moves its target (§18.3). The only retired element is the tablet ops-drawer backdrop (no drawer any more); its toggle id survives as an alias.
14. **No contract change.** "Windows 11" in the reference's activity row is not in `desktop_status` (§24).
15. **Gates are local** (Windows, Playwright project `gates`, new headless). CI keeps running the default project, unchanged. V-1 (§21.1) is the new reference diff; the rest of §21 carries the old gates over.

**Design discipline.** The owner pinned the visual direction, so this is a reproduction, not a reinterpretation. Where the reference is silent (Agents, Activity, Settings, dialogs, states, phone), reuse its own vocabulary: the card anatomy (icon, title, subtitle, optional chevron), tiles, coloured bars, timeline dots, the pill shapes. Spend boldness only on the orb. Keep the reference's two tracked uppercase lines (hero eyebrow, sidebar tagline) and its tracked quote exactly as drawn, and add **no** new tracked-caps labels, decorative numbering or ambient animation anywhere else. Motion answers state changes; only the orb moves on its own.

---

## 1. Porting the prototype (rules)

- **Look** (colours, gradients, hairlines, glows, fonts, radii) comes from the prototype's base layer (the first `<style>` of `final_neon.html`, `neon_base.css`) and `neon.css`. **Geometry** (sizes, positions, grids, breakpoints) comes **only** from `fluid.css` (`html.fluid`, `html.comp`, `html.dk/.tl/.ph` rules).
- **Do not port:** `html:not(.fluid)` artboard rules, `#sizer`, stage scaling (`fit()` transform), `?still`, `?layout`, `?proto`, the prototype controls (`#proto`, Alt+P), `layoutPills()` and `PILL_GEO` (artboard only), simulated data and audio (`seedData`, `Speech`, `Mic`, `demoVoice`, the orb click demo and its `title`), the `nominal` flag of the demo request (the product rule is §10.2), and `STILL` code paths (reduced motion replaces them).
- Replace the prototype's `html.dk/.tl/.ph` classes with media queries (`(min-width:1180px)`, `(min-width:1024px) and (max-width:1179.98px)`, `(max-width:1023.98px)`); `useLayout()` (one `matchMedia` listener) gives JS the same answer. Keep the container queries. The header's compact-label and greeting decisions depend on the header row width: a `ResizeObserver` on the header sets `data-compact` and `data-greet` (prototype `fit()`).
- Keep the prototype's DOM order, headings, roles and accessible names unless this spec says otherwise. React components replace its string templates; small stores on the X-96 subscription registry (voice state X-97, activity feed X-105, the existing hooks) replace its globals `S`, `D` and `V`.
- **CSS architecture:** `src/assets/styles/tokens.css` (§2) and `src/assets/styles/neon/{base,shell,overview,orb,panel,overlays,dialogs,screens,agents,activity,settings,phone}.css` (plain CSS ported from the prototype; keep its `n-` primitive class names; `overlays.css` holds the popovers, `screens.css` login, session check and offline), imported by `index.css` before `@tailwind utilities`. Tailwind stays for utility layout inside pages (theme §2.4). No CSS-in-JS. A legacy rule in `index.css` is deleted only when its component is gone.
- **Data:** `orb_data.json` becomes `src/features/voice/orb/orbData.ts` (typed `as const` export, values unchanged); `icons.svg` becomes the sprite of §4.8.
- **Strangler rule (implementation order, §23).** When a task replaces a container (TopBar, OperationsRail, AssistantCore, the ChatPanel tabs), every control of that container that has not reached its new home yet stays in a temporary *legacy strip* (`data-legacy`, plain styling) in the nearest new container. The task that moves the last control deletes the strip. After every task the e2e suite and the inventory check stay green. Nothing is deployed before X-95.

---

## 2. Design tokens

### 2.1 `src/assets/styles/tokens.css` (the only colour, type and shape source)

Values come from the prototype (fitted to the reference) and `forensics.md` §3/§14. `commandTheme.ts` and `tailwind.config.js` reference these variables; no other file defines a hex colour for UI chrome (canvas palettes are in §2.3).

```css
:root {
  color-scheme: dark;

  /* ---- canvas (never follows the persona) ---- */
  --bg: #060d1e;              --bg-top: #0a1023;          --bloom-rgb: 13,30,84;
  --sidebar: #0a1124;         --sidebar-divider: #1f2a48;

  /* ---- surfaces ---- */
  --card:      linear-gradient(180deg,#0e152d 0%,#0d1529 25%,#0b1326 50%,#0a1325 100%);
  --card-edge: linear-gradient(180deg,#17233f 0%,#13203a 40%,#101b33 100%);
  --panel:     linear-gradient(180deg,#10172f 0%,#0d1427 11%,#0b1224 17%,#0a1123 40%,#0a1123 100%);
  --panel-edge:linear-gradient(180deg,#232d47 0%,#18223a 35%,#111a30 100%);
  --tile: #151e34;            --tile-edge: linear-gradient(180deg,#1f2b45,#1d2840 50%,#1c263e);
  --track: #222b46;           --divider: #18213a;         --tabs-divider: #161e33;
  --field: #15203d;           --field-edge: #22304f;      --input-edge: #1b2744;
  --composer:      linear-gradient(180deg,#121b32 0%,#0f192f 30%,#0e172c 100%);
  --composer-edge: linear-gradient(180deg,#24314f 0%,#1d2742 40%,#131f36 100%);
  --code: #060c1c;            --code-edge: linear-gradient(#161e30,#151d2f);
  --ghost: #131b30;           --ghost-edge: #2b3450;      /* Deny, quiet buttons */
  --ghost-run: #121b34;       --ghost-run-edge: #232e4c;  /* Run Task */
  --ghost-mic: #18213b;       --ghost-mic-edge: #2a3450;  /* Mic Off */
  --pressed: #1d2a50;                                     /* Run Task / Mic Off pressed */
  --pill-system: #15233f;     --pill-devices: #132145;    --pill-voice: #111f43;
  --pill-edge: linear-gradient(180deg,#243765,#1c2749);
  --bubble-user: #192139;     --bubble-ai: #121b32;       --avatar-user: #36405c;
  --nav-active: linear-gradient(90deg,#16285b 0%,#162753 10%,#16244b 25%,#172347 45%,#172141 70%,#172141 100%);
  --nav-active-edge: linear-gradient(180deg,rgba(120,150,225,.22),rgba(110,135,205,.16) 60%,rgba(105,130,200,.2));
  --avatar: linear-gradient(129deg,#0b1a3f 0%,#0f2667 25%,#0e1c48 50%,#0d193e 75%,#080e1f 100%);
  --avatar-edge: linear-gradient(180deg,#213977,#28396d 50%,#314170);
  --warn-card: #151826;       --warn-card-edge: #3a2c29;  /* the only warm surface: Confirm Command */
  --warn-pill: #2e231e;       --warn-pill-edge: linear-gradient(180deg,#563d28,#4f392a);
  --scrim: rgba(3,6,16,.62);

  /* ---- text ---- */
  --t1: #ffffff;   --t-pill: #f2f5fa;  --t2: #c0cae2;  --t3: #a6b1d0;  --t4: #94a0be;  --t5: #8190b6;
  --t-nav: #9ba7cf;          --t-sub: #a6b1d9;          --t-brand-sub: #7682a4;     --t-tagline: #727fa8;
  --t-chat-user: #c0cae2;    --t-chat-ai: #a5b1d1;      --t-avatar-user: #c6d3f4;
  --t-eyebrow: #96a7d8;      --t-eyebrow-on: #92a3d4;   --t-role: #7a8bc4;          --t-quote: #95aadf;
  --t-tile-label: #909bb9;   --t-act-sub: #8794b4;      --t-time: #7484aa;
  --t-bubble-time: #7b8fb9;  --t-ai-time: #7387ae;      --t-placeholder: #94a4ca;   --t-hint: #8d9bb5;
  --t-code: #e6e7ec;         --t-cf-q: #a8b3cd;         --t-cf-pill: #d89a48;       --t-greet: #a7b0d2;

  /* ---- status (never follows the persona; always shape + word) ---- */
  --ok: #22c086;   --ok-t: #6fe0b6;   --warn: #e9ab4a;  --warn-t: #f3c27a;  --err: #f2555a;  --err-t: #ff9a9d;
  --info: #4c97fe; --info-t: #9cc6ff; --dev: #7b53f9;   --dev-t: #b7a6ff;   --neutral: #8895bc;
  --badge-red: #cf3740;      --shield: #f9c74c;         --shield-high: #ff6b70;     --sparkle: #f1a74d;

  /* ---- data series (never follow the persona) ---- */
  --cpu: #35b992;  --mem: #3891fc;    --disk: #f9bc54;  --devc: #8981f6;
  --ic-cpu: #4ce0bf; --ic-mem: #65b7fa; --ic-disk: #fbc551; --ic-dev: #bfbdf9; --ic-card: #80befd;

  /* ---- persona accent: JARVIS (sampled from the reference) ---- */
  --acc-fill: #2d66fc;  --acc-text: #83bafa;  --acc-icon: #63aafb;  --acc-line: #4ba8ff;  --acc-rule: #6290f2;
  --acc-author: #acc8f9; --acc-name: #dbe2fa; --acc-badge: #3a5fd2; --acc-deep: #163b80;  --acc-ring: #3a73c8;
  --acc-avatar: #74b9fd; --acc-pill-ic: #66b8fc; --tab-ic: #55b0fb;  --chip-task-t: #bcd6ff;
  --name-hi: #e4eafd;   --name-mid: #e0e6fb;   --name-lo: #d5ddf8;
  --acc-glow: 37,90,255;     --acc-icon-rgb: 99,170,251;   --acc-rule-rgb: 98,144,242;   --acc-edge-rgb: 75,168,255;
  --btn-ring: 80,150,255;    --btn-inset: 140,180,255;
  --btn-edge1: 160,215,255;  --btn-edge2: 120,180,255;     --btn-edge3: 90,140,255;
  --fl-spark: #bfe6ff;  --fl-arc: #6fb6ff;  --fl-arc2: #4fb4ff;           /* hero flare SVG */
  --hg1: 27,37,111; --hg2: 24,69,162; --hg3: 16,47,153; --hg4: 20,32,89; --hg5: 12,17,34;   /* hero lights */
  --hgt: 14,21,49;  --hgm: 10,18,45;  --hgb: 10,20,49;                                       /* hero base */
  --acc-grad:    linear-gradient(159deg,#2aaffc 0%,#3b82fd 25%,#2757fc 50%,#254af8 75%,#285bff 100%); /* Start Listening */
  --acc-approve: radial-gradient(65% 126% at 50% 66%,#2050fb 0%,#265dfc 43%,#3185fd 100%);           /* Approve & Run */
  --acc-btn:     linear-gradient(159deg,#2a66f2 0%,#2559f0 45%,#2348e3 100%);                         /* every other primary */
  --acc-tint: rgba(37,90,255,.14);   --acc-edge: rgba(75,168,255,.5);

  /* ---- type (§3) ---- */
  --font-ui:    "Segoe UI","Noto Sans Variable","Noto Sans",system-ui,-apple-system,sans-serif;
  --font-med:   "Noto Sans Variable","Noto Sans","Segoe UI",system-ui,sans-serif;   /* weights 500–560 */
  --font-brand: "Montserrat Variable","Montserrat","Segoe UI",sans-serif;
  --font-mono:  "JetBrains Mono Variable","JetBrains Mono","Source Code Pro",Consolas,monospace;

  /* ---- shape (§4.1) ---- */
  --r-hero: 15px; --r-panel: 15px; --r-card: 13px; --r-card-page: 14px; --r-tile: 13px; --r-pill: 14px;
  --r-btn-hero: 13px; --r-btn: 12px; --r-code: 12px; --r-bubble: 14px; --r-composer: 14px; --r-confirm: 14px;
  --r-input: 11px; --r-nav: 11px; --r-chip: 999px;

  /* ---- glow and depth (§4.4) ---- */
  --glow-primary: 0 6px 22px rgba(var(--acc-glow),.45);
  --glow-underline: 0 0 10px 1px rgba(var(--acc-edge-rgb),.45);
  --glow-ok: 0 0 6px rgba(34,192,134,.45);
  --hairline-card: 0 0 0 1px rgba(21,33,57,.5);    --hairline-tile: 0 0 0 1px rgba(27,41,65,.4);
  --hairline-hero: 0 0 0 1px rgba(45,58,112,.42);  --hairline-composer: 0 0 0 1px rgba(35,48,78,.55);
  --shadow-pop: 0 18px 50px rgba(0,0,0,.6);  --shadow-dlg: 0 30px 80px rgba(0,0,0,.6);  --shadow-toast: 0 16px 40px rgba(0,0,0,.55);

  /* ---- motion (§4.7) ---- */
  --ease-out: cubic-bezier(.2,.8,.2,1);  --ease-std: cubic-bezier(.2,.7,.2,1);

  /* ---- layers ---- */
  --z-header: 30; --z-pop: 72; --z-scrim: 80; --z-sheet: 95; --z-dialog: 95; --z-lightbox: 110; --z-toast: 120; --z-screen: 200; --z-window: 500;
}
```

### 2.2 ANNA: the same keys under `html[data-persona="anna"]`

`data-persona` sits on `<html>` and comes from the server-synced setting (`settings_sync`, optimistic while pending), never local-only. Every accent is a variable, so ANNA is never half themed (D13). ANNA values were matched to JARVIS in luminance and darkened where white labels must keep 4.5:1.

| Token | Use | JARVIS (reference) | ANNA |
|---|---|---|---|
| `--acc-fill` | switches on, pressed fills, focus fills | `#2d66fc` | `#9a2bc8` |
| `--acc-text` | status line, links, chevrons, inline actions | `#83bafa` | `#eba6f6` |
| `--acc-icon` / `--acc-icon-rgb` | active nav icon + its glow | `#63aafb` / `99,170,251` | `#e483f4` / `228,131,244` |
| `--acc-line` / `--acc-edge-rgb` | tab underline, focus ring, active nav bar, rule ends | `#4ba8ff` / `75,168,255` | `#d35ce0` / `211,92,224` |
| `--acc-rule` / `--acc-rule-rgb` | accent rule under "AI Assistant" | `#6290f2` / `98,144,242` | `#d77bf0` / `215,123,240` |
| `--acc-author` | author name in chat bubbles | `#acc8f9` | `#e9b7f7` |
| `--acc-name`, `--name-hi/-mid/-lo` | hero name sheen | `#dbe2fa`, `#e4eafd/#e0e6fb/#d5ddf8` | `#f4ddf7`, `#f9eafb/#f6e2f8/#edd3f2` |
| `--acc-badge` | Approvals count | `#3a5fd2` | `#9631c9` |
| `--acc-deep` | send square | `#163b80` | `#561c7a` |
| `--acc-ring` | assistant avatar ring | `#3a73c8` | `#b54fd6` |
| `--acc-avatar` | header avatar initial | `#74b9fd` | `#eba6f6` |
| `--acc-pill-ic`, `--tab-ic` | header pill icons, active tab icon | `#66b8fc`, `#55b0fb` | `#e48af4`, `#e483f4` |
| `--acc-glow` | glows (rgb) | `37,90,255` | `190,60,240` |
| `--btn-ring/-inset/-edge1..3` | Start Listening edge lights | `80,150,255` / `140,180,255` / `160,215,255`, `120,180,255`, `90,140,255` | `214,120,255` / `240,170,255` / `250,200,255`, `232,150,255`, `200,110,250` |
| `--chip-task-t` | "Task mode" chip text | `#bcd6ff` | `#f2cdf9` |
| `--fl-spark/-arc/-arc2` | hero flare | `#bfe6ff/#6fb6ff/#4fb4ff` | `#f8cdff/#e08af2/#d470ee` |
| `--hg1..5`, `--hgt/-hgm/-hgb` | hero lights and base | as §2.1 | `58,24,104`, `118,34,156`, `96,22,140`, `44,20,82`, `20,12,34`; `24,16,50`, `18,13,45`, `20,14,49` |
| `--acc-grad` | Start Listening | as §2.1 | `linear-gradient(159deg,#a535d6 0%,#9a2fcb 25%,#9126c4 50%,#8320b8 75%,#8c27bd 100%)` |
| `--acc-approve` | Approve & Run | as §2.1 | `radial-gradient(65% 126% at 50% 66%,#7d1fb4 0%,#8b25c0 43%,#9a2fcb 100%)` |
| `--acc-btn` | other primaries (Sign in, Re-enroll, Review…) | as §2.1 | `linear-gradient(159deg,#a535d6 0%,#9328c8 50%,#8120b6 100%)` |
| `--acc-tint`, `--acc-edge` | selected chips, segmented, accent pills | `rgba(37,90,255,.14)`, `rgba(75,168,255,.5)` | `rgba(190,60,240,.14)`, `rgba(211,92,224,.55)` |

**What follows the persona:** orb palette, particles, glows; hero name; status-line accent; accent rule; waveform; Start Listening, Approve & Run and other primaries; active nav bar, border and icon glow; tab underline; Approvals badge; send square; assistant avatar ring; links and chevrons; focus ring; composer placeholder ("Message ANNA..."); typing text; helper copy gender (raha/rahi, ga/gi).
**What never does:** page navy, cards, borders, text greys; the sidebar logo ring (it carries both personas); Quick Stats colours; status colours; timeline dot colours; the amber confirm card; **past chat replies keep their own persona name and colour.**

### 2.3 Canvas palettes (`src/features/voice/orb/palette.ts`)

Canvas layers cannot read CSS variables per frame, so they use constants from `orbData.ts`:

- **Orb:** the fitted JARVIS colours (`OP`, `RINGS`, `PARTICLES`, `SWIRL`). ANNA colours are derived per colour with `annaMap(c) = [c₀ + .45c₁ + .75c₂, .2c₀ + .6c₁ + .1c₂, .5c₀ + .55c₁ + c₂]` (clamped to 0–255) and mixed by `palMix` (0 = JARVIS, 1 = ANNA). Grey looks mix toward `[.84l, .9l, 1.08l]` with `l = .3r + .59g + .11b`, by `greyMix`. Both mixes ease with `k = 1 − e^(−4.2·dt)` (grey ×1.4) and snap within .01.
- **Logo ring:** `LOGO` (12 conic stops sampled from the reference every 30°) plus `LOGO_IN`, `LOGO_OUT`, `LOGO_EDGE` bands. Identical in both personas.
- **Mini orbs (Agents › Who answers):** `MINI.jarvis` glow `37,90,255`, body `#dffafe #47acfc #1438a1 #0a1544`, rim `#7fd0ff → #553bf8`; `MINI.anna` glow `190,60,240`, body `#ffe6fb #e070f0 #8a1fb8 #2a0d44`, rim `#f6a6ff → #7b53fa`.
- **Waveform:** the 30 measured bars of `WAVE` (`#3d4ca8` at the left edge, `#63e0ff` near the centre, `#122249` at the right edge). ANNA via `annaMap`; listening mixes 65 % toward `#ebf2ff`; flat states toward slate `#5a6482`. Colours are cached per (palette mix, mode); no colour parsing per frame.

### 2.4 Tailwind 3.4 theme extension (`tailwind.config.js`)

`<alpha-value>` cannot work with variables that hold full colours (Tailwind 3 needs channel-only variables for opacity modifiers), so colours are plain `var()` and alpha shades are named tokens. **Never** put a `/NN` opacity modifier on these colours (Tailwind 3 silently drops the class). Before switching `command.*` to variables, replace every `command-[a-z]+/[0-9]` hit.

```js
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    screens: { sm: '390px', md: '768px', tl: '1024px', dk: '1180px', xl: '1440px', '2xl': '1672px' },
    extend: {
      colors: {
        bg: 'var(--bg)', sidebar: 'var(--sidebar)', tile: 'var(--tile)', track: 'var(--track)',
        divider: 'var(--divider)', field: 'var(--field)', code: 'var(--code)', ghost: 'var(--ghost)',
        bubble: { user: 'var(--bubble-user)', ai: 'var(--bubble-ai)' },
        ink: { 1: 'var(--t1)', 2: 'var(--t2)', 3: 'var(--t3)', 4: 'var(--t4)', 5: 'var(--t5)',
               nav: 'var(--t-nav)', sub: 'var(--t-sub)', time: 'var(--t-time)', hint: 'var(--t-hint)' },
        acc: { DEFAULT: 'var(--acc-fill)', text: 'var(--acc-text)', icon: 'var(--acc-icon)', line: 'var(--acc-line)',
               rule: 'var(--acc-rule)', author: 'var(--acc-author)', badge: 'var(--acc-badge)', deep: 'var(--acc-deep)',
               tint: 'var(--acc-tint)', edge: 'var(--acc-edge)' },
        ok: { DEFAULT: 'var(--ok)', t: 'var(--ok-t)' }, warn: { DEFAULT: 'var(--warn)', t: 'var(--warn-t)' },
        err: { DEFAULT: 'var(--err)', t: 'var(--err-t)' }, info: { DEFAULT: 'var(--info)', t: 'var(--info-t)' },
        dev: { DEFAULT: 'var(--dev)', t: 'var(--dev-t)' }, neutral: 'var(--neutral)',
        series: { cpu: 'var(--cpu)', mem: 'var(--mem)', disk: 'var(--disk)', dev: 'var(--devc)' },
        // migration aliases: keep until every usage has moved; never with /NN
        command: { bg: 'var(--bg)', panel: 'var(--bubble-ai)', soft: 'var(--tile)', border: 'var(--divider)',
                   text: 'var(--t1)', muted: 'var(--t4)', accent: 'var(--acc-fill)', blue: 'var(--acc-line)',
                   danger: 'var(--err)', warning: 'var(--warn)' },
      },
      backgroundImage: { card: 'var(--card)', panel: 'var(--panel)', composer: 'var(--composer)',
        'acc-grad': 'var(--acc-grad)', 'acc-btn': 'var(--acc-btn)', 'acc-approve': 'var(--acc-approve)',
        'nav-active': 'var(--nav-active)', avatar: 'var(--avatar)' },
      fontFamily: { ui: 'var(--font-ui)', med: 'var(--font-med)', brand: 'var(--font-brand)', mono: 'var(--font-mono)' },
      fontSize: {   // roles of §3.2 at their reference (maximum) size
        tagline: ['11.5px', { lineHeight: '16px', letterSpacing: '4px' }], xs12: ['12px', '16px'],
        hint: ['13px', '18px'], meta: ['13.5px', '19px'], small: ['14.25px', '20px'], body: ['15px', '20px'],
        chat: ['16.25px', '24px'], 'card-title': ['16.5px', '22px'], tab: ['17px', '22px'], status: ['20.25px', '28px'],
        role: ['26.25px', '32px'], 'page-title': ['31.5px', '40px'], 'hero-name': ['65.5px', '72px'] },
      borderRadius: { hero: '15px', panel: '15px', card: '13px', 'card-page': '14px', tile: '13px', pill: '14px',
        'btn-hero': '13px', btn: '12px', code: '12px', bubble: '14px', composer: '14px', input: '11px', nav: '11px' },
      boxShadow: { 'glow-primary': 'var(--glow-primary)', 'glow-underline': 'var(--glow-underline)', 'glow-ok': 'var(--glow-ok)',
        'hair-card': 'var(--hairline-card)', 'hair-tile': 'var(--hairline-tile)', pop: 'var(--shadow-pop)',
        dlg: 'var(--shadow-dlg)', toast: 'var(--shadow-toast)' },
      zIndex: { header: '30', pop: '72', scrim: '80', sheet: '95', dialog: '95', lightbox: '110', toast: '120', screen: '200', window: '500' },
      transitionTimingFunction: { out: 'cubic-bezier(.2,.8,.2,1)', std: 'cubic-bezier(.2,.7,.2,1)' },
    },
  },
  plugins: [],
};
```

Delete the dead config: families `orbitron`, `outfit`, `rajdhani`; shadows `hardware`, `neon-cyan`, `neon-amber`; animations `pulse-slow`, `spin-slow`, `scan`.

### 2.5 Contrast (WCAG 2.x, computed on the real backgrounds)

| Pair | Ratio | Pair | Ratio |
|---|---|---|---|
| nav `#9ba7cf` on sidebar | 7.9 | tagline `#727fa8` on sidebar | 4.7 (the reference's `#5c6a94` is 3.5, §22) |
| brand sub `#7682a4` on sidebar | 4.9 | page subtitle `#a6b1d9` on `#0a1023` | 8.9 |
| hero role `#7a8bc4` / quote `#95aadf` / eyebrow `#92a3d4` on hero | 5.5 / 8.0 / 7.4 | status JARVIS `#83bafa` / ANNA `#eba6f6` on hero | 9.1 / 9.9 |
| tile label `#909bb9` on tile | 6.0 | activity sub `#8794b4` / time `#7484aa` on card | 6.0 / 4.9 |
| chat user `#c0cae2` / AI `#a5b1d1` on bubbles | 9.7 / 8.0 | bubble times `#7b8fb9` / `#7387ae` | 4.9 / 4.7 |
| author JARVIS `#acc8f9` / ANNA `#e9b7f7` on AI bubble | 10.1 / 10.2 | placeholder `#94a4ca` on input | 6.5 |
| confirm question `#a8b3cd` / pill `#d89a48` | 8.4 / 6.3 | code `#e6e7ec` on `#060c1c` | 15.8 |
| white on `--acc-btn` (J start / mid / end) | 4.9 / 5.6 / 6.8 | white on `--acc-grad` at the label (J `#2757fc`, A `#9126c4`) | 5.5 / 6.4 |
| white on Approve (J `#265dfc`, A `#8b25c0`) | 5.2 / 6.7 | white on badge (J / A) | 5.6 / 5.9 |
| `--ok-t` / `--warn-t` / `--err-t` / `--info-t` on card | 11.3 / 11.2 / 9.0 / 10.4 | `--err` `#f2555a` on card (non-text shapes) | 5.4 |

`--acc-grad` starts at `#2aaffc` (2.4:1 with white), but at 159° the gradient runs mostly downward and the label sits over the 33–76 % band (`#3574fc` → `#2757fc` → `#254af8`); measured on the rendered pixels (5th percentile of the glyph pixels, the prototype's contrast suite) every label passes 4.5:1. X-94 re-measures it the same way. Rules: `--t5` and darker never carry text under 13 px; persona accents are never small text on `--acc-tint`.

---

## 3. Typography

### 3.1 Families and loading

| Role | Stack (token) | Why |
|---|---|---|
| UI text (body, labels, chat, pills, nav) | `--font-ui`: **Segoe UI** → Noto Sans Variable | Of 28 sans families tested against the reference glyphs, Segoe UI matches best (word NCC .726; the best Google font scores .661). The owner runs Windows (Chrome, Electron), so it is exact there and needs no download. Phones and other systems get **Noto Sans** (same designer lineage, humanist shapes). |
| Medium labels (500–560): hero buttons, confirm buttons, active nav item, active tab, card and dialog titles, Recent Activity titles, the hero status line | `--font-med`: **Noto Sans Variable** | The reference draws these one step lighter than Segoe UI Semibold; Segoe UI on Windows 10 has no 500. |
| Brand words: "JARVIS & ANNA" wordmark (600), hero name (700), persona names on Agents (700) | `--font-brand`: **Montserrat Variable** | Wide geometric caps; word NCC .85 on "JARVIS", .83 on the wordmark (Segoe UI .33). |
| Code, terminal, tool names, args | `--font-mono`: **JetBrains Mono Variable** | Matches the confirm code block (15 px, 8.92 px advance). |

```ts
// src/main.tsx (first lines)
import '@fontsource-variable/noto-sans';        // "Noto Sans Variable", wght 100–900
import '@fontsource-variable/montserrat';       // "Montserrat Variable"
import '@fontsource-variable/jetbrains-mono';   // "JetBrains Mono Variable"
import './assets/styles/index.css';             // imports tokens.css first
```

- Delete the Google Fonts `@import` at `index.css:1` (render-blocking, needs a CSP allowance, fails in Electron offline). Fontsource ships the files in the bundle (`font-display: swap`, unicode-range subsets), so Electron `file://` and the planned CSP (C-109) work. Log the three packages in `done_highlight.md` (X-69).
- Gates wait for `document.fonts.ready` before any screenshot or measurement.

### 3.2 Scale (reference size = maximum; fluid floors per `fluid.css`)

`u` = one hero pixel (§7.1), `vu`/`hu` = one reference pixel of the window (§5.1). Every size is `clamp(floor, fluid, reference)`.

| Role | Family / weight | Size at 1672×941 (floor) | Line height | Letter-spacing | Colour |
|---|---|---|---|---|---|
| Sidebar wordmark | brand 600 | 21 (17) | 1.24 | −0.3 px | `--t1` |
| Sidebar subtitle | ui 400 | 14.5 (12.5) | 1.38 | .3 px | `--t-brand-sub` |
| Nav label / active | ui 400 / med 500 | 17.5 / 16.75 (15 / 14.5) | 24 px | 0 | `--t-nav` / `--t1` |
| Sidebar tagline (uppercase, as drawn) | ui 600 | 11.5 (fixed) | 16 px | 4 px (2.6) | `--t-tagline` |
| Page title | ui 700 | 31.5 (23; tl 26; ph 21) | 1.27 | 0 | `--t1` |
| Page subtitle | ui 400 | 16.5 (13.5; hidden tl/ph) | 1.333 | .06 px | `--t-sub` |
| Header pill label | ui 400 | 15.25 (13.5; tl 14; ph 13) | 20 px | 0 | `--t-pill` |
| Greeting / name | ui 400 / ui 600 | 16 / 17 (13.5 / 14) | 1.375 / 1.3 | 0 | `--t-greet` / `--t1` |
| Header avatar initial | ui 700 | 19 (15) | 1 | 0 | `--acc-avatar` |
| Hero eyebrow (uppercase, as drawn) | ui 400 | 14.25u (12) | 20u (16) | 4.3u (2.4 px) | `--t-eyebrow` |
| Hero name | brand 700 | 65.5u (30; ph 44) | 72u (34) | −.5u | sheen gradient (§7.3) |
| Hero role "AI Assistant" | ui 400 | 26.25u (14.5) | 32u (19) | 0 | `--t-role` |
| Hero status line | med 600 | 20.25u (13.5) | 28u (19) | 0 | `--acc-text` |
| Hero buttons | med 500 | 14.875–15.125u (13) | 20 px | 0 | `--t1` |
| Hero quote | ui 400 | 14.25u (12) | 20u (16) | 1.6u (.8 px) | `--t-quote` |
| Card title (Quick Stats / Recent Activity) | med 560 | 14.875 / 17.25 (14 / 14.5) | 22 px | 1.2 px / 0 | `--t1` |
| Card subtitle | ui 400 | 14.75 / 14.5 (13) | 20 px | 0 | `#94a0be` / `#92a1c2` |
| Tile value / label | ui 600 / ui 400 | 17.5–18.5 / 12.5–13.75 (15 / 12) | 22 / 18 px | 0 | `--t1` / `--t-tile-label` |
| Activity row title / sub / time | med 500 / ui 400 / ui 400 | 14.25 / 14.25 / 14 (13.5 / 13 / 12.5) | 20 px | 0 | `#f4f6f9` / `--t-act-sub` / `--t-time` |
| Panel tab / active | ui 400 / med 550 | 17 / 15.875 (15 / 14.5) | 22 px | 0 | `#9fa9c7` / `--t1` |
| Tab badge | ui 700 | 12.5 | 26 px circle | 0 | white |
| Confirm title / pill / question | med 560 / ui 500 / ui 400 | 16.125 / 12.25 / 16.25 | 22 / 18 / 24 px | 0 | `--t1` / `--t-cf-pill` / `--t-cf-q` |
| Confirm code | mono 400 | 15 | 24 px | −.07 px | `--t-code` |
| Confirm buttons | med 500 | 16 | 20 px | 0 | white |
| Chat text / author / time | ui 400 / ui 700 / ui 400 | 16.25 / 14 / 13.75–14 | 24 / 20 / 18–20 px | 0 / .3 px / 0 | `--t-chat-*` / `--acc-author` / bubble times |
| Composer placeholder / hints | ui 400 | 16 / 13 | 24 / 18 px | 0 | `--t-placeholder` / `--t-hint` |
| Page card title / subtitle (Agents, Activity, Settings) | ui 700 / ui 400 | 16.5 / 14.25 | 22 / 20 px | .2 px / 0 | `--t1` / `--t4` |
| Row label / small / key-value | ui 600 / ui 400 / ui 400 | 15 / 13.5 / 14.5 | 20 / 19 / 20 px | 0 | `#f4f6f9` / `--t4` / `--t4` + `#e6ebf7` |
| Buttons (`n-btn`, sm, xs) / pills (`n-pill`) / chips | ui 600 / ui 600 / ui 500 | 15, 14, 13 / 12.5 / 13.5 | 1 | 0 | per variant |
| Terminal and tool rows | mono 400 | 13 | 21 px | 0 | `--t2`, level colours |

### 3.3 Rules

- Sentence case everywhere except the reference's own uppercase lines (eyebrow, tagline), the persona names (proper nouns) and log levels (INFO, WARN, ERROR).
- **No visible text under 12 px.** The decorative sidebar tagline (11.5 px) is the only exception.
- `font-variant-numeric: tabular-nums` for vitals, percentages, times, countdowns, counts.
- Roman Urdu copy carries `lang="ur-Latn"` (helper line, typing row, Roman Urdu toasts, notes, Settings hints, restart notice).
- `text-wrap: pretty` on long copy (alerts, chat, notes, hints); single-line slots end in an ellipsis and keep the full text in `title`.

---

## 4. Shape, spacing, borders, glows, layers, motion, icons

### 4.1 Radii (measured)

| Element | Radius | Element | Radius |
|---|---|---|---|
| Hero card, right panel | 15 | Quick Stats, Recent Activity | 13 |
| Page cards (`n-card`), confirm card, composer, bubbles, header pills, popovers | 14 | Tiles | 13 |
| Hero buttons | 13 | Deny / Approve, `n-btn`, code block, send square | 12 |
| Nav item, input | 11 | "Requires approval" pill, chips, badges, progress bars, status pills | full |
| Dialogs, sheets (top corners on phones) | 16 / 20 | Tooltips | 8 |

### 4.2 Spacing (reference pixels; fluid rules scale them)

- **Page:** sidebar 272 incl. its 1 px divider; sidebar → content 23; content → panel 18; panel → window edge 17; hero → cards 16; Quick Stats → Recent Activity 15; bottom margin 27–29; header band 114.
- **Hero (830×474):** orb centre (239, 203); text column x 447; button row y 360 (card-relative), gaps 15; quote centre x 399.5, top 437.
- **Card header:** icon 26 px at (22, 25); title x 66 (Recent Activity 65); title baseline to subtitle baseline 22–23; chevron 28 px button 19 px from the right.
- **Tiles:** 180×91 (first row), 180×95 (second row); gap 15 × 14; inner padding 16–18; icon → value 25–27; bar inset 16 left / 17 right, 13–15 above the bottom, 9 high.
- **Activity rows:** pitch 54 (first 53); dot centre x 29.5; text x 62; time 20 from the right.
- **Right panel:** tabs band 67; confirm inset 19/17, padding 21/22; avatar → bubble 13; bubble padding 13–16 / 19–20; bubble gap 23; composer inset 15/16, input 450×58, send 54×54.
- **Page cards:** padding 20 / 22 / 22; header grid 26 px icon, 18 px gap; card gap 16; group label 14.5 px with a fading rule.

### 4.3 Hairline borders, lit from the top

Every container border is 1 px and brighter at the top (~1.3×). It is a gradient painted on a pseudo-element so boxes keep their size:

```css
.edge::before { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: var(--bw, 1px); pointer-events: none;
  background: var(--edge); -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude; }
```

Each surface sets `--edge` (`--card-edge`, `--tile-edge`, `--panel-edge`, `--composer-edge`, `--avatar-edge`, `--nav-active-edge`, `--warn-pill-edge`) and adds its soft outer half as `box-shadow: var(--hairline-*)`. The hero edge is brightest at its two top corners: `radial-gradient(170px 110px at 0 0, rgba(80,96,210,.9), transparent), radial-gradient(170px 110px at 100% 0, rgba(40,96,200,.9), transparent), linear-gradient(180deg,#212c4d,#1c2a50 50%,#182749)`. The reference's 1 px dark halos around bright edges are image sharpening: do not draw them.

### 4.4 Glows and depth

- **No drop shadows on cards.** Depth comes from the lit hairlines, faint vertical surface gradients, the hero's fitted lights and the page blooms.
- Glows only where the reference has them: Start Listening (`--glow-primary` while pressed, softer `-2px 4px 12px rgba(var(--acc-glow),.18)` while not), active tab underline (`--glow-underline`), active nav icon (`drop-shadow(0 0 5px rgba(var(--acc-icon-rgb),.35))`), active nav bar (`0 0 5px 1px rgba(var(--acc-glow),.55)`), the green status dot (`--glow-ok`), timeline dots (blue/violet `0 0 8px` at .5; green faint; grey none; 2 px ring of card colour), progress fills (`0 0 8px` of the fill at .35–.55), assistant avatar ring (`0 0 10px rgba(var(--acc-glow),.45)`), the orb.
- Popovers, dialogs, sheets and toasts use `--shadow-pop/-dlg/-toast`. Glass blur only on scrims (`blur(4px)`) and the login card.

### 4.5 Surface recipes (fitted; copy them as written)

- **Page (dk/tl):** `radial-gradient(30.92% 18.38% at 53.65% 2.87%, rgba(13,30,84,.414) 0%, …0), radial-gradient(20.87% 7.76% at 41.93% 11.58%, rgba(12,28,69,.578)…), radial-gradient(7.36% 34.54% at 19.14% 34.75%, rgba(18,24,71,.141)…), radial-gradient(23.98% 9.78% at 81.58% .74%, rgba(9,25,62,.43)…), linear-gradient(180deg, rgb(10,16,35) 0%, rgb(6,13,32) 31.88%, rgb(6,13,30) 100%)` (full stop lists in `fluid.css`, "page background").
- **Page (ph):** `radial-gradient(90vw 40vh at 60% -8%, rgba(13,30,84,.5), transparent 70%), radial-gradient(50vw 40vh at 0% 100%, rgba(21,18,67,.45), transparent 70%), linear-gradient(180deg,#0a1023 0,#060d20 320px,#060d1e 100%)`.
- **Sidebar:** three fitted gradients (`fluid.css`, `html.dk .sidebar`) + the silk-wave SVG (§6.1) + 1 px `--sidebar-divider` on the right.
- **Hero:** six fitted layers using `--hg1..5` and `--hgt/-hgm/-hgb` in % of the card (`fluid.css`, `html.comp .hero`): top-left violet bloom, top-right blue flare, left-edge cyan glow, lower band, a dark vignette behind the text, the base gradient. Plus the flare SVG (one 2 px spark and two thin arcs in `--fl-*`) and the hero edge (§4.3). Phones use the three-layer version (`html.ph .hero`).
- **Cards** `--card` + `--card-edge`; **tiles** `--tile` + `--tile-edge`; **panel** `--panel` + `--panel-edge`; **composer** `--composer` + `--composer-edge`; **active nav** `--nav-active` + `--nav-active-edge` + the 2 px left bar (inset 3 px, `--acc-line`); **avatar** `--avatar` + `--avatar-edge`.

### 4.6 Layers

Header 30 · popovers 72 · scrim 80 · sheet and dialogs 95 · lightbox 110 · toasts 120 · full screens 200 · Electron window buttons 500.

### 4.7 Motion

Only `transform` and `opacity` animate (the canvas is the exception). framer-motion is used only for popovers, dialogs, the sheet, toasts and message/row entrances, inside `<MotionConfig reducedMotion="user">`.

| What | Duration and easing |
|---|---|
| Persona switch | accents switch at once; orb palette eases `1 − e^(−4.2·dt)`; hero name rolls out 220 ms (ease-in, −14 px) and in 320 ms (`--ease-out`, +14 px); View Transition circle reveal from the orb centre, 760 ms `cubic-bezier(.2,.7,.1,1)` where supported (§15) |
| Popovers | 150 ms opacity, 200 ms transform (drop 4 px) |
| Dialogs / sheet | 200 ms opacity, 250–300 ms transform (rise 10 px; the sheet slides 40 px from the bottom) |
| Toasts | 300 ms in / 200 ms out, 8 px |
| Confirm card arrival | 320 ms, from −8 px, `--ease-out` |
| New activity or chat row | 500 ms `--ease-out` (fade + 6 px) |
| Bars (tiles, machines, Voice-ID, jobs, enrollment) | 600 ms `--ease-out` on `transform: scaleX()` (`transform-origin: left`); never `width` |
| Tab underline | 250 ms `transform` |
| Hold to approve | 1000 ms linear fill (`scaleX`) |
| Orb and waveform | §8 |

**Reduced motion:** no rAF loop (one static orb frame per state, persona or size), no name roll, no View Transition, CSS animations at `.001ms`, the Voice pill has no ping, the state stays readable by word and shape.

### 4.8 Icons

- Port `icons.svg` (75 `<symbol id="i-…">`, `viewBox 0 0 24 24`) to `src/shared/ui/icons/Sprite.tsx`, mounted once in `App`, and `<Icon name="…" className filled? />` that renders `<svg aria-hidden="true"><use href="#i-name"/></svg>`.
- Base style: `fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round`; filled icons add `.fill { fill: currentColor; stroke: none }`. 24 symbols were fitted to the reference ink boxes (`forensics.md` §9): filled `house`, `mic-fill`, `zap-fill`, `send`, `sparkle`; outline `users`, `activity`, `settings`, `monitor`, `mic-off`, `chart`, `cpu`, `memory`, `disk`, `laptop`, `clock`, `msg`, `box`, `shield`, `paperclip`, `chev-r`, `chev-d`, `clip`, `x`. Sizes as measured: nav 24, tab 20–23.6, card 26, tile 26–29, hint 16–18.
- `lucide-react` stays for icons the reference does not show; never mix both for the same meaning.

---

## 5. Layout system

### 5.1 Units

```css
:root { --vu: min(1px, calc(100vh / 941)); --hu: min(1px, calc(100vw / 1672)); }
```

At 1672×941 every `clamp()` sits on its reference value; below it the layout shrinks without transform scaling, and text floors (12–13 px, tagline 11.5) stop it. Above it the reference sizes hold and the extra space goes to the main column and the chat list.

### 5.2 Breakpoints and classes

| Layout | Window | Shell |
|---|---|---|
| **dk** | ≥ 1180 px | Sidebar, header, main, persistent right panel (§5.3) |
| **tl** | 1024–1179 px | 76 px icon rail, compact header, main, right panel `clamp(340px, 33vw, 372px)` |
| **ph** | < 1024 px | Compact header + pill row, one main column, 5-tab bottom bar (§6.6) |
| Electron | `window.electronAPI?.isElectron` → `.is-electron` on `<html>` | Header reserve and window buttons (§5.7) |

Container queries inside cards and pages (`container-type: inline-size`): card grids switch at 719 / 720 / 860 / 900 px; Quick Stats labels shorten below 400 / 290 px; Recent Activity subtitle shrinks below 340 px; the panel hint shortens below 390 px and hides below 366 px; the confirm title reads "Confirm" below 430 px; bubbles reflow below 480 px.

### 5.3 dk grid

```css
.app      { display:grid; height:100dvh; overflow:hidden;
            grid-template-columns: var(--sw) minmax(0,1fr) calc(var(--pw) + var(--gr));
            grid-template-rows: var(--hh) minmax(0,1fr);
            grid-template-areas: "side head head" "side main panel"; }
.app      { --sw: clamp(220px,16.27vw,272px); --pw: clamp(360px,30.63vw,512px);
            --hh: clamp(66px,12.115vh,114px);
            --gl: clamp(16px,1.376vw,23px);   /* sidebar → content */
            --gm: clamp(14px,1.077vw,18px);   /* content → panel */
            --gr: clamp(12px,1.017vw,17px);   /* panel → window edge */
            --gb: clamp(14px,2.87vh,27px); }  /* bottom margin */
```

Measured column widths (sidebar / main / panel): 1920: 272 / 1078 / 512 · 1672: 272 / 830 / 512 · 1536: 250 / 762 / 470 · 1366: 222 / 678 / 418 · 1280: 220 / 623 / 392 · 1200: 220 / 570 / 368.

- **Main column, Overview:** grid `minmax(0,411fr) minmax(0,404fr)` columns, rows `auto minmax(150px,1fr)`; hero spans both columns; column gap `15 * hu`, row gap `16 * vu`; margin `2vu var(--gm) 0 var(--gl)`; bottom padding `29 * vu`; it scrolls inside itself when the window is too short (never the page).
- **Main column, other pages:** one scrolling column with the same margins, `container-type: inline-size`.
- **Right panel:** `margin: 0 var(--gr) var(--gb) 0`, a flex column (§10.1).

### 5.4 tl (1024–1179 px)

Icon rail 76 px (logo 64, nav buttons 56×56 with tooltips; wordmark, wave and tagline hidden), header without subtitle and greeting (avatar only), pills compact when the header row is < 760 px, the main column and the right panel as dk. Quick Stats and Recent Activity stack when the main column is narrow (container rules), Activity › Logs uses the Logs / Reasoning tabs.

### 5.5 ph (< 1024 px)

One column: header (logo 30 px, page title, avatar 38 px; the three pills on a second row), the active page, the tab bar (64 px + safe area). The right panel becomes the `#/chat` page. Details §6.6, §10.9.

### 5.6 Scrolling and mounting

- `html, body` never scroll; every page and the chat list scroll inside their own column. Nothing collapses to 1 px (D4).
- All four pages stay mounted and inactive ones are hidden with `hidden` + `inert` (the camera, voice and form state never restart). Inside Activity only the active sub-tab body renders (the state lives in the stores), which also keeps strict e2e texts unique (§18.2).
- On phones, hidden zones also get `content-visibility: hidden` (the camera keeps running across tabs: the `getUserMedia` count test).

### 5.7 Electron

`.is-electron`: the header reserves 140 px + 12 px at the right in dk (150 px in tl, a 132 px margin after the avatar on phones); `WindowControls` (`window-minimize`, `window-maximize`, `window-close`) are `position: fixed` at the window's top-right (z 500) inside that reserve; the header is the drag region (`-webkit-app-region: drag`) and every control in it is `no-drag`. Default window 1200×800 = dk with compact pills. Fixes D6.

### 5.8 Viewport sizes the gates use

| Group | Sizes |
|---|---|
| Laptop / desktop | 1280×595 (owner laptop, Chrome maximised), 1280×635, 1280×560, 1366×657, 1366×768, 1536×730, 1440×900, 1600×900, 1672×941, 1920×1080 |
| Electron | 1200×800 default window, with and without `.is-electron` |
| Tablet | 1179×640, 1024×768, 1024×600 |
| Phone | 412×915, 390×844, 375×667, 360×740, 360×640, 320×640 |

The prototype passes all of them (`fix_r1/gates_ABC.json`): the composer is fully inside the panel and the window in every size, nominal, with a long high-risk command pending and while typing a long message; no transform; no horizontal scroll; no visible text under 12 px.

---

## 6. Shell

### 6.1 Sidebar (`src/features/shell/Sidebar.tsx`)

| Part | Spec |
|---|---|
| Container | `side` area, flex column; fitted background (§4.5); 1 px `--sidebar-divider` on the right |
| Logo ring | `LogoRing` canvas (§2.3 `LOGO*` bands), `max(44px, 64vu)`, centred, `margin-top: 22vu`. Static; the same ring is the favicon, the phone header logo, the login mark and the assistant chat avatar. `data-testid="brand-mark"`, `role="img"`, name "JARVIS & ANNA" |
| Wordmark | "JARVIS & ANNA", brand 600 `clamp(17px, 2.232vh, 21px)`, −0.3 px, white; `margin-top: 12vu`. Rendered as text (never an image) |
| Subtitle | "Your AI Command Center", `clamp(12.5px, 1.541vh, 14.5px)`, `--t-brand-sub` |
| Nav | `<nav aria-label="Pages">`, `margin-top: 39vu`, padding `0 15px 0 16px`, gap `max(3px, 3vu)`. Four links `<a href="#/overview">` etc. with `data-testid="nav-overview|nav-agents|nav-activity|nav-settings"`, height `max(44px, 61vu)`, icon 24 px at x 42, label at x 93 (padding-left 76). Active: `aria-current="page"`, `--nav-active` fill + edge + 2 px left bar (inset 3 px top/bottom, `--acc-line`, glow), filled `house` (Overview) or the outline icon in `--acc-icon` with its glow, label `--t1` med 500. Inactive: transparent, icons `#acb9e2`, labels `--t-nav`; hover: label `#c9d2ee` and fill `rgba(120,150,225,.06)` |
| Nav marks (off-nominal) | Activity: amber/red count while unseen WARN+/critical alerts exist; Agents: red dot on laptop panic or agent offline. Never in the nominal state (§24, point 6) |
| Settings title | `nav-settings` carries `title="Settings"`, the **only** element with that title once the drawer is gone (X-88; helpers and specs use `getByTitle('Settings')`) |
| Silk wave | inline SVG ported from the prototype (`.side-wave`, the fitted crest curves of `forensics.md` §11), pinned to the bottom (`bottom: 51vu`, height `max(150px, 330vu)`), `aria-hidden` |
| Tagline | two lines "A SMARTER" / "TOMORROW TOGETHER", ui 600 11.5 px uppercase, letter-spacing `clamp(2.6px, .2392vw, 4px)`, `--t-tagline`, at `bottom: 70vu` and `48vu`, left `clamp(24px, 13.24%, 36px)`; `aria-hidden` (decorative) |
| tl rail | 76 px: logo 64 at (6, 14); nav buttons 56×56 at x 10, y 104 / 168 / 232 / 296, radius 14, icon 26; labels as tooltips (and accessible names); wave, wordmark and tagline hidden |

### 6.2 Page header (`src/features/shell/PageHeader.tsx`)

dk grid: columns `minmax(0,1fr) auto auto auto`, areas `"title pills div acct" "sub pills div acct"`, padding `0 38hu 0 calc(var(--gl) + 19hu)`, vertically centred in `--hh`.

| Part | Spec |
|---|---|
| Title | `<h1 id="page-title" tabindex="-1" data-testid="page-title">`: "Overview", "Agents", "Activity", "Settings" (phones also "Chat"); ui 700 `clamp(23px, 3.348vh, 31.5px)` |
| Subtitle | dk only: "Control, collaborate, and automate with JARVIS & ANNA." · "Personas, voice and the devices JARVIS & ANNA run on." · "Events, logs, tasks, notes and media in one place." · "Session, voice, automation and safety." (phones, Chat: "Talk or type to JARVIS & ANNA.") |
| Pills | §6.3; gap `clamp(10px, .927vw, 15.5px)`, margin-left 16 |
| Divider | 1 px `#354369`, height `clamp(24px, 3.4vh, 32px)`, side margins `clamp(16px, 7.653vw − 78.96px, 49px)` / `…50px` |
| Account block | button grid "av g1 ch / av g2 ch": avatar circle `clamp(42px, 6.38vh, 60px)` (`--avatar` + edge) with "M" (ui 700, `--acc-avatar`); "Good evening," (`--t-greet`, `clamp(13.5px, 1.7vh, 16px)`) over "Matloob" (ui 600, `clamp(14px, 1.81vh, 17px)`); chevron-down 13×7. Unread badge (`task-bell-count`, `--badge-red`, 99+) on the avatar. It is the trigger `topbar-task-bell` of the Account & reminders menu (§6.4.4) |
| Greeting | "Good morning," before 12:00, "Good afternoon," before 17:00, else "Good evening," (local time; re-evaluated each minute) |
| Responsive | a `ResizeObserver` on the header row: `data-greet="off"` (avatar only) below 1120 px; `data-compact` (compact pill labels) below 1000 px in dk and 760 px in tl |
| Document title | "JARVIS & ANNA" on Overview, "JARVIS & ANNA · <Page>" elsewhere; "⚠ Confirm: <tool>" flashes only while a confirmation is pending and the tab is hidden (§10.4) |

### 6.3 The three status pills (`src/features/shell/StatusPills.tsx`)

Each pill is a `<button aria-haspopup="dialog" aria-expanded>` (radius 14, height `clamp(40px, 5.74vh, 54px)`, `--pill-*` fill + `--pill-edge`, icon `clamp(18px, 2.48vh, 23.4px)` in `--acc-pill-ic`, label `--t-pill`), min widths `163hu / 205hu / 153hu`. `data-active` is kept for the e2e suite.

**P1 `chip-agent` "System Online"** (driven by `agent_status` only, so the e2e words stay stable):

| Condition (first match) | Shape | Full label | Compact | `data-active` |
|---|---|---|---|---|
| Socket down | ▲ | System Unreachable | Unreachable | false |
| No `agent_status` yet | ○ dashed | System Unknown | Unknown | false |
| Restarting | spinner | System Restarting | Restarting | false |
| Paused | ▲ | System Paused | Paused | false |
| Offline | ◆ | System Offline | Offline | false |
| Online | ● (green dot 12 px with `--glow-ok`) | System Online | Online | true |

Title: "Agent (VPS), last seen 3 s ago". Opens the Agent popover (§6.4.1).

**P2 `chip-bridge` "3 Connected Devices"**: socket connected → "{N} Connected Devices" / "{N} Devices" (singular "1 Connected Device"; ≤ 340 px phone: just "{N}", accessible name "{N} connected devices"), `data-active="true"`; socket down → ◆ "Bridge Offline" / "Offline", `data-active="false"`. N = this browser (1) + other UI clients (`ui_clients − 1` from `GET /vortex/status`, ≥ 0, polled every 30 s while the page is visible and on popover open) + Agent (VPS) if `agent_status.online` + Laptop if `desktop_status.online` (unknown until the first `desktop_status`: not counted). Icon marks (off-nominal): ▲ laptop stale/locked, ◆ panic, slate dot read-only, red dot while the camera is on (privacy). Opens the Devices popover (§6.4.2).

**P3 `chip-voice` "Voice Ready"**: label per voice state (§9.2), mic icon (`mic-fill`, `mic-off`, `ear-off`, `speaker-off` per state), ping ring while speaking or listening (never under reduced motion). `data-active="true"` in idle, mic off, listening, thinking and speaking. Title "Voice: <status line>. Voice <Charon|Aoede>". Opens the Voice popover (§6.4.3).

### 6.4 Popovers and the account menu

One primitive `Popover` (`src/features/shell/popovers/Popover.tsx`): `role="dialog"` with `aria-label` (mixed content, never `role="menu"`), anchored under its trigger (z 72, radius 14, `--card` + edge, `--shadow-pop`), Esc closes, focus moves in on open and back to the trigger on close, outside click closes, it stays open while its switches toggle. On phones it is a bottom sheet (radius 20 at the top, scrim, max-height 82 %). Closed popovers are **not rendered** (strict e2e texts, §18.2).

#### 6.4.1 Agent popover (`chip-agent-menu`, kept)

"Agent (VPS)" + state shape and word; session, brain (vps/pc), mode (gated/fast), host, version, last seen; health from `GET /vortex/status` (bridge, DB, LiveKit latency, agent in room); **Pause agent** (`agent-pause`) or **Start / Wake** (`agent-wake` + `agent-wake-spinner`; toast 'Agent abhi tak online nahi…' after 90 s); link "Open Agents ›" (`#/agents/agent`).

#### 6.4.2 Devices popover (`devices-menu`, new)

| Row | Contents |
|---|---|
| This browser | Bridge ● Live / ◆ Offline, base `/vortex`, **Reconnect** (`topbar-reconnect`, kept; title "Reconnect: reloads the dashboard and rejoins voice" until X-96, then "Reconnect bridge and voice"), voice room (Joined / linked to another device → Take over mirror), camera state |
| Agent (VPS) | state + "Open Agents ›" |
| Laptop (Desktop Node) (`chip-laptop`, new) | shape + state: ○ *unknown* (until the first `desktop_status`), ● Full, ▲ Stale, ○ Offline, ▲ Locked, ◆ Panic, slate ● Read-only; mode, locked, voice active, busy tool, last seen; **Stop desktop** (`desktop-stop-menu`, new, danger; sends `desktop_stop` once, visible whenever the laptop is online); hint "Stop switches the laptop to read-only. Only the laptop tray can switch back to Full." |

#### 6.4.3 Voice popover (`voice-menu`, new)

State sentence + "Voice Charon/Aoede"; **Microphone (this browser)** switch (`topbar-mic-toggle`, kept; disabled while offline or joining); **<NAME> listening** switch (`topbar-agent-listening`, kept, `data-muted`; title "<NAME> mic se nahi sune<ga|gi>; typed chat chalti rahegi"); **Wake word** switch (`chip-wake`, kept, `data-active` mirrors `aria-checked`; `chip-wake-spinner` while pending; "Enforced: yes/no"); **Stop speaking** (`topbar-stop`, kept, title "<NAME> ko chup karao"); Voice-ID mini (score vs threshold, Enroll); mirrors of Enable audio / Take over / Wake when relevant (no testids); link "Voice settings ›".

#### 6.4.4 Account & reminders menu (`task-bell-menu`, kept)

Trigger: the account block (`topbar-task-bell`). Header "Matloob (owner)", "Session ends HH:MM". **Reminders**: last 20 rows (`task-notif-row`): text, due time, 🔊 Spoken aloud (`task-notif-spoken`) or 💬 Screen only (`task-notif-screen-only`), ✓ Delivered or ▲ Not acknowledged yet (X-67 data). Footer: desktop notifications ● on / ○ off [Turn on] (`Notification.requestPermission()`) / ◆ blocked in browser settings. Actions: Settings (a link to `#/settings`, **no** `title`), **Sign out** (`logout`, kept). Opening clears the unread count and acknowledges what is shown (`notification_ack`).

### 6.5 Routing and focus (`src/features/shell/router.ts`)

- Hash routes, no router dependency: `#/overview` (default), `#/agents[/personas|voice|vid|flow|agent|laptop|browser|machines|map]` (scrolls to the anchor), `#/activity[/timeline|logs|tasks|notes|media]` (selects the sub-tab), `#/settings[/session|connection|persona|voice|voice-id|automation|vision|confirmations|notifications|sim|about]`, phones `#/chat` (on dk/tl it redirects to `#/overview` and focuses the composer). Browser Back works.
- A route change from a link moves focus to `#page-title` (no visible outline) and a polite status says "<Page> page".
- `data-testid="app-ready"`: visually hidden text "Dashboard ready" (1×1 px, counted as visible by Playwright), present only while the authenticated shell is shown (hidden on session check, login, offline). It replaces 'System Flow' as the login-success marker (§18.3).
- Page containers: `page-overview` (also keeps `layout-zone-core` on the hero), `page-agents`, `page-activity`, `page-settings` (also `settings-drawer`).

### 6.6 Phone shell (< 1024 px)

| Part | Spec |
|---|---|
| Header row 1 (≈ 56 px) | ring logo 30 px, page title (21/28), avatar 38 px with the unread badge (opens Account & reminders as a sheet) |
| Header row 2 (≈ 48 px) | the three pills, one line, `flex: 1 1 0`, height 38, radius 12, compact labels ("Online", "3 Devices", "Ready"; ≤ 340 px "3"); tap opens the popover as a bottom sheet |
| Tab bar (`mobile-tab-bar`) | 64 px + `env(safe-area-inset-bottom)`, five buttons icon over label (11.5/14): **Overview** `mobile-tab-core`, **Chat** `mobile-tab-chat`, **Agents** `mobile-tab-ops`, **Activity** `mobile-tab-trace`, **Settings** `mobile-tab-settings` (new); `aria-current="page"` + `data-active`; active: white label 700, accent icon, 3 px top bar in `--acc-line` with glow. Marks: Chat ◆ + count while approvals wait; Activity ▲ while unseen alerts exist. The bar hides while the message field has focus (`html.typing`), so the composer sits on the keyboard |
| Gutters | 16 px; no horizontal scroll |
| Toasts | at the top (never over the composer or the tab bar) |

---

## 7. Overview page

### 7.1 Hero card composition (`src/features/overview/HeroCard.tsx`)

The hero keeps the reference composition as **one unit** that scales with its card; real text, no transform.

```css
.hero { grid-column: 1 / -1; position: relative; container-type: size; border-radius: var(--r-hero);
        height: min(clamp(240px, calc(58vh - 71.5px), 474px), calc(100cqw * .5712));
        --u:  min(calc(100cqw / 830), calc(100cqh / 474));        /* one reference pixel of the hero */
        --ox: calc((100cqw - 830 * var(--u)) / 2);  --oy: calc((100cqh - 474 * var(--u)) / 2); }
```

Positions (left, top in `u` from `--ox/--oy`; sizes in `u`; floors in px):

| Part | Box | Floors |
|---|---|---|
| Orb canvas (`speaker-orb`) | (−21, −57), 520×520 (sphere centre (239.6, 203.7)) | — |
| User ring (`user-speaking-indicator`) | (115.6, 79.7), 248×248 circle: `box-shadow: 0 0 0 2px rgba(255,255,255,.85), 0 0 18px 2px rgba(255,255,255,.35), inset 0 0 14px rgba(255,255,255,.18)`; the renderer writes `scale(1 + .05U)` while listening | — |
| Orb glyph (DOM, `aria-hidden`) | (213.6, 177.7), 52×52 disc `rgba(7,11,26,.78)` with a 1 px `rgba(160,175,215,.28)` ring, icon 24 px (`--warn` / `--err-t` by tone, else `#c9d3ee`) | 40 px |
| Eyebrow / persona switch | (447, 67), 14.25/20, tracking 4.3 | 12 / 16 / 2.4 px |
| Name (`hero-persona-name`) | (447, 96), 65.5/72, −.5 | 30 / 34 px |
| Role "AI Assistant" | (448, 167), 26.25/32 | 14.5 / 19 px |
| Accent rule | (447, 214), 57×3 | 2 px high |
| Waveform canvas (`agent-audio-visualizer` container) | (446, 238), 216×40 | — |
| Status line (`voice-status`) / user label (`user-speaking-label`) | (447, 280), 20.25/28 | 13.5 / 19 px |
| Button row | (108, 360), gap 15: Start Listening 217×56, Run Task 193×56, Mic Off 180×56 | height 36 px, label 13 px, icons 16–17 px |
| Quote / helper (`hero-helper`) | centre x 399.5, top 437, 14.25/20, tracking 1.6 (helper: left 108, tracking .2, max-width to the card edge − 16 px, ellipsis) | 12 / 16 / .8 px |

Decorations: the six hero light layers and the flare SVG (§4.5), the lit hero edge (§4.3). The waveform and the orb are drawn by the orb renderer (§8); the rest is DOM.

### 7.2 Eyebrow = persona switch (`chip-persona`, kept)

- Markup: `<div role="radiogroup" aria-label="Who answers" data-testid="chip-persona">` with two `<button role="radio" aria-checked>` texts "Jarvis" and "Anna" (uppercase by CSS, so screen readers say the names) separated by " & ", then `chip-persona-spinner` (12 px ring) while the switch is pending (`aria-busy="true"` on the group).
- Roving `tabindex` (the checked radio is 0); Left/Right/Up/Down and Space/Enter switch; titles "JARVIS answers. Voice Charon. Switch to ANNA: Voice Aoede" (and the reverse).
- Look: both names in the reference colour (`--t-eyebrow`, checked `--t-eyebrow-on`); the checked one carries a 1 px underline in `rgba(var(--acc-glow),.55)` 6 px below; hover/focus lights the other name in its persona colour (`--acc-text` of that persona). Hit area ≥ 24 px high (44 px on phones) via a transparent `::after` (−4 / −6 px); 2 px focus ring.
- Behaviour: §15.

### 7.3 Name, role, rule

- **Name** (`hero-persona-name`, new): "JARVIS" or "ANNA" in brand 700 with the sheen `background: linear-gradient(180deg, var(--name-hi) 18%, var(--acc-name) 46%, var(--name-lo) 58%, var(--name-mid) 68%, var(--name-lo) 81%); background-clip: text; -webkit-text-fill-color: transparent; filter: drop-shadow(0 2px 6px rgba(3,7,22,.6))`. Rolls on a persona switch (§4.7).
- **Role**: "AI Assistant" (both personas), `--t-role`.
- **Rule**: 57×3, a vertical gradient of `rgba(var(--acc-rule-rgb), …)` (prototype `.hero-rule`).

### 7.4 Status line (`voice-status`, kept) and inline actions

- One line, med 600, `--acc-text` in calm states; `data-tone` `mute` (`--t3`), `warn` (`--warn-t`), `err` (`--err-t`). A `StatusShape` (10 px) leads in attention states.
- Copy per state: §9.2. In attention states one inline action button follows the text (pill, 14 px, white on `rgba(255,255,255,.08)`): **Take over** (`voice-take-over`, kept), **Wake** (`voice-wake-agent`, kept), **Enable audio** (`enable-audio`, kept; also resumes every analyser context), **Stop** while speaking (`hero-stop`, new). These replace the banners above the dashboard (D8): nothing pushes the layout.
- While you speak, `user-speaking-label` "● You are speaking" (white) is shown in the same box and `voice-status` stays mounted but visually hidden ("Listening to you"). `user-speaking-label` is `aria-hidden` while not listening.

### 7.5 Buttons

| Button | testid | Look | Behaviour |
|---|---|---|---|
| **Start Listening** (mic-fill icon) | `voice-toggle` (kept) | `--acc-grad`, radius 13, edge lights `--btn-*`; pressed (mic on **and** agent listening): `--glow-primary` (the reference look); not pressed: the softer glow | Mic on (publish) and `unmute` when muted. Label "Joining…" and disabled while joining; disabled while offline (`voice.spec` expects it disabled while voice is unavailable). Part of `role="group"` "Microphone" with Mic Off |
| **Run Task** (zap-fill icon) | `swarm-mode-toggle` (kept, `data-active`) | ghost `--ghost-run` + `--ghost-run-edge`; pressed: `--pressed` fill + accent edge | Toggles task (swarm) mode: the next Send emits `swarm {task}`; focuses the composer (phones: goes to `#/chat`); the composer shows the "⚡ Task mode ×" chip and the placeholder "Describe a task for <NAME>..." |
| **Mic Off** (mic-off icon) | `hero-mic-off` (new) | ghost `--ghost-mic` + `--ghost-mic-edge` (1.5 px); pressed while the mic is off | Mic off (stop publishing); disabled while offline or joining |

### 7.6 Quote and helper slot (`hero-helper`, new)

Calm states (idle, listening, thinking, speaking) show the quote **“Ideas become reality, faster.”** (both personas). Attention states show the helper copy (§16.3, `lang="ur-Latn"`); offline adds the voice error "Voice: <error>" in `--err-t`. Long text ends in an ellipsis with the full text in `title`. The slot never changes height.

### 7.7 Quick Stats card (`src/features/overview/QuickStatsCard.tsx`, `qs-card`)

- Card (`--card`, radius 13), grid rows `auto auto minmax(0,1fr)`, `container-type: inline-size`. Header: chart icon 26 (`--ic-card`), "Quick Stats" (med 560 `clamp(14px, 1.581vh, 14.875px)`, tracking 1.2 px), subtitle "Live system status", chevron button (`qs-open`, "Open machines") → `#/agents/machines`.
- Tiles (`qs-cpu`, `qs-memory`, `qs-disk`, `qs-devices`; 2×2, gaps 15 × `max(10px, 14vu)`, height `clamp(64px, 91vu, 91px)` / 95 for the second row): icon (cpu `--ic-cpu`, memory `--ic-mem`, disk `--ic-disk`, laptop `--ic-dev`), value (ui 600), label ("CPU Usage" → "CPU" below 290 px; "Memory"; `disk_label` default "Disk (C:)"; "Connected Devices" → "Devices" below 400 px), track 9 px `--track` with the fill (`--cpu`, `--mem` gradient, `--disk`, `--devc`, glow) scaled by `transform: scaleX(v)`.
- Data: `vitals_pc` (cpu, ram, disk, disk_label) while the laptop is online; otherwise `vitals_vps` with "Disk (VPS)" and the subtitle "Laptop offline: showing Agent (VPS)". Devices tile = N of §6.3; its bar = `min(1, N / 11)` (a fixed scale chosen so the nominal 3 draws at the reference's 27 %).
- Off-nominal: ▲ on the tile at CPU ≥ 85 %, memory or disk ≥ 90 %; a "stale" tag (top-right) when the vitals are older than 30 s.
- Clicking a tile opens `#/agents/machines` (devices tile: `#/agents/laptop`).

### 7.8 Recent Activity card (`src/features/overview/RecentActivityCard.tsx`, `activity-card`)

- Header: clock icon, "Recent Activity" (med, `clamp(14.5px, 1.833vh, 17.25px)`), "Latest events from JARVIS & ANNA", chevron (`activity-open`) → `#/activity/timeline`.
- The **4 newest** rows of the activity feed (§12.1), `data-testid="activity-row"` with `data-kind` and `data-level`: dot 13 px (2 px ring of card colour; colours green `--ok` faint glow, blue `--info` glow, violet `--dev` glow, grey `--neutral`, amber, red), title (med 500), subtitle (`--t-act-sub`), time "9:41 PM" (`--t-time`, tabular); 1 px connector `#2b3552` at x 29.5; row height `clamp(46px, 54vu, 54px)`. A row is a link to its deep target (e.g. a tool row → `#/activity/logs`).
- Empty (first seconds after sign-in): "No activity yet" in `--t4`.

### 7.9 Phone Overview

The hero stacks: orb 340 px canvas (negative margins, sphere ≈ 196 px) centred, eyebrow, name 44/50, role 19/24, rule, waveform, status line (wraps), **Start Listening** full width (50 px), **Run Task** + **Mic Off** side by side (48 px), quote/helper centred. Then Quick Stats (2×2 tiles, 100 px) and Recent Activity. At ≥ 620 px container width the hero becomes two columns (orb | text), at ≥ 740 px the two cards sit side by side.

---

## 8. The orb component

### 8.1 Anatomy (back to front), from the fitted reference

| # | Layer | Data | Reacts to |
|---|---|---|---|
| 1 | Glow behind the glass + light lobes outside it (`lighter`) | `OP.glow`, `OP.olobes` | radius × (1 + .1L), alpha × (1 + .9L) / (1 + .6L) |
| 2 | Particle halo, 215 measured dots on loose rings (r 140–230), inner and outer groups counter-rotate | `PARTICLES`, `OP.pa`, `OP.ps` | radius × (1 + .06L) × pull; size × (1 + .3L); alpha (.85 + .6L); slow spin only in live states |
| 3 | Five orbit rings A–E (measured radius, centre offset, FWHM, light every 5°) | `RINGS` | scale = pulse × pull; brightness copy at .6L; spin .5 rad/s speaking/thinking, .03 listening |
| 4 | Glass sphere (body gradient, 9 light/dark lobes split by the streak axis, streak glow, inner rim light) + inner ring | `OP.body`, `OP.lobes`, `OP.axis`, `OP.sg`, `OP.ir`, `OP.rim`, ring `inner` | scale = pulse (1 + .06L) |
| 5 | Swirl ribbons inside the glass (7 measured ellipses) + their blurred copy, clipped to the glass | `SWIRL`, `OP.swg/sww/swglow` | slow drift `sin(.11t)·.05 + t·(.02L)` (+ .03 thinking); alpha .85 + .5L |
| 6 | Streak (22° lens line) + core hotspot | `OP.streak`, `OP.core`, `OP.cyan` | streak length × (1 + .5L) + alpha copy .45L; core radius × (1 + .25L), breathing ±6 % (5.2 s) in idle/mic off (half in thinking) |
| 7 | Rim (conic measured light) + rim glow | ring `rim`, `OP.rimGlow` | speaking: a wobbling membrane `r(θ) = r·pulse·(1 + .035L·Σ)`; JARVIS modes 3/5/7 (speeds 5/−7/11 rad/s, weights .5/.3/.2), ANNA 2/3/5 (3/−4.3/5.6, .45/.3/.25) |
| 8 | State layers | — | thinking: a comet on R × 1.045 (period 1.7 s, 14 trail segments); muted: dashed ring R × 1.13 (`[5,6]`, 1.6 px, `rgba(160,172,205,.55)`); offline: amber notch at 12 o'clock (R × 1.07, ±.17 rad, 4 px, `#e9ab4a` + glow) |
| 9 | Ripples | — | outward on a speech onset (R × 1.02 → 1.38, .9 s JARVIS / 1.25 s ANNA, alpha .22 + .5L, max 6); inward white while you speak (R × 1.35 → 1.0, 1.1 s, every ≥ .35 s when U > .14) |
| — | **Pulse / vibration** | — | whole orb scale 1 + .06L; when L > .3 while speaking, a random translate of `(L − .3)·.03R` on both axes (ANNA × .6) |
| — | Listening pull | — | halo and rings pulled in by `1 − .07U` |

R = 107 canvas px (the reference sphere radius). The canvas is 520×520 canvas px around the centre (260.6, 260.7).

### 8.2 Data and files

- `src/features/voice/orb/orbData.ts`: `orb_data.json` as a typed constant (`OP`, `RINGS`, `PARTICLES`, `SWIRL`, `LOGO`, `LOGO_IN`, `LOGO_OUT`, `LOGO_EDGE`, `WAVE`, `MINI`, geometry). Values unchanged: they were fitted against the reference (orb region MAE 5.19, SSIM .924).
- `src/features/voice/orb/palette.ts`: `annaMap`, `pc(colour)` (persona mix + grey mix), the colour caches.
- `src/features/voice/orb/orbRenderer.ts`: plain TS class, the exact renderer (`drawOrb`, used for still frames and while colours cross-fade) and the fast path (`drawOrbFast`: every static layer pre-rendered per pixel size / palette / grey look into offscreen bitmaps, then about 20 `drawImage` calls with transforms and alphas per frame; one gradient per frame for the breathing core).
- `src/features/voice/orb/waveform.ts`: the waveform renderer (§8.6).
- `src/features/voice/orb/OrbCanvas.tsx`: the React wrapper (props: `state`, `persona`, `reducedMotion`; the two canvases, the user ring and the glyph); sizes from `ResizeObserver`, visibility from `IntersectionObserver` + the route + `document.hidden`.
- `src/shared/ui/LogoRing.tsx` and `src/features/agents/MiniOrb.tsx` reuse the same data.

### 8.3 Renderer API

```ts
export type VoiceState = 'offline' | 'joining' | 'agent-absent' | 'audio-blocked' | 'speaking'
                       | 'thinking' | 'muted' | 'listening' | 'mic-off' | 'idle';
export class OrbRenderer {
  constructor(orb: HTMLCanvasElement, wave: HTMLCanvasElement, userRing: HTMLElement, opts: { phone: boolean });
  setState(state: VoiceState, persona: 'jarvis' | 'anna'): void;  // restarts the loop when something will move
  setReducedMotion(on: boolean): void;                             // on: no loop, one exact static frame
  resize(orbCssPx: number, waveCssW: number, waveCssH: number): void;  // from ResizeObserver only
  setVisible(on: boolean): void;                                   // route + IntersectionObserver + document.hidden
  snapPalette(): void;                                             // View Transition running: jump to the target colours
  stats(): { loop: 'full' | 'breath' | 'stop'; draws: number; glowWrites: number };
  destroy(): void;
}
```

The React wrapper never re-renders per frame; the only React inputs are the coarse state, the persona and reduced motion.

### 8.4 Inputs from livekit-client (through the X-74 `audioEngine`)

- **Agent level and bands:** `audioEngine.readAgent()` → `{ level L (0..1), dbfs, bands[24], waveform[128], onset }`. The engine binds the agent `RemoteAudioTrack` with livekit's `createAudioAnalyser(track, { fftSize: 1024, smoothingTimeConstant: .6, minDecibels: −90, maxDecibels: −22 })`, computes `L = clamp((dBFS + 56) / 40)` from the time-domain RMS, 24 log bands 80 Hz–5 kHz, attack 30 ms / release 120 ms, and calls `cleanup()` on every unbind. Keep the hidden `<audio>` element (Chrome remote-audio quirk).
- **Your level:** `audioEngine.readUser()` (local mic, `cloneTrack: true`) while the mic publishes. U drives the inward ripples, the pull and the listening waveform.
- **Coarse React state only:** `agentState` (`lk.agent.state`), `isSpeaking` (`speaking` or agent RMS above the threshold ≥ 120 ms), `isUserSpeaking` (mic dBFS > −45 for 100 ms, released after 400 ms below −50, confirmed by `ActiveSpeakersChanged`). Never `setState` per frame.
- **Sampling rate (alignment of X-74, done in X-75):** the engine samples per animation frame only while the orb runs in `full` mode (`audioEngine.setRate('frame')`); otherwise at a 30 Hz timer (`'coarse'`), which is enough for `isSpeaking` and the VAD. The engine stops on `document.hidden` (already).
- **Autoplay:** "Enable audio" calls `room.startAudio()` and `audioEngine.resumeAll()`; every mic gesture also calls `resumeAll()`.

### 8.5 State looks

| State | Grey | Dim | Overlay / glyph | Motion |
|---|---|---|---|---|
| offline | 1 | .72 | amber notch, glyph `alert` (amber) | none |
| joining | 1 | .6 | — | none |
| agent-absent | 1 | .6 | glyph `user-off` | none |
| audio-blocked | 1 | .6 | glyph `speaker-off` (amber) | none |
| speaking | 0 | 1 | — | full, driven by L |
| thinking | 0 | 1 | comet | full, swirl .03, half breathing |
| muted | 1 | .72 | dashed ring, glyph `ear-off` | none |
| listening | 0 | 1 | user ring (`user-speaking-indicator`) | full, driven by U |
| mic-off | 0 | .86 | — | breathing only |
| idle | 0 | 1 | — | breathing only (the reference) |

Grey and dim ease (`1 − e^(−4.2·dt)` × 1.4) so state changes never snap. Glyphs are DOM icons over the orb (`aria-hidden`; the state is in `voice-status`).

### 8.6 Waveform (`agent-audio-visualizer`, kept)

- Canvas 216×40 `u` at hero (446, 238) inside the container that carries `agent-audio-visualizer` (`aria-hidden`; the words are in `voice-status`). 30 rounded bars, width 2.6, at the measured centres, colours and lengths of `WAVE`.
- Modes: **idle** (the reference resting pattern: measured lengths at the measured centres); **speaking** and **listening** (bar height `2 + (4 + 30·c²)·clamp(1.4·level + .08, 0, 1.1)·(.55 + .45·band)`, c = closeness to the centre, eased .35 per frame; listening colours mix 65 % toward `#ebf2ff`); **thinking** (a cosine highlight sweeping at 4 rad/s); **dots** (2.6 px dots; mic off and joining); **off** (2 px grey dots; offline, agent absent, audio blocked, muted).
- Drawn in the same loop as the orb; colours cached (§2.3).

### 8.7 Loop policy (one `requestAnimationFrame` loop owns orb, waveform and user ring)

| Mode | When | Rate |
|---|---|---|
| **full** | speaking, listening, thinking, push-to-talk; or while the palette, the grey/dim look, the level (> .01) or a ripple is still settling | every frame, but a frame is skipped when less than 12.5 ms passed (60 fps on 60/120 Hz, ≤ 75 on 144 Hz) |
| **breath** | idle and mic off (only the core breathes) | ~15 fps (a 66 ms timer, then rAF) |
| **stop** | muted, offline, joining, agent absent, audio blocked once settled (one exact frame, then nothing); `document.hidden`; the orb off-screen (`IntersectionObserver`); any page other than Overview (phones: any tab other than Overview); reduced motion | no frames; the last frame stays on the canvas |

Any state, persona, size or visibility change restarts the loop; it stops again by itself. Sizes only from `ResizeObserver`; **no layout reads in the loop**. DPR is capped at 2 on desktop and 1.5 on phones. The fast path runs once the colours have settled; the exact renderer runs during a cross-fade (about a second) and for still frames.

### 8.8 Performance budget (acceptance; prototype numbers on the owner's laptop, Iris Xe, new headless)

| Profile | Gate | Prototype |
|---|---|---|
| Desktop 1440×900 DPR 1 and DPR 2, speaking | ≥ 58 fps, ≤ 1.5 ms script per frame | 60 fps, 0.42–0.48 ms per draw |
| Desktop DPR 2, 4× CPU throttle, speaking | ≥ 50 fps | 58.8 fps (1.71 ms per draw at 1×) |
| Phone 390×844 DPR 3, 4× CPU, speaking | ≥ 50 fps | 60 fps (5.84 ms per draw throttled) |
| Idle and mic off | ≤ 16 draws/s, no style recalc | 12.6 draws/s, main thread 1.8 % busy |
| Muted, offline, joining, agent absent, audio blocked | 0 draws once settled | 0 |
| Orb off-screen, tab hidden, another page or phone tab | 0 draws | 0 |
| 144 Hz display (rAF shim) | ≤ 75 draws/s | — (loop skips frames under 12.5 ms) |
| Layout reads in the loop | 0 | 0 |
| Engine sampling while the orb is in breath or stop with a track bound | ≤ 31 ticks/s | — (new, §8.4) |

### 8.9 Reduced motion

No loop. One exact static frame per state, persona or size change: t = 0, L = 0 except speaking (L = .62) and listening (pull .5, L = .4), so the state stays readable; the waveform shows its mode at rest; glyphs, dashed ring and notch stay. The persona switch is instant. Zero frame difference over time (presence.spec).

### 8.10 Persona palette

`palMix` eases to the target persona (§2.3); during a View Transition the renderer snaps (`snapPalette()`). Past chat replies are never recoloured; only the live orb and accents change.

### 8.11 Test hook additions (`e2eHooks.ts`, e2e builds only)

X-74's `window.__jarvisTest` stays. X-75 changes `stats()` so `loop`, `draws` and `roomWrites` (renamed meaning: glow writes, kept key) come from the `OrbRenderer`, and adds `engineLoop` / `engineTicks`. X-97 wires `setVoiceState` / `setVoiceInputs` into the state derivation (they are not consumed yet). X-109 adds `referenceFrame(on)` and `frameReady()` (§21.1). The hook accepts both spellings of the states ('agent absent' / 'agent-absent', 'audio blocked', 'mic off'). A plain `npm run build` leaves no trace (`grep -rl __jarvisTest dist` is empty).

---

## 9. Voice state machine

### 9.1 Derivation (`src/features/voice/voiceState.ts`, first match wins; nothing sets a state directly)

Inputs: `voiceUnavailable` or room error (including "voice linked to another tab or device"), room state, `agentMissing` (no agent participant 15 s after joining) or agent offline/restarting, `playbackBlocked` (`!room.canPlaybackAudio`), `isSpeaking`, `lk.agent.state === 'thinking'`, the mute flag (agent listening off), `isUserSpeaking` with the mic on (or push-to-talk), mic publishing. The e2e hook overrides the inputs. `status_change` no longer drives the orb (it keeps its other uses: the Voice pill title and the chat typing row when LiveKit is down).

### 9.2 State table

`pressed` = `aria-pressed="true"`. Start Listening keeps its label except while joining; it is pressed only while the mic is on **and** the agent listens; Mic Off is pressed while the mic is off.

| # | State | Status line (`voice-status`) + inline action | Quote slot (`hero-helper`) | Waveform | Start / Run / Mic Off | Voice pill (full / compact) |
|---|---|---|---|---|---|---|
| 1 | **offline** | ◆ "Voice unavailable" (variant: "Voice is on another device" + **Take over**) | "Voice abhi band hai. Chat aur controls chalte rahenge." + "Voice: <error>" (variant: "Voice doosre tab ya device par chal rahi hai. Take over dabayein; chat chalti rahegi.") | off | disabled / enabled / disabled | ◆ Voice Offline / Offline |
| 2 | **joining** | spinner "Joining voice…" (`--t3`) | "Voice room join ho raha hai. Chat chalti rahegi." | dots | "Joining…" disabled / enabled / disabled | spinner Joining Voice / Joining |
| 3 | **agent-absent** | ▲ "<NAME> is not in the voice room" + **Wake** (variant restarting: spinner "Agent session restarting…", no action) | "<NAME> room me nahi. Wake dabayein; chat chalti rahegi." (variant: "Agent session restart ho raha hai. Thodi der mein wapas aa jayega.") | off | enabled / enabled / enabled | ▲ No Agent / No agent |
| 4 | **audio-blocked** (wins over speaking) | ▲ "Audio is blocked" + **Enable audio** | "Browser ne awaaz rok di hai. Enable audio dabayein." | off | enabled / enabled / enabled | ▲ Audio Blocked / Blocked |
| 5 | **speaking** | "<NAME> is speaking" + **Stop** (`hero-stop`) | the quote | speaking (agent bands) | per mic state | ● Speaking + ping |
| 6 | **thinking** | "Thinking…" | the quote | thinking | per mic state | ● Thinking |
| 7 | **muted** (speaking and thinking still win) | ▲ "Not listening (muted)" (`--warn-t`) | "<NAME> mic se nahi sune<ga\|gi>; typed chat chalti rahegi." | off | not pressed (click = unmute + mic on) / enabled / per mic state | ▲ Not Listening / Muted |
| 8 | **listening** (you speak, mic on) | "● You are speaking" (`user-speaking-label`, white); `voice-status` "Listening to you" visually hidden | the quote | listening (your bands, white) | pressed / enabled / not pressed | ● Listening + ping |
| 9 | **mic-off** (room joined, not publishing; **the default after sign-in and Reconnect**) | ○ "Mic is off" (`--t3`, not a warning) | "Mic band hai. Start Listening dabayein ya type karein." | dots | primary, not pressed / enabled / pressed | ● Voice Ready / Ready (mic-off icon) |
| 10 | **idle** (mic on, agent hears, nobody speaks) | **"Listening for your command"** (the reference copy, `--acc-text`) | the quote **“Ideas become reality, faster.”** | idle (the reference pattern) | pressed / enabled / not pressed | ● Voice Ready / Ready |

State 10 with the agent present is exactly the reference picture.

### 9.3 Mic controls and mirrors

`voice-toggle`, `hero-mic-off`, `topbar-mic-toggle` (Voice popover), the composer mic and the push-to-talk are **disabled while offline or joining**. Start Listening also sends `unmute` when muted. One store flag for "agent listening" (today three instances, audit 1.13) feeds `topbar-agent-listening`, `assistant-agent-listening` (Agents › Voice & wake) and `settings-muted`.

### 9.4 Push-to-talk (composer mic, `composer-mic`, new)

Same LiveKit mic as Start Listening, never a second local analyser. Press (pointer, Space or Enter) → publish + agent listening (no wake word), label "Listening…", `aria-pressed="true"`; release → the previous mic and listening state come back and the turn is sent. A tap under 350 ms shows the toast "Hold to talk". In offline / joining / agent absent / audio blocked a toast gives the state sentence instead.

### 9.5 Announcements

One hidden polite region (`#sr`), debounced 1.2 s, never repeated: into and out of offline, muted, mic off, agent absent and audio blocked (e.g. "JARVIS: voice unavailable", "Mic is off", "Voice back"). The per-turn listening / thinking / speaking cycle is **not** announced. The orb canvas has `role="img"` and the name "<NAME> voice orb: <status line>".

### 9.6 After sign-in and Reconnect

The room joins with the mic off (privacy, autoplay) → state 9. After Reconnect: toast "Voice rejoined with the mic off."

---

## 10. Right panel (`src/features/panel/RightPanel.tsx`, `chat-panel` kept)

### 10.1 Structure

A flex column inside `--panel` + `--panel-edge` (radius 15): **tabs** (fixed, `clamp(50px, 7.12vh, 67px)`) → **Confirm Command card** (only while pending; `flex: 0 1 auto`, `max-height: 64 %`, only its code block shrinks) → **tab content** (`flex: 1 1 0; min-height: 0`; it may shrink to zero) → **captions strip** (while live) → **composer** (never shrinks). **The composer is fully visible at every size, with or without a pending approval.**

Tabs: three `<button aria-pressed>` with `data-active` (never `role="tab"`: the e2e suite clicks `getByRole('button', { name: 'tools' })`), names "Chat", "Approvals" (+ badge `approvals-count`, `--acc-badge` circle `clamp(22px, 2.76vh, 26px)`, hidden at 0, `aria-label="N waiting"`), "Tools". Icons `msg` (active `--tab-ic`), `clip` (clipboard-check), `box`. Active: med 550 white label, 3 px underline `--acc-line` with `--glow-underline`; inactive `#9fa9c7`. dk widths `123hu / 181hu / 106hu` with the reference offsets; a 1 px divider (`--tabs-divider`) under the band. Normalised: the Approvals label sits on the Chat/Tools baseline (§22).

### 10.2 Confirm Command card (`src/features/confirm/ConfirmCard.tsx`, `confirm-dialog` kept)

| Part | Spec |
|---|---|
| Surface | `--warn-card` + 1 px `--warn-card-edge`, radius 14, margins `21vu 17 0 19`, padding `21vu 21 21vu 22`; arrival animation 320 ms |
| Header | `shield` icon 24–27 px (`--shield`; high risk `--shield-high`), title "Confirm Command" (med 560; "Confirm" when the panel is < 430 px), pill (`--warn-pill` + edge, radius full, `sparkle` icon `--sparkle`, label `--t-cf-pill` 12.25 px): **"Requires approval"** (medium), **"High risk"** (high), **"Expires in m:ss"** in the last 60 s |
| Question | `summary` (ui 400 16.25/24, `--t-cf-q`) |
| Code block | `args_preview` (mono 15/24, `--t-code` on `--code`, radius 12, padding `19vu 20 13vu`, `max-height: clamp(70px, 22vh, 160px)`); hidden when absent; lines kept, wrapped only between tokens (`white-space: pre-wrap; word-break: normal; overflow-wrap: normal`, each token `nowrap`, continuation lines indented 2ch), never hyphenated; scrolls inside the card (`tabindex="0"`, `role="region"`, name "Command to run, N lines") |
| Buttons | **Deny** (`confirm-deny`, ghost `--ghost` + 1.5 px `--ghost-edge`) · **Approve & Run** (`confirm-allow`, `--acc-approve`); grid 1fr 1fr, gap 15, height `clamp(40px, 5.2vh, 49px)`; high risk: label "Hold to approve" (accessible name "Hold to approve (hold to allow). Press and hold for 1 second, or hold Space.") |
| **Meta line** (product rule) | always shown for a real request, under the buttons, `--t4` 13 px: "Tool **<tool>**" · "On **PC (laptop)** / **Agent (VPS)**" (when known) · **"Queue: N"** · "N lines" (multi-line) · 🎤 'Voice: "haan" bolein' (voice approval allowed) · "Expires in **m:ss**" (one text node, `aria-live="off"`, written only when it changes, server-time corrected and clamped to the TTL, D11). The text `Queue: N` exists once in the DOM (confirm.spec) |
| Notices | "Voice se cancel hua" (`confirm-cancel-notice`, kept) |
| Keys | Esc in the card = Deny; **Enter never allows**; Space hold = approve (high risk) |
| Roles | `role="alertdialog"` with `aria-labelledby` (title) and `aria-describedby` (question); `aria-modal="false"` inline on desktop, `"true"` as the phone sheet. **No `aria-live` on the card** (§10.4) |
| Focus | Desktop: focus is not stolen (the owner may be typing); the toast's **Review** focuses Deny. Phone sheet: Deny focused first, focus trapped |

### 10.3 Long commands and the hold

- Above ~160 px the code block scrolls; **"Show full command (N lines)"** (`confirm-expand`, new; `aria-expanded`, "Show less") lets the card use the whole panel between the tabs and the composer (the tab content shrinks to zero); the block still scrolls when the window is too short.
- **High risk: "Hold to approve" stays disabled** (`aria-disabled="true"`, described by "Scroll to the end to enable approval") **until the whole command has been seen**: the block was scrolled to its end, or every line is on screen (it fits, or fits after expanding or after the window grew; re-checked with a `ResizeObserver`). Expanding alone never unlocks it. Then the announcer says "Whole command seen. Hold to approve is enabled." A press while disabled → toast "Read the whole command first". Deny always works; medium risk is never gated.
- **Hold:** 1 s by pointer, mouse (`mousedown`/`mouseup`, as the e2e helper dispatches), touch or Space, with a `scaleX` fill; a guard ignores the duplicate start of one press; releasing early → toast "Keep holding for 1 second to approve".

### 10.4 Queue, announcements and alerts

- One queue (`useConfirmQueue`, oldest first). The card shows the head; resolving it shows the next after 260 ms; any resolution (`confirm_resolved`, expiry, voice cancel) closes it.
- **Announcements:** one separate `role="status"` region (`#cf-announce`) says each new head once ("Approval needed: <label>, <risk> risk, on <where>. Expires in m:ss."), "Another approval is waiting: <label>. Queue N." for the others, and the countdown only at 60 s and 10 s.
- **Every new request:** toast "Approval needed: <label>" with **Review** (8 s; on phones the opening sheet is the notice instead), one 880 Hz chime (gain .12, 380 ms), vibration `[120, 60, 120]` on phones, and while the tab is hidden the title flash "⚠ Confirm: <tool>" (1 s) and a browser `Notification('Confirm: <tool>', { body: summary, tag: id })` when permission is granted. The title returns to the page title on visibility or resolution.
- **Decision log:** the last 20 decisions are kept (Approvals tab history) and logged to the trace as category `confirm` (as today); the Activity feed adds a row only after the resolution ("Command approved" / tool, §12.1), never while the request is pending and never with its summary.

### 10.5 Chat tab

| Part | Spec |
|---|---|
| List | `role="log"`, `aria-live="polite"`, `aria-label="Conversation"`, thin scrollbar; auto-scroll only while the reader is at the bottom; anchored to the bottom so a growing confirm card does not move the newest messages |
| Top | "Purane messages" (`chat-load-older`, GET `/messages?limit=50&cursor=`); after the last page "Start of conversation" |
| User message (`chat-msg` `data-role="user"`) | left avatar 45 px circle `--avatar-user` with "M" (`--t-avatar-user`, ui 600 19); bubble `--bubble-user`, radius 14, padding 13–16 / 19–20, text `--t-chat-user` 16.25/24; time bottom-right "9:42 PM" (`--t-bubble-time`). Source: **typed = no icon** (screen-reader text "typed"), voice = `mic` icon, reminder = `bell` icon, each with visually hidden text |
| Delivery (under the bubble, `chat-status-hint`) | Sending (clock) · Delivered ✓ (ack `sent`) · "Queued (agent offline)" · "Not delivered" ◆ + **Retry** (`chat-retry`) · "Failed: <reason>". Nothing is shown once delivered except on hover/focus |
| Assistant message (`data-role="assistant"`, `data-persona`) | avatar: the ring logo (34 px) on a near-black disc inside a 44 px ring (1.5–2 px `--acc-ring` of **that message's** persona, glow); author "JARVIS"/"ANNA" (ui 700 14, .3 px, that persona's `--acc-author`); time top-right (`--t-ai-time`); bubble `--bubble-ai`, text `--t-chat-ai`; a follow-up of the same `turn_id` drops avatar and header (time kept for screen readers). Past replies keep their persona |
| Feedback | thumbs up/down once per answer (`chat-feedback`, `chat-feedback-up`, `chat-feedback-down`), shown on hover/focus (always on touch) |
| System line | centred, `data-role="system"`: "Switched to ANNA" (after `settings_sync`) |
| Typing row | the persona ring avatar 24 px + three accent dots + "JARVIS jawab de raha hai…" / "ANNA jawab de rahi hai…" (`lang="ur-Latn"`) |
| Media chip | "🖼 <label>: View" (`chat-media-chip`, new) when `intelligence_update` arrives → Activity › Media |
| Empty (`chat-empty`, new) | ring logo, "No conversation yet", "<NAME> se kuch bhi poochein, bol kar ya likh kar.", three suggestion pills |
| Live captions (`chat-live-captions`, `chat-live-user`, `chat-live-assistant`) | a strip above the composer, faint accent wash: "● You (live)" white and "● <NAME> (live)" accent; the last 14 words; `aria-live="off"` |
| Send path | `ui_command chat {id, text}` with ack handling, 20 s delivery timeout, offline queue re-sent once (unchanged) |

### 10.6 Composer (`src/features/panel/Composer.tsx`)

Grid "input input input" / "attach kbd mic" inside `--composer` + edge (radius 14, margins `12vu 16 15vu 15`, padding `15vu 15 16vu`): the **field** (`--field`, radius 11–12, height `clamp(46px, 6.16vh, 58px)`, textarea 16.25 px, placeholder "Message JARVIS..." / "Message ANNA..." in `--t-placeholder`; grows to 140 px; Enter sends, Shift+Enter new line; its own focus ring on the box: 2 px `--acc-line` + 4 px glow) with the **send square** inside at the right (`--acc-deep`, 54 px, radius 12, filled `send` icon, `title="Send command"`); hints row: **paperclip** (`composer-attach`, `aria-disabled="true"`, name "Attach (coming soon)", click → toast "Attachments are not available yet"), plain text "Shift + Enter for new line" ("Shift+Enter: new line" below 390 px, hidden below 366 px and on touch), **mic + "Use microphone"** (`composer-mic`, push-to-talk §9.4; label "Listening…" while held). Task mode adds the chip "⚡ Task mode ×" above the field (`--chip-task-t`). Sending from Approvals or Tools switches to Chat.

### 10.7 Approvals tab (`src/features/panel/ApprovalsTab.tsx`, `approvals-panel`)

Rendered only while active. "Waiting": "Only the request above" or rows 2..N (`approvals-queue-row`: "Request 2 of 3", risk pill, summary, tool, expires in). "Decisions — Last 20" (`approvals-history-row`: ● Allowed / ◆ Denied / ○ Expired, nothing ran / ◆ Cancelled by voice, tool, the first line of the command, time). Empty: shield icon, "No approvals waiting", "Guarded tools always ask first." and the link "See guarded tools ›" (`#/settings/confirmations`, a link, never a button: its name contains "tools").

### 10.8 Tools tab (`tools-tab-panel`, kept)

Header row "Workspaces": **SIM lookup** button (`title="SIM lookup workspace"`, the only element with that title; opens the SIM dialog). **Jobs** (`jobs-list`, `job-row` with `data-status` running/done/error/cancelled and `data-id`, `job-progress-bar`, `job-progress-fill` `data-percent` + `scaleX`, `job-cancel`, `job-open-artifact` "PC par kholo", `jobs-empty`). **Tool log** (`tool-log-list`, filter `tool-log-filter` All / Running / Success / Error / Issues, expandable `tool-row` with `data-status`, `aria-expanded` on the inner button only; name, status pill, time, progress, duration, args, result, action; DESKTOP_* results with Play / Download via `/vortex/media`; `tool-log-empty`). Toast '<tool> chal raha hai' for medium-risk starts.

### 10.9 Phones

- The panel is the `#/chat` page (tab Chat): tabs (60 px), content, captions, composer directly above the tab bar; while the field has focus the tab bar hides (`html.typing`) and the composer sits on the keyboard.
- **Confirm sheet:** on every tab a pending request opens the card as a bottom sheet above the tab bar (scrim, focus trap, Deny focused first, **Approve & Run** first and full width, 50 px; "Press and hold for 1 second to allow." for high risk). **Decide later** (`confirm-later`, new) collapses it into a 48 px bar in Chat (`confirm-bar`, new: "1 approval waiting · <label>", accessible name with the expiry; tap = Review = re-open the sheet with Deny focused); on other tabs a toast "Approval is waiting in Chat — Open Chat to decide." The choice resets when the queue empties or a new request arrives; a new request always re-opens the sheet (toast + chime + vibration). Chat tab mark ◆ + count, hidden text ", N confirmations waiting".

---

## 11. Agents page (`src/features/agents/AgentsPage.tsx`, `page-agents`)

Three groups with a group label (14.5 px, a fading rule) and the reference card anatomy (`n-card`: radius 14, padding 20/22/22, header = 26 px icon + title 16.5/700 + subtitle 14.25 `--t4`, optional chevron). Grid: one column below 720 px of page width, two columns from 720 px, the persona cards side by side. Each card has its own error boundary.

| Group / card (anchor) | Contents | testids |
|---|---|---|
| **JARVIS & ANNA** › **Who answers** (`personas`) | "Both share memory, tools and approvals. The voice and the accent colour change." Two persona cards: mini orb (92 px canvas, `MINI` palettes), name (brand 700), "Voice Charon" / "Voice Aoede", trait ("Calm and precise" / "Warm and quick"); the answering one shows "● Answering now", the other a button "⇄ Make ANNA answer" (same switch as the eyebrow) | `persona-card-jarvis`, `persona-card-anna` (new) |
| › **Voice & wake** (`voice`) | "The voice room, listening and the wake word." Rows: Voice room (● Joined / Joining / Offline + identity), **<NAME> listening** switch, **Wake word** switch ("Say "JARVIS" before a command. Enforced: yes/no"), Follow-up window (read-only, edit in Settings), **Stop speaking**, link "Voice settings ›" | `assistant-agent-listening` (kept, `data-muted`), `agents-wake-toggle` (new) |
| › **Voice-ID** (`vid`) | "Only your voice can give commands." Meter: big score "82%" + "match, last turn", state pill (● Enforcing, above threshold / ◆ below / slate Monitor / Not enrolled), bar with the threshold tick, 0 % / Threshold 45 % / 100 %, Unknown voice (Ignored silently / Polite reply), Samples; **Enroll** / **Re-enroll** → enrollment dialog | `voice-id-meter` (`data-state`), `voice-id-meter-label`, `voice-id-enroll-link` (kept) |
| › **System Flow** (`flow`) | "Voice and vision come in, the core assistant decides, memory and tools act." Five nodes Voice → Vision → Core assistant → Memory → Tools with shape + value (`flowMapStatus.ts` logic); *unknown* in italics with a dashed ○; links animate only while voice or tools move data (never on phones or under reduced motion); phones: a 5-tile grid. The heading "System Flow" appears **once** in the DOM | `flow-voice-status`, `flow-vision-status`, `flow-core-status`, `flow-memory-status`, `flow-tools-status` (kept) |
| **Devices** (container keeps `operations-rail`) › **Agent (VPS)** (`agent`) | "The brain that listens and answers. Runs on the VPS." State, session, brain, mode, host, version, last seen, health from `/vortex/status`; Pause / Start-Wake mirrors; **Restart session** ghost button (danger on hover/focus; disabled with the title "Agent update pending: …" when rejected, or with the note `terminate-unsupported` when unsupported); restart notice "Agent session restart ho raha hai" | `agent-card` (new), `terminate-session`, `agent-session-restart-banner` (kept), `terminate-unsupported` (new) |
| › **Laptop (Desktop Node)** (`laptop`) | "Apps, files, screenshots and typing on your PC." State (unknown until the first `desktop_status`), mode off/read/full, locked, voice active, busy tool, brain, version + manifest ok, RTT, last seen; **Stop desktop** (danger) + the read-only/panic hint; **Location** row: city, coordinates (4 decimals), "updated X min ago" | `laptop-card`, `desktop-stop` (new), `device-location-card`, `device-location-updated` (kept) |
| › **This browser** (`browser`) | "Your dashboard link, the voice room and the camera." Bridge (● Live / ◆ Offline, base `/vortex`, "Secure link active"), Reconnect mirror, voice room + linked identity (Take over mirror), audio playback (Enable audio mirror), **Camera**: toggle, mirrored 4:3 preview with the placeholders 'Camera off' / 'Starting camera…' / the error name and text, link badge ('Offline', 'Live to agent (LiveKit)', 'Not linked — Take over', 'Fallback (bridge)', 'Local preview only'; red dot while sharing; "Not sending yet" while starting), **Share with agent** switch. The camera counts as on (pill red dot, Vision node, badge) only once the stream runs; a failed start turns the switch back off and leaves the error in the preview. Frame capture never depends on the page being visible (off-DOM `<video>` or `ImageCapture`) | `browser-card` (new), `camera-toggle`, `camera-link-badge`, `camera-share-switch`, `.camera-empty` (kept) |
| **Machines and place** › **PC (laptop)**, **Agent (VPS)** (`machines`) | CPU, Memory, Disk (label) bars, "updated Xs ago", ▲ on high values, "stale" after 30 s; subtitles host (`vitals_pc.host`) and the VPS line | `vitals-pc`, `vitals-pc-updated`, `vitals-pc-stale`, `vitals-vps`, `vitals-vps-updated`, `vitals-vps-stale` (kept) |
| › **Telemetry** | "Last 24 h of turns." Success %, latency s (sparkline with a hover readout), confusion %, samples, status, source ("Agent (VPS)" when brain = vps, else "Agent (PC)"; "Bridge") | `telemetry-card`, `telemetry-success`, `telemetry-latency`, `telemetry-confusion`, `telemetry-samples`, `telemetry-status`, `telemetry-source` (kept) |
| › **Map** (`map`) | Map focus title, **Open map** (OSM link), sandboxed lazy OSM iframe; empty "No map focus yet. Ask <NAME> to show a place." | `map-focus-panel`, `map-focus-empty`, `map-focus-open-link`, `map-focus-iframe` (kept) |

Phones: one column in the same order.

---

## 12. Activity page (`src/features/activity/ActivityPage.tsx`, `page-activity`)

Sub-tabs in the reference tab style (icon + label + 3 px underline), buttons with `aria-pressed`, `data-testid="activity-tab-timeline|logs|tasks|notes|media"`: **Timeline** (default) · **Logs** · **Tasks & reminders** · **Notes** · **Media**. Button names are unique among visible buttons: "Tasks & reminders" is the only one containing "tasks", "Notes" the only one containing "notes", and no filter chip says "Tools" ("Tool runs"). Sub-tab state (filter, pause buffer, INFO toggle, selected plan, featured item) lives in the stores. On narrow screens the strip scrolls sideways with an edge fade (`data-more="left|right|both"` + `mask-image`).

### 12.1 Timeline (`alerts-card`, `timeline-row`) and the activity feed store

- **Alerts card** on top: system alerts (`system-alert-row`: severity shape + word, title, ×count, last seen; ≤ 10) and WARN/ERROR/CRITICAL log alerts (`log-alert-row`, `data-level`; ≤ 10); "Normal. No alerts." when empty.
- Filter chips: All · System · Voice · Devices · Approvals · Tool runs · Reminders · Media.
- The full feed, newest first, ×N for repeats, the same row anatomy as Recent Activity.

**Feed store** (`src/features/activity/feedStore.ts`, client-side, no contract change; fed by the X-96 subscriptions):

| Kind | Trigger | Title / subtitle (examples) | Dot |
|---|---|---|---|
| system | socket up + agent online; agent offline/paused/restarting | "System online" / "All systems operational"; "Agent paused"; "Agent offline" / "Wake it from System" | green / amber / red |
| sync | `init_state` | "Agent sync complete" / "JARVIS & ANNA ready" | grey |
| voice | room joined + agent in room; mic published | "Voice service ready" / "Microphone initialized" (or "Voice room joined, mic off") | blue |
| voice (problem) | room lost, audio blocked, agent absent | "Voice offline" / reason | amber / red |
| device | `desktop_status` transitions | "Laptop connected" / "<host> • 3 devices online" (host from `vitals_pc.host`); "Laptop locked"; "Desktop stopped (read-only)"; "Laptop panic" | violet / amber / red |
| alert | `system_alert` | title / "×count, severity" | blue / amber / red |
| log | ERROR / CRITICAL log | text / category | red |
| reminder | `task_notification`, `reminder_due` | description / "Spoken aloud" or "Screen only" | blue |
| approval | after a decision or `confirm_resolved` | "Command approved" / "<tool>"; "Command denied"; "Approval expired, nothing ran"; "Cancelled by voice" | green / red / grey / grey |
| tool | `tool_update` error, or medium/high-risk success | "<tool> failed" / reason; "<tool> finished" | red / green |
| media | `intelligence_update`, `map_focus_update` | "JARVIS shared an image" / label; "Map: Lahore" | blue |
| persona | persona switch confirmed | "ANNA is answering" / "Voice Aoede" | blue |
| memory | `memory_cleared` | "Short-term memory cleared" / "Backup saved" | grey |
| voice rejected | `turn_rejected` | "Voice ignored" / reason | amber |

Rules: newest first; repeats within 60 s fold into ×N; keep 200; Overview shows 4, Timeline all. **Never** include chat text, pending confirmation summaries or tools, reasoning steps, task descriptions from `task_sync`, telemetry ticks or INFO logs (they would duplicate strings the e2e suite asserts strictly). Times are local "h:mm AM/PM".

### 12.2 Logs (`system-trace-panel`, `layout-zone-trace` kept)

Header **"System Logs"** (once in the DOM) + ● Live / ◆ Offline (never the word "connected"). Toolbar: WARN+ chip, INFO toggle (`trace-info-toggle`), **Filter logs** (`trace-log-filter`; the input carries the focus ring), Pause/Resume with the buffered count (`trace-log-pause`), Clear (client only, `trace-log-clear`), "▲ Bridge ne N purane logs drop kiye" (`trace-log-dropped`; the word visually hidden below 1440). Terminal (`trace-log-terminal`, `role="log"`, `tabindex="0"`; lines `trace-log-line` `data-level`: time, level, category, text, ×N; 300 lines; "Waiting for bridge events"); three columns at ≥ 1440 of page width, one flowing line below, phones put the category on a second line with ×N after the message. **All levels** pane (`all-logs-list`, new: today's chat 'logs' tab). **System feed** (`trace-system-feed`, last 6 telemetry/diagnostics/audit lines). **Reasoning** card (`trace-reasoning-card`, own error boundary: history select of 5 `trace-reasoning-select` with ellipsis + title, intent, `trace-ambiguity-badge` + source badge, time, top-3 `trace-confidence-bars`, `trace-plan-progress` "Step c/t - action (status)", up to 6 `trace-reasoning-step`, empty hint). Window ≥ 1180 px: terminal and Reasoning both visible (side by side when the page column is ≥ 900 px, else stacked; Reasoning is a named region). < 1180 px: Logs / Reasoning tabs (`system-trace-tabs`, `trace-tab-logs`, `trace-tab-reasoning`; `role="tablist"`/`tab`/`tabpanel` with arrow keys). Data: `new_log`, `vortex_log_batch`, `log_batch`, `new_logs`, `reasoning_update`, `plan_progress(_update)`, `telemetry_update`, `diagnostics_update`, `audit_update`, `turn_rejected` (toast + trace line + feed row).

### 12.3 Tasks & reminders (`tasks-tab-panel` kept)

Refresh (`tasks-refresh-btn`); rows (`task-row` `data-id`, `data-source`): kind pill (task / reminder), status pill, countdown (`task-countdown`, "In 5 min 3 s" / "Overdue 2 min", D11), due clock, description, two-step Cancel (`task-cancel-btn` → `task-cancel-confirm-panel` with "Yes, cancel" `task-cancel-confirm` / "Keep" `task-cancel-dismiss`); `tasks-list`, `tasks-empty`. Below: reminder history (`reminder-history-row`, new: delivered notifications, spoken / screen only, ack state).

### 12.4 Notes (`memory-tab-panel` kept)

Search (`memory-search`), rows (`memory-row`: role pills user/assistant, source pills extractor/fact/vector, relative time), `memory-list`, `memory-empty`, "Saved short-term memory saaf karo" (`memory-clear-btn`) → inline confirm `memory-clear-confirm-panel` with "Haan, saaf karo" (`memory-clear-confirm`) / Cancel (`memory-clear-cancel`), note "Live voice conversation par asar nahi hota." (`lang="ur-Latn"`).

### 12.5 Media (`intelligence-panel` kept)

Featured item: JSON key/value table (`intelligence-json`), link (`intelligence-link`), audio (`intelligence-audio` → `intelligence-media-audio`), image (`intelligence-image` → `intelligence-media-image`; click → lightbox), LAN-only notice "Sirf PC network par" + Copy URL (`intelligence-lan`, `intelligence-lan-audio`, `intelligence-copy-lan`), "Loading media…" / "Media unavailable" + Retry as 16:9 placeholders; collapse toggle (`intelligence-toggle`); history chips of the last 20 (`intelligence-history`, `intelligence-history-<id>`; ellipsis at 150 px, full label in `title`, `aria-current` on the shown item). Empty: "Nothing shared yet". Every new item also raises the toast "<NAME> shared an image: View", a feed row and the chat media chip. Signed media with one re-sign on 401/403 (unchanged).

---

## 13. Settings page (`src/features/settings/SettingsPage.tsx`, `page-settings` + `settings-drawer` kept)

Layout: an index (sticky; a 214 px side list from 860 px of page width, otherwise a horizontal strip with edge fades) + the sections as `n-card`s with the reference header anatomy. Every field shows applied / pending / error (`settings-status-<field>`), "Agent update pending" after 10 s (`settings-agent-pending-<field>`), reverts after 5 s with the toast 'Setting sync timeout — value revert ho gaya', and is disabled while the bridge is offline. Opening the page sends no `request_state` (D3). Field ids are the product's camelCase names (`turnMode`, `wakeFollowupS`, `transcriptScript`, `speakTypedReplies`, `quietHours`, `wakeWord`, `voiceIdReject`, `voiceIdThreshold`, `voiceId`, `automationDesktopControl`, …, `visionMode`); the wire keys in `settings_update` stay snake_case. Segmented controls and switches carry `data-active` mirroring `aria-pressed`/`aria-checked`; names 'Fast', 'Native', 'Always', 'Polite', 'Sirf poochne par' stay unique.

| Section (anchor) | Subtitle | Controls (testids kept unless marked) | Sends |
|---|---|---|---|
| Session (`session`) | Who is signed in on this device. | "Matloob (owner)" ● Signed in, Session ends HH:MM, **Sign out** (`settings-logout`) | POST /auth/logout |
| Connection (`connection`) | How this dashboard reaches the VPS. | Bridge base `/vortex`; status tiles Bridge / Agent (VPS) / Laptop / Voice (shape + word, no chip testids); "Secure link active. Cookie session over TLS." / "Bridge offline"; **Reconnect** (secondary; note "Reconnect reloads the dashboard for now; voice rejoins with the mic off." until X-96) | — |
| Persona (`persona`) | Who answers by default. | JARVIS / ANNA segmented (`settings-persona`) | `{persona}` |
| Voice & conversation (`voice`, `settings-voice-conversation`) | How JARVIS & ANNA speak and when they answer. | Voice (30 voices or `available_voices`; defaults Charon / Aoede) `settings-voice-select` · Language auto/ur/hi/en `settings-language-select` · Turn mode Secure gated / Fast `settings-turn-mode` + `settings-turn-mode-restart-badge` · Follow-up 0/10/20/30 s `settings-wake-followup-select` · Transcript script Roman / Native `settings-transcript-script` · Speak typed replies Auto / Always / Never `settings-speak-typed-replies` · Quiet hours `settings-quiet-hours` (**commit on blur or Enter**, D12) | `{voice}`, `{language}`, `{turn_mode}`, `{wake_followup_s}`, `{transcript_script}`, `{speak_typed_replies}`, `{quiet_hours}` |
| Voice-ID & wake (`voice-id`, `settings-voice-id-block`) | Who may command, and how a command starts. | Wake word `settings-wakeWord` (enforced) · Agent listening `settings-muted` · Voice-ID `settings-voiceId` (disabled until enrolled) + `settings-voice-id-monitor-badge` · Reject Silent / Polite `settings-voice-id-reject` · Threshold 0.20–0.90 `settings-voice-id-threshold` (**commit on release**, D12) · Enroll / "Voice-ID enrolled" `settings-voice-enroll-link` | `{wake_word}`, mute/unmute, `{voice_id}`, `{voice_id_reject}`, `{voice_id_threshold}` |
| Automation (`automation`) | What the laptop may do for you. | Desktop control, Clipboard helper, Speak clipboard tips, Voice may confirm high-risk actions (`settings-automation*`) | `{automation:{…}}` |
| Vision (`vision`) | When the camera frames reach the agent. | Live / 'Sirf poochne par' (`settings-vision-mode`) | `{vision_mode}` |
| Confirmations (`confirmations`) | These tools always ask before they run. | Guarded tools (`settings-guarded-tools-list`) + "list .env (JARVIS_CONFIRM_EXTRA/SKIP) se badalti hai" | — |
| Notifications (`notifications`, new section) | Reminders and approvals when this tab is in the background. | Desktop notifications on / off [Turn on] / blocked | `Notification.requestPermission()` |
| SIM lookup (`sim`, new section) | Owner and network of a Pakistani number, through an authorized provider. | Provider configured or "Authorized source configure nahi hua (SIM_LOOKUP_API_URL / SIM_LOOKUP_API_KEY)"; **Open workspace** (no `title`) | `open_sim_panel` |
| About (`about`, new section) | Versions on this device. | Version/build; with `?debug=1` the LiveKit identity (K-10 `window.__jarvisDebug.identity`) | — |

Optional later, only if the owner asks: Persona › Style and Emotional intelligence (the hook exists, no control today, F112).

---

## 14. Dialogs, overlays and full screens

| Overlay | Spec | testids |
|---|---|---|
| **Dialog primitive** | `n-dlg`: radius 16 (phones: a 20 px-top sheet, max-height 94 %), `--card` + edge, `--shadow-dlg`, scrim `--scrim` + `blur(4px)`; `role="dialog"`, `aria-modal`, focus trap, initial focus, Esc, focus returns to the opener; rendered only while open | — |
| **Voice enrollment** | opened from Agents › Voice-ID, Settings link, Voice popover. Prerequisites with shapes ("Voice room joined" (never "connected"), "Mic off hai — pehle mic on karein", "Agent mute hai — pehle unmute karein"), 3 sentences (the first follows the persona: "<NAME> sun <raha\|rahi> hai? Aaj ka kaam shuru karte hain."), live mic level bar (`scaleX`) + dB, "Ns / 20s" + quality, success (score, samples), error ("Enrollment failed: …"), 3-min timeout; Start, Commit, Cancel/Close | `voice-enroll-dialog`, `voice-enroll-connect`, `voice-enroll-mic-hint`, `voice-enroll-agent-muted`, `voice-enroll-mic-level`, `voice-enroll-progress`, `voice-enroll-success`, `voice-enroll-timeout`, `voice-enroll-start`, `voice-enroll-commit` (kept); `voice-enroll-error` (new, optional) |
| **SIM Access** | opened from the Tools tab, Settings › SIM lookup, agent `sim_panel_open`. "SIM Access", "Authorized lookup workspace", close (`title="Close SIM access"`), provider banner, status chip (idle, loading, blocked, validation error, rate limited, success, offline, not configured), purpose (≥ 5 chars when required), query + search (`.sim-search button`), offline hint, masked result card, 20 s timeout 'Agent offline ya jawab nahi' | `sim-dialog`, `sim-provider-banner` (`data-configured`), `sim-status-chip` (`data-status`), `sim-purpose-input`, `sim-query-input`, `sim-agent-offline-hint`, `sim-identity-title`, `sim-cnic-masked`, `sim-phone-masked`, `sim-result-message` (kept) |
| **Lightbox** | image + caption + close; `role="dialog"`, Esc works (on a focusable element, D9), z 110 | `intelligence-lightbox` (kept) |
| **Toasts** | custom renderer for react-hot-toast: radius 12, `--card` + edge, `--shadow-toast`, `StatusShape` by kind (ok/warn/err/busy/off/acc), text + sub line + optional action (Review, View, Play, Download, Turn on) + dismiss; at most 3; bottom centre of the main column on desktop (`left = sidebar + main/2`), top on phones; `role="status"`; Roman Urdu texts with `lang`. Texts kept: 'Reconnected', 'Reconnect failed', 'Spoken aloud', 'Screen only', 'short term cleared · Backup: <path>', 'Setting sync timeout — value revert ho gaya', '<tool> chal raha hai', 'Agent offline', 'Command abhi allowed nahi', 'Agent abhi tak online nahi…', the `turn_rejected` texts, Electron 'JARVIS & ANNA backend band ho gaya (code N)'. New: "ANNA is answering. Voice Aoede.", "Approval needed: <label>", "<NAME> shared an image: View", DESKTOP_* toasts with Play / Download (X-67) | `role="status"` |
| **Panel error** | inside the crashed card only: ◆ "<Card> stopped working", "The rest of the dashboard keeps running.", **Retry**; one boundary per card (each Agents/Activity/Settings card, hero, Quick Stats, Recent Activity, panel tabs, confirm card, popovers, dialogs) | `panel-error`, `panel-error-retry` (kept) |
| **Session check** | full screen (z 200), page background + the ring logo breathing (static under reduced motion) + "Checking session…" ("Reconnecting…" during the interim Reconnect reload) | — |
| **Login** | page background with the reference blooms; glass card (radius 16, blur 14, `--card` + edge): ring logo 58 px, "JARVIS & ANNA" (brand 600 24), "Your AI Command Center", "Owner session: password and authenticator", Password (show/hide eye button), Authenticator code (6 digits, mono, spaced), 'Recovery code use karein' (link button) + recovery field, error line (401 'Password ya code ghalat hai', 429 'Bohat zyada koshishen - N s baad', 503 'Login abhi set nahi hua (owner enroll karein)', network 'Bridge offline'), expired hint 'Session expire ho gaya…', primary **Sign in** / "Signing in…" (`--acc-btn`), foot "Socket, voice and camera start only after you sign in." | `login-password`, `login-totp`, `login-recovery`, `login-error`, `login-submit` (kept) |
| **Electron offline page** | `electron/offline.html`, tokens inline (no external CSS): ring logo, "JARVIS & ANNA: VPS unreachable", "Dashboard abhi load nahi ho saka…", **Retry** | texts 'VPS unreachable', 'Retry' (kept) |
| **Window controls** | Electron only: minimise, maximise/restore, close-to-tray, fixed top-right in the header reserve | `window-minimize`, `window-maximize`, `window-close` (kept) |

---

## 15. Persona switch (JARVIS ⇄ ANNA)

- **Where:** the hero eyebrow (primary), Agents › Who answers ("Make ANNA answer"), Settings › Persona (`settings-persona`), and by voice (the UI follows `settings_sync`).
- **Behaviour (unchanged contract `settings_update {persona}`):** optimistic; `data-persona` on `<html>` switches at once; `chip-persona-spinner` + `aria-busy` until `settings_sync` (never on a timer). After sync: chat system line "Switched to ANNA", toast "ANNA is answering. Voice Aoede.", feed row. No sync within 5 s: switch back instantly (no animation, `:root.no-acc-tr`) + toast 'Setting sync timeout — value revert ho gaya' with "JARVIS hi jawab de raha hai."; no chat line.
- **Motion:** `document.startViewTransition(() => flushSync(() => setPersonaLocal(p)))` where available and reduced motion is off: `clip-path: circle(0 → r)` from the orb centre on `::view-transition-new(root)`, 760 ms `cubic-bezier(.2,.7,.1,1)`, `::view-transition-old/new(root) { animation: none; mix-blend-mode: normal }`, `:root.vt-run` while it runs, the orb renderer snaps its palette. Elsewhere the accents switch at once and only the orb palette eases. The name rolls (§4.7). Reduced motion: instant.
- **Copy and placeholder:** "Message ANNA...", the typing row, titles and helper gender follow (§16).

---

## 16. Copy

### 16.1 Owner and constants (`src/shared/owner.ts`)

`OWNER = { name: 'Matloob', initial: 'M', label: 'Matloob (owner)' }` (the session user is the generic `owner`, so this is a UI constant); `QUOTE = '“Ideas become reality, faster.”'` (both personas).

### 16.2 Persona table (`src/shared/persona.ts`)

| Key | JARVIS | ANNA |
|---|---|---|
| `name`, `initial`, `voice`, `trait` | JARVIS, J, Charon, Calm and precise | ANNA, A, Aoede, Warm and quick |
| `g` (continuous) | raha | rahi |
| `fut` (future) | ga | gi |

Strings that follow it: typing row "<NAME> jawab de <g> hai…"; Stop title "<NAME> ko chup karao"; listening title "<NAME> mic se nahi sune<fut>; typed chat chalti rahegi"; enrollment sentence 1 "<NAME> sun <g> hai? Aaj ka kaam shuru karte hain."; status lines and helpers of §9.2; placeholder "Message <NAME>..."; empty chat "<NAME> se kuch bhi poochein, bol kar ya likh kar."; map empty "No map focus yet. Ask <NAME> to show a place."; wake hint "Say "<NAME>" before a command." Today's persona-blind strings to replace: `useChat.ts:109`, `AgentControls.tsx:106,260`, `AssistantCore.tsx:239`, `VoiceEnrollDialog.tsx:11`.

### 16.3 Helper copy by voice state (`lang="ur-Latn"`)

| State | Copy |
|---|---|
| offline | Voice abhi band hai. Chat aur controls chalte rahenge. (+ "Voice: <error>") |
| offline, other tab or device | Voice doosre tab ya device par chal rahi hai. Take over dabayein; chat chalti rahegi. |
| joining | Voice room join ho raha hai. Chat chalti rahegi. |
| agent absent | <NAME> room me nahi. Wake dabayein; chat chalti rahegi. |
| agent restarting | Agent session restart ho raha hai. Thodi der mein wapas aa jayega. |
| audio blocked | Browser ne awaaz rok di hai. Enable audio dabayein. |
| muted | <NAME> mic se nahi sune<fut>; typed chat chalti rahegi. |
| mic off | Mic band hai. Start Listening dabayein ya type karein. |
| idle, listening, thinking, speaking | (the quote) |

The mic-off line replaces today's footer copy "Mic off — room linked hai. Mic on dabao jab bolna ho…" (nothing is lost: the listening meaning is in the muted copy and the listening title).

### 16.4 Page titles and subtitles

§6.2. Card titles and subtitles: §7.7, §7.8, §11, §13 (from the prototype; precedence §0).

---

## 17. Mapping from today's files

### 17.1 File by file (`stonix_ui/src/…`)

| Today | v2 home | Task |
|---|---|---|
| `app/App.tsx` | providers, `<MotionConfig reducedMotion="user">`, `Sprite`, `data-persona`, `.is-electron`, session check | X-69, X-80, X-90, X-93, X-100 |
| `features/command-center/Dashboard.tsx` | the authenticated root; renders `AppShell` (`features/shell/AppShell.tsx`, new) | X-80 |
| `…/components/TopBar.tsx` | `features/shell/PageHeader.tsx` + `StatusPills.tsx` + popovers; its other controls wait in the legacy strip and the file is deleted when the last one has moved | X-78, X-99, X-79, X-92, X-83, X-88, X-104 |
| `…/components/OperationsRail.tsx` | Agents › Devices and Machines cards (`features/agents/*`); Alerts → Activity › Timeline `AlertsCard`; `operations-rail` stays on the Devices group | X-85, X-79 (Laptop), X-108 (Alerts) |
| `…/components/CameraPanel.tsx`, `cameraState.ts` | inside `BrowserCard` (logic unchanged) | X-85 |
| `…/components/FlowMap.tsx`, `flowMapStatus.ts` | `SystemFlowCard` on Agents | X-81 |
| `…/components/AssistantCore.tsx` | `features/overview/HeroCard.tsx` (orb, waveform, status, buttons, quote); Terminate → `AgentVpsCard`; Listening → `VoiceWakeCard` + Voice popover; Voice-ID meter → `VoiceIdCard` | X-104, X-97, X-85, X-107 |
| `…/components/SystemTrace.tsx` | Activity › Logs (`LogsTab`) | X-87 |
| `…/components/ChatPanel.tsx` | `features/panel/RightPanel.tsx` (tabs, chat list, Tools tab) + `Composer.tsx` + `ApprovalsTab.tsx`; its logs / notes / tasks tabs → Activity sub-tabs | X-83, X-106, X-101, X-108, X-87 |
| `…/components/SettingsPanel.tsx` | `features/settings/SettingsPage.tsx` | X-88 |
| `…/components/SimAccessDialog.tsx` | same file, dialog semantics | X-89 |
| `…/components/MobileTabBar.tsx` | `features/shell/PhoneTabBar.tsx` (5 tabs) | X-82 |
| `…/hooks/useCommandCenter.ts` | its socket listeners move to the subscription registry; exports kept | X-96 |
| `…/hooks/useLiveKitVoice.ts`, `features/jobs/JobsPanel.tsx`, `features/auth/useAuth.ts`, `mergeFromInit` | untouched (legacy, F113) | — |
| `features/auth/LoginScreen.tsx` | neon login | X-90 |
| `features/auth/AuthProvider.tsx`, `api.ts` | session-expiry listener through the registry; behaviour unchanged | X-96 |
| `features/chat/useChat.ts`, `liveCaptions.ts`, `types.ts` | persona copy, delivery states, captions strip | X-72, X-83 |
| `features/confirm/ConfirmDialog.tsx` | `features/confirm/ConfirmCard.tsx` (pinned card / phone sheet) | X-84 |
| `features/confirm/useConfirmQueue.ts` | the single queue (+ history, announcements, hidden-tab alert) | X-84, X-101, X-102 |
| `features/intelligence/*` | Activity › Media + chat media chip + lightbox | X-86 |
| `features/jobs/useJobs.ts`, `features/tools/*` | Tools tab | X-83 |
| `features/location/MapFocus.tsx` | Agents › Map | X-85 |
| `features/memory/*` | Activity › Notes | X-108 |
| `features/settings/useSettings.ts` | commit on blur/release (D12), persona pending | X-88, X-104 |
| `features/status/AgentControls.tsx` | its rows move into the Agent and Voice popovers and the Agents cards | X-99, X-107 |
| `features/status/useAgentStatus.ts` | one instance (store), no `request_state` on mount (D3) | X-96 |
| `features/tasks/*` | Activity › Tasks & reminders | X-108 |
| `features/telemetry/TelemetryCard.tsx` | Agents › Machines | X-85 |
| `features/voice/LiveKitProvider.tsx` | banners become the hero's inline actions | X-97 |
| `features/voice/VoiceEnrollDialog.tsx` | neon dialog + error/timeout | X-89 |
| `features/voice/audioEngine.ts`, `e2eHooks.ts`, `speechSim.ts` | kept (X-74 done); coarse sampling, orb stats, `referenceFrame()` | X-75, X-97, X-109 |
| `features/voice/useAudioLevels.ts`, `useLocalMicLevel.ts`, `useVoiceRoom.ts`, `voiceMeter.ts`, `livekitToken.ts`, `requestMicrophonePermission.ts` | data paths unchanged; mic-off default, push-to-talk | X-97, X-106 |
| `shared/realtime/socket.ts` | + `subscriptions.ts` registry | X-96 |
| `shared/theme/commandTheme.ts` | `var()` references | X-69 |
| `shared/ui/Panel.tsx` | `Card` primitive; `StatusDot` → `StatusShape` | X-73 |
| `shared/ui/ErrorBoundary.tsx` | neon fallback | X-91 |
| `shared/ui/WindowControls.tsx` | fixed in the reserve; renamed toast | X-100, X-70 |
| `shared/voices.ts` | used by `persona.ts` | X-72 |
| `toast/provider.tsx`, `toast/index.ts` | neon renderer | X-92 |
| `assets/styles/index.css` | `tokens.css` + `neon/*.css`; legacy rules removed as components go | X-69 … |
| `main.tsx` | font imports, e2e hook | X-69 |
| `index.html`, `public/favicon/*`, `electron/main.cjs`, `electron/offline.html`, `README.md` | branding (§19) | X-70, X-71, X-90 |

### 17.2 New files

`src/assets/styles/tokens.css`, `src/assets/styles/neon/*.css`, `src/shared/ui/{StatusShape,Card,Tile,Pill,Button,Switch,Segmented,Badge,Bar,LogoRing,Dialog}.tsx`, `src/features/confirm/useHiddenTabAlert.ts`, `src/shared/ui/icons/{Sprite,Icon}.tsx`, `src/shared/{owner,persona,time}.ts`, `src/shared/realtime/subscriptions.ts`, `src/features/shell/{AppShell,Sidebar,SilkWave,PageHeader,StatusPills,AccountMenu,PhoneTabBar,router,useLayout}.tsx|ts`, `src/features/shell/popovers/{Popover,AgentPopover,DevicesPopover,VoicePopover}.tsx`, `src/features/overview/{OverviewPage,HeroCard,QuickStatsCard,RecentActivityCard,usePersonaSwitch}.tsx|ts`, `src/features/voice/orb/{orbData,palette,orbRenderer,waveform,OrbCanvas}.ts|tsx`, `src/features/voice/voiceState.ts`, `src/features/panel/{RightPanel,PanelTabs,ChatList,Composer,ApprovalsTab,ToolsTab}.tsx`, `src/features/confirm/ConfirmCard.tsx`, `src/features/agents/{AgentsPage,PersonasCard,MiniOrb,VoiceWakeCard,VoiceIdCard,SystemFlowCard,AgentVpsCard,LaptopCard,BrowserCard,MachinesCards,MapCard}.tsx`, `src/features/activity/{ActivityPage,TimelineTab,AlertsCard,feedStore}.tsx|ts`, `src/features/settings/SettingsPage.tsx`, `scripts/testid-inventory.mjs` (full), `scripts/render-favicons.mjs`, `tests/testids.baseline.txt`, `tests/visual/*`, new e2e specs (§18.3), `assets/brand/jarvis-anna-mark.svg`.

### 17.3 Every feature has a home

`mapping.md` §3 lists all 113 features (F1–F113) with their new home and testids; §11–§14 above repeat the homes per page. Summary by audit section: global layers and screens F1–F16 → §6.5, §7.4, §14; TopBar F17–F32 → §6.1–§6.4, §7.2, §7.5, §10.8; operations rail F33–F42 → §11, §12.1; core F43–F58 → §7, §8, §11, §12.5; trace F59–F65 → §12.2; chat panel F66–F83 → §10, §12; overlays F84–F101 → §10.2–§10.4, §13, §14; feedback (audit 1.8) → §14 toasts; contracted UI F102–F111 → §6.4.2, §6.4.4, §11, §13; legacy F112–F113 → untouched. X-95's audit (§21.9) checks every row.

---

## 18. Testids and the e2e suite

### 18.1 Testids

- **Kept** (the 180 static ids except `ops-drawer-backdrop`, and the dynamic families `settings-<field>`, `settings-status-<field>`, `settings-agent-pending-<field>`, `mobile-tab-<tab>`, `vitals-pc|vps(-updated|-stale)`, `chip-*-spinner`, `chip-bridge`, `chip-agent`, `chip-voice`, `chip-wake`, `chip-persona`, `intelligence-history-<id>`, `intelligence-lan(-audio)`, `flow-*-status`): homes in §6–§14.
- **New:** `app-ready`, `nav-overview`, `nav-agents`, `nav-activity`, `nav-settings`, `page-title`, `page-overview`, `page-agents`, `page-activity`, `page-settings`, `brand-mark`, `speaker-orb`, `hero-persona-name`, `hero-mic-off`, `hero-stop`, `hero-helper`, `qs-card`, `qs-open`, `qs-cpu`, `qs-memory`, `qs-disk`, `qs-devices`, `activity-card`, `activity-open`, `activity-row`, `approvals-count`, `approvals-panel`, `approvals-queue-row`, `approvals-history-row`, `confirm-expand`, `confirm-later`, `confirm-bar`, `chat-empty`, `chat-media-chip`, `composer-attach`, `composer-mic`, `devices-menu`, `voice-menu`, `chip-laptop`, `desktop-stop-menu`, `desktop-stop`, `persona-card-jarvis`, `persona-card-anna`, `agents-wake-toggle`, `agent-card`, `laptop-card`, `browser-card`, `alerts-card`, `timeline-row`, `activity-tab-timeline|logs|tasks|notes|media`, `all-logs-list`, `reminder-history-row`, `mobile-tab-settings`, `terminate-unsupported`, optional `voice-enroll-error`.
- **Moved (same id, new element):** `chip-persona` (hero radiogroup), `topbar-task-bell` (account block), `logout` (account menu), `topbar-reconnect` (Devices popover), `topbar-mic-toggle`, `topbar-agent-listening`, `topbar-stop`, `chip-wake` (Voice popover), `voice-toggle` (Start Listening), `swarm-mode-toggle` (Run Task), `confirm-dialog` (pinned card), `settings-drawer` (Settings page), `layout-zone-core` (hero), `layout-zone-trace` (Activity › Logs), `operations-rail` (Agents › Devices), `mobile-tab-core|chat|ops|trace` (Overview, Chat, Agents, Activity).
- **Alias:** `ops-drawer-toggle` = a `<span>` filling the nav Agents link (a click navigates to Agents, where `operations-rail` and `camera-toggle` are).
- **Retired (the only one):** `ops-drawer-backdrop` (no drawer any more; no spec uses it). The inventory baseline lists it under `retired:` with that reason.

### 18.2 Uniqueness rules (strict locators; pages stay mounted)

- `getByTestId`, `getByText` and `getByTitle` also match **hidden** elements, so duplicates anywhere in the DOM fail strict mode: one testid per element; `title="Settings"` only on `nav-settings`; `title="SIM lookup workspace"` only on the Tools tab button; the placeholder `Message JARVIS/ANNA` only in the composer.
- Text the suite asserts strictly exists once: "System Flow", "System Logs", 'SIM Access', 'Queue: N', pending summaries and tool names ('run_shell', 'First queued action', 'echo deny-test'), reasoning steps, chat message text, 'Queued (agent offline)', 'short term cleared' + 'Backup: …' (toast only). Hence the feed exclusions (§12.1), closed popovers and dialogs not rendered, and the Approvals tab rendered only while active.
- `getByRole` skips hidden elements: among visible buttons only one name contains "notes", "tasks" or "tools" (case-insensitive substring); links may (they are not buttons).
- "connected" (substring) appears in the header pill ("Connected Devices"), so confirm.spec's `getByText('connected')` becomes a `chip-bridge` `data-active="true"` check (X-78).

### 18.3 Spec edits, each in the commit that moves its target (Cursor lane)

| Task | Spec edits |
|---|---|
| X-70 | `auth.spec:37`, `dashboard.spec:11`, `voice.spec:11`: `/STONIX/` → `await expect(page).toHaveTitle(/JARVIS & ANNA/)`; `tests/mock-bridge/fixtures/init_state.json:6` → "Mock bridge ready. JARVIS & ANNA dashboard online." |
| X-78 | `confirm.spec:39` `getByText('connected')` → `expect(page.getByTestId('chip-bridge')).toHaveAttribute('data-active', 'true')` |
| X-80 | `helpers.ts` and `helpers/login.ts`: the post-login marker `getByText(/System Flow/i)` → `getByTestId('app-ready')` (visible / hidden), plus `gotoPage(page, 'agents' \| 'activity' \| 'settings', sub?)` (desktop clicks `nav-*`, phones `mobile-tab-*`, then `activity-tab-<sub>`); the marker lines in `auth.spec`, `intelligence.spec`, `trace.spec` |
| X-104 | `dashboard.spec`: `getByRole('radiogroup', { name: 'Who answers' })` visible |
| X-99 | `controls-agent.spec`: click `chip-voice` before `topbar-stop` / `topbar-agent-listening`; `settings.spec`: before `chip-wake`; `voiceid.spec`: before `topbar-agent-listening`; `status.spec`: click `chip-bridge` before `topbar-reconnect` |
| X-79 | new `laptop.spec` (the seven laptop states, `desktop-stop-menu` and `desktop-stop`) |
| X-92 | `auth.spec`: click `topbar-task-bell` before `logout` |
| X-83 | `sim.spec` `openSimDialog`: click `getByRole('button', { name: 'tools' })` first |
| X-85 | `camera.spec`, `location.spec` (before and after reload), `vitals.spec`, `controls.spec` (Terminate), `voice-glue.spec` (Terminate): `gotoPage('agents')`; `responsive.spec`: the phone Ops tab (`mobile-tab-ops`) is Agents and still shows `operations-rail` and `camera-toggle`; the tablet test keeps clicking `ops-drawer-toggle` (the alias on nav Agents) |
| X-81 | `dashboard.spec`, `trace.spec` (three FlowMap tests): 'System Flow' after `gotoPage('agents')`; `responsive.spec`: the phone core tab expects `voice-status` only, and the tablet test checks 'System Flow' after the `ops-drawer-toggle` click instead of before it |
| X-107 | `voiceid.spec`: `gotoPage('agents')` before the meter and Enroll |
| X-108 | `memory.spec`, `tasks.spec`, `voice-glue.spec`: `gotoPage('activity')` before 'notes' / 'tasks'; `tasks.spec`: `getByText(/Call home now/).first()`; `vitals.spec`: `gotoPage('activity', 'timeline')` before `log-alert-row` |
| X-87 | `trace.spec` `beforeEach`: `gotoPage('activity', 'logs')`; `dashboard.spec` 'System Logs' after it; `responsive.spec` trace tab → click `activity-tab-logs` |
| X-86 | `intelligence.spec`: `gotoPage('activity', 'media')` |

Unchanged: `chat`, `contracts`, `tools`, `voice-settings`, `audio-engine`, Electron. New specs: `layout.spec`, `header-layout.spec`, `presence.spec`, `persona-copy.spec`, `persona-switch.spec`, `reconnect.spec`, `panel-error.spec`, `confirm-hidden.spec`, `laptop.spec`, `a11y.spec`, `visual.spec`, `perf.spec`, `refdiff.spec` (§21).

---

## 19. Branding changes

| Where | Today | New |
|---|---|---|
| `src/features/auth/LoginScreen.tsx:78-79` | "STONIX", "Owner session — password + authenticator" | ring logo + "JARVIS & ANNA" + "Your AI Command Center", "Owner session: password and authenticator" |
| `src/features/command-center/components/TopBar.tsx:139-140` | "STONIX", "Assistant command center" | the sidebar brand (§6.1) |
| `src/features/confirm/ConfirmDialog.tsx:15` | `BASE_TITLE = 'STONIX Command'` | the page title (§6.2); flash "⚠ Confirm: <tool>" |
| `index.html:36` | "JARVIS & ANNA // Neural Interface" | "JARVIS & ANNA"; `<meta name="theme-color" content="#060d1e">`; `<link rel="icon" type="image/svg+xml" href="/favicon/favicon.svg">` |
| `public/favicon/*` (stained glass) | 16, 32, 180, 192, 512 PNG | **the reference ring logo** (the `LOGO*` conic ring, no glow) on `#060d1e` with a 8/32 radius; **same file names** (the Electron tray uses `favicon-32x32.png`); `favicon.svg`; master `assets/brand/jarvis-anna-mark.svg`; rendered by `scripts/render-favicons.mjs` (Playwright screenshot of the SVG) |
| `electron/main.cjs:278-289` | tray "Show JARVIS", "Quit JARVIS", tooltip "JARVIS-AGENT" | "Show JARVIS & ANNA", "Quit JARVIS & ANNA", "JARVIS & ANNA" |
| `electron/main.cjs:308` | "JARVIS & ANNA Neural Interface" | "JARVIS & ANNA" (`electron.spec` matches `/JARVIS & ANNA/`); keep the literal strings `ELECTRON_IS_DEV` and `../dist/index.html` (smoke test) |
| `electron/offline.html:6, :52` | "JARVIS — VPS unreachable" | "JARVIS & ANNA: VPS unreachable"; keep "VPS unreachable" and "Retry" |
| `src/shared/ui/WindowControls.tsx:26` | "JARVIS backend band ho gaya (code N)" | "JARVIS & ANNA backend band ho gaya (code N)" |
| `stonix_ui/README.md:1` | "# STONIX dashboard (stonix_ui)" | "# JARVIS & ANNA dashboard (stonix_ui)" |
| Owner placeholders of the reference | "Alex", "AM", "A" | "Matloob", "M", "M" |

Unchanged: the folder `stonix_ui` (deploy, CI and lanes depend on it; the leftover check is the whole-word `grep -rwi stonix`, which ignores `stonix_ui` and `stonixRoot`), the internal `command-*` aliases.

---

## 20. Accessibility rules (each verified in the prototype)

1. **Text size:** nothing visible under 12 px (the decorative tagline 11.5 px is `aria-hidden`).
2. **Focus:** a visible ring on every focusable element: `:focus-visible { outline: 2px solid var(--acc-line); outline-offset: 2px }`; inputs and textareas carry it on the element itself (composer box via `:focus-within`, Filter logs, memory search, SIM inputs, settings inputs, login fields). Never `outline: none` without a replacement.
3. **No `aria-label` on role-less elements.** Icons are `aria-hidden`; meaning goes in visually hidden text or the control's own name. Named scroll containers get `role="region"`, `tabindex="0"` and the name.
4. **Roles:** popovers `role="dialog"`; the confirm card `role="alertdialog"`; the phone sheet modal; Activity Logs/Reasoning below 1180 px `tablist`/`tab`/`tabpanel`; panel and Activity sub-tabs are buttons with `aria-pressed` (e2e names); the nav is `<nav>` with `aria-current="page"`; the persona switch a `radiogroup`.
5. **Live regions:** the chat log (`role="log"`, polite); toasts `role="status"`; one voice announcer (§9.5); one confirm announcer (§10.4); captions `aria-live="off"`; the countdown `aria-live="off"`.
6. **Status is never colour alone:** `StatusShape` (● ok, ▲ warn, ◆ err, ○ off/unknown, spinner busy) always with a word (visible or visually hidden on the compact pills).
7. **Dialogs:** focus trap, initial focus, Esc, focus returns to the opener; the confirm sheet cannot be closed by clicking the scrim (it focuses Deny).
8. **Keyboard:** persona radiogroup arrow keys; hold to approve with Space; Enter never allows; Esc denies; popovers Esc; routes move focus to the page title.
9. **Language:** Roman Urdu strings carry `lang="ur-Latn"`; `<html lang="en">`.
10. **Hit areas:** ≥ 24 px (44 px on phones) for the eyebrow names, chevrons, chips and tab bar items.
11. **Contrast:** WCAG AA for all text (§2.5); the reference's three failing colours are lifted (§22).
12. **Reduced motion:** §4.7, §8.9.

---

## 21. Acceptance checks

### 21.1 V-1: visual diff against the reference (X-109, `@gate`, Windows-local)

**Setup** (`visual_gate/reference_fixture.json`): Playwright project `gates` (`channel: 'chromium'`, new headless), `viewport 1672×941`, `deviceScaleFactor 1`, `timezoneId 'Asia/Karachi'`, `locale 'en-US'`, `reducedMotion 'reduce'`, launch arg `--force-color-profile=srgb` (as `diff.py`), `page.clock.setFixedTime(new Date('2026-10-02T21:42:10+05:00'))`. The mock bridge emits the fixture (agent online, laptop online, `ui_clients 1`, vitals 21/63/82, the two chat messages at 9:42 PM, the PowerShell confirm request, medium risk, 10 min TTL; the mock's `server_time` equals the fixed page clock, so the pill reads "Requires approval" and never a countdown). The hook sets the voice inputs to idle with the mic on (`setVoiceInputs({ room: 'connected', micOn: true, listening: true, … })`) and calls `referenceFrame(true)`, which (only in e2e builds) seeds the four Recent Activity rows of the reference (9:41, 9:40, 9:38, 9:37 PM), hides the confirm meta line of the head request (§22) and hides toasts. Wait for `document.fonts.ready` and `__jarvisTest.frameReady()`. Screenshot the page.

**Metrics** (`tests/visual/refdiff.ts`, a TypeScript port of `replica_work/diff.py`; PNG decode with `pngjs`, dev dependency): MAE, luminance SSIM (Gaussian σ 1.5), CIE76 mean and share < 10, per-region MAE / SSIM / ΔE2000 of means, 96 ink boxes, 104 container edges, all defined in `visual_gate/regions.json`. Output: a JSON report, the heat map (`|diff| × 4`, inferno) and a side-by-side PNG in `test-results/refdiff/`.

**Thresholds** (the fluid prototype's own numbers + margin; `regions.json`):

| Measure | Gate | Prototype (fluid dk) |
|---|---|---|
| Frame MAE | ≤ 7.0 | 5.75 |
| Frame SSIM | ≥ 0.875 | 0.897 |
| CIE76 mean / share of pixels ΔE < 10 | ≤ 5.0 / ≥ 92 % | 4.18 / 93.2 % |
| Region MAE (sidebar, header, hero, orb, hero_text, hero_buttons, quickstats, activity, panel, panel_tabs, confirm, chat, composer, page_bg_gaps) | ≤ 5.0, 10.5, 7.5, 6.5, 14.0, 9.5, 7.0, 11.5, 8.0, 10.0, 11.0, 8.0, 8.0, 2.0 | 3.67, 8.03, 5.64, 5.19, 10.89, 7.30, 5.58, 8.88, 6.38, 8.00, 8.63, 6.32, 6.06, 1.21 |
| Region SSIM | ≥ prototype − .04 | per region in `regions.json` |
| Region ΔE2000 of mean colours | ≤ 1.5 each | ≤ 0.7 |
| Ink boxes (96) | ≥ 80 within 2 px, ≥ 88 within 3 px, none > 6 px | 85 / 90 / 6 |
| Container edges (104) | ≥ 90 within 1 px, none > 3 px | 93 / 3 |

The gate fails with the worst regions, boxes and edges listed. **Owner check (optional, once):** the side-by-side PNG of the passing run is attached to the X-109 `done_highlight.md` entry.

### 21.2 V-2: product baselines (X-95, `@gate`, Windows-local)

`toHaveScreenshot` under reduced motion with clocks, times and the camera masked, `maxDiffPixelRatio: 0.01`: at **1672×941** (Overview reference fixture, Overview ANNA, Agents, Activity › Timeline, Activity › Logs, Settings, confirm high risk, login), at **1280×595** (Overview, Agents) and at **390×844** (Overview, Chat, Agents, Activity, Settings, confirm sheet). Committed with Playwright's `-gates-win32` suffix.

### 21.3 Layout and viewport gates (`layout.spec`, `header-layout.spec`, default project)

- At every size of §5.8: no horizontal page scroll (`scrollWidth ≤ clientWidth + 1`); no transform scaling on the shell; the composer fully inside the panel and the window — nominal, with a long high-risk command pending, and while typing a long message; pills, page title and avatar inside the window; hero parts inside the hero; card headers inside their cards; no visible text under 12 px (tagline excepted).
- Phones (§5.8): with the approval bar shown, composer bottom ≤ tab bar top; with the field focused the tab bar is hidden and the composer bottom ≤ window bottom.
- Header (`header-layout.spec`): no overflow or overlap at 1024, 1179, 1180, 1200, 1280, 1366, 1440, 1672 and 1920; compact labels and greeting rules of §6.2.
- Electron (emulated `window.electronAPI`): at 1200×800, 1280, 1366, 1440 and 1024 no header control intersects `window-*` and ≥ 140 px stay free at the right (the reserve of §5.7; the prototype has zero intersections at every size).
- The suite's default 1280×720: `trace.spec` (Reasoning visible beside or below the logs at 1280; tabs at 1100) and `intelligence.spec` pass with their §18.3 edits.

### 21.4 Performance gates (`perf.spec`, `@gate`, `gates` project, medians of three runs)

§8.8, driven by the hook (`setVoiceState('speaking')` + `feedAgent('speech')`); CDP `Emulation.setCPUThrottlingRate` for 4×; `Performance.getMetrics` `ScriptDuration` per frame; `stats().draws` for idle and the static states; a 144 Hz rAF shim for the high-refresh check. The numbers and the browser mode go into the `done_highlight.md` entry.

### 21.5 Motion and presence (`presence.spec`, default project, through the hook)

- Sample the orb canvas at 96×96 every 100 ms: speaking with `feedAgent('speech')` ≥ 5× the idle frame difference; speaking with `feedAgent(0)` ≤ 2× idle (motion follows the level, never the state alone); reduced motion 0 and no running infinite animation (`document.getAnimations()`).
- Loop: `stats().loop` `'stop'` and no new draws over 1 s in muted, offline, joining, agent absent and audio blocked; ≤ 16 draws a second in idle and mic off; > 30 while speaking.
- No layout shift: the rects of the orb canvas, the status line box, the button row and the quote slot are identical in all ten states, and in mic off before and during a spoken reply, at 1672×941, 1440×900, 1366×768, 1200×800 and 390×844.
- Each state shows its status line, helper, pill label and button states of §9.2 for JARVIS and ANNA.

### 21.6 Accessibility gate (`a11y.spec`, X-94)

`@axe-core/playwright` (tags wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa): **0 violations** and no `aria-prohibited-attr` among the incomplete results on: Overview in every voice state (hook) for both personas and under reduced motion; Agents; every Activity sub-tab; Settings; each panel tab; the confirm card (medium, high, long command, expanded); every popover; the enrollment dialog (incl. error); SIM; lightbox; toasts; login; session check; offline page; tablet 1024 (rail, Logs tabs); phone 390 (every tab, the confirm sheet, the approval bar, popover sheets). Text-size walk: no visible text node under 12 px except the tagline. Focus walk: 40 tab stops, each with an outline or box-shadow. Announcer check: a scripted conversation of 20 s produces no listening/thinking/speaking announcements.

### 21.7 Electron (`electron.spec` + `smoke:electron`)

Title `/JARVIS & ANNA/`, `login-password` visible, window buttons visible and clear of every header control at 1200×800, offline page with 'VPS unreachable' and Retry, tray strings, favicons render the ring logo.

### 21.8 Regression suite

`npm run test:e2e` (CI: `--project=chromium`, never `@gate`) green on Windows and in CI after every task, with the §18.3 edits only. `node scripts/testid-inventory.mjs --check` green after every task. `npm run build` leaves no `__jarvisTest` in `dist`. `npm run lint` clean.

### 21.9 Nothing removed (X-95)

A table in the X-95 `done_highlight.md` entry: every F1–F113 row of `mapping.md` §3 with its new home, the testid(s) and the spec or manual check that proved it reachable (popovers, pages, tabs, dialogs). Any missing row blocks X-95.

---

## 22. Deliberate differences from the reference

| # | Difference | Why |
|---|---|---|
| D-1 | "Alex", "AM", "A" → "Matloob", "M", "M" | The owner's name (same boxes and styles) |
| D-2 | The Approvals tab label sits on the Chat/Tools baseline (the reference draws it 3–4 px low) | AI-render irregularity |
| D-3 | Progress fills are bound to the values (CPU 21 % fills 21 %, Disk 82 % fills 82 %); the devices bar uses the fixed scale of §7.7 | Honest data; the reference draws 26 % and 79 % |
| D-4 | No 1 px dark halos around bright text and borders | Image sharpening, not design |
| D-5 | Three reference colours lifted to AA: sidebar tagline `#5c6a94` → `#727fa8` (3.5 → 4.7), user bubble time `#7186b4` → `#7b8fb9`, assistant bubble time `#5d739f` → `#7387ae` | WCAG AA |
| D-6 | A real approval shows the meta line (tool, where, Queue: N, expiry) under the buttons; the reference shows a moment without it. V-1 hides it through the e2e-only `referenceFrame()` | Existing feature (nothing removed) and safety; the e2e suite asserts the tool name and `Queue: N` |
| D-7 | Typed user messages show no source icon; voice and reminder messages show `mic` / `bell` | Matches the reference while keeping the source feature |
| D-8 | The product starts in "Mic is off" (state 9); the reference is state 10 | Privacy and browser autoplay |
| D-9 | Off-nominal additions (nav marks, pill marks, stale tags, ▲ on tiles, inline actions, toasts) | Never visible in the nominal state |

---

## 23. Implementation order (Cursor tasks X-69 … X-109)

The task blocks (same fields as `plan.md`) are in `tasks_v2.json`. Each task leaves the e2e suite and the inventory check green; containers move by the strangler rule (§1).

| Wave | Tasks | What lands |
|---|---|---|
| 0 Foundation | X-69 tokens, fonts, sprite, inventory · X-96 socket store · X-72 persona copy · X-73 primitives + StatusShape · X-70 brand + rename · X-71 favicons + Electron strings | One token source, fonts offline, the testid guard, reliable subscriptions (D1–D3), the n- kit |
| 1 Shell | X-80 app shell + router + `app-ready` · X-103 sidebar · X-78 header + pills · X-99 popover primitive + Agent, Voice and Devices popovers (Reconnect moves there) · X-82 phone shell · X-100 Electron reserve | The reference frame without content; legacy containers live in it |
| 2 Overview | X-104 hero card + persona switch · X-75 orb renderer · X-76 waveform · X-97 voice state machine · X-98 reduced motion + presence.spec · X-77 persona motion · X-105 Quick Stats + Recent Activity + feed store | The reference Overview |
| 3 Panel | X-83 tabs + chat · X-106 composer + push-to-talk · X-84 Confirm Command card · X-101 Approvals tab + phone sheet bar · X-102 hidden-tab alert | The reference right panel |
| 4 Pages | X-107 Agents (personas, voice, Voice-ID) · X-81 System Flow · X-85 Agents devices and machines (Laptop card shell + location) · X-79 Laptop states + Stop desktop in the Devices popover and the Laptop card (needs X-67) · X-108 Activity (timeline, tasks, notes) · X-87 Logs · X-86 Media · X-88 Settings page · X-92 account menu + toasts (needs X-67) | Every feature at home; TopBar, OperationsRail, AssistantCore and the old tabs deleted |
| 5 Polish and gates | X-89 dialogs · X-90 login · X-91 panel error · X-93 motion hygiene · X-94 accessibility gate · X-109 V-1 reference diff (also creates the `gates` Playwright project) · X-95 gates + nothing-removed audit | Ready for Claude's deploy |

X-74 (audio engine + e2e hook) is done; X-75 adds the coarse sampling and the orb stats to it. Recommended sequence (it respects every `depends_on`): X-69 → X-96 → X-72 → X-73 → X-70 → X-71 → X-80 → X-103 → X-78 → X-99 → X-82 → X-100 → X-104 → X-75 → X-76 → X-97 → X-98 → X-77 → X-105 → X-83 → X-106 → X-84 → X-101 → X-102 → X-107 → X-81 → X-85 → X-108 → X-87 → X-86 → X-88 → X-89 → X-90 → X-91 → X-93 → X-79 → X-92 → X-94 → X-109 → X-95. X-79 and X-92 wait for X-67; everything else can proceed without it.

---

## 24. Open points (owner / Claude)

1. **"Windows 11"** in the reference's "Laptop connected" row is not in `desktop_status` (K-74). The row uses the laptop's host name (`vitals_pc.host`); an `os` field would need a `REQ-CONTRACT`. The V-1 fixture seeds the reference text.
2. **Paperclip** has no feature behind it: a disabled icon ("Attachments are not available yet"). Real attachments would need a new upload path → `REQ-CONTRACT` if the owner wants them.
3. **Device count** uses `ui_clients` from `GET /vortex/status` (contracted, first UI use, polled). Until X-67 ships `desktop_status`, the laptop is "unknown" and not counted (the nominal pill then reads "2 Connected Devices").
4. **Mic off after sign-in and Reconnect** stays the default (privacy). The reference picture appears after Start Listening.
5. **Owner name** is a UI constant ("Matloob"); the session user is the generic `owner`.
6. **Nav marks** (Activity count, Agents dot) are additions the reference has no place for; never in the nominal state. Drop them if the owner wants the sidebar untouched.
7. **Reconnect** reloads the page until X-96 lands, then re-binds in place; the 'Reconnected' toast stays.
8. **X-67 data** (laptop states, Stop desktop, ack, DESKTOP_* toasts) is designed in; X-79 and X-92 wait for X-67.
9. **Quote:** one quote for both personas unless the owner wants an ANNA line.
10. **CI gates:** if the owner wants V-1, V-2 and the perf gates in CI, Cursor writes `REQ-CI` in `plan.md` section 11 (Linux baselines from the Playwright image); `.github/**` is Claude's lane.
