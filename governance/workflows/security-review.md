---
name: security-review
description: Review a change or area for security weaknesses, including secrets, access control, injection, prompt injection and guardrail bypass.
---

# Workflow: Security Review

## Objective
Find and clearly report security weaknesses with evidence and severity, without exploiting them.

## Inputs
- A diff, branch, module, or a whole-repository scope.

## Preconditions
- Level 1 (READ) plus read-only local scans. Do not contact external systems or open secret files.

## Files to inspect
- The scoped code and its configuration
- `governance/SECURITY_POLICY.md`, `governance/TOOL_PERMISSIONS.md`
- `.claude/settings.json`, hooks in `scripts/hooks/`, workflows in `.github/workflows/`
- Dependency manifests and lockfiles

## Steps
1. Define scope and the assets and trust boundaries involved.
2. Run `npm run security:check` and read the results.
3. Look for: committed secrets or credentials in logs; missing input validation; injection (SQL, command, template); SSRF and unsafe URL handling; broken authentication or authorisation; tenant isolation gaps; unsafe file or temporary-file handling.
4. For agent features: ways untrusted content could trigger an action, bypass of the permission gate, approvals not bound to the exact payload, exposure of the Docker socket or host mounts.
5. For CI and tooling: unpinned third-party actions, over-broad workflow permissions, secrets exposed to pull-request code, hooks or validators that can be silently weakened.
6. For dependencies: new or changed packages, known vulnerabilities, install-time scripts.
7. Rate each finding: Critical, High, Medium, Low, Info; state evidence and the realistic impact.

## Required validation
- State which scans ran and which could not (missing scanner, no project). Do not call an area secure because a tool was absent.

## Documentation requirements
- Record durable findings or decisions in `docs/security/` (create it when first needed) and an ADR if a security boundary changes.

## Approval requirements
- Reporting needs none. Fixes to protected paths or security policy are Level 3.

## Expected outputs
A findings list with severity, location, evidence, impact and remediation; residual risks; and the commands run. Do not include working exploit code or real secrets.
