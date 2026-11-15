# UI redesign: page briefs and work packages

**Date:** 2026-11-09
**Status:** Planning input for the desktop UI redesign. Nothing here is implemented. Built on the audit in [UI_PAGE_STATE_MATRIX.md](UI_PAGE_STATE_MATRIX.md); finding ids (E1, E2, ...) refer to its section 5. The standards in [UI_UX_STANDARDS.md](UI_UX_STANDARDS.md) and `apps/web/AGENTS.md` still apply to every package.

## 1. Rules every package follows

1. **Tokens only.** All colour, size, radius, shadow, z-index and duration come from `globals.css` tokens. The 14 view files that use the zinc palette (E6) are rewritten, not patched. Light is the default theme and gets the same review as dark.
2. **Design for the worst case first** (matrix section 4): 600 items, 300-character titles, unbroken URLs and hashes, 20 KB messages, Arabic and Hindi, 60 running tasks. Every text node that renders a backend string gets `min-w-0` and a wrap rule; every truncated string has a way to read the rest.
3. **Every data view has seven states:** loading skeleton, empty (with a next step), error with retry that is never mistaken for empty (E8), partial, offline, permission or expired session, and a long list (paging or "load more" with a total, E17).
4. **Layout contract by width:** 1440 and wider is the reference; 1024 keeps the sidebar and one content region with the detail in a drawer; 760 and below collapse the sidebar to a menu with a scrim, Escape and focus return (E4) and stack list and detail as separate screens; 320 must not clip or collapse any control (E1, E2, E5). Nothing important lives only in the right panel (E3).
5. **Approval screens are exact and still:** real payload in full, no animation, blur or glow near the decision controls (E24).
6. **Motion:** transform and opacity, tokens, static equivalent under `prefers-reduced-motion`; loops only where `chat.css.test.tsx` allows named ones.
7. **No invented content** (E22) and no developer jargon in headings.
8. **Proof:** each package ends with the harness run for its pages (`UI_PAGE_STATE_MATRIX.md` section 7): `--plan standard`, plus `--zoom 2`, `--forced-colors`, `--reduced-motion` and `--keyboard`. Counts must fall against the baseline in `results.json` and no new element signature may appear. Then the four repository checks: lint, typecheck, `npm --prefix apps/web test`, build.
9. **Approval first (Level 3)** for any new package, font, animation library, CDN or asset.

## 2. Page briefs

### 2.1 Shell (title bar, sidebar, status bar, gate)

- **Content and regions:** title bar (brand, current view title, voice chip, theme switch), left rail with new-conversation, search and conversation list, main navigation (Tasks, Approvals, Artifacts, Memory, Connections, Activity, Settings with the pending-approvals count), one main region, optional right drawer, status bar (backend state, voice status, user, sign out). Gate cards for starting, unreachable and signed out.
- **Responsive:** at 1024 the right panel becomes an overlay drawer; at 760 and below the rail becomes a menu with a scrim (E4) and the title bar drops the chip labels to icons (E1); at 320 the status bar becomes icon plus tooltip and never wraps (E5). The right drawer must open at every width (E3).
- **Worst case:** 200 conversations (add paging or search-first, E17), a 300-character title, a user id of 40 characters, more than 50 pending approvals ("50+", E26), backend lost after sign-in, session expiry while a draft exists (keep the draft; see the matrix, section 3.3).

### 2.2 Conversation

- **Content and regions:** thread (user and assistant messages, voice turns, task cards, approval cards, system notes), composer (text, mic, Run as task, send or stop), voice dock, notices with retry, empty state.
- **Responsive:** the composer wraps its actions under the field below 480 px instead of squeezing the field (E2); the Work panel opens as a drawer below 1024 (E3); message column stays about 70 characters wide up to 2560 px.
- **Worst case:** 300 messages (virtualise or window, E27), a 20 KB reply with a wide table (render tables with their own horizontal scroll, E19), 400-character unbroken URL and code lines (already scroll in their block), task and approval cards with long words (E20), Arabic and Hindi (set `dir="auto"` per message and per title, E18), 20 task cards each holding a stream (E28).

### 2.3 Tasks

- **Content and regions:** ledger (filters, search, rows with title, status, cost, time), detail (header with status and abort, instruction, plan graph, step list, failure reasons, deliverables), New Task dialog.
- **Responsive:** two panes at 1440; at 1024 the detail is a drawer; at 760 and below list and detail are separate screens with a back control (E10). Rows never shrink (E7); the plan graph and node inspector stack instead of sitting side by side below 1600 px (E9).
- **Worst case:** 600 tasks (paging with total, E17, and refresh without a button, E30), 300-character titles and instructions (wrap, expand), 400-character failure reasons (full text in an expandable row), 60 running tasks (live status without 60 open streams, E28).

### 2.4 Approvals

- **Content and regions:** pending queue (tool, tier, requester, expiry), detail with exact payload, diff or email preview, digest, decision controls, step-up code entry.
- **Responsive:** same list and detail pattern as Tasks; decision controls are always reachable (sticky bar under the payload) and never inside an animated container.
- **Worst case:** 60 pending, payload with 400-character lines (scroll inside a block with a visible cue), a 128-character hash (wrap, E15), a failed list load (shown as an error, never as "none", E8), expiry countdown while reading.

### 2.5 Artifacts and the canvas

- **Content and regions:** deliverables table (name, type, size, verification, task, created), preview, download, verification report.
- **Responsive:** table at 1024 and wider, cards or a compact list below; filter chips become a select below 760 (E13).
- **Worst case:** 500 files (paging), 300-character names (middle truncation plus full name on focus and in the detail, E33), a name that is a URL, a failed verification (shape and text, not colour alone).

### 2.6 Memory

- **Content and regions:** summary of what MAYA remembers and what it saved this session, category filter, semantic search, list with decay or permanent marker, add, export, purge with a confirm step.
- **Responsive:** one column below 1024; filters collapse to a select below 760; destructive actions never share a row with primary ones at 320 px.
- **Worst case:** 600 items (paging, E17), 400-character content (clamp with expand), metadata with long URLs, the load error shown readably in both themes (E6), a purge that hits a 401.

### 2.7 Connections (MCP)

- **Content and regions:** registered servers (health, transport, tools, schema hash, last ping), suggested integrations (clearly labelled as suggestions), register form, tool inspector, ping.
- **Responsive:** servers as a table at 1024 and wider (with scroll cue) and as stacked cards below; register form as a dialog that fits 320 px (E25).
- **Worst case:** 60 servers, long command arguments and URLs, 128-character hashes, a dead server with many consecutive failures, a 500 from health with raw server text (show a mapped message, not the raw one).

### 2.8 Activity (audit)

- **Content and regions:** hash-chain status banner, filters, event rows (time, type, actor, status, details), expandable payloads, export.
- **Responsive:** rows become two-line cards below 760; the filter bar scrolls with a cue and keeps focus visible (E13).
- **Worst case:** 600 events (paging, E17), long actor and URL values (wrap, E14), unverifiable chain shown as a distinct, non-colour-only state, payload JSON with long lines.

### 2.9 Settings

- **Content and regions:** profile and session, chat behaviour (task detection, memory in chat), the reference tabs that have no backend yet (labelled planned), danger zone with confirmation.
- **Responsive:** tabs become a vertical list or a select below 760 (E12).
- **Worst case:** long user id, a failed identity load, toggles that fail to save (rollback and message). Remove every hard-coded value that is not read from the backend (E22).

### 2.10 Auth, onboarding, error, loading, not found

- **Content and regions:** sign-in, register, forgot, reset, verify, first-run owner creation, "already signed in"; app-level error, loading and 404 cards; the legacy redirect notice.
- **Responsive:** one centred card from 320 up; no decorative blobs (E23, E34); follows the chosen theme (E34).
- **Worst case:** long emails, RTL names, password-manager overlays, throttled sign-in, backend down on the sign-in screen (say so instead of showing a form that cannot work), first-run on a fresh database.

## 3. Work packages

Packages 2 to 6 can run in parallel once package 1 has landed the tokens and the shared primitives. The legacy redirect routes stay in package 1.

### WP1: shell, navigation, layout, tokens and shared primitives

- **Owns:** `components/layout/*` (`AppShell`, `AppGate`, `WorkspaceProvider`, `LegacyRedirect`), `app/globals.css`, `app/layout.tsx`, `app/(app)/layout.tsx`, `components/chat/ThemeToggle.tsx`, and a new shared primitives folder that replaces `components/ui/*` (button, card, badge, dialog, drawer, tabs, table, empty state, error state, skeleton, toast). Redirect pages `agents/`, `chat/`, `deliverables/`, `integrations/`, `workspace/` stay as they are unless the route map changes.
- **Fixes:** E1, E3, E4, E5, E16, E25, E26, E32; the token duplicate in `chat.css`.
- **Tests that pin behaviour (will change):** `components/layout/AppShell.test.tsx` (10), `AppGate.test.tsx` (6), `WorkspaceProvider.test.tsx` (5), `app/routes.test.tsx` (8), `components/ui/glass-primitives.test.tsx` (37, replaced by tests for the new primitives).
- **Guards that must keep passing:** `test/source-scan.test.tsx` (no `innerHTML`, `eval` and similar), and `components/chat/chat.css.test.tsx` for token rules: its colour-role, no-literals-outside-tokens and no-pure-black-or-white tests read `chat.css`, which still holds a copy of the tokens. Coordinate the token move with WP2.

### WP2: conversation, composer, voice

- **Owns:** `components/chat/*` except `ThemeToggle.tsx` (`ConversationRuntime`, `ConversationView`, `ThreadList`, `MessageItem`, `MarkdownRenderer`, `CodeBlock`, `ChatInput`, `ChatNotice`, `ConversationSidebar`, `MemorySavedChip`, `highlight`, `threadItems`, `chat.css`, `useReducedMotion`), `components/voice/*`, `lib/markdown.ts` if tables are added.
- **Fixes:** E2, E16, E18, E19, E20, E27; legacy `chat.css` rules with no user (`.maya-chat--embedded`, `.maya-home`, E21).
- **Tests:** `ChatInput.test.tsx` (19), `ChatNotice.test.tsx` (18), `CodeBlock.test.tsx` (16), `ConversationRuntime.test.tsx` (11), `ConversationView.test.tsx` (11), `highlight.test.tsx` (12), `MarkdownRenderer.test.tsx` (24), `MemorySavedChip.test.tsx` (7), `threadItems.test.ts` (14), `chat.css.test.tsx` (27, the largest pin: it asserts keyframes, durations, focus ring, 44 px touch targets, forced-colors, no external requests and more, so every change to `chat.css` must be paired with its test), `components/voice/VoiceDock.test.tsx` (6), `VoiceOrb.test.tsx` (2), `lib/markdown.test.tsx` (49) if the markdown parser changes. Hook tests (`useChatStream` 78, `useLiveVoice` 16) must not change: this package does not touch the hooks.
- **Shared with WP3:** `TaskCard` and `ApprovalCard` render inside `ThreadList`; changes there must keep `ConversationView.test.tsx` and `ThreadList` behaviour passing.

### WP3: work panel, tasks, approvals, plan graph

- **Owns:** `components/tasks/*` (`TaskCenterView`, `NewTaskModal`, `TaskCard`, `WorkPanel`, `taskStatus`), `components/dag/*`, `components/approvals/*`, `app/(app)/tasks/*`, `app/(app)/approvals/page.tsx`.
- **Fixes:** E3 (with WP1), E7, E8, E9, E10, E11, E15, E17, E20 (with WP2), E24, E28, E29, E30.
- **Tests:** `TaskCenterView.test.tsx` (3), `NewTaskModal.test.tsx` (5), `TaskCard.test.tsx` (4), `WorkPanel.test.tsx` (2), `taskStatus.test.ts` (6), `DagVisualizer.test.tsx` (2), `ApprovalCenterView.test.tsx` (5), `ApprovalCenterModal.test.tsx` (8), `ApprovalCard.test.tsx` (4), `GitDiffViewer.test.tsx` (1); `app/routes.test.tsx` if the tasks route changes. Add tests for the error-is-not-empty rule (E8) and for the 50-item cap message. `hooks/useTaskEvents.test.ts` (4) must keep passing.

### WP4: memory, activity, artifacts, canvas, citations

- **Owns:** `components/memory/*`, `components/activity/*`, `components/artifacts/*` (including `ArtifactPreview` and `ArtifactDownloadButton`, which the Work panel imports: agree the props with WP3), `components/canvas/*`, `components/citations/*`, `app/(app)/memory`, `activity`, `artifacts` pages.
- **Decision needed:** `DeliverableCanvas`, `ResearchCitationsView` and `ArtifactCard` have no importer (E21). Either wire them into the Work panel and thread in this package or delete them with their tests.
- **Fixes:** E6, E13, E14, E17, E31, E33.
- **Tests:** `MemoryView.test.tsx` (8), `ActivityView.test.tsx` (7), `ArtifactsManagerView.test.tsx` (5), `DeliverableCanvas.test.tsx` (3), `ResearchCitationsView.test.tsx` (2), `BrowserTakeoverModal.test.tsx` (3, if the browser modal is kept). Client tests under `lib/` (`memory-client`, `audit-client`, `artifacts-client`) stay unchanged unless paging parameters are added; then extend them.

### WP5: connections, settings, agents

- **Owns:** `components/mcp/*`, `components/settings/*`, `components/agents/*`, `app/(app)/connections`, `app/(app)/settings`.
- **Decision needed:** `AgentFleetView` is dead (E21): delete it and its 4 tests, or give it a real route. `McpStatusIndicator` is also unused; either place it in the status bar or delete it.
- **Fixes:** E6, E12, E13, E22, E25 (toast), E33.
- **Tests:** `McpGatewayView.test.tsx` (3), `McpStatusIndicator.test.tsx` (5), `SettingsView.test.tsx` (9), `AgentFleetView.test.tsx` (4). Settings tests pin the reference-only wording; remove invented values and their assertions together.

### WP6: auth, onboarding, error, loading, not found

- **Owns:** `components/auth/*` (`AuthPageLayout`, `AuthView`, `UnifiedAuthForm`, `AccountForm`, `AuthProvider`), `app/login`, `register`, `forgot-password`, `reset-password`, `verify`, `app/error.tsx`, `loading.tsx`, `not-found.tsx`.
- **Fixes:** E23 (blobs, blur, gradients), E34, first-run and backend-down states on the sign-in screen.
- **Tests:** `AccountForm.test.tsx` (7), `AuthPageLayout.test.tsx` (1), `AuthView.test.tsx` (4), `UnifiedAuthForm.test.tsx` (11), `hooks/useAuth.test.tsx` (9) and `lib/auth-client.test.tsx`, `lib/auth-session.test.tsx` must not change (no behaviour change), `app/routes.test.tsx` for the redirect pages.

### Dependencies and conflict zones

| Zone                                                                                            | Packages      | Rule                                                                                              |
| :---------------------------------------------------------------------------------------------- | :------------ | :------------------------------------------------------------------------------------------------ |
| `globals.css` and the token copy in `chat.css`                                                  | WP1, WP2      | WP1 lands tokens first; WP2 removes the `chat.css` copy in the same change as `chat.css.test.tsx` |
| `WorkspaceProvider` extension contract (`conversation.rightPanel`, `sidebarList`, `voice.chip`) | WP1, WP2, WP3 | Keep the contract stable; changes go through WP1                                                  |
| `TaskCard`, `ApprovalCard` in the thread                                                        | WP2, WP3      | WP3 owns the files, WP2 owns the thread tests                                                     |
| `ArtifactPreview`, `ArtifactDownloadButton` in the Work panel                                   | WP3, WP4      | WP4 owns the files, WP3 owns the panel                                                            |
| Shared primitives                                                                               | WP1 first     | Other packages do not add their own buttons, dialogs or tables                                    |

Test blocks (`it`/`test` calls, so fewer than the 1190 vitest cases, which count parametrised runs) in the files each package must touch: WP1 66, WP2 216 (including `markdown` 49), WP3 40, WP4 28, WP5 21, WP6 23 (not counting the guard tests listed as must-not-change).

## 4. Acceptance checklist for the redesign

- [ ] Light and dark both pass the harness contrast detector with no failures on body text.
- [ ] No element wider than its parent at 320, 480, 760, 1024, 1440, 2560, and at 200% zoom of 1440.
- [ ] Every list has paging or a total, and a load error is shown as an error.
- [ ] Every approval screen shows the full payload with no motion near the controls.
- [ ] Keyboard-only walkthrough reaches every control; focus is never obscured or off screen.
- [ ] Reduced motion and forced colors screenshots reviewed.
- [ ] `apps/web` lint, typecheck, test and build pass; `npm run docs:check` passes.
