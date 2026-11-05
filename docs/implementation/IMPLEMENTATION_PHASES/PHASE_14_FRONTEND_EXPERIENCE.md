# Phase 14 — Frontend Product Experience

## A. Phase Overview

- **Phase Number:** Phase 14
- **Phase Name:** Frontend Product Experience
- **Objective:** Build the complete Next.js 15 user experience for MAYA AI, combining the three core surfaces defined in the master plan: the Ambient Workspace (voice orb, conversation stream, and side-by-side Deliverable Canvas), the Task Center & Live Work Drawer (DAG visualizer, step-by-step progress, evidence inspector), and the Approval Center (exact-payload diff cards, step-up auth modals).
- **Why This Phase is Necessary:** As detailed in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §16, users interact with a digital employee not merely through text, but through ambient multimodal awareness, live task transparency, and verifiable deliverable previews. A fragmented UI degrades user trust; unifying voice, chat, task progress, and cryptographic approvals into a cohesive canvas is essential for daily enterprise adoption.
- **Expected Outcome:** A Next.js 15 web client featuring:
  - Ambient Workspace with interactive Voice Waveform Orb and synchronized audio states.
  - Side-by-side Deliverable Canvas previewing spreadsheets, PDFs, diffs, and documents.
  - Task Center & Live Work drawer rendering interactive DAGs and live SSE progress.
  - Approval Center rendering exact payload diffs with Approve/Reject/Edit controls and TOTP step-up modal.
  - Responsive layouts, dark/light themes, and accessible design.
- **Priority:** High
- **Estimated Complexity:** High
- **Estimated Duration:** 12 business days.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 04 (Chat and Conversation System), Phase 06 (Background Task Execution Engine), Phase 07 (Security, Permissions, and Human Approval), Phase 10 (File Processing), and Phase 12 (Real-Time Voice System).
- **Required Infrastructure:** Node.js 22+; running FastAPI backend exposing REST, WebSocket, and SSE endpoints.
- **Required Credentials or External Services:** None.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §16 (Three Core Surfaces) and §11.2 (Exact-payload review cards).
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md).

---

## C. Scope

### Included Features

- Ambient Workspace Surface:
  - Voice Waveform Orb component animating across four states: `listening`, `thinking`, `speaking`, `interrupted`.
  - Chat thread synchronized with live audio transcripts.
  - Deliverable Canvas panel placed alongside the chat feed:
    - Interactive spreadsheet viewer for XLSX deliverables.
    - PDF inline viewer with page zoom and text selection.
    - Unified Git diff viewer with syntax highlighting.
    - Markdown previewer for reports and agendas.
- Task Center & Live Work Drawer:
  - Plain-language progress indicators ("Step 2 of 4 — running sandbox tests").
  - Interactive DAG visualizer (React Flow or SVG) showing node execution states, dependencies, and timing.
  - Pause, Abort, and Resume task controls.
  - Deterministic verification proof inspector (viewing exit codes, hashes, structural reports).
- Approval Center Surface:
  - Exact-payload review cards:
    - Email preview (recipient, subject, rich-text body).
    - Unified Git diff for proposed code changes.
    - Notion database property updates.
  - Action buttons: _Approve & Execute_, _Reject_, _Edit & Execute_.
  - Step-up modal prompting for TOTP code on RESTRICTED tier actions.
- Memory & Settings Center:
  - Interactive memory browser displaying episodic and knowledge items with raw similarity and decay scores.
  - Spend limits, model alias routing preferences, and PII retention settings.
- UI Resilience:
  - Skeleton loaders for async operations.
  - Error boundaries preventing whole-app crashes.
  - Responsive design adapting gracefully from desktop canvas to tablet/mobile viewports.

### Excluded Features

- Full desktop packaging with Tauri (deferred to post-MVP).
- Custom generative UI mini-app execution (post-MVP).

### Deferred Features

- Multi-user real-time collaborative cursor sharing on the deliverable canvas.

### Dependencies on Future Phases

- Phase 15 runs automated end-to-end Playwright tests against the frontend surfaces built here.

---

## D. Implementation Breakdown

### Task P14-T001: Ambient Workspace and Voice Waveform Orb

- **Task ID:** P14-T001
- **Task Name:** Build Ambient Voice Orb and Workspace Shell
- **Detailed Description:** Implement `VoiceOrb.tsx` in `apps/web/src/components/voice/`. Render an animated canvas/WebGL or SVG orb with smooth ripple physics reacting to audio volume frequencies from `AudioWorklet`. Support states:
  - `idle`: gentle pulsation.
  - `listening`: dynamic wave reacting to mic volume.
  - `thinking`: spinning glow.
  - `speaking`: oscillating speech waveform.
  - `interrupted`: immediate contraction and reset.
  - Embed orb in the workspace header alongside chat and canvas split-views.
- **Expected Files:** apps/web/src/components/voice/VoiceOrb.tsx (planned artifact), apps/web/src/components/workspace/AmbientWorkspace.tsx (planned artifact).
- **Dependencies:** Phase 12.
- **Assigned Agent Role:** Senior Frontend / Creative Developer.
- **Required Tests:** Playwright component snapshot tests in apps/web/tests/e2e/voice_orb.spec.ts (planned artifact).
- **Documentation Updates:** docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Orb animates smoothly at 60 FPS; transitions between states without visual artifacts; audio volume modulates particle radius.

### Task P14-T002: Deliverable Canvas Surface

- **Task ID:** P14-T002
- **Task Name:** Build Side-by-Side Deliverable Canvas
- **Detailed Description:** Implement `DeliverableCanvas.tsx` in `apps/web/src/components/canvas/`. Positioned adjacent to the chat thread on desktop viewports. When a task produces an artifact or a message references a file:
  - Spreadsheets: render interactive table with sticky headers using `@tanstack/react-table`.
  - PDFs: render using `pdfjs-dist` with page navigation.
  - Code Diffs: render side-by-side and unified git diffs with syntax coloring.
  - Provide download button calling `GET /api/v1/artifacts/{id}`.
- **Expected Files:** apps/web/src/components/canvas/DeliverableCanvas.tsx (planned artifact), apps/web/src/components/canvas/SpreadsheetViewer.tsx (planned artifact), apps/web/src/components/canvas/PdfViewer.tsx (planned artifact), apps/web/src/components/canvas/DiffViewer.tsx (planned artifact).
- **Dependencies:** Phase 10.
- **Assigned Agent Role:** Frontend Systems Engineer.
- **Required Tests:** `npm --prefix apps/web test`.
- **Documentation Updates:** docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Deliverables render alongside chat without page reloads; spreadsheet allows column resizing; PDF pages render clearly; download button downloads verified file.

### Task P14-T003: Task Center and Interactive DAG Visualizer

- **Task ID:** P14-T003
- **Task Name:** Build Task Center Drawer and DAG Graph Visualizer
- **Detailed Description:** Implement `TaskCenter.tsx` and `DagGraph.tsx` in `apps/web/src/components/tasks/`. Ingest live SSE task events from `GET /api/v1/tasks/{id}/events`. Render DAG nodes with color-coded statuses (`pending`: gray, `in_progress`: blue pulse, `awaiting_approval`: amber, `completed`: green, `failed`: red). Clicking a node opens an inspection panel showing inputs, outputs, execution logs, and deterministic verification proofs. Include a prominent `Abort Task` button.
- **Expected Files:** apps/web/src/components/tasks/TaskCenter.tsx (planned artifact), apps/web/src/components/tasks/DagGraph.tsx (planned artifact), apps/web/src/components/tasks/TaskEvidenceDrawer.tsx (planned artifact).
- **Dependencies:** Phase 06.
- **Assigned Agent Role:** Frontend Data Visualization Engineer.
- **Required Tests:** Playwright component test asserting DAG node status color updates on SSE events.
- **Documentation Updates:** docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** DAG reflects plan topology accurately; status transitions animate in real-time as SSE events arrive; abort button sends abort request to backend.

### Task P14-T004: Approval Center and Step-Up Modal

- **Task ID:** P14-T004
- **Task Name:** Build Approval Review Cards and Step-Up TOTP Modal
- **Detailed Description:** Implement `ApprovalCenter.tsx` in `apps/web/src/components/approvals/`. Fetch pending requests from `GET /api/v1/approvals`. Render exact payload cards:
  - Email card: sender, recipient, subject, and formatted body.
  - Git card: repository, branch name, and unified diff.
  - Notion card: updated database fields with before/after comparison.
  - Render action buttons: _Approve & Execute_, _Reject_, _Edit & Execute_.
  - If request is RESTRICTED, display modal requiring 6-digit TOTP code before dispatching approval.
- **Expected Files:** apps/web/src/components/approvals/ApprovalCenter.tsx (planned artifact), apps/web/src/components/approvals/ApprovalCard.tsx (planned artifact), apps/web/src/components/approvals/StepUpModal.tsx (planned artifact).
- **Dependencies:** Phase 07.
- **Assigned Agent Role:** Security & Frontend Engineer.
- **Required Tests:** Playwright end-to-end test simulating approval grant with TOTP code.
- **Documentation Updates:** docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Renders exact payloads; approval calls `POST /api/v1/approvals/{id}`; RESTRICTED blocks until TOTP code is entered; rejection initiates compensation rollback.

### Task P14-T005: Memory Browser and Settings Dashboard

- **Task ID:** P14-T005
- **Task Name:** Build Memory Management and User Settings Screens
- **Detailed Description:** Implement `MemoryManager.tsx` and `SettingsDashboard.tsx` in `apps/web/src/components/settings/`. Allow users to browse semantic memories, view raw similarity scores and decay ages, delete individual memories, and execute full GDPR purge with confirmation modal. Implement settings panel for monthly spend caps, default model aliases, and PII scrubbing preferences.
- **Expected Files:** apps/web/src/components/settings/MemoryManager.tsx (planned artifact), apps/web/src/components/settings/SettingsDashboard.tsx (planned artifact), apps/web/src/app/settings/page.tsx (planned artifact).
- **Dependencies:** Phase 11.
- **Assigned Agent Role:** Frontend Product Developer.
- **Required Tests:** Playwright component test verifying memory search and item deletion.
- **Documentation Updates:** docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Users can browse, inspect, and delete memory records; GDPR purge requires typing confirmation phrase; settings persist to backend profile.

---

## E. File and Folder Changes

- **Files to Create:**
  - apps/web/src/components/voice/VoiceOrb.tsx (planned artifact)
  - apps/web/src/components/workspace/AmbientWorkspace.tsx (planned artifact)
  - apps/web/src/components/canvas/DeliverableCanvas.tsx (planned artifact)
  - apps/web/src/components/canvas/SpreadsheetViewer.tsx (planned artifact)
  - apps/web/src/components/canvas/PdfViewer.tsx (planned artifact)
  - apps/web/src/components/canvas/DiffViewer.tsx (planned artifact)
  - apps/web/src/components/tasks/TaskCenter.tsx (planned artifact)
  - apps/web/src/components/tasks/DagGraph.tsx (planned artifact)
  - apps/web/src/components/tasks/TaskEvidenceDrawer.tsx (planned artifact)
  - apps/web/src/components/approvals/ApprovalCenter.tsx (planned artifact)
  - apps/web/src/components/approvals/ApprovalCard.tsx (planned artifact)
  - apps/web/src/components/approvals/StepUpModal.tsx (planned artifact)
  - apps/web/src/components/settings/MemoryManager.tsx (planned artifact)
  - apps/web/src/components/settings/SettingsDashboard.tsx (planned artifact)
  - apps/web/src/app/settings/page.tsx (planned artifact)
  - apps/web/tests/e2e/voice_orb.spec.ts (planned artifact)
  - apps/web/tests/e2e/approval_flow.spec.ts (planned artifact)
  - docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact)
- **Files to Modify:**
  - `apps/web/src/app/page.tsx`
  - `apps/web/src/app/layout.tsx`
  - apps/web/src/lib/api-client.ts (planned artifact)
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `apps/web/src/components/`, `apps/web/tests/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. Frontend Client Session State Contract

```typescript
interface ClientAppState {
  currentSessionId: string | null;
  activeTaskId: string | null;
  voiceState: "idle" | "listening" | "thinking" | "speaking" | "interrupted";
  canvasDeliverable: {
    type: "xlsx" | "pdf" | "docx" | "diff" | "markdown";
    title: str;
    artifactUrl: str;
    verificationStatus: "pending" | "verified" | "failed";
  } | null;
  pendingApprovalsCount: number;
}
```

---

## G. Security Requirements

- **XSS & Content Injection Prevention:** All Markdown and HTML previews use strict DOMPurify sanitization.
- **Review Card Integrity:** The Approval Center must display the raw canonical payload returned by the API; it must never render an LLM-summarized version of the action.
- **Session Protection:** Auth tokens stored in secure, `HttpOnly`, `SameSite=Strict` cookies or transient memory; refresh tokens rotated on each issuance.

---

## H. Testing Strategy

- **Playwright End-to-End Tests:** Automate browser scenarios:
  - Submit task -> verify DAG nodes render -> verify step updates on SSE.
  - Trigger approval -> review card appears -> submit TOTP -> card transitions to approved.
  - Generate deliverable -> verify canvas preview loads and download succeeds.
- **Visual Regression Tests:** Snapshot tests of Voice Orb and Deliverable Canvas across desktop and mobile viewports.
- **Accessibility Tests:** Run axe-core accessibility audits on all views; assert WCAG 2.1 AA conformity.

---

## I. Definition of Done

1. Ambient Workspace renders Voice Orb and synchronizes with audio states.
2. Deliverable Canvas renders XLSX, PDF, and diffs cleanly next to chat.
3. Task Center visualizes DAG progress in real-time from SSE events.
4. Approval Center displays exact payload diffs and enforces TOTP for RESTRICTED operations.
5. All Playwright UI tests pass with zero flakiness.
6. Documentation in docs/architecture/FRONTEND_ARCHITECTURE.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `npm --prefix apps/web test` and Playwright test suite pass cleanly; end-to-end demonstration shows seamless interaction across Voice Orb, Chat, Canvas, and Approvals.
- **Dependent Phase Unlocked:** Phase 15 (Testing, Security Hardening, and Evaluation) is cleared to begin.
