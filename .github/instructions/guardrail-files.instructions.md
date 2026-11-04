---
applyTo: ".github/workflows/**,scripts/hooks/**,scripts/validation/**,scripts/governance/**,scripts/lib/**,.claude/settings.json,package.json,package-lock.json"
---

These files are guardrails or supply-chain inputs. The canonical instructions are in `AGENTS.md`.

- Changing them is Level 3 (APPROVAL_REQUIRED): get explicit human approval first and record it.
- Never weaken a check, skip a hook, loosen workflow permissions, or edit a validator to make a change pass.
- Pin third-party GitHub Actions to full commit SHAs and keep workflow `permissions` minimal.
- Any validator change needs a test, including a case that must fail. Run `npm test` and `npm run validate`.
- Commits that touch these paths carry a `Human-Approval:` trailer (`governance/GIT_WORKFLOW.md`).
