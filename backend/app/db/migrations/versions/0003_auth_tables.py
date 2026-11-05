"""Authentication storage: one-time-code challenges and refresh-token sessions.

Both tables belong to a workspace and a user, so they get the same forced row-level
security as the plan's tenant tables (applied in revision 0004). They are named
``auth_*`` because the plan's ``sessions`` table is the chat-conversation table.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    # Only the MAC of a code is stored. Attempts, expiry and single use are enforced by
    # conditional UPDATEs; the partial unique index allows one live challenge per purpose.
    op.execute(
        """
        CREATE TABLE auth_otp_challenges (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          user_id UUID NOT NULL,
          purpose VARCHAR(30) NOT NULL CHECK (purpose IN ('email_verification','password_reset')),
          code_hmac BYTEA NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          expires_at TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ,
          invalidated_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT auth_otp_user_fk FOREIGN KEY (workspace_id, user_id)
            REFERENCES users (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX auth_otp_one_live_challenge ON auth_otp_challenges (user_id, purpose) "
        "WHERE consumed_at IS NULL AND invalidated_at IS NULL"
    )
    op.execute("CREATE INDEX auth_otp_workspace_idx ON auth_otp_challenges (workspace_id)")

    # One row per login. refresh_jti is the only refresh token that currently works. Every
    # rotation raises refresh_generation, so presenting ANY older token (not just the previous
    # one) is recognised as a replay of a stolen token and revokes the session.
    op.execute(
        """
        CREATE TABLE auth_sessions (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          user_id UUID NOT NULL,
          refresh_jti UUID NOT NULL,
          refresh_generation INTEGER NOT NULL DEFAULT 0 CHECK (refresh_generation >= 0),
          refresh_expires_at TIMESTAMPTZ NOT NULL,
          absolute_expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at TIMESTAMPTZ,
          revoked_reason VARCHAR(30)
            CHECK (revoked_reason IN ('logout','password_reset','reuse_detected')),
          CONSTRAINT auth_sessions_user_fk FOREIGN KEY (workspace_id, user_id)
            REFERENCES users (workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX auth_sessions_workspace_idx ON auth_sessions (workspace_id)")
    op.execute(
        "CREATE INDEX auth_sessions_live_user_idx ON auth_sessions (user_id) "
        "WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE auth_sessions")
    op.execute("DROP TABLE auth_otp_challenges")
