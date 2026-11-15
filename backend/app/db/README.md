# Database infrastructure

The async SQLAlchemy engine uses asyncpg, pre-ping, a pool of 20 connections and at most 10 overflow connections.
Connection acquisition and connection attempts use the configured probe timeout. SQL parameter values are hidden from
engine error rendering.

The runtime uses the non-owner `maya_app` login, never the Compose bootstrap administrator. `tenant.py` opens
transactions with the transaction-local `app.workspace_id` that row-level security reads, and checks at startup that the
connected role is not privileged and owns nothing. Schema changes are Alembic revisions in `migrations/`, run with the
separate migration role (`MAYA_MIGRATION_DATABASE_URL`), which the running API never uses.

LangGraph checkpoints use a separate Psycopg connection bound to one server-authenticated workspace and user. Revision
`0006` provisions the checkpointer tables and applies forced RLS using both owner settings; the runtime checkpointer
does not call `setup()` or create schema. Rows already present without an owner stay NULL and inaccessible rather than
being assigned an inferred identity.

`repositories/` holds data access built on a tenant transaction. `chat.py` reads and writes `sessions` and
`messages` for one workspace and one user: literal SQL with bound parameters only, nothing commits (the caller owns
the transaction), and a session of another user is indistinguishable from a missing one.

Chat idempotency claims and terminal states are stored in `chat_idempotency` by revision `0007`. The repository
uses the scoped key digest, forced RLS on workspace and user settings, and explicit workspace/user predicates; it never
stores the raw idempotency key or request content. Claim insertion is unique, updates use an owned in-progress
compare-and-set, and terminal result updates share
a transaction with the assistant message. These rows do not expire. Redis idempotency entries are only a cache, so
Redis expiry or loss cannot make a committed database claim available again. A process stopped before finalization
leaves the key in progress and causes repeats to return `request_in_progress`.

See the [data model, roles and migration commands](../../../docs/architecture/DATA_MODEL.md) and
[ADR-0006](../../../docs/adr/0006-tenant-isolation-with-row-level-security.md).
