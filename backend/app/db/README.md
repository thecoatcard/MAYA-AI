# Database infrastructure

The async SQLAlchemy engine uses asyncpg, pre-ping, a pool of 20 connections and at most 10 overflow connections.
Connection acquisition and connection attempts use the configured probe timeout. SQL parameter values are hidden from
engine error rendering.

The runtime uses the non-owner `maya_app` login, never the Compose bootstrap administrator. `tenant.py` opens
transactions with the transaction-local `app.workspace_id` that row-level security reads, and checks at startup that the
connected role is not privileged and owns nothing. Schema changes are Alembic revisions in `migrations/`, run with the
separate migration role (`MAYA_MIGRATION_DATABASE_URL`), which the running API never uses.

See the [data model, roles and migration commands](../../../docs/architecture/DATA_MODEL.md) and
[ADR-0006](../../../docs/adr/0006-tenant-isolation-with-row-level-security.md).
