"""Per-user settings (ADR-0016): one JSONB row per user, first key ``chat_intent_classifier``.

Tenant table with forced row-level security (``ws_isolation``, workspace only; the repository
also filters by user). The runtime role gets SELECT, INSERT and UPDATE; no DELETE. Idempotent
upgrade; the downgrade drops the table.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: None = None
depends_on: None = None

WORKSPACE_SETTING = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
          workspace_id UUID NOT NULL,
          user_id UUID NOT NULL,
          settings JSONB NOT NULL DEFAULT '{}'::jsonb,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT user_settings_pk PRIMARY KEY (workspace_id, user_id),
          CONSTRAINT user_settings_user_fk
            FOREIGN KEY (workspace_id, user_id)
            REFERENCES users (workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT user_settings_is_object CHECK (jsonb_typeof(settings) = 'object')
        )
        """
    )
    op.execute("ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_settings FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON user_settings")
    op.execute(
        "CREATE POLICY ws_isolation ON user_settings "
        f"USING (workspace_id = {WORKSPACE_SETTING}) "
        f"WITH CHECK (workspace_id = {WORKSPACE_SETTING})"
    )
    op.execute("REVOKE ALL PRIVILEGES ON user_settings FROM maya_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_settings TO maya_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_settings")
