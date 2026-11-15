# Local data-service infrastructure

The root Compose configuration runs PostgreSQL 17 with pgvector/pg_trgm and Redis
7.4 with AOF. Image digests pin the verified artifacts. Both services publish
loopback-only ports and use project-scoped named volumes.

The PostgreSQL bootstrap creates extensions and the restricted `maya_app` login
only on the first initialization of an empty volume. It does not create application
tables or implement RLS. The bootstrap administrator must never be used by the API.

The example credentials and unauthenticated Redis service are local development
only, not production configuration. Read the [runbook](../docs/operations/LOCAL_DEVELOPMENT.md)
before starting or stopping services. Never remove a volume to repair an initialization
error without checking its contents and obtaining approval for data deletion.
