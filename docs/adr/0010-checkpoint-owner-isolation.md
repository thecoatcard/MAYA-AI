# ADR-0010: Bind LangGraph checkpoints to workspace and user owners

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Repository owner
- **Related:** [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [Phase 05 orchestrator](../architecture/ORCHESTRATOR_SPECIFICATION.md), [data model](../architecture/DATA_MODEL.md)

## Context

The LangGraph PostgreSQL saver uses `thread_id` as its storage key and its default tables had no workspace or user
owner columns or RLS. A caller that knew another thread ID could therefore read or mutate its checkpoint through the
shared runtime role. The saver also exposes `setup()`, which creates and migrates tables at runtime, contrary to the
repository's migration-role boundary. Existing checkpoint rows, if any, do not record a trustworthy owner.

## Decision

1. Add nullable `workspace_id` and `user_id` columns to `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes`.
   New rows default from `app.workspace_id` and `app.user_id`, which the server sets from authenticated identities.
2. Add a composite foreign key from each owner pair to `(users.workspace_id, users.id)`, then enable and force RLS on all
   three tables. Both the `USING` and `WITH CHECK` expressions require the row's workspace and user to match the
   connection's settings.
3. Construct each PostgreSQL saver with a dedicated connection whose owner settings are fixed for the saver lifetime.
   The caller must pass server-verified UUIDs; graph state and client-controlled checkpoint data are not identity sources.
4. Provision the tables only through Alembic. Runtime checkpointer construction never calls `setup()`.
5. Do not assign owners to legacy rows. Their NULL owner values remain inaccessible to runtime identities. Recovery, if
   required, needs separately reviewed trustworthy ownership evidence.

## Alternatives considered

- **Use only a workspace-prefixed `thread_id`:** an application convention is weaker than database enforcement and does
  not separate users in one workspace.
- **Backfill existing rows from checkpoint payloads or thread IDs:** neither source is an authenticated owner record, so
  guessing could expose one user's state to another.
- **Call `AsyncPostgresSaver.setup()` at runtime:** requires the application to own schema initialization and grants
  DDL behavior to runtime code; rejected in favor of Alembic.

## Consequences

- Positive: all saver reads and writes pass through forced workspace-and-user RLS, including reads of a known foreign
  thread ID; the checkpointer does not create schema at runtime.
- Negative: a dedicated connection is held for each saver context; ownerless legacy checkpoints are unavailable until
  separately recovered; a user holding `maya_app` SQL execution capability could alter custom settings, so this is not
  protection against arbitrary SQL execution.
- Mitigations: do not expose the connection outside the saver context; provide identity only from the verified
  authentication/session path; keep `maya_app` least-privileged; restore checkpoint access only after a reviewed,
  evidence-based recovery.

## Security and compliance impact

The database now enforces user-level ownership for the three checkpoint persistence tables in addition to workspace
isolation. Foreign-owner rows are invisible and writes carrying a mismatched owner fail RLS or the composite foreign
key. Existing ownerless rows are retained but hidden. Thread IDs should be server-generated and are not an authorization
credential. The graph/checkpointer is not wired into production routes, and no live PostgreSQL cross-tenant
verification was performed for this change.

## Implementation notes

Alembic revision `0006_checkpoint_owner_isolation.py` adds the tables/columns, defaults, grants, indexes and RLS.
`backend/app/agents/checkpointer.py` opens the owner-bound connection and does not run saver setup. Offline tests cover
connection identity setup and migration statements; PostgreSQL integration tests are included for a later configured
run. Stop old checkpoint writers before applying the migration and deploy the owner-bound implementation before
resuming checkpoint writes. Revision `0006` refuses downgrade after checkpoint data exists because removing owner RLS
would expose surviving rows. The migration has not been applied to any database as part of this change.

## Review trigger

Review this decision before wiring the graph to production routes, changing the checkpoint library's table schema or
connection/pooling behavior, adding checkpoint consumers that bypass the owner-bound saver, or recovering legacy rows.
