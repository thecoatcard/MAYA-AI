# ADR-0004: Local API, web and data-service foundation

- **Status:** Proposed
- **Date:** 2026-11-05
- **Deciders:** Repository owner
- **Related:** [Phase 01](../implementation/IMPLEMENTATION_PHASES/PHASE_01_FOUNDATION.md)

## Context

The owner approved scaffolding, dependency installation and local infrastructure.
The master-plan P1 spans detailed Phases 01-04; infrastructure alone cannot deliver
the full milestone. ADR-0003 is reserved by the Phase 01 queue-spike plan.

## Decision

Implement a Python 3.12/FastAPI application factory with Pydantic Settings,
SQLAlchemy 2.0/asyncpg, Redis async clients, structured redacted logs and health probes.
Use Next.js 15/React 19, strict TypeScript and Tailwind for a static, honest web shell.
Pin direct dependencies and lock transitive versions with uv and npm.

Run digest-pinned PostgreSQL 17/pgvector and Redis 7.4 AOF locally in Compose,
publishing only loopback ports. Separate the bootstrap administrator from the
runtime login. Load dotenv files only when explicitly selected.

This record remains Proposed for human architectural review; implementation approval
does not record acceptance of unrelated product decisions or earlier ADRs.

## Alternatives considered

- **Next.js latest:** Not selected; the approved phase calls for major version 15.
- **Provider and queue libraries in the scaffold:** Deferred until their consumer
  or spike is implemented. Registry metadata confirms the current Gemini SDK requires
  Pydantic at least 2.12.5; pinned Pydantic 2.14.2 satisfies that floor. The future
  Postgres checkpointer requires psycopg 3; asyncpg is not a replacement for it.
- **Object storage now:** Deferred by owner decision; there is no consumer yet.

## Consequences

- Positive: Reproducible, independently testable local infrastructure with no model calls.
- Negative: Two dependency managers and local service prerequisites; no product workflow yet.
- Mitigations: Declared scripts match stack checks; deterministic tests use mocks and
  local smoke checks are opt-in. Budget and auth decisions remain outside this change.

## Security and compliance impact

No real credentials are stored in source. The root example contains local-only values.
Readiness returns generic statuses, runtime connection configuration is secret-typed,
and application logs omit raw exception details. Redis is unauthenticated and is not
suitable for non-local use. Authentication, RLS and approval gates are not implemented.

Next.js 15 pins an affected PostCSS version, so the web package overrides PostCSS to
8.5.32 within the same major and validates the build. Remaining development-tool
advisories must be reported rather than silently forcing incompatible major versions.

## Implementation notes

See the [backend](../../backend/README.md), [web](../../apps/web/README.md),
[runbook](../operations/LOCAL_DEVELOPMENT.md) and [tests](../testing/FOUNDATION.md).

## Review trigger

Revisit when adding tenant data, model/queue consumers, network-accessible deployment,
or a supported Next.js major upgrade.
