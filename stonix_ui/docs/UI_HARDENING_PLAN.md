# UI hardening plan

Source: `stonix_ui/docs/UI_AUDIT.md` and the 2026-10-08 owner mission. Nothing in the baseline testid file is deleted. Live dashboard stays look-only.

Priority: P0 broken or unsafe, P1 fragile or misleading, P2 clutter, P3 polish.

## Special plans (doubts)

1. **Question:** Does `settings_sync` include the new persona before `persona_update`? **Hypothesis:** yes, which would flip the hero early. **Experiment:** UI-H02 ignores persona on sync while `revertRef` has `persona`, and does not clear that pending from sync equality. **Decision:** only `persona_update` or the existing sync timeout applies the change. Live timing UNVERIFIED until a mock-bridge persona switch test runs.
2. **Question:** Full e2e/visual/perf/axe/electron gates on this PC tonight. **Experiment:** not run this session. **Decision:** do not mark those gates green. Next execution step is `npm run test:e2e` against the mock bridge only.

## Tasks

### UI-H01 — 503 owner_not_enrolled copy (P0, S)

- **Problem:** `mapAuthError` returned a generic enroll line for every 503 (`src/features/auth/api.ts`). K-09 v33 requires a setup hint. The mission names `scripts/ops/set_owner_credentials.py --target local`.
- **Fix:** if `error === 'owner_not_enrolled'`, the message includes that command. Other 503s stay generic.
- **Accept:** login error text contains `set_owner_credentials.py --target local`.
- **Verify:** `src/features/auth/api.test.ts`.
- **Risk:** copy length on a narrow login card. No feature removed.

### UI-H02 — Persona does not flip on click (P0, M)

- **Problem:** `applyOptimistic` wrote `persona` immediately (`useSettings.ts`). C-50: `persona_update` arrives only after the voice session actually switches.
- **Fix:** do not write persona in `applyOptimistic`. While `revertRef` has `persona`, `applySettingsObject` keeps the current persona. `resolvePendingFields` does not clear `persona`. `persona_update` sets the persona and clears the pending timer. Hero pending UI already exists (`HeroCard.tsx`).
- **Accept:** after `changePersona`, displayed persona stays until `persona_update`. Timeout still reverts the pending flag.
- **Verify:** `npx tsc --noEmit`. A component test of the hook is not in this commit (hook pulls socket, settings, timers). UNVERIFIED in the browser until mock e2e.
- **Risk:** a `settings_sync` that was the only persona confirmation would no longer flip the UI. Contract path is `persona_update`.

### UI-H03 — Contract event matrix (P1, L) — DONE

- **Problem:** `contracts.spec.ts` covers normalizers, not every socket event the UI subscribes to.
- **Fix:** `socketEventMatrix.ts` tables every `useSocketEvent` name to a K-id; Vitest asserts the inventory matches the grep snapshot and runs missing/extra-field cases on normalizers. No BLOCKED rows this pass (no REQ-CONTRACT).
- **Accept:** every subscribed event is either tested or listed as BLOCKED in section 11.
- **Verify:** `npm test -- src/shared/contracts/socketEventMatrix.test.ts`.

### UI-H04 — Console guard on e2e (P1, M) — DONE

- **Problem:** no proof that e2e fails on `console.error` / `pageerror`.
- **Fix:** `tests/e2e/helpers/consoleGuard.ts` + `fixtures.ts` (auto-fail unless `softConsole`); proof in `console-guard.spec.ts`.
- **Accept:** a deliberate `console.error` in a fixture fails one test.
- **Verify:** `npx playwright test tests/e2e/console-guard.spec.ts --project=chromium`.

### UI-H05 — Chat list cap / virtualize (P1, L) — DONE

- **Problem:** `useChat` trims to 300 (`MAX_MESSAGES`) but the DOM still renders the whole list (`ChatList.tsx`).
- **Fix:** `CHAT_RENDER_WINDOW` (80) newest messages mounted; "Show earlier in view" expands by 80. Composer/latest testids unchanged. Perf budget still UNVERIFIED (`npm run test:perf`).
- **Accept:** 300 messages do not mount 300 bubbles.
- **Verify:** `npm test -- src/features/panel/ChatList.test.tsx`.

### UI-H06 — Failure states per panel (P1, L)

- **Problem:** empty/error/offline copy is uneven. Audit did not click every panel this session.
- **Fix:** one empty and one offline pattern per Activity, Tools, Approvals, Agents. No control removed.

### UI-H07 — Confirm card single answer (P1, M) — DONE

- **Problem:** double-click Allow is UNVERIFIED against `useConfirmQueue.ts`.
- **Fix:** `ConfirmCard` locks Allow/Deny after the first ack for that id; `markResponded` still guards the emit. Mock e2e double-click asserts one `confirm_response`.
- **Verify:** `npx playwright test tests/e2e/confirm.spec.ts -g "double-click Allow" --project=chromium`.

### UI-H08 — Accessibility pass (P1, L)

- **Problem:** axe serious/critical count UNVERIFIED.
- **Fix:** run `a11y.spec.ts`; fix only failures; do not drop testids.

### UI-H09 — Layout 360–1920 (P1, L)

- **Problem:** phone Approvals/Tools were hard to open before the chat-sheet sticky tabs (`phone.css`). Full matrix UNVERIFIED this session.
- **Fix:** mock-bridge screenshots at 360, 768, 1280, 1920 (untracked). No horizontal scroll.

### UI-H10 — Visual tokens (P2, L)

- **Problem:** neon CSS still uses many raw colors (`assets/styles/neon/*.css`). No lint rule yet.
- **Fix:** inventory hex outside token files; collapse only duplicates. Glow limited to active states. Do not restyle by deleting panels.

### UI-H11 — Swarm job UX (P2, M)

- **Problem:** K-31 v34 swarm is a job (`tool_progress`, `job_cancel`, system report). UI jobs panel exists (`useJobs.ts`). Whether the swarm button shows that progress is UNVERIFIED.
- **Fix:** mock `tool_progress` for a swarm job and assert the jobs row. Do not change the `{task}` payload.

### UI-H12 — Mute vs stop copy (P2, S) — DONE

- **Problem:** mission: Stop interrupts; mute only stops mic input. Labels must not say the agent is deaf to typed chat.
- **Fix:** shared EN constants on `persona.ts`; VoicePopover uses them; Vitest asserts mute/mic-off mention typed chat and stop stays interrupt-only. No contract change.
- **Verify:** `npm test -- src/shared/persona.muteStop.test.ts`.

### UI-H13 — Electron CSP and navigation (P2, M)

- **Problem:** isolation flags are on (`electron/main.cjs:309-312`). CSP and `window.open` allow-list UNVERIFIED.
- **Fix:** read preload and will-navigate; tighten only if a hole is proven. Tests via `npm run test:electron` on the mock bridge, and only when the Desktop Node is not running.

### UI-H14 — Reduced motion and orb pause (P3, M)

- **Problem:** orb canvas pause on hidden tab UNVERIFIED.
- **Fix:** pause rAF when `document.hidden` or `prefers-reduced-motion`. One animated element at rest (the orb).

### UI-H15 — Flake check (P1, M)

- **Problem:** e2e not run 3 times.
- **Fix:** three `npm run test:e2e` runs; file any flake as its own task.

## Execution order

H01–H05, H07, H12 done with tests. Still TODO: H06, H08–H11, H13–H15. Full section 6 gates (e2e suite ×3, visual, perf, refdiff, axe, electron, `build:prod`) are **not** claimed green in this file.

## Final report

In progress. Stop condition in the mission section 9 is **not** met.
