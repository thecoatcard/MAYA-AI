# Database infrastructure

The async SQLAlchemy engine uses asyncpg, pre-ping, a pool of 20 connections and at
most 10 overflow connections. Connection acquisition and connection attempts use
the configured probe timeout. SQL parameter values are hidden from engine error rendering.

The runtime uses a non-owner `maya_app` login, never the Compose bootstrap administrator.
Migration configuration is separate and is not used by the running API. The database
has no application tables, migrations or tenant isolation yet; those belong to Phase 02.

See [current data-store state](../../../docs/architecture/DATA_MODEL.md).
