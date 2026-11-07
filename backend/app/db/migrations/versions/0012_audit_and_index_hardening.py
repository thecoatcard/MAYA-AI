"""Audit trail, approval idempotency, and query index hardening.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # 1. Partial unique index to enforce idempotency_key uniqueness per workspace for approvals
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_requests_workspace_idempotency
          ON approval_requests (workspace_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL
        """
    )

    # 2. Re-enforcing subtask foreign key with composite workspace isolation
    op.execute(
        """
        ALTER TABLE approval_requests
          DROP CONSTRAINT IF EXISTS approval_requests_subtask_fk,
          ADD CONSTRAINT approval_requests_subtask_fk
            FOREIGN KEY (workspace_id, subtask_id)
            REFERENCES subtasks (workspace_id, id) ON DELETE CASCADE
        """
    )

    # 3. Composite performance and lookup indexes across core tenant tables
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_workspace_tool
          ON audit_log (workspace_id, tool_name, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_workspace_session_created
          ON messages (workspace_id, session_id, created_at ASC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_workspace_user_created
          ON sessions (workspace_id, user_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_compensations_workspace_task
          ON compensations (workspace_id, task_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_file_artifacts_workspace_task
          ON file_artifacts (workspace_id, task_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_file_artifacts_workspace_task")
    op.execute("DROP INDEX IF EXISTS idx_compensations_workspace_task")
    op.execute("DROP INDEX IF EXISTS idx_sessions_workspace_user_created")
    op.execute("DROP INDEX IF EXISTS idx_messages_workspace_session_created")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_workspace_tool")
    op.execute(
        """
        ALTER TABLE approval_requests
          DROP CONSTRAINT IF EXISTS approval_requests_subtask_fk,
          ADD CONSTRAINT approval_requests_subtask_fk
            FOREIGN KEY (workspace_id, subtask_id)
            REFERENCES subtasks (workspace_id, id) ON DELETE CASCADE
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_approval_requests_workspace_idempotency")
