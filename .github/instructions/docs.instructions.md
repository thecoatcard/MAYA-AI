---
applyTo: "docs/**,governance/**,**/*.md"
---

Documentation rules for this repository. The canonical instructions are in `AGENTS.md`.

- Follow `governance/DOCUMENTATION_POLICY.md`: documentation describes implemented behavior; mark anything unbuilt as planned.
- Verify every statement against the code or by running the command. Do not write from the plan alone.
- Use the templates in `templates/`. Use safe placeholder values and never real secrets.
- Run `npm run docs:check` and fix every finding.
- `AGENTS.md` and `governance/**` are guardrail files: changing them needs explicit human approval.
