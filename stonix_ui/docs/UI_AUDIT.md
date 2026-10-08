# UI audit (Phase 0 inventory + Phase 1 lenses)

Date: 2026-10-08. Lane: `stonix_ui/**`. Contracts: `docs/CONTRACTS.md` (K-09 v33, persona/swarm notes in the mission; exact text wins over this file).

Evidence rule: a status without a `file:line`, test name, or command is UNVERIFIED.

## Baseline (this session)

| Gate | Result | Evidence |
|---|---|---|
| Vitest | 5 passed before this audit's new tests | `npm test` in `stonix_ui` (chat awaitingReply + ChatList), 2026-10-08 23:09 |
| tsc + vite build | pass | `npm run build` same evening, `dist/assets/index-*.js` 1267.13 kB / gzip 367.55 kB, css 178.12 kB / gzip 35.58 kB |
| eslint full tree | UNVERIFIED this session | command not re-run over all of `src` |
| Playwright e2e / visual / perf / refdiff / axe / electron | UNVERIFIED this session | not run; do not treat prior chats as a green gate |
| Live dashboard clicks | not done | rule: look-only; no `ui_command` |

## Screens and controls (reachable)

| Surface | Entry | Status | Test |
|---|---|---|---|
| Login | `src/features/auth/LoginScreen.tsx` | FRAGILE: 503 copy did not name the enroll script (fixed in UI-H01) | was none; `api.test.ts` added |
| Overview hero, stats, activity | `Dashboard.tsx` + `features/overview/*` | CLUTTERED (owner verdict + 2026-10-08 visual shots, untracked `assets/qa-visual-shots/`) | e2e layout specs exist; this session UNVERIFIED |
| Chat / Approvals / Tools | `features/panel/RightPanel.tsx` | Chat typing bubble WORKS for pending typed reply (`useChat.ts` `awaitingReply`, test `useChat.test.tsx`) | Vitest |
| Agents, Activity, Settings | hash routes in shell | UNTESTED this session | e2e nav specs exist |
| Confirm dialog | `features/confirm/*` | UNTESTED this session | e2e confirm specs exist |
| Electron window | `electron/main.cjs:309-312` | WORKS for isolation flags: `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true` | electron spec UNVERIFIED this session |

`data-testid` baseline: `stonix_ui/tests/testids.baseline.txt` (must only grow). Nothing removed in UI-H01/UI-H02.

## Socket events the UI reads (sample, not every line)

Handlers via `useSocketEvent` include `init_state`, `new_message`, `update_message`, `agent_status`, `settings_sync`, `persona_update`, `confirm_request`, `confirm_resolved`, `tool_progress`, `session_expired`, `reasoning_update`, vitals and logs. See grep of `useSocketEvent(` under `stonix_ui/src`. Contract coverage in `tests/e2e/contracts.spec.ts` is normalizer-level (message, confirm, plan, settings, tasks, mock fixtures), not one Playwright case per live event. Gap: full event matrix vs K-xx is UNVERIFIED line-by-line this session.

## Commands the UI sends

`ui_command` types found: `chat`, `settings_update`, `request_state`, `stop`, `start`, `mute`/`unmute`, `desktop_stop`, `notification_ack`, plus wake/agent controls in `AgentControls.tsx` and `VoiceWakeCard.tsx`. Chat payload stays `{id, text}` (`useChat.ts` emit). Swarm button was left on `{task}` (C-42 follow-up).

## State

- Settings: `useSettings.ts` (`localPending`, `valuesRef`, `revertRef`).
- Chat: `useChat.ts` message map, `awaitingReply`.
- No `localStorage` token writes found (`rg localStorage stonix_ui/src` → no matches).

## Lens findings (Phase 1)

1. **Contract / login.** K-09 v33: 503 `owner_not_enrolled` must show a setup hint. Code was a generic Urdu line (`api.ts` `mapAuthError`). UI-H01 names `scripts/ops/set_owner_credentials.py --target local`.
2. **Persona (C-50).** `applyOptimistic` set `persona` on click (`useSettings.ts`). Mission: show switching until `persona_update`. UI-H02 holds the previous persona while `revertRef` has `persona`, ignores persona in `resolvePendingFields`, and applies the new persona only in the `persona_update` handler. Hero already sets `aria-busy` when pending (`HeroCard.tsx` ~197).
3. **Security.** No `dangerouslySetInnerHTML` in `stonix_ui/src`. `SafeChatMarkdown.tsx` builds React nodes, no raw HTML. Link `href` rendering: none in that file (URLs stay text). UNVERIFIED: other panels.
4. **Message pipeline.** Typing row: `ChatList.tsx` `showTyping = isThinking || awaitingReply`. Technical dumps collapse in `messageDisplay.ts`.
5. **Clutter.** Owner verdict plus Activity alert flood and chat error dumps. Partial UI cleanup commit `a2e8876`. Full token audit (hex/magic px) UNVERIFIED (grep not completed as a lint rule).
6. **Performance.** Chat list is not virtualized (no `react-window` usage found in this pass). Bundle gzip ~368 kB JS. 30-minute soak UNVERIFIED.
7. **A11y / layout / flaky e2e.** Specs exist under `stonix_ui/tests/e2e/`. Pass rate this session: UNVERIFIED.

## Gaps left open

See `UI_HARDENING_PLAN.md`. Anything outside `stonix_ui/**` is a section 11 request, not an edit.
