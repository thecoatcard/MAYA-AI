# Architecture documentation

| Document                                           | Describes                                                       | Status                                                              |
| :------------------------------------------------- | :-------------------------------------------------------------- | :------------------------------------------------------------------ |
| [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md)           | Implemented components and the planned MAYA AI system           | Partially implemented locally                                       |
| [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md) | What is in the repository today and where new code goes         | Current                                                             |
| [MODEL_GATEWAY.md](MODEL_GATEWAY.md)               | Gemini model gateway: aliases, resilience, metering, budgets    | Implemented locally (Phase 03); never run against the real provider |
| [UI_UX_STANDARDS.md](UI_UX_STANDARDS.md)           | Look, feel and motion standard and review gate for every web UI | Proposed (owner accepts with the first design brief)                |

Architectural decisions are recorded as ADRs in [docs/adr](../adr/README.md). The full product plan is [MAYA_AI_FINAL_PLAN.md](../../MAYA_AI_FINAL_PLAN.md).

When architecture changes, update these documents and any diagram in the same change, and add an ADR (`governance/CHANGE_MANAGEMENT.md`).
