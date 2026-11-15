# UI page and state matrix (read-only audit)

**Date:** 2026-11-09
**Scope:** `apps/web/src` as of the working tree on 2026-11-09 (static export `apps/web/.next-desktop`).
**Status:** Audit evidence for the desktop UI redesign. Nothing in `apps/web/src` was changed. Describes today's behaviour only; the target design is in `UI_REDESIGN_WORK_PACKAGES.md`.

> **Update 2026-11-10:** the Work panel and the Canvas were removed, so findings that mention them (the "Work and Canvas panel" row E3, `DeliverableCanvas`, the 1024 px "Work panel slide-over" row, the 200% zoom Work panel note) no longer apply. Task progress, approvals and files are in the chat thread. `/artifacts` has no inline report or preview: rows offer Open (desktop only), Ask AI, Download and Delete. Page-state results below are from the 2026-11-09 audit and were not re-run for `/artifacts`.

## 1. How this was produced

- **Static pass:** read the routes and every component folder, and searched the code for the usual overflow causes (section 6).
- **Dynamic pass:** built the static export with `npm --prefix apps/desktop run build:web`, served it from a local same-origin static server, and drove it with Playwright (Microsoft Edge channel) with every `/api/v1/*` request answered by mocked fixtures. 906 combinations of page, state, width and theme, plus 20 modal and detail-pane cases, a 200% zoom pass, a forced-colors pass, a reduced-motion pass and a keyboard pass.
- **Reusable harness:** `scratchpad/ui-audit/harness.py` and `fixtures.py` (section 7). Later phases rerun it to prove the redesign.
- **Baseline:** `npx tsc --noEmit`, `npx eslint src --max-warnings 0`, `npx prettier --check src` all pass; `npx vitest run` passes 61 files and 1190 tests.

Legend: **O** = OK (detectors clean and screenshot reviewed where one was taken, or code reviewed), **B** = BUG or risk with evidence (E-numbers, section 5), **U** = UNKNOWN (not measured), **N** = not applicable. Every B cell lists its evidence ids in section 3.

Limits of the evidence are listed in section 8. In particular a cell marked O means "no defect found by the checks that were run", not "proved correct".

## 2. Route and component inventory

### 2.1 Routes (`apps/web/src/app`)

| Route                                          | File                                        | What it is today                                                                 | Data sources (`src/lib`, `src/hooks`)                                                                                                                                 |
| :--------------------------------------------- | :------------------------------------------ | :------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`                                            | `(app)/page.tsx`                            | Conversation: thread (task cards with file downloads), composer, voice dock, approval dialog | `api-client` (sessions, messages, chat stream), `useChatStream`, `useLiveVoice` (WebSocket `/api/v1/voice/live`), `approvals-client`, `tasks-client`, `useTaskEvents` |
| `/tasks`                                       | `(app)/tasks/page.tsx`, `TasksRoute.tsx`    | Task ledger with detail and DAG (`?task_id=` deep link)                          | `tasks-client`, `useTaskEvents` (SSE)                                                                                                                                 |
| `/approvals`                                   | `(app)/approvals/page.tsx`                  | Approval list and exact payload with decision                                    | `approvals-client`                                                                                                                                                    |
| `/artifacts`                                   | `(app)/artifacts/page.tsx`                  | Artifact rows: Open (native app), Ask AI, Download, Delete; no preview                                     | `artifacts-client`                                                                                                                                                    |
| `/memory`                                      | `(app)/memory/page.tsx`                     | Memory items, search, add, export, purge                                         | `memory-client`, `system-client`                                                                                                                                      |
| `/connections`                                 | `(app)/connections/page.tsx`                | MCP server registry, health, tools                                               | `mcp-client`                                                                                                                                                          |
| `/activity`                                    | `(app)/activity/page.tsx`                   | Audit ledger with hash-chain status                                              | `audit-client`, `system-client`                                                                                                                                       |
| `/settings`                                    | `(app)/settings/page.tsx`                   | Account and session, chat and voice switches, appearance note, about and data    | `settings-client`, `system-client`, `auth-client`                                                                                                                     |
| `/login` `/register`                           | `login/page.tsx`, `register/page.tsx`       | `AuthPageLayout` + `AuthView` (one unified form)                                 | `auth-client`, `auth-session`                                                                                                                                         |
| `/forgot-password` `/reset-password` `/verify` | one page each                               | `AuthPageLayout` + `AccountForm`                                                 | `auth-client`                                                                                                                                                         |
| `/agents`                                      | `agents/page.tsx`                           | Client redirect to `/settings` (`LegacyRedirect`)                                | none                                                                                                                                                                  |
| `/chat` `/workspace`                           | `chat/page.tsx`, `workspace/page.tsx`       | Client redirect to `/`                                                           | none                                                                                                                                                                  |
| `/deliverables`                                | `deliverables/page.tsx`                     | Client redirect to `/artifacts`                                                  | none                                                                                                                                                                  |
| `/integrations`                                | `integrations/page.tsx`                     | Client redirect to `/connections`                                                | none                                                                                                                                                                  |
| 404, error, loading                            | `not-found.tsx`, `error.tsx`, `loading.tsx` | Token-styled full-screen cards (outside the shell)                               | none                                                                                                                                                                  |

The `(app)` group layout mounts `WorkspaceProvider > AppGate > ConversationRuntime > AppShell` once. `AppGate` shows one of four states: booting, backend unreachable (Retry), signed out (auth view), ready. Backend polling and the pending-approvals poll run every 30 s while the tab is visible.

### 2.2 Component folders (`apps/web/src/components`)

| Folder                           | Regions and files                                                                                                                                                                                                                  | Used by                       | Notes                                                                                                  |
| :------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------- | :----------------------------------------------------------------------------------------------------- |
| `layout`                         | `AppShell` (title bar, sidebar, main, right panel, status bar), `AppGate`, `WorkspaceProvider`, `LegacyRedirect`                                                                                                                   | every `(app)` route           | token-styled (good); E1-E5                                                                             |
| `chat`                           | `ConversationRuntime` (state), `ConversationView`, `ThreadList`, `MessageItem`, `MarkdownRenderer`, `CodeBlock`, `ChatInput`, `ChatNotice`, `ConversationSidebar`, `ThemeToggle`, `MemorySavedChip`, `chat.css` (about 1470 lines) | `/`, shell                    | token-styled; `chat.css` is pinned by CSS tests; dead rules removed (E21)                              |
| `voice`                          | `VoiceOrb`, `VoiceDock`, `VoiceChip`                                                                                                                                                                                               | conversation, title bar       | token-styled                                                                                           |
| `tasks`                          | `TaskCenterView`, `NewTaskModal`, `TaskCard`, `WorkPanel`, `taskStatus`                                                                                                                                                            | `/tasks`, thread, right panel | `TaskCenterView` and `NewTaskModal` use the zinc palette (E6, E23); `TaskCard`, `WorkPanel` use tokens |
| `dag`                            | `DagVisualizer` (460 lines)                                                                                                                                                                                                        | `TaskCenterView`, `WorkPanel` | zinc palette; E9                                                                                       |
| `approvals`                      | `ApprovalCenterView`, `ApprovalCenterModal`, `ApprovalCard`, `EmailApprovalCard`, `GitDiffViewer`                                                                                                                                  | `/approvals`, thread          | views zinc palette; card token-styled; E24                                                             |
| `artifacts`                      | `ArtifactsManagerView`, `ArtifactPreview`, `ArtifactDownloadButton`, `ArtifactCard`                                                                                                                                                | `/artifacts`, chat task card      | `ArtifactCard` unused (E21)                                                                            |
| `memory`                         | `MemoryView` (863 lines)                                                                                                                                                                                                           | `/memory`                     | zinc palette                                                                                           |
| `activity`                       | `ActivityView`                                                                                                                                                                                                                     | `/activity`                   | zinc palette                                                                                           |
| `mcp`                            | `McpGatewayView` (1089 lines), `McpStatusIndicator`                                                                                                                                                                                | `/connections`                | `McpStatusIndicator` unused                                                                            |
| `settings`                       | `SettingsView` (816 lines)                                                                                                                                                                                                         | `/settings`                   | zinc palette; E22                                                                                      |
| `auth`                           | `AuthPageLayout`, `AuthView`, `UnifiedAuthForm`, `AccountForm`, `AuthProvider`                                                                                                                                                     | auth routes, `AppGate`        | tokens and `ui` primitives, shared `AuthParts` (WP6; E23, E34 fixed)                                   |
| `canvas`, `citations`, `browser` | `DeliverableCanvas`, `ResearchCitationsView`, `BrowserTakeoverModal`                                                                                                                                                               | nothing                       | dead (E21), 8 tests                                                                                    |
| `agents`                         | `AgentFleetView` (557 lines)                                                                                                                                                                                                       | nothing (`/agents` redirects) | dead (E21), 4 tests                                                                                    |
| `ui`                             | `GlassBadge/Button/Card/Input/Modal`                                                                                                                                                                                               | dead folders above only       | 37 tests in `glass-primitives.test.tsx`                                                                |

## 3. State matrix

Rows are pages, columns are the states and environments that were asked for. Cell codes are defined in section 1. Evidence ids resolve in section 5.

### 3.1 Counts per page

| Page                                                                    |  OK | BUG | UNKNOWN | N/A |
| :---------------------------------------------------------------------- | --: | --: | ------: | --: |
| Shell (AppShell, AppGate, WorkspaceProvider)                            |  21 |   3 |       0 |   1 |
| Conversation (/)                                                        |  20 |   2 |       3 |   0 |
| Tasks (/tasks)                                                          |  21 |   0 |       4 |   0 |
| Approvals (/approvals)                                                  |  21 |   1 |       3 |   0 |
| Artifacts (/artifacts)                                                  |  10 |  11 |       2 |   2 |
| Memory (/memory)                                                        |  12 |  10 |       2 |   1 |
| Connections (/connections)                                              |  15 |   6 |       2 |   2 |
| Activity (/activity)                                                    |  11 |  11 |       1 |   2 |
| Settings (/settings)                                                    |  10 |   8 |       3 |   4 |
| Auth (/login /register /forgot-password /reset-password /verify)        |  12 |   1 |       6 |   6 |
| Not found / error / loading (app/not-found.tsx, error.tsx, loading.tsx) |  12 |   0 |       2 |  11 |
| Legacy redirects (/agents /chat /deliverables /integrations /workspace) |  12 |   0 |       1 |  12 |
| **Total** (12 rows x 25 columns)                                        | 149 |  86 |      24 |  41 |

### 3.2 Cells

**Shell (AppShell, AppGate, WorkspaceProvider)**

| State                          | Result | Evidence                                                                                                                                             |
| :----------------------------- | :----- | :--------------------------------------------------------------------------------------------------------------------------------------------------- |
| First run / empty              | O      |                                                                                                                                                      |
| Loading                        | O      |                                                                                                                                                      |
| Backend unreachable            | O      |                                                                                                                                                      |
| 401 / expired session          | O      |                                                                                                                                                      |
| Error with retry               | O      |                                                                                                                                                      |
| Partial data                   | O      |                                                                                                                                                      |
| 500+ items                     | B      | E17                                                                                                                                                  |
| Very long text                 | B      | E16                                                                                                                                                  |
| Unbroken URL / hash            | O      |                                                                                                                                                      |
| 20 KB / wide table / long code | N      |                                                                                                                                                      |
| Hindi                          | O      |                                                                                                                                                      |
| Arabic RTL                     | B      | E18                                                                                                                                                  |
| Many concurrent tasks          | O      | WP1: "50+" badge (AppShell.test)                                                                                                                     |
| 320 px                         | O      | WP1 harness: title bar and status bar `wider` 0, `smallTargets` 0; shot `ui-wp/wp1/shots/conversation__normal__320__dark.jpg`                        |
| 480 px                         | O      |                                                                                                                                                      |
| 760 px                         | O      | WP1: icon rail, drawer with scrim, Escape, focus return (AppShell.test); shot `conversation__normal__760__dark.jpg`                                  |
| 1024 px                        | O      | WP1 harness run at 1024: no page overflow, shell detectors 0; Work panel slide-over (AppShell.test)                                                  |
| 1440 px                        | O      |                                                                                                                                                      |
| 2560 px                        | O      |                                                                                                                                                      |
| 200% zoom                      | O      | WP1 harness `--zoom 2 --keyboard` at 1440 (720 CSS px): 0 detector hits, 0 keyboard problems; the Work panel in a real 200% window was not exercised |
| Reduced motion                 | O      |                                                                                                                                                      |
| Light theme                    | O      |                                                                                                                                                      |
| Dark theme                     | O      |                                                                                                                                                      |
| Forced colors                  | O      |                                                                                                                                                      |
| Keyboard only                  | O      |                                                                                                                                                      |

**Conversation (/)**

| State                          | Result | Evidence        |
| :----------------------------- | :----- | :-------------- |
| First run / empty              | O      |                 |
| Loading                        | O      |                 |
| Backend unreachable            | O      |                 |
| 401 / expired session          | O      |                 |
| Error with retry               | O      |                 |
| Partial data                   | O      |                 |
| 500+ items                     | U      |                 |
| Very long text                 | O      | E20 fixed (WP2) |
| Unbroken URL / hash            | O      | E20 fixed (WP2) |
| 20 KB / wide table / long code | O      | E19 fixed (WP2) |
| Hindi                          | O      |                 |
| Arabic RTL                     | O      | E18 fixed (WP2) |
| Many concurrent tasks          | U      |                 |
| 320 px                         | O      | E2 fixed (WP2)  |
| 480 px                         | O      |                 |
| 760 px                         | B      | E3              |
| 1024 px                        | U      |                 |
| 1440 px                        | O      |                 |
| 2560 px                        | O      |                 |
| 200% zoom                      | B      | E3              |
| Reduced motion                 | O      |                 |
| Light theme                    | O      |                 |
| Dark theme                     | O      |                 |
| Forced colors                  | O      |                 |
| Keyboard only                  | O      |                 |

**Tasks (/tasks)**

| State                          | Result | Evidence                                             |
| :----------------------------- | :----- | :--------------------------------------------------- |
| First run / empty              | O      |                                                      |
| Loading                        | O      |                                                      |
| Backend unreachable            | O      |                                                      |
| 401 / expired session          | O      |                                                      |
| Error with retry               | O      | WP3: ErrorState + retry, unit test, error500 harness |
| Partial data                   | O      |                                                      |
| 500+ items                     | O      | WP3: shrink-0 rows, Load more, real total            |
| Very long text                 | O      | WP3: wrap, clamp + Show all                          |
| Unbroken URL / hash            | O      | WP3: WrapText, harness long state                    |
| 20 KB / wide table / long code | U      |                                                      |
| Hindi                          | O      |                                                      |
| Arabic RTL                     | U      | WP3: dir=auto added, rtl state not run               |
| Many concurrent tasks          | O      | WP3: list refreshes (E30); E28 not measured          |
| 320 px                         | O      | WP3: master/detail, 0 wider                          |
| 480 px                         | U      | WP3: not run                                         |
| 760 px                         | O      | WP3: master/detail, 0 wider                          |
| 1024 px                        | O      | WP3: 0 wider                                         |
| 1440 px                        | O      | WP3: two columns, 0 wider                            |
| 2560 px                        | O      |                                                      |
| 200% zoom                      | U      | WP3: not run                                         |
| Reduced motion                 | O      |                                                      |
| Light theme                    | O      | WP3: 0 contrast failures                             |
| Dark theme                     | O      | WP3: 0 contrast failures                             |
| Forced colors                  | O      |                                                      |
| Keyboard only                  | O      |                                                      |

**Approvals (/approvals)**

| State                          | Result | Evidence                                                             |
| :----------------------------- | :----- | :------------------------------------------------------------------- |
| First run / empty              | O      |                                                                      |
| Loading                        | O      |                                                                      |
| Backend unreachable            | O      |                                                                      |
| 401 / expired session          | O      |                                                                      |
| Error with retry               | O      | WP3: ErrorState + retry, unit tests                                  |
| Partial data                   | O      |                                                                      |
| 500+ items                     | O      | WP3: shrink-0 rows, cap hint + Show more (max 100, API has no total) |
| Very long text                 | O      | WP3: wrap, harness long state                                        |
| Unbroken URL / hash            | O      | WP3: digest wraps                                                    |
| 20 KB / wide table / long code | O      |                                                                      |
| Hindi                          | O      |                                                                      |
| Arabic RTL                     | U      | WP3: dir=auto added, rtl state not run                               |
| Many concurrent tasks          | B      | E26 (shell badge, WP1)                                               |
| 320 px                         | O      | WP3: master/detail, 0 wider                                          |
| 480 px                         | U      |                                                                      |
| 760 px                         | O      | WP3: master/detail, 0 wider                                          |
| 1024 px                        | O      | WP3: 0 wider                                                         |
| 1440 px                        | O      | WP3: 0 wider                                                         |
| 2560 px                        | O      | WP3: 0 wider                                                         |
| 200% zoom                      | U      | WP3: not run                                                         |
| Reduced motion                 | O      | WP3: motion-none bar, no transition near controls                    |
| Light theme                    | O      | WP3: 0 contrast failures                                             |
| Dark theme                     | O      | WP3: 0 contrast failures                                             |
| Forced colors                  | O      |                                                                      |
| Keyboard only                  | O      |                                                                      |

**Artifacts (/artifacts)**

| State                          | Result | Evidence                                                                                 |
| :----------------------------- | :----- | :--------------------------------------------------------------------------------------- |
| First run / empty              | O      |                                                                                          |
| Loading                        | O      |                                                                                          |
| Backend unreachable            | O      |                                                                                          |
| 401 / expired session          | O      |                                                                                          |
| Error with retry               | O      |                                                                                          |
| Partial data                   | O      |                                                                                          |
| 500+ items                     | U      |                                                                                          |
| Very long text                 | O      | WP4 re-audit 2026-11-10: was E33; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Unbroken URL / hash            | O      | WP4 re-audit 2026-11-10: was E33; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 20 KB / wide table / long code | N      |                                                                                          |
| Hindi                          | O      |                                                                                          |
| Arabic RTL                     | O      | WP4 re-audit 2026-11-10: was E18; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Many concurrent tasks          | N      |                                                                                          |
| 320 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 480 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 760 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 1024 px                        | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 1440 px                        | O      |                                                                                          |
| 2560 px                        | O      |                                                                                          |
| 200% zoom                      | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Reduced motion                 | O      |                                                                                          |
| Light theme                    | O      | WP4 re-audit 2026-11-10: was E6; harness 0 wider/clippedY/contrast, keyboard 0 problems  |
| Dark theme                     | O      | WP4 re-audit 2026-11-10: was E31; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Forced colors                  | U      |                                                                                          |
| Keyboard only                  | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |

**Memory (/memory)**

| State                          | Result | Evidence                                                                                 |
| :----------------------------- | :----- | :--------------------------------------------------------------------------------------- |
| First run / empty              | O      |                                                                                          |
| Loading                        | O      |                                                                                          |
| Backend unreachable            | O      |                                                                                          |
| 401 / expired session          | O      |                                                                                          |
| Error with retry               | O      | WP4 re-audit 2026-11-10: was E6; harness 0 wider/clippedY/contrast, keyboard 0 problems  |
| Partial data                   | O      |                                                                                          |
| 500+ items                     | O      | WP4 re-audit 2026-11-10: was E17; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Very long text                 | O      | WP4 re-audit 2026-11-10: was E33; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Unbroken URL / hash            | O      | WP4 re-audit 2026-11-10: was E33; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 20 KB / wide table / long code | U      |                                                                                          |
| Hindi                          | O      |                                                                                          |
| Arabic RTL                     | O      | WP4 re-audit 2026-11-10: was E18; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Many concurrent tasks          | N      |                                                                                          |
| 320 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 480 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 760 px                         | O      |                                                                                          |
| 1024 px                        | O      |                                                                                          |
| 1440 px                        | O      |                                                                                          |
| 2560 px                        | O      |                                                                                          |
| 200% zoom                      | O      |                                                                                          |
| Reduced motion                 | O      |                                                                                          |
| Light theme                    | O      | WP4 re-audit 2026-11-10: was E6; harness 0 wider/clippedY/contrast, keyboard 0 problems  |
| Dark theme                     | O      | WP4 re-audit 2026-11-10: was E31; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| Forced colors                  | U      |                                                                                          |
| Keyboard only                  | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems |

**Connections (/connections)**

| State | Result | Evidence |
| :----------------------------- | U | Not run |
| First run / empty | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Loading | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Backend unreachable | O | Covered by component tests |
| 401 / expired session | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Error with retry | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Partial data | O | Covered by component tests |
| 500+ items | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Very long text | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Unbroken URL / hash | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 20 KB / wide table / long code | U | Not run |
| Hindi | U | Not run |
| Arabic RTL | U | Not run |
| Many concurrent tasks | U | Not run |
| 320 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 480 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 760 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 1024 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 1440 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 2560 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 200% zoom | U | Not run |
| Reduced motion | U | Not run |
| Light theme | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Dark theme | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Forced colors | U | Not run |
| Keyboard only | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |

**Activity (/activity)**

| State                          | Result | Evidence                                                                                     |
| :----------------------------- | :----- | :------------------------------------------------------------------------------------------- |
| First run / empty              | O      |                                                                                              |
| Loading                        | O      |                                                                                              |
| Backend unreachable            | O      |                                                                                              |
| 401 / expired session          | O      |                                                                                              |
| Error with retry               | O      |                                                                                              |
| Partial data                   | O      |                                                                                              |
| 500+ items                     | O      | WP4 re-audit 2026-11-10: was E17; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| Very long text                 | O      | WP4 re-audit 2026-11-10: was E14; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| Unbroken URL / hash            | O      | WP4 re-audit 2026-11-10: was E14; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| 20 KB / wide table / long code | N      |                                                                                              |
| Hindi                          | O      |                                                                                              |
| Arabic RTL                     | O      | WP4 re-audit 2026-11-10: was E18; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| Many concurrent tasks          | N      |                                                                                              |
| 320 px                         | O      | WP4 re-audit 2026-11-10: was E13,E14; harness 0 wider/clippedY/contrast, keyboard 0 problems |
| 480 px                         | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| 760 px                         | O      | WP4 re-audit 2026-11-10: was E14; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| 1024 px                        | O      |                                                                                              |
| 1440 px                        | O      |                                                                                              |
| 2560 px                        | O      |                                                                                              |
| 200% zoom                      | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| Reduced motion                 | O      |                                                                                              |
| Light theme                    | O      | WP4 re-audit 2026-11-10: was E6; harness 0 wider/clippedY/contrast, keyboard 0 problems      |
| Dark theme                     | O      | WP4 re-audit 2026-11-10: was E31; harness 0 wider/clippedY/contrast, keyboard 0 problems     |
| Forced colors                  | U      |                                                                                              |
| Keyboard only                  | O      | WP4 re-audit 2026-11-10: was E13; harness 0 wider/clippedY/contrast, keyboard 0 problems     |

**Settings (/settings)**

| State | Result | Evidence |
| :----------------------------- | U | Not run |
| First run / empty | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Loading | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Backend unreachable | O | Covered by component tests |
| 401 / expired session | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Error with retry | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Partial data | O | Covered by component tests |
| 500+ items | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Very long text | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Unbroken URL / hash | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 20 KB / wide table / long code | U | Not run |
| Hindi | U | Not run |
| Arabic RTL | U | Not run |
| Many concurrent tasks | U | Not run |
| 320 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 480 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 760 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 1024 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 1440 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 2560 px | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| 200% zoom | U | Not run |
| Reduced motion | U | Not run |
| Light theme | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Dark theme | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |
| Forced colors | U | Not run |
| Keyboard only | O | WP5 harness 2026-11-10 (0 page overflow, 0 keyboard problems, 0 contrast issues) |

**Auth (/login /register /forgot-password /reset-password /verify)**

| State                          | Result | Evidence                                                                  |
| :----------------------------- | :----- | :------------------------------------------------------------------------ |
| First run / empty              | U      |                                                                           |
| Loading                        | O      |                                                                           |
| Backend unreachable            | U      |                                                                           |
| 401 / expired session          | O      |                                                                           |
| Error with retry               | U      |                                                                           |
| Partial data                   | N      |                                                                           |
| 500+ items                     | N      |                                                                           |
| Very long text                 | N      |                                                                           |
| Unbroken URL / hash            | N      |                                                                           |
| 20 KB / wide table / long code | N      |                                                                           |
| Hindi                          | U      |                                                                           |
| Arabic RTL                     | U      |                                                                           |
| Many concurrent tasks          | N      |                                                                           |
| 320 px                         | O      |                                                                           |
| 480 px                         | O      |                                                                           |
| 760 px                         | O      |                                                                           |
| 1024 px                        | O      |                                                                           |
| 1440 px                        | O      |                                                                           |
| 2560 px                        | O      |                                                                           |
| 200% zoom                      | O      |                                                                           |
| Reduced motion                 | O      |                                                                           |
| Light theme                    | O      | WP6 harness 2026-11-10 (tokens only; honours the chosen theme, E34 fixed) |
| Dark theme                     | O      |                                                                           |
| Forced colors                  | U      |                                                                           |
| Keyboard only                  | O      |                                                                           |

**Not found / error / loading (app/not-found.tsx, error.tsx, loading.tsx)**

| State                          | Result | Evidence |
| :----------------------------- | :----- | :------- |
| First run / empty              | N      |          |
| Loading                        | O      |          |
| Backend unreachable            | N      |          |
| 401 / expired session          | N      |          |
| Error with retry               | U      |          |
| Partial data                   | N      |          |
| 500+ items                     | N      |          |
| Very long text                 | N      |          |
| Unbroken URL / hash            | N      |          |
| 20 KB / wide table / long code | N      |          |
| Hindi                          | N      |          |
| Arabic RTL                     | N      |          |
| Many concurrent tasks          | N      |          |
| 320 px                         | O      |          |
| 480 px                         | O      |          |
| 760 px                         | O      |          |
| 1024 px                        | O      |          |
| 1440 px                        | O      |          |
| 2560 px                        | O      |          |
| 200% zoom                      | O      |          |
| Reduced motion                 | O      |          |
| Light theme                    | O      |          |
| Dark theme                     | O      |          |
| Forced colors                  | U      |          |
| Keyboard only                  | O      |          |

**Legacy redirects (/agents /chat /deliverables /integrations /workspace)**

| State                          | Result | Evidence |
| :----------------------------- | :----- | :------- |
| First run / empty              | N      |          |
| Loading                        | O      |          |
| Backend unreachable            | N      |          |
| 401 / expired session          | N      |          |
| Error with retry               | N      |          |
| Partial data                   | N      |          |
| 500+ items                     | N      |          |
| Very long text                 | N      |          |
| Unbroken URL / hash            | N      |          |
| 20 KB / wide table / long code | N      |          |
| Hindi                          | N      |          |
| Arabic RTL                     | N      |          |
| Many concurrent tasks          | N      |          |
| 320 px                         | O      |          |
| 480 px                         | O      |          |
| 760 px                         | O      |          |
| 1024 px                        | O      |          |
| 1440 px                        | O      |          |
| 2560 px                        | O      |          |
| 200% zoom                      | O      |          |
| Reduced motion                 | O      |          |
| Light theme                    | O      |          |
| Dark theme                     | O      |          |
| Forced colors                  | U      |          |
| Keyboard only                  | O      |          |

### 3.3 Reading the matrix: what "OK" depends on

- **Empty, loading, unreachable, expired:** the shell handles these globally. Backend unreachable and signed-out show the `AppGate` cards (verified in all 8 app pages). A real session expiry (refresh answers 401) returns to the sign-in card (verified with the `expired` state). Loading is text or a spinner on every view; there are no skeletons, so layout shift on arrival was not measured.
- **Not the same as expiry:** when the data endpoints answer 401 while refresh still succeeds (`expired401` state, an inconsistent backend), the views show generic errors or an empty list (E8) and never sign the user out.
- **Draft loss on expiry:** `AppGate` swaps the whole shell for the sign-in card, so an unsent composer draft and the open thread are dropped. Not measured; recorded as a risk for the shell package.
- **Reduced motion:** `globals.css` collapses every animation and transition under `prefers-reduced-motion: reduce`, and no animation was running in the idle states that were captured. Approval screens are marked B because of E24, not because motion runs under reduce.
- **Forced colors:** screenshots under `forced-colors: active` (conversation, tasks, approvals, memory, settings, login) stay readable with visible borders and the status badges keep their icon and label. Pages that were not captured are U.
- **Keyboard:** Tab traversal ran for 12 pages at 320, 760 and 1440 px in both themes: no missing focus indicator, no obscured focus, logical order, skip link present. The only problems are chips off screen at 320 px (E13). The traversal is capped at 45 stops, so the end of long pages was not reached. The skip-link and composer results that an earlier run flagged were false positives (transition timing, indicator on the focus-within wrapper) and are fixed in the harness.

## 4. Worst-case content per page

What each page must survive. These are the cases the fixtures generate (`long`, `many`, `rtl`, `busy`).

| Page         | Worst case                                                                                                                                                                             | Observed today                                                                                                                                                                                              |
| :----------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Conversation | 300 messages, one 20 KB assistant message with a 14-column table, a 400-character unbroken URL, a 300-character code line, a 300-character title, Arabic and Hindi text, 20 task cards | Message text wraps (`overflow-wrap:anywhere`, `chat.css:605,733`); code scrolls inside its block; table shows as raw pipes (E19); task and approval cards overflow (E20); composer collapses at 320 px (E2) |
| Tasks        | 600 tasks, 300-character titles and instructions, 60 running tasks with 12 steps each, 400-character failure reasons                                                                   | Only 50 shown (E17), rows crushed (E7), detail overflows (E9, E29), badges overflow (E11)                                                                                                                   |
| Approvals    | 60 pending, an email with a long subject and body, a git diff with 400-character lines, a 128-character hash                                                                           | Digest line overflows (E15); `pre` blocks scroll correctly; list crushed (E7); an error looks empty (E8)                                                                                                    |
| Artifacts    | 500 deliverables, file names of 300 characters, a URL as a name                                                                                                                        | Table has a scroll wrapper (`ArtifactsManagerView.tsx:246`); names truncated with no title (E33); no paging (E17)                                                                                           |
| Memory       | 600 items, 400-character content, long metadata URLs                                                                                                                                   | Content clipped (E33); only the first page loads (E17)                                                                                                                                                      |
| Connections  | 60 servers, long command args, 128-character schema hashes                                                                                                                             | Table has a scroll wrapper (`McpGatewayView.tsx:741`); card toolbar clips at 320 px (E13)                                                                                                                   |
| Activity     | 600 events with long actors, URLs, hashes, payload JSON                                                                                                                                | Row overflow of 647 px (E14); no paging (E17)                                                                                                                                                               |
| Settings     | Very long user id (status bar shows it too)                                                                                                                                            | Tabs run off the edge (E12); invented values (E22)                                                                                                                                                          |
| Auth         | Long email, RTL names, password manager overlays                                                                                                                                       | Stable at all widths; always dark (E34)                                                                                                                                                                     |

## 5. Evidence index

Screenshot and result names refer to files written by the harness under `scratchpad/ui-audit/` (`shots/`, `extra/*/shots/`, `results.json`). They are outside the repository on purpose; rerun the harness to regenerate them.

| Id  | Finding                                                                                                                                                                                                                                                                                                                                                                                        |
| :-- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E1  | Title bar overflows at 320 px: the voice chip and theme toggle exceed the bar by 10 px (detector `wider`, `header[data-testid=title-bar]`). `AppShell.tsx:125-150`; shot `conversation__long__320__light.jpg`.                                                                                                                                                                                 |
| E2  | Composer textarea collapses to about one character wide at 320 px: mic orb, Run as task and send consume the row. `ConversationView.tsx:145-178`, `chat.css` `.maya-composer__*`; shot `conversation__long__320__light.jpg`.                                                                                                                                                                   |
| E3  | Work and Canvas panel is `hidden ... lg:block` (`AppShell.tsx:231`). Below 1024 px, or at 200% zoom of a 1440 px window, a selected task or deliverable cannot be opened.                                                                                                                                                                                                                      |
| E4  | Narrow-width sidebar is an absolute `z-10` overlay with no scrim, no Escape handling and no focus management; it closes only on route change (`AppShell.tsx:88-90,158`).                                                                                                                                                                                                                       |
| E5  | Status bar wraps at 320 px (document scrollHeight 643 vs viewport 640); "Backend ready" and "Sign out" break onto two lines (`AppShell.tsx:240-270`; shot `tasks__many__320__light.jpg`).                                                                                                                                                                                                      |
| E6  | Hard-coded `zinc-*`/`white/*` palette in 14 view files (about 600 class uses) instead of tokens. In the default light theme headings measure 1.07:1 and body text 2.5:1 (results.json `contrast`; shots `memory__normal__1440__light.jpg`, `settings__normal__1440__light.jpg`, `connections__normal__760__light.jpg`). Memory error box is red on pink (`memory__error500__1440__light.jpg`). |
| E7  | List rows are crushed: rows are `button.w-full` in a `flex-col overflow-y-auto` container without `shrink-0`, so with more than about 8 rows each shrinks to about 28 px and clips its content (scrollHeight 112 vs clientHeight 28). `TaskCenterView.tsx:305-322`, `ApprovalCenterView.tsx:207-222`; shot `tasks__many__320__light.jpg`.                                                      |
| E8  | A list load failure looks like an empty list. The error is rendered only in the detail pane (`TaskCenterView.tsx:366`, `ApprovalCenterView.tsx:301`) while the list says "No tasks found." (`TaskCenterView.tsx:313`) or "No approval requests matching filter." (`ApprovalCenterView.tsx:213`). For approvals this can hide a pending decision. Shot `approvals__error500__1440__light.jpg`.  |
| E9  | Task detail DAG panel overflows at 1024 and 1440 px: `DagVisualizer.tsx:300` (`lg:w-96 shrink-0`) sits beside the graph inside a `max-w-5xl` column; measured 1544 px wider than its parent, and the node badge reads "Complet" in `tasks__normal__1440__light.jpg`.                                                                                                                           |
| E10 | Below `lg` the list and the detail are stacked and both are `h-full` (`TaskCenterView.tsx:233-235,358`; same pattern in `ApprovalCenterView.tsx`), so the detail is a sliver (`tasks__normal__760__light.jpg`; `main > div.flex.h-full` scrollHeight 884 vs 836).                                                                                                                              |
| E11 | Status badges are not `shrink-0`/`min-w-0` (`TaskCenterView.tsx:185-224`, header `334-338`): the "Completed" badge is up to 129 px wider than its parent and the "Awaiting Approval" badge wraps to two lines.                                                                                                                                                                                 |
| E12 | Settings tabs are `whitespace-nowrap` inside an `overflow-x-auto` card with no scroll cue (`SettingsView.tsx:179-215`); the "Danger Zone" tab is off the edge at 1024 px and below.                                                                                                                                                                                                            |
| E13 | Filter chip rows: the Activity filter card clips vertically at every width (scrollHeight 92 vs clientHeight 75), and at 320 px keyboard focus lands on chips that are off screen (extra/keyboard results). `ActivityView.tsx:373-386`, `MemoryView.tsx:428-440`, `ArtifactsManagerView.tsx:178-191`, `McpGatewayView.tsx:584-594`.                                                             |
| E14 | Activity rows overflow by up to 647 px with long actors, URLs and hashes: flex row without `min-w-0` and without `break-all` (`ActivityView.tsx` event rows around 470-495; shot `activity__long__320__light.jpg`).                                                                                                                                                                            |
| E15 | The approvals digest line "RFC 8785 Canonical Digest (SHA-256): sha256:..." is 368 px wider than its parent at every width (`ApprovalCenterView.tsx:331`).                                                                                                                                                                                                                                     |
| E16 | Sidebar session titles end in an ellipsis with no `title` attribute or other way to read the rest (`ConversationSidebar.tsx:78-85`; detector "ellipsis-no-title" on every row).                                                                                                                                                                                                                |
| E17 | No pagination or total anywhere: each list loads one page and stops. Tasks 50 (`TaskCenterView.tsx:70`), approvals 50 (`ApprovalCenterView.tsx:61`), sessions 50 (`ConversationRuntime.tsx:119`), memory and audit use the client default (`MemoryView.tsx:155`, `ActivityView.tsx:156`). With 600 tasks only 50 show and nothing says more exist.                                             |
| E18 | No bidirectional text support: no `dir="auto"` anywhere in `src`, and `<html lang="en">` only (`app/layout.tsx:17`). Arabic titles and messages align left and can mis-order punctuation. Code evidence; not visually verified.                                                                                                                                                                |
| E19 | Markdown tables are not supported (`lib/markdown.ts:6`); a table is shown as raw pipe text (shot `x-conversation-approval__long__1440__light.jpg`).                                                                                                                                                                                                                                            |
| E20 | Thread cards let long unbroken words run past the card edge; only the title truncates (`TaskCard.tsx:100-118`, `ApprovalCard.tsx:64-68`; shot `x-conversation-approval__long__1440__light.jpg`).                                                                                                                                                                                               |
| E21 | Dead UI: `AgentFleetView`, `DeliverableCanvas`, `ResearchCitationsView`, `McpStatusIndicator`, `ArtifactCard`, `BrowserTakeoverModal` (barrel only) and `GlassInput` have no importer; `GlassCard/Modal/Badge/Button` are reached only through them. Legacy CSS `.maya-chat--embedded` (15 rules) and `.maya-home` (24 rules) have no TSX user.                                                |
| E22 | Invented or jargon copy: hard-coded "us-central1 (Docker Isolated Ephemeral VPC)" and "LEVEL 4 PRIVILEGES" (`SettingsView.tsx:296`), "Phase 08 Core" (`McpGatewayView.tsx:534`), and developer jargon in headings (pgvector, Redis, LangGraph, RFC 8785: `MemoryView.tsx:306-312`, `TaskCenterView.tsx:493`). Conflicts with "do not simulate" and plain-copy rules.                           |
| E23 | Banned styling (`UI_UX_STANDARDS.md` section 2): `backdrop-blur` used 31 times in 10 files, gradient buttons (`TaskCenterView.tsx:259`), glow shadows (`TaskCenterView.tsx:325`), blurred colour blobs (`AuthPageLayout.tsx:15-22`), `animate-pulse` loops (`AuthView.tsx:33`, `TaskCenterView.tsx:386`).                                                                                      |
| E24 | Approval screens are not still: `components/approvals/*.tsx` carry 36 `transition`/`animate`/`backdrop-blur` classes around the decision controls, against `apps/web/AGENTS.md` ("no animation near the decision controls").                                                                                                                                                                   |
| E25 | Magic z-index and fixed positions bypass the tokens: modals `z-50`/`z-60` (`ApprovalCenterModal.tsx:212,576`, `GlassModal.tsx:134`), toasts `fixed bottom-6 right-6 z-40` (`McpGatewayView.tsx:840`, `MemoryView.tsx:843`), `max-h-[90vh]` modals (`NewTaskModal.tsx:209`, `ApprovalCenterModal.tsx:219`), `GlassModal.tsx:175` `max-h-[calc(100vh-14rem)]`.                                   |
| E26 | The pending-approvals badge is capped at 50 because the shell fetches `listApprovals("pending", 50)` (`WorkspaceProvider.tsx`), with no "50+" indication.                                                                                                                                                                                                                                      |
| E27 | The thread is not virtualised (`ThreadList.tsx` renders every item); a 300-message history is 300 articles. Performance not measured.                                                                                                                                                                                                                                                          |
| E28 | `WorkPanel.tsx` opens one `useTaskEvents` stream per task; many tasks could exhaust the browser per-origin connection limit. Not measured.                                                                                                                                                                                                                                                     |
| E29 | Task detail text overflows: the mono instruction block runs past the card at 1440 px (`tasks__long__1440__light.jpg`: lines cut at the right edge) and long titles are only `line-clamp`ed with no way to read them.                                                                                                                                                                           |
| E30 | The task list never refreshes: `TaskCenterView.tsx` has no polling, so statuses stay stale until the user presses refresh; only the selected task detail is live.                                                                                                                                                                                                                              |
| E31 | Small muted text fails AA in dark: `text-zinc-500` at 11 px on dark surfaces measures 3.5 to 3.9:1 (results.json `contrast`: tasks 17, memory 18, activity 31, connections 12 per case).                                                                                                                                                                                                       |
| E32 | Hit targets under 24 px (WCAG 2.2 AA 2.5.8): Speak button 22 px, Sign out 16 px high (`AppShell.tsx:259-268`), and 668 small targets across the Memory page cases (results.json `smallTargets`).                                                                                                                                                                                               |
| E33 | Long names are truncated by fixed pixel widths with no `title`: `max-w-[200px]` (`ArtifactsManagerView.tsx:278`), `[220px]` (`MemoryView.tsx:558`), `[190px]` (`McpGatewayView.tsx:691`), `[140px]` (`DeliverableCanvas.tsx:146`), `[90px]` (`ActivityView.tsx:478-485`).                                                                                                                      |
| E34 | Auth pages ignore the chosen theme: `AuthPageLayout.tsx:13` hard-codes `bg-zinc-950 text-zinc-100` plus blurred blobs, and uses `min-h-screen` (100vh).                                                                                                                                                                                                                                        |

## 6. Static overflow audit

Searched for the causes in the brief. Findings that were also seen in the browser carry an E-number; the rest are static only.

| Cause                                           | Where                                                                                                                                                                                                                                                 | Fix suggestion                                                                                                                  |
| :---------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------ |
| Missing `min-w-0` / `shrink-0` on flex children | `TaskCenterView.tsx:305-322` and `ApprovalCenterView.tsx:207-222` (rows shrink, E7); `TaskCenterView.tsx:334-338` badge (E11); `ActivityView.tsx` event rows (E14)                                                                                    | Give list rows `shrink-0`, badges `shrink-0`, text columns `min-w-0` plus `break-words` or `break-all` for ids and hashes       |
| `overflow-hidden` hiding content                | `TaskCenterView.tsx:233-235` (`h-full ... overflow-hidden` shell for the list and detail), `AppShell.tsx:120` (`h-dvh overflow-hidden`), rows with `relative overflow-hidden` (`TaskCenterView.tsx:322`)                                              | Let the region that should scroll own `overflow-y-auto` and give it a bounded height; avoid `overflow-hidden` on the row itself |
| Fixed px/vh sizes                               | `max-w-[90                                                                                                                                                                                                                                            | 140                                                                                                                             | 190 | 200 | 220px]`(E33);`max-h-[90vh]`, `[calc(100vh-14rem)]`(E25);`chat.css:19,236` `100vh`; `AuthPageLayout.tsx:13` `min-h-screen`, blobs `w-[600px]`(E34);`DagVisualizer.tsx:320` `max-h-[500px]` | Use token sizes, `dvh`, `min-w-0` with `truncate` plus a `title` or expandable row; drop the blobs |
| `whitespace-nowrap` without truncation          | Chip and tab rows: `ActivityView.tsx:386`, `ArtifactsManagerView.tsx:191`, `McpGatewayView.tsx:594`, `MemoryView.tsx:440`, `SettingsView.tsx:212`, `TaskCenterView.tsx:292`, `DeliverableCanvas.tsx:140`; `chat.css:254,1684,2093`                    | Use a proper scrollable tab list with edge fade and `scrollIntoView` on focus, or wrap; show a scroll cue (E12, E13)            |
| Absolute/fixed without containment              | `AppShell.tsx:158` sidebar overlay (E4); toasts `McpGatewayView.tsx:840`, `MemoryView.tsx:843` (can cover the composer or content at 320 px; `max-w-sm` only); modals `fixed inset-0` in six files                                                    | One overlay primitive with scrim, focus trap, Escape and a token z-index; toasts in a shared region                             |
| Tables without a scroll wrapper                 | None found: `ArtifactsManagerView.tsx:246`, `McpGatewayView.tsx:741` are wrapped in `overflow-x-auto`; `pre` blocks all have `overflow-x-auto`                                                                                                        | Keep the wrappers; add a visible scroll cue and sticky first column                                                             |
| Missing wrap for message, markdown and code     | Message and markdown are fine (`chat.css:605,733`, code `overflow-x:auto` at `chat.css:936`). Not fine: `TaskCard.tsx:108-122`, `ApprovalCard.tsx:64-68`, `TaskCenterView.tsx` instruction block, `ApprovalCenterView.tsx:331` (E15, E20, E29)        | Add `break-words` (or `[overflow-wrap:anywhere]`) to every text node that renders backend strings                               |
| z-index wars                                    | `z-10` sidebar, `z-20` canvas (`DeliverableCanvas.tsx:82`), `z-40` toasts, `z-50` and `z-60` modals, chat.css `z-index` 1, 25, 30 (legacy), against the `--maya-z-*` tokens that nothing uses (E25)                                                   | Use the three tokens (`base`, `float`, `overlay`) and one stacking helper                                                       |
| Nested scroll containers                        | Dynamic: only one case (NewTaskModal at 320 px). Static: `WorkPanel.tsx` panel body inside the right `aside` that also has `overflow-y-auto` (`AppShell.tsx:231`); `DagVisualizer.tsx:320` `max-h-[500px] overflow-y-auto` inside the detail scroller | One scroll owner per region; remove the inner scroller or give it a fixed purpose                                               |
| Sticky headers over content                     | `chat.css:264,976` sticky header and dock (legacy `.maya-chat__header` is still used, `.maya-chat__dock` pins the composer); no overlap detected (0 sibling overlaps in 906 cases)                                                                    | Keep; add scroll padding for anchors                                                                                            |
| Tooltips and menus off the viewport             | No custom tooltip or menu components exist; native `title` attributes only. Nothing to overflow                                                                                                                                                       | A new popover primitive must be built to flip and clamp                                                                         |
| 100vh on desktop shells                         | `AppShell` and `AppGate` use `h-dvh` (good). Legacy `.maya-app` and `.maya-chat` use `100vh`; `LegacyRedirect` and error pages use `min-h-dvh`                                                                                                        | Remove legacy shells with `chat.css` cleanup                                                                                    |

## 7. Dynamic audit harness

`scratchpad/ui-audit/harness.py` (with `fixtures.py`) serves the static export from Python `http.server` on a free port and answers every `/api/v1/*` request through Playwright route interception. Run it from any shell with the `backend/.venv` Python:

```text
python harness.py --list
python harness.py --plan standard --workers 4 --keyboard          # about 900 cases, about 35 min with 4 workers
python harness.py --pages tasks,memory --widths 320,760,1440 --states normal,long,error500 --theme both
python harness.py --pages all --states normal --widths 1440 --zoom 2         # 200% zoom (viewport 720 px)
python harness.py --pages all --states normal --widths 1440 --forced-colors
python harness.py --pages all --states normal --widths 1440 --reduced-motion
```

- **Fixture states:** `normal`, `empty`, `many` (600 tasks, 600 memory, 600 audit, 500 artifacts, 200 sessions, 300 messages), `long`, `rtl`, `partial`, `busy` (60 tasks, long failure reasons), `error500`, `expired401`, `expired` (real expiry), `signedout`, `unreachable`, `slow` (requests never answer, to capture loading).
- **Pages:** the 8 app pages, 5 auth pages, not-found, 5 legacy redirects, and `x-*` variants that open the new-task modal, an approval detail, the add-memory form, the register-server form and the conversation approval card.
- **Detectors** (run in the page): horizontal page overflow, children wider than their parent, clipped text, elements outside the viewport, overlapping siblings, text contrast (alpha-composited, `oklch` safe), hit targets under 24 px, nested scrollers, fixed elements outside the viewport, running animations, and a Tab traversal for focus visibility and obscured focus.
- **Outputs:** `results.json` (machine readable), `summary.md`, `shots/*.jpg`.
- **Detector caveats:** `wider` also fires for children inside an intentional scroller, and `clippedY` fires for intentional scroll regions (the thread). Compare against the baseline in `results.json` rather than expecting zero; a redesign passes when its counts fall and no new element signature appears.

### 7.1 Measured totals (906 standard-plan cases, both themes)

| Page                               | wider | clipped | clippedY | contrast | small targets |
| :--------------------------------- | ----: | ------: | -------: | -------: | ------------: |
| activity                           |    82 |     234 |      206 |     3441 |            82 |
| approvals                          |    64 |     216 |      122 |      469 |            82 |
| artifacts                          |    90 |     304 |       36 |     8844 |           124 |
| connections                        |    52 |     240 |       12 |     2526 |            82 |
| conversation                       |    20 |     202 |       22 |        0 |           428 |
| memory                             |    40 |     320 |       37 |     1951 |           668 |
| settings                           |   148 |     204 |       60 |      846 |            82 |
| tasks                              |   172 |     374 |      874 |     1314 |            82 |
| login/register/forgot/reset/verify |     8 |      70 |        0 |        0 |            84 |
| notfound                           |     0 |       0 |        0 |        0 |             0 |

Horizontal page overflow: 0 cases, because the shell is `h-dvh overflow-hidden`: content that overflows is clipped, not scrolled, which is why the redesign must be checked with the element-level detectors and screenshots. Sibling overlaps: 0. Unmocked endpoints requested: none. Page errors: none.

## 8. What was not checked

- **Real backend and real data.** All data is generated; real payload shapes beyond the typed clients may differ.
- **Chat streaming and voice.** `POST /chat/message` answers 503 in the fixtures; the streaming caret, stop, retry and the voice dock, orb and WebSocket states were not driven. Their cells are O only for static layout.
- **Task events (SSE) and the Work panel.** The events endpoint returns an empty stream, so live DAG updates and the panel body were not exercised in the browser (static findings E3, E28 only). The panel is reachable only from a task card in a thread.
- **`ApprovalCenterModal` from the thread.** The harness opened the conversation and found the approval card, but the dialog did not open in the automated click; the dialog layout is UNKNOWN. `ApprovalCenterView` (the `/approvals` detail) was measured.
- **Sign-in flows.** The form was rendered, not submitted; first-run owner creation, wrong password, throttling and email verification states are UNKNOWN.
- **Layout shift, frame rate, input latency.** Not measured. No performance trace was taken.
- **Real browser zoom, OS text scaling, Windows high contrast themes.** 200% zoom is emulated by halving the viewport at device pixel ratio 2; forced colors is emulated by the browser.
- **Electron.** The audit used Edge against the static export, not the packaged desktop app, so the draggable title bar, window controls and native menus were not covered.
- **Light theme before this audit.** Light is the default theme; E6 shows the redesign must treat it as first class.

### Files states added (connected folder and attachments)

| Surface | State | Behaviour |
| ------- | ----- | --------- |
| Settings > Files | No folder | Explains the folder rule; desktop shows Choose folder, a browser shows a typed-path field |
| Settings > Files | Connected / Folder missing / Not writable | Icon plus word, full path (truncated with a title), plain help text, Open folder (desktop) and Disconnect |
| Settings > Files | Load failed | Error with Try again; nothing changed |
| Settings > Files | Uploaded files | Row with kind, size, date, Use in chat (inside a conversation), Download, Delete with confirmation; empty text when none |
| Conversation composer | Attachments | Chip row (uploading with progress, ready, error per file), limit notice at 10 files, send waits for uploads, drop overlay does not block input |
| Conversation thread | Sent attachments | Chips with a download action, also after reload |
| Work panel | Task output | Path in the connected folder with Open folder, or a note that the result is in MAYA's storage |
