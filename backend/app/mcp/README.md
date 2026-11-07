# MCP Governance & Permission Engine

Tool permission classification, human approval gating, cryptographic token verification, and input sanitization (Phase 07, Decisions D4 & D5).

## Modules

- `permissions.py`: Four-tier permission classification (`READ`, `WRITE`, `APPROVAL_REQUIRED`, `RESTRICTED`) with deny-by-default enforcement. Unregistered tools are rejected with `PermissionDeniedError`. MCP server hints can only elevate permission tiers, never lower them.
- `approvals.py`: Deterministic canonical JSON serialization and SHA-256 payload digest engine (`compute_payload_hash`) binding execution arguments, tool name, workspace ID, user ID, and idempotency key.
- `tokens.py`: Cryptographically signed single-use HS256 JWT minting (`mint_approval_token`, capped at 5 minutes / 300s) and atomic PostgreSQL CAS consumption (`consume_approval_atomically`) preventing replay and TOCTOU race conditions.
- `sanitizer.py`: Multi-layered defense-in-depth sanitization for untrusted external tool content. Strips null bytes, ANSI escape sequences, BiDi overrides, normalizes Unicode NFKC, runs heuristic prompt-injection scanners, and wraps outputs in structural boundary markers (`<<<UNTRUSTED_EXTERNAL_INPUT>>>`).

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).
