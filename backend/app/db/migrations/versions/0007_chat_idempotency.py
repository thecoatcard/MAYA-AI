"""Persist chat idempotency claims and terminal results in PostgreSQL.

Redis remains a best-effort cache; a durable in-progress row is never reclaimed. This
conservative rule prevents a restarted process or lost Redis lease from invoking the model twice.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: None = None
depends_on: None = None

WORKSPACE_SETTING = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chat_idempotency (
          workspace_id UUID NOT NULL,
          user_id UUID NOT NULL,
          key_digest VARCHAR(64) NOT NULL,
          request_hash VARCHAR(64) NOT NULL,
          state VARCHAR(20) NOT NULL,
          session_id UUID,
          user_message_id UUID NOT NULL,
          assistant_message_id UUID NOT NULL,
          finish_reason VARCHAR(30),
          error_code VARCHAR(40),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CONSTRAINT chat_idempotency_pk
            PRIMARY KEY (workspace_id, user_id, key_digest),
          CONSTRAINT chat_idempotency_user_fk
            FOREIGN KEY (workspace_id, user_id)
            REFERENCES users (workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT chat_idempotency_key_digest_format
            CHECK (key_digest ~ '^[0-9a-f]{64}$'),
          CONSTRAINT chat_idempotency_request_hash_format
            CHECK (request_hash ~ '^[0-9a-f]{64}$'),
          CONSTRAINT chat_idempotency_state_known
            CHECK (state IN ('in_progress', 'completed', 'interrupted', 'failed')),
          CONSTRAINT chat_idempotency_result_fields
            CHECK (
              (state = 'in_progress' AND finish_reason IS NULL AND error_code IS NULL)
              OR (state IN ('completed', 'interrupted')
                  AND finish_reason IS NOT NULL AND error_code IS NULL)
              OR (state = 'failed' AND finish_reason IS NULL AND error_code IS NOT NULL)
            ),
          CONSTRAINT chat_idempotency_finish_reason_known
            CHECK (finish_reason IS NULL OR finish_reason IN ('stop', 'max_tokens', 'interrupted')),
          CONSTRAINT chat_idempotency_error_code_known
            CHECK (error_code IS NULL OR error_code IN (
              'content_blocked', 'reply_deadline_exceeded', 'stream_interrupted',
              'model_unavailable', 'internal_error'
            )),
          CONSTRAINT chat_idempotency_terminal_has_session
            CHECK (state = 'in_progress' OR session_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX chat_idempotency_workspace_user_idx ON chat_idempotency "
        "(workspace_id, user_id)"
    )
    op.execute("ALTER TABLE chat_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_idempotency FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY ws_isolation ON chat_idempotency "
        f"USING (workspace_id = {WORKSPACE_SETTING} AND user_id = {USER_SETTING}) "
        f"WITH CHECK (workspace_id = {WORKSPACE_SETTING} AND user_id = {USER_SETTING})"
    )
    # No DELETE or TRUNCATE access. The scoped claim identity and attempt identifiers are immutable;
    # runtime updates can attach a session and perform the in-progress-to-terminal CAS only.
    op.execute("GRANT SELECT, INSERT ON chat_idempotency TO maya_app")
    op.execute(
        "GRANT UPDATE (state, session_id, finish_reason, error_code, updated_at) "
        "ON chat_idempotency TO maya_app"
    )


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON chat_idempotency FROM maya_app")
    op.execute("DROP POLICY ws_isolation ON chat_idempotency")
    op.execute("ALTER TABLE chat_idempotency NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_idempotency DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE chat_idempotency")
