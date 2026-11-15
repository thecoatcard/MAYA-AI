"""Bind LangGraph checkpoint rows to an authenticated workspace and user.

Existing checkpoint data has no owner metadata. This migration retains such rows with NULL
owner columns; forced RLS makes them invisible rather than guessing an owner or exposing them.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: None = None
depends_on: None = None

TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

CREATE_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
      thread_id TEXT NOT NULL,
      checkpoint_ns TEXT NOT NULL DEFAULT '',
      checkpoint_id TEXT NOT NULL,
      parent_checkpoint_id TEXT,
      type TEXT,
      checkpoint JSONB NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}',
      PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_blobs (
      thread_id TEXT NOT NULL,
      checkpoint_ns TEXT NOT NULL DEFAULT '',
      channel TEXT NOT NULL,
      version TEXT NOT NULL,
      type TEXT NOT NULL,
      blob BYTEA,
      PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_writes (
      thread_id TEXT NOT NULL,
      checkpoint_ns TEXT NOT NULL DEFAULT '',
      checkpoint_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      idx INTEGER NOT NULL,
      channel TEXT NOT NULL,
      type TEXT,
      blob BYTEA NOT NULL,
      task_path TEXT NOT NULL DEFAULT '',
      PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    )
    """,
)


def _owner_columns(table: str) -> None:
    # Keep pre-existing, ownerless rows NULL. Defaults apply only to future inserts and are
    # supplied by the dedicated checkpointer connection after verified server-side auth.
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS workspace_id UUID")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN workspace_id SET DEFAULT "
        "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN user_id SET DEFAULT "
        "NULLIF(current_setting('app.user_id', true), '')::uuid"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = '{table}_owner_fk'
              AND conrelid = '{table}'::regclass
          ) THEN
            ALTER TABLE {table}
              ADD CONSTRAINT {table}_owner_fk
              FOREIGN KEY (workspace_id, user_id)
              REFERENCES users (workspace_id, id)
              ON DELETE CASCADE NOT VALID;
          END IF;
        END $$
        """
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY ws_isolation ON {table} "
        "USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid "
        "AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid "
        "AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)"
    )


def upgrade() -> None:
    for statement in CREATE_TABLES:
        op.execute(statement)

    for table in TABLES:
        _owner_columns(table)

    # Compatibility with the installed LangGraph Postgres saver schema (including versions
    # that create task_path in a separate setup migration).
    op.execute(
        "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''"
    )

    for table in TABLES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {table}_thread_id_idx ON {table} (thread_id)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO maya_app")


def downgrade() -> None:
    # Removing forced owner RLS would expose every surviving checkpoint to the old runtime.
    # Allow rollback only before any checkpoint data has been written.
    # Temporarily exempt the migration owner from FORCE RLS so the guard can see every row.
    # A raised exception rolls this DDL back with the Alembic revision.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM checkpoints LIMIT 1)
             OR EXISTS (SELECT 1 FROM checkpoint_blobs LIMIT 1)
             OR EXISTS (SELECT 1 FROM checkpoint_writes LIMIT 1) THEN
            RAISE EXCEPTION
              'Revision 0006 cannot be downgraded after checkpoint data exists; preserve owner RLS';
          END IF;
        END $$
        """
    )
    for table in TABLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON {table} FROM maya_app")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_owner_fk")
        op.execute(f"DROP POLICY ws_isolation ON {table}")
        op.execute(f"DROP TABLE {table}")
