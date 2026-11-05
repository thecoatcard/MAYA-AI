# Architecture documentation

| Document | Describes | Status |
| :--- | :--- | :--- |
| [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md) | The planned MAYA AI system | Planned; nothing is implemented |
| [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md) | What is in the repository today and where new code goes | Current |
| [MODEL_GATEWAY.md](MODEL_GATEWAY.md) | Gemini model gateway: aliases, resilience, metering, budgets | Implemented locally (Phase 03); never run against the real provider |

Architectural decisions are recorded as ADRs in [docs/adr](../adr/README.md). The full product plan is [MAYA_AI_FINAL_PLAN.md](../../MAYA_AI_FINAL_PLAN.md).

When architecture changes, update these documents and any diagram in the same change, and add an ADR (`governance/CHANGE_MANAGEMENT.md`).
