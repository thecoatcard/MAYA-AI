"""Identity tables: extensions, workspaces and users.

Revision ID: 0001
Revises:
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE TABLE workspaces (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name VARCHAR(255) NOT NULL,
          slug VARCHAR(100) UNIQUE NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # The plan's users table, adjusted for the owner's own-account decision: a username,
    # a verified-email timestamp and a mandatory password hash (no external identity provider).
    op.execute(
        """
        CREATE TABLE users (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          username VARCHAR(32) NOT NULL,
          email VARCHAR(255) NOT NULL,
          full_name VARCHAR(255) NOT NULL,
          hashed_password VARCHAR(255) NOT NULL,
          email_verified_at TIMESTAMPTZ,
          password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          role VARCHAR(50) NOT NULL DEFAULT 'user',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT users_email_key UNIQUE (email),
          CONSTRAINT users_username_key UNIQUE (username),
          CONSTRAINT users_workspace_id_id_key UNIQUE (workspace_id, id),
          CONSTRAINT users_email_lowercase CHECK (email = lower(email)),
          CONSTRAINT users_username_format CHECK (username ~ '^[a-z0-9_.-]{3,32}$'),
          CONSTRAINT users_role_known CHECK (role IN ('admin', 'user'))
        )
        """
    )
    op.execute("CREATE INDEX users_workspace_idx ON users (workspace_id)")


def downgrade() -> None:
    op.execute("DROP TABLE users")
    op.execute("DROP TABLE workspaces")
    # Extensions are created by the database image and shared; they are left in place.
