"""Phase 08: MCP server health, tool manifest, and supervisor state tracking.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # 1. Add supervisor health and tool manifest columns to mcp_servers
    op.execute(
        """
        ALTER TABLE mcp_servers
          ADD COLUMN IF NOT EXISTS health_status VARCHAR(20) NOT NULL DEFAULT 'healthy'
            CHECK (health_status IN (
              'healthy', 'degraded', 'dead', 'starting', 'stopping', 'quarantined'
            )),
          ADD COLUMN IF NOT EXISTS last_ping_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS tool_count INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS tools_manifest JSONB NOT NULL DEFAULT '[]',
          ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )

    # 2. Add composite index for supervisor polling and active server lookups
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mcp_servers_workspace_active
          ON mcp_servers (workspace_id, is_active, health_status)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mcp_servers_workspace_active")
    op.execute(
        """
        ALTER TABLE mcp_servers
          DROP COLUMN IF EXISTS updated_at,
          DROP COLUMN IF EXISTS tools_manifest,
          DROP COLUMN IF EXISTS tool_count,
          DROP COLUMN IF EXISTS consecutive_failures,
          DROP COLUMN IF EXISTS last_ping_at,
          DROP COLUMN IF EXISTS health_status
        """
    )
