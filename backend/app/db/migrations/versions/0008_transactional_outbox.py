"""Phase 06: Transactional outbox table for durable task events.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: None = None
depends_on: None = None

WORKSPACE_SETTING = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_outbox_events (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          task_id UUID NOT NULL,
          event_type VARCHAR(100) NOT NULL,
          payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          status VARCHAR(30) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'dispatched', 'failed')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          dispatched_at TIMESTAMPTZ,
          CONSTRAINT task_outbox_events_task_fk FOREIGN KEY (workspace_id, task_id)
            REFERENCES tasks (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_task_outbox_events_pending
          ON task_outbox_events (status, created_at)
          WHERE status = 'pending'
        """
    )
    op.execute(
        """
        CREATE INDEX idx_task_outbox_events_task
          ON task_outbox_events (workspace_id, task_id)
        """
    )
    op.execute("ALTER TABLE task_outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_outbox_events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY ws_isolation
          ON task_outbox_events
          USING (workspace_id = {WORKSPACE_SETTING})
          WITH CHECK (workspace_id = {WORKSPACE_SETTING})
        """
    )
    op.execute("GRANT SELECT, INSERT ON task_outbox_events TO maya_app")
    op.execute("GRANT UPDATE (status, dispatched_at) ON task_outbox_events TO maya_app")


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON task_outbox_events FROM maya_app")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON task_outbox_events")
    op.execute("ALTER TABLE task_outbox_events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_outbox_events DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS task_outbox_events CASCADE")
