# Frontend Architecture: Direction A ("Quiet Instrument")

**Date:** 2026-11-07
**Version:** 1.0.0
**Framework:** Next.js 15 (App Router, React 19, TypeScript 5, Tailwind CSS)
**Status:** Certified & Production Verified

---

## 1. Design Direction: "Quiet Instrument"

The MAYA AI user interface adheres strictly to **Design Direction A ("Quiet Instrument")**:

- **Design Philosophy:** Minimal visual distraction, professional utility, dark studio aesthetic with subtle frosted glass surfaces and high-contrast typography.
- **Elevation System:** Hairline border boundaries (`border-white/10`, `border-zinc-800`), dark background depth layers (`bg-neutral-950`, `bg-zinc-900/60`), and focused accent glows (`teal-500`, `amber-500`, `emerald-500`).
- **Glassmorphism Primitives:** Frosted glass reserved exclusively for floating headers, drawers, composers, and dialog overlays (`backdrop-blur-xl bg-black/60`).
- **Purposeful Motion:** Pure CSS animations for state transitions, message streaming caret, and live voice orb frequency ripple. Zero heavyweight animation libraries; fully compliant with `prefers-reduced-motion`.

---

## 1a. Shell, routes and tokens

**Persistent shell.** `app/(app)/layout.tsx` mounts `WorkspaceProvider` > `AppGate` > `AppShell` once; the shell (title bar, sidebar, one `<main>`, status bar) stays mounted while pages change. There is no Work panel and no Canvas (removed 2026-11-10): task progress, approvals and produced files appear in the chat thread. Width modes (`useShellMode`): full and compact >= 1024 px (same layout); narrow 760-1023 (icon rail, conversations in a drawer); mobile < 760 (one drawer behind a menu button). Drawers use `useOverlay` (scrim, Escape, focus trap, focus return, `inert` main). The title bar is a drag region (`-webkit-app-region`, controls opt out) so Electron can use it later; it is inert in a browser. The app has no marketing pages.

- `WorkspaceProvider` holds auth (`useAuth`), backend readiness (`checkBackendReady`, polled every 30 s while the tab is visible), the pending-approvals count (polled every 30 s) and two extension points, `conversation` (new-conversation action, sidebar list, title) and `voice` (title-bar chip, status text), filled by slice A4.
- `AppGate` states: `booting`, `backend_unreachable` (Retry plus first-run hint), `signed_out` (the auth view inside app chrome), `ready`. `booting` admits slowness after 8 s and offers Retry; `signed_out` shows a "session ended" notice when the session ended without the user signing out (`WorkspaceProvider.sessionExpired`). `WorkspaceProvider` also mounts the toast region (`notify`, `dismissToast`), exposes `signOut`, and sets `<html data-paused>` while the tab is hidden.

**Route map** (all static client pages; compatible with `output: 'export'`, no middleware or route handlers):

| Route                                                                                                                      | View                                                        |
| :------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------- |
| `/`                                                                                                                        | Conversation (`ChatWorkspace` until A4 replaces it)         |
| `/tasks` (`?task_id=`), `/approvals`, `/artifacts`, `/memory`, `/connections`, `/activity`, `/settings`                    | Secondary views in the shell                                |
| `/login`, `/register`, `/verify`, `/forgot-password`, `/reset-password`                                                    | Bare auth pages outside the shell (email links point here)  |
| `/chat`, `/workspace` -> `/`; `/deliverables` -> `/artifacts`; `/integrations` -> `/connections`; `/agents` -> `/settings` | Client-side redirects (`LegacyRedirect`), query string kept |

**Tokens.** Direction A tokens live in `app/globals.css` on `:root` (light), redefined for system dark and for `data-maya-theme="dark"`; `@theme inline` maps them to utilities (`bg-maya-surface`, `text-maya-muted`, `border-maya-border`). `components/ui` (GlassCard, GlassButton, GlassBadge, GlassInput, GlassModal keep their names) use only these tokens: no blur, glow or ripple, visible focus rings, reduced motion respected. `chat.css` no longer carries a token block (deleted in WP2); tokens live only in `globals.css`.

---

## 2. Core Surfaces Inventory

### 2.1 Conversation (`/`; `/workspace` and `/chat` redirect here)

- **Voice Waveform Orb (`VoiceOrb.tsx`):**
  - Smooth particle and wave animation reacting to live WebAudio microphone frequencies.
  - State machine transitions: `idle`, `listening`, `thinking`, `speaking`, `interrupted`.
- **Synchronized Message Thread (`MessageThread.tsx`):**
  - Live SSE message streaming, automatic scroll anchoring, syntax-highlighted code blocks, and structured notice banners.
- **Files in the chat (`tasks/TaskFiles.tsx`):**
  - A finished task's `TaskCard` lists the files it produced as compact cards (name, type, size, authenticated Download). Chat is the one place to download a file from a task. The saved name keeps its extension (`downloadFileName` in `lib/artifacts-client.ts`, zips included) and `TaskOutputLocation` says where the files went. There is no deliverable preview or Canvas.

### 2.2 Task Center (`/tasks`)

- **Task list and detail (`TaskCenterView.tsx`, `TaskCard.tsx`):**
  - Real-time SSE task event listener (`useTaskEvents.ts`); the list also refreshes itself every 10 s and pages with "Load more" and the real total.
  - Two columns (`min-w-0`, one vertical scroll per pane) when the content region is at least about 1100 px wide; below that the list and the detail are separate screens with a back button (container query, not the window width).
  - A failed task reads as a danger state; the cause is one plain sentence (`explainFailure` in `taskStatus.ts`) with the technical text in a wrapped "Technical details" region. Cost shows "Cost not tracked" unless the API reports a positive number.
  - The thread `TaskCard` follows the live task (it refetches on every lifecycle event and accepts `liveStatus`); it shows progress, Cancel, Try again (the new task gets its own card through `ConversationRuntime.trackTask`) and its files. Approvals for the thread's tasks are inline `ApprovalCard`s and stay on `/approvals`.
- **Plan graph (`DagVisualizer.tsx`):**
  - Steps in order in a list that scrolls inside its own region (up to 30 steps), arrow-key navigation between nodes, a step inspector (description, copyable id, sources, proof, output, failure). Nothing in the panel needs horizontal scrolling.

### 2.3 Approval Center (`/approvals`)

- **Approval page and dialog (`ApprovalCenterView.tsx`, `ApprovalCenterModal.tsx`) share one review screen (`ApprovalDetail.tsx`):**
  - Exact payload in full, email preview (`EmailApprovalCard.tsx`), Git diff (`GitDiffViewer.tsx`), payload digest and execution token.
  - Decision bar fixed under the content inside `motion-none`; code dialog (RESTRICTED tier) and rejection-reason dialog. A failed list is an error, a capped list says so, and after a decision the next request is not auto-selected. `/approvals?task_id=` selects that task's request.

### 2.4 Browser Takeover Modal (`BrowserTakeoverModal.tsx`)

- **Human-in-the-Loop Bot Challenge Resolution:**
  - Invoked when Phase 13 `AntiBotDetector` flags Cloudflare Turnstile, reCAPTCHA, or hCaptcha challenges.
  - Strict compliance with anti-bypass security policy: MAYA AI never attempts programmatic CAPTCHA solving.
  - Provides viewport screenshot, session resumption token, and resume trigger (`POST /api/v1/browser/session/{id}/resume`).

### 2.5 Operational Surfaces

- **Connections (`/connections`, `/integrations` redirects here):** the tool servers registered in the workspace with their stored health (Connected, Degraded, Failed, Unknown), tool count and the pinned version and schema hash; register (stdio or streamable_http, the transports the backend accepts) and remove with a confirmation. "Refresh status" reads the status the server last recorded; it does not probe the server. URLs are shown without credentials or query, and arguments that look like secrets are masked. A fixed list of suggestions is labelled as not connected; whether GitHub, Gmail, Calendar and Notion are configured is planned (no API exposes it yet).
- **Memory (`/memory`):** only real records (text, category, source when stored, date, retention), debounced search, add, two-step delete, purge with typed confirmation, and "Load more" paging (`limit`/`offset`; the API returns no total, so the count reads "50+" until the last short page). Citations are links only for http(s) URLs.
- **Activity (`/activity`):** audit rows with the full status vocabulary as icon plus word (failed and denied differ), chain status (verified, broken at row id, verifying, unknown), the server total, "Load more" paging, and a block inspector. Search covers loaded rows only.
- **Artifacts Manager (`/deliverables`):** Verified file download repository with SHA-256 verification badges.
- **Settings (`/settings`):** sections Account and session (user id, workspace id, role, server reachability, sign out), Chat and voice (two switches saved at once with a Saved or error state and rollback: Task detection and Memory in chat and voice), Appearance (points to the title-bar theme toggle), About and data (what leaves this machine, links, a planned note). Only real values are shown; if the account cannot be loaded it shows an error with retry.
- **Files (connected folder and attachments):** `lib/files-client.ts` talks to `/api/v1/files`; `lib/desktop-bridge.ts` reads `window.maya` (desktop only). Settings > Files (`settings/FilesSection`) shows the working folder and the uploaded files. The composer (`ChatInput`, state in `hooks/useAttachments`, drop handling in `hooks/useFileDrop`) uploads each file on select and `useChatStream` sends the ready ids as `attachments`; `components/files/FileParts` holds the shared chip and icon, `TaskOutputLocation` shows where a task saved its result. States: no folder, connected, folder missing, not writable, uploading, upload error per file, limit reached.
- **Tasks from chat:** the `task.created` SSE event (see `docs/api/CHAT.md`) is decoded in `lib/api-client.ts`, passed through `useChatStream({ onTaskCreated })` and added by `ConversationRuntime` as the same inline task card used by "Run as task" (deduped by task id). On reload, `collectTaskRefs` reads `metadata.task` from stored messages and the runtime fetches each task (max 10, failures ignored). Settings has a "Task detection" switch backed by `lib/settings-client.ts` (`/api/v1/settings/chat`).

---

## 3. Verification & Build Quality

- **TypeScript Typecheck:** `tsc --noEmit` passed with 0 errors.
- **Linters & Style:** ESLint and Prettier passed with 0 warnings/errors.
- **Test Suite:** 1,074 Vitest tests passing across 49 test suites (100% pass rate).
- **Production Build:** Next.js 15.5 compiled and generated 25/25 static routes with 0 errors.

### Artifacts page and opening files (updated 2026-11-10)

- **`/artifacts` (`ArtifactsManagerView.tsx`)** renders no report and no preview. Each row shows name, type, size and date, with: **Open** (the PC's own app, desktop app only), **Ask AI** (`artifactToFile` fetches the file with the bearer token and `useAttachments.addFiles` attaches it, then the app goes to `/` so the user says what to do with it), **Download** and **Delete** (a confirm dialog, then `DELETE /api/v1/artifacts/{id}`). A file the API reports as `in_connected_folder` has no Delete (MAYA never deletes a file on the user's disk) and shows "Stays on your disk".
- **Open in a browser:** with no `window.maya.openArtifact` (`getArtifactOpener` in `lib/desktop-bridge.ts`), Open is replaced by a Download labelled "Download to open" and a note says opening needs the desktop app.
- **Open in the desktop app:** `openArtifactNatively` sends `{ artifactId, fileName, token }` (never a path) over `window.maya.openArtifact`; the main process downloads it to a MAYA temp folder and opens it. See `apps/desktop/README.md`. Executable types are refused and the page shows "not opened from MAYA for safety, download it instead".
- **Old URLs:** `/workspace` redirects to `/` and `/deliverables` to `/artifacts` (`LegacyRedirect`).
