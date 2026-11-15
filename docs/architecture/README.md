# Architecture documentation

| Document                                                     | Describes                                                                         | Status                                                              |
| :----------------------------------------------------------- | :-------------------------------------------------------------------------------- | :------------------------------------------------------------------ |
| [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md)                     | Implemented components and the planned MAYA AI system                             | Partially implemented locally                                       |
| [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md)           | What is in the repository today and where new code goes                           | Current                                                             |
| [MODEL_GATEWAY.md](MODEL_GATEWAY.md)                         | Gemini model gateway: aliases, resilience, metering, budgets                      | Implemented locally (Phase 03); never run against the real provider |
| [UI_UX_STANDARDS.md](UI_UX_STANDARDS.md)                     | Look, feel and motion standard and review gate for every web UI                   | Proposed (owner accepts with the first design brief)                |
| [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md)                   | Console design system: tokens, two themes, fonts, primitives, motion kit, contrast proof | Implemented (foundation WP1a); shell and pages pending |
| [UI_PAGE_STATE_MATRIX.md](UI_PAGE_STATE_MATRIX.md)           | Audit of every web page: states, overflow findings, measured results (2026-11-09) | Audit evidence                                                      |
| [UI_REDESIGN_WORK_PACKAGES.md](UI_REDESIGN_WORK_PACKAGES.md) | Page briefs and six work packages for the desktop UI redesign                     | Planning input                                                      |
| [DOCUMENT_GENERATION.md](DOCUMENT_GENERATION.md)             | Themed PDF, PPTX and DOCX generation from a structured model                      | Implemented locally; tested with a real Edge; not run with Gemini   |
| [AGENT_REQUEST_FLOW.md](AGENT_REQUEST_FLOW.md)               | Chat message to verified result for each specialist agent                         | Implemented locally; tested offline; not run against real services  |

Architectural decisions are recorded as ADRs in [docs/adr](../adr/README.md). The full product plan is [MAYA_AI_FINAL_PLAN.md](../../MAYA_AI_FINAL_PLAN.md).

When architecture changes, update these documents and any diagram in the same change, and add an ADR (`governance/CHANGE_MANAGEMENT.md`).
