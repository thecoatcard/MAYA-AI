# Phase 14 — Frontend Product Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete, integrate, test, and certify Phase 14 (Frontend Product Experience) to production standards, integrating all surfaces defined in `MAYA_AI_FINAL_PLAN.md` §16: Ambient Workspace (VoiceOrb, Chat, DeliverableCanvas), Task Center & Live Work Drawer (DAG visualizer, live SSE progress), Approval Center (exact-payload diff cards, TOTP step-up), Browser Takeover Modal (human-in-the-loop CAPTCHA handoff), Memory & Settings, and full production Next.js build.

**Architecture:** A cohesive Next.js 15 client following Direction A ("Quiet Instrument" with subtle frosted glass and elevation tokens):
1. Ambient Workspace: interactive VoiceOrb synchronized with WebAudio states (`idle`, `listening`, `thinking`, `speaking`, `interrupted`), chat thread with Markdown, and DeliverableCanvas;
2. Task Center & DAG: step-by-step progress, interactive DAG visualizer, pause/abort controls, verification proof modal;
3. Approval Center: exact-payload cards (email preview, git diff, Notion update) with RFC 8785 hashes and single-use token consumption;
4. Browser Takeover Modal: human-in-the-loop CAPTCHA/bot challenge resolution interface;
5. All app routes (`/`, `/chat`, `/tasks`, `/approvals`, `/memory`, `/integrations`, `/agents`, `/settings`, `/activity`, `/deliverables`) verified against real API endpoints and mocks.

**Tech Stack:** Next.js 15, React 19, TypeScript 5, Tailwind CSS, Lucide icons, Vitest, Testing Library.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§16, §11.2, §11.4, Decisions D4, D5, D12), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_14_FRONTEND_EXPERIENCE.md`.

## Global Constraints

- **Design Direction A:** "Quiet Instrument" — elevation tokens, hairline borders, frosted glass on floating headers/modals only, flat cards, CSS-only purposeful motion, prefers-reduced-motion fallbacks.
- **Decision D5 (Visual UI Approval Only):** Approvals require visual confirmation on exact payload cards; voice channel cannot approve.
- **Anti-Bot Policy (Decision D14 & System Rule):** Zero automated CAPTCHA bypass; challenge requires interactive human takeover modal.
- **Zero Regressions:** All existing frontend Vitest tests and Next.js production build must pass.

---

### Task 1: Browser Takeover Modal for Human CAPTCHA Resolution

**Files:**
- Create: `(planned) apps/web/src/components/browser/BrowserTakeoverModal.tsx`
- Create: `(planned) apps/web/src/components/browser/BrowserTakeoverModal.test.tsx`

**Interfaces:**
- `BrowserTakeoverModal`: displays challenge details (`bot_type`, `screenshot_base64`, `current_url`), explains that automated solving is prohibited by policy, provides manual completion instructions, and "I have solved the challenge / Resume" button calling `POST /api/v1/browser/session/{session_id}/resume`.

- [ ] **Step 1: Write Vitest tests for `BrowserTakeoverModal` rendering challenge details, instructions, and resume trigger**
- [ ] **Step 2: Implement `BrowserTakeoverModal.tsx` in `apps/web/src/components/browser/`**
- [ ] **Step 3: Run Vitest tests to verify 100% pass rate**

---

### Task 2: Integrate Browser Takeover into Task Center and Approval Flow

**Files:**
- Modify: `apps/web/src/components/tasks/TaskCenterDrawer.tsx`
- Modify: `apps/web/src/components/approvals/ApprovalCenterModal.tsx`

- [ ] **Step 1: Wire `human_browser_takeover` action type into TaskCenterDrawer and ApprovalCenterModal to open BrowserTakeoverModal**
- [ ] **Step 2: Run Vitest suite to verify components pass**

---

### Task 3: Production Build & Asset Verification

**Commands:**
- `npm --prefix apps/web run build`
- `npm --prefix apps/web run lint`
- `npm --prefix apps/web run typecheck`

- [ ] **Step 1: Execute `npm --prefix apps/web run typecheck` (`tsc --noEmit`)**
- [ ] **Step 2: Execute `npm --prefix apps/web run lint`**
- [ ] **Step 3: Execute `npm --prefix apps/web run build` and assert 0 build errors**

---

### Task 4: Documentation & Status Updates

**Files:**
- Create: `(planned) docs/architecture/FRONTEND_ARCHITECTURE.md`
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Author `(planned) docs/architecture/FRONTEND_ARCHITECTURE.md` documenting the three surfaces, Direction A design system, and WebAudio integration**
- [ ] **Step 2: Update `docs/implementation/IMPLEMENTATION_STATUS.md` (mark Phase 14 as Implemented & Verified locally)**
- [ ] **Step 3: Update `CHANGELOG.md`**
- [ ] **Step 4: Run `npm run docs:check`**
