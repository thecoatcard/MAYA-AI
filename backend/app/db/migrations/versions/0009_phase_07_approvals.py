"""Phase 07: Security permissions and approval requests token enhancements (Decisions D4, D5).

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TABLE approval_requests ALTER COLUMN subtask_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE approval_requests "
        "ADD COLUMN IF NOT EXISTS tool_args JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS approval_token TEXT")
    op.execute(
        """
        ALTER TABLE approval_requests
          ADD COLUMN IF NOT EXISTS decision_channel VARCHAR(30)
          CHECK (decision_channel IN ('web', 'api'))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_requests_pending
          ON approval_requests (workspace_id, user_id, status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_requests_task
          ON approval_requests (workspace_id, task_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_requests_hash
          ON approval_requests (workspace_id, payload_hash)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_hash")
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_task")
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_pending")
    op.execute("ALTER TABLE approval_requests DROP COLUMN IF EXISTS decision_channel")
    op.execute("ALTER TABLE approval_requests DROP COLUMN IF EXISTS approval_token")
    op.execute("ALTER TABLE approval_requests DROP COLUMN IF EXISTS tool_args")
    op.execute("ALTER TABLE approval_requests ALTER COLUMN subtask_id SET NOT NULL")
