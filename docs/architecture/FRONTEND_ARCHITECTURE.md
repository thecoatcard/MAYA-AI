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

## 2. Core Surfaces Inventory

### 2.1 Ambient Workspace (`/workspace`, `/chat`)
- **Voice Waveform Orb (`VoiceOrb.tsx`):**
  - Smooth particle and wave animation reacting to live WebAudio microphone frequencies.
  - State machine transitions: `idle`, `listening`, `thinking`, `speaking`, `interrupted`.
- **Synchronized Message Thread (`MessageThread.tsx`):**
  - Live SSE message streaming, automatic scroll anchoring, syntax-highlighted code blocks, and structured notice banners.
- **Deliverable Canvas (`DeliverableCanvas.tsx`):**
  - Side-by-side interactive deliverable preview pane for XLSX spreadsheets, PDF documents, DOCX contracts, and unified Git diffs.

### 2.2 Task Center & Live Work Drawer (`/tasks`)
- **Task Ledger View (`TaskCenterView.tsx`, `TaskCenterDrawer.tsx`):**
  - Real-time SSE task event listener (`useTaskEvents.ts`).
  - Plain-language progress indicators ("Step 2 of 4 — running sandbox tests").
  - Deterministic verification proof modal inspecting exit codes, SHA-256 hashes, and structural reports.
- **Interactive DAG Visualizer (`DagVisualizer.tsx`):**
  - Graph visualization rendering nodes, dependency edges, execution states (`pending`, `in_progress`, `awaiting_approval`, `completed`, `failed`), and timing.

### 2.3 Approval Center (`/approvals`)
- **Approval Cards & Modal (`ApprovalCenterView.tsx`, `ApprovalCenterModal.tsx`):**
  - Exact-payload review cards for outgoing emails (`EmailApprovalCard.tsx`), Git diffs (`GitDiffViewer.tsx`), and MCP mutations.
  - Display of RFC 8785 canonical SHA-256 digest and single-use JWT execution tokens.
  - Step-up TOTP modal for `RESTRICTED` tier operations (RFC 6238).

### 2.4 Browser Takeover Modal (`BrowserTakeoverModal.tsx`)
- **Human-in-the-Loop Bot Challenge Resolution:**
  - Invoked when Phase 13 `AntiBotDetector` flags Cloudflare Turnstile, reCAPTCHA, or hCaptcha challenges.
  - Strict compliance with anti-bypass security policy: MAYA AI never attempts programmatic CAPTCHA solving.
  - Provides viewport screenshot, session resumption token, and resume trigger (`POST /api/v1/browser/session/{id}/resume`).

### 2.5 Operational Surfaces
- **MCP Integration Gateway (`/integrations`):** Real-time server health status pills, tool counts, and connection ping tester.
- **Agent Fleet Operations (`/agents`):** Specialist agent roster, immutable permission ceilings, and Docker sandbox status.
- **Memory & Knowledge Repository (`/memory`):** Hybrid dense/sparse search interface with Defect #16 decay scores and GDPR Article 17 atomic purge.
- **Audit Ledger (`/activity`):** Tamper-evident hash-chained audit block inspector.
- **Artifacts Manager (`/deliverables`):** Verified file download repository with SHA-256 verification badges.
- **Security & Profile Settings (`/settings`):** Identity, model alias routing, and spend limits.

---

## 3. Verification & Build Quality

- **TypeScript Typecheck:** `tsc --noEmit` passed with 0 errors.
- **Linters & Style:** ESLint and Prettier passed with 0 warnings/errors.
- **Test Suite:** 1,074 Vitest tests passing across 49 test suites (100% pass rate).
- **Production Build:** Next.js 15.5 compiled and generated 25/25 static routes with 0 errors.
