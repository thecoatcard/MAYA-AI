"""Phase 07: Approval execution lifecycle and claim tracking.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # 1. Update status check constraint to support explicit execution lifecycle states
    op.execute(
        "ALTER TABLE approval_requests DROP CONSTRAINT IF EXISTS approval_requests_status_check"
    )
    op.execute("ALTER TABLE approval_requests ALTER COLUMN status TYPE VARCHAR(30)")
    op.execute(
        """
        ALTER TABLE approval_requests ADD CONSTRAINT approval_requests_status_check
          CHECK (status IN (
            'pending', 'approved', 'rejected', 'expired', 'revoked',
            'claimed', 'executing', 'completed', 'failed', 'denied', 'consumed'
          ))
        """
    )

    # 2. Add execution tracking columns
    op.execute(
        """
        ALTER TABLE approval_requests
          ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(100),
          ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS failure_reason TEXT,
          ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128),
          ADD COLUMN IF NOT EXISTS execution_output JSONB
        """
    )

    # 3. Add index for execution queries and claim recovery
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approval_requests_execution
          ON approval_requests (workspace_id, status, expires_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_execution")
    op.execute(
        """
        ALTER TABLE approval_requests
          DROP COLUMN IF EXISTS execution_output,
          DROP COLUMN IF EXISTS idempotency_key,
          DROP COLUMN IF EXISTS failure_reason,
          DROP COLUMN IF EXISTS failed_at,
          DROP COLUMN IF EXISTS completed_at,
          DROP COLUMN IF EXISTS started_at,
          DROP COLUMN IF EXISTS claimed_at,
          DROP COLUMN IF EXISTS claimed_by
        """
    )
    op.execute(
        "ALTER TABLE approval_requests DROP CONSTRAINT IF EXISTS approval_requests_status_check"
    )
    op.execute("ALTER TABLE approval_requests ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute(
        """
        ALTER TABLE approval_requests ADD CONSTRAINT approval_requests_status_check
          CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'consumed'))
        """
    )
