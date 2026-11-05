"""The remaining tables of the master plan, section 14.

One deliberate hardening over the plan's DDL: every reference to a ``users``, ``sessions``,
``tasks`` or ``subtasks`` row is a *composite* foreign key ``(workspace_id, <parent>_id)`` onto
``UNIQUE (workspace_id, id)``. PostgreSQL does not apply row-level security to foreign-key
checks, so with plain keys a row of one workspace could point at another workspace's row (an
existence oracle) and deleting a parent could cascade across workspaces. Composite keys make a
cross-workspace reference impossible at the constraint level.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: None = None
depends_on: None = None

TABLES = (
    """
    CREATE TABLE sessions (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id UUID NOT NULL,
      title VARCHAR(255),
      channel VARCHAR(20) NOT NULL DEFAULT 'web' CHECK (channel IN ('web','voice','desktop')),
      status VARCHAR(20) NOT NULL DEFAULT 'active',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT sessions_workspace_id_id_key UNIQUE (workspace_id, id),
      CONSTRAINT sessions_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE messages (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      session_id UUID NOT NULL,
      sender VARCHAR(20) NOT NULL CHECK (sender IN ('user','orchestrator','agent','system')),
      agent_name VARCHAR(100),
      modality VARCHAR(20) NOT NULL DEFAULT 'text'
        CHECK (modality IN ('text','audio','multimodal')),
      content TEXT NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}',
      audio_s3_uri VARCHAR(512),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT messages_session_fk FOREIGN KEY (workspace_id, session_id)
        REFERENCES sessions (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE tasks (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      session_id UUID NOT NULL,
      user_id UUID NOT NULL,
      idempotency_key VARCHAR(128) NOT NULL,
      title VARCHAR(255) NOT NULL,
      instruction TEXT NOT NULL,
      dag_plan JSONB NOT NULL,
      status VARCHAR(30) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','executing','awaiting_approval','completed','failed',
                          'cancelled','compensating')),
      budget_usd NUMERIC(8,2) NOT NULL DEFAULT 1.50,
      spent_usd  NUMERIC(8,4) NOT NULL DEFAULT 0,
      tool_turns INTEGER NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      completed_at TIMESTAMPTZ,
      UNIQUE (workspace_id, idempotency_key),
      CONSTRAINT tasks_workspace_id_id_key UNIQUE (workspace_id, id),
      CONSTRAINT tasks_session_fk FOREIGN KEY (workspace_id, session_id)
        REFERENCES sessions (workspace_id, id) ON DELETE CASCADE,
      CONSTRAINT tasks_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE subtasks (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      task_id UUID NOT NULL,
      assigned_agent VARCHAR(50) NOT NULL,
      description TEXT NOT NULL,
      dependencies JSONB NOT NULL DEFAULT '[]',
      status VARCHAR(30) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','awaiting_approval','completed','failed')),
      input_data JSONB NOT NULL DEFAULT '{}',
      output_result JSONB,
      verification_proof JSONB,
      error_message TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      completed_at TIMESTAMPTZ,
      CONSTRAINT subtasks_workspace_id_id_key UNIQUE (workspace_id, id),
      CONSTRAINT subtasks_task_fk FOREIGN KEY (workspace_id, task_id)
        REFERENCES tasks (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE approval_requests (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      task_id UUID NOT NULL,
      subtask_id UUID NOT NULL,
      user_id UUID NOT NULL,
      tool_name VARCHAR(150) NOT NULL,
      permission_level VARCHAR(30) NOT NULL
        CHECK (permission_level IN ('APPROVAL_REQUIRED','RESTRICTED')),
      action_description TEXT NOT NULL,
      payload_hash CHAR(64) NOT NULL,
      payload_diff JSONB NOT NULL,
      status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','denied','expired','consumed')),
      token_jti UUID,
      decided_at TIMESTAMPTZ,
      consumed_at TIMESTAMPTZ,
      expires_at TIMESTAMPTZ NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT approval_requests_task_fk FOREIGN KEY (workspace_id, task_id)
        REFERENCES tasks (workspace_id, id) ON DELETE CASCADE,
      CONSTRAINT approval_requests_subtask_fk FOREIGN KEY (workspace_id, subtask_id)
        REFERENCES subtasks (workspace_id, id) ON DELETE CASCADE,
      CONSTRAINT approval_requests_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE compensations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      task_id UUID NOT NULL,
      subtask_id UUID,
      forward_tool VARCHAR(150) NOT NULL,
      forward_ref JSONB NOT NULL,
      compensating_tool VARCHAR(150),
      compensating_args JSONB,
      permission_level VARCHAR(30) NOT NULL,
      status VARCHAR(30) NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered','pending_approval','executed','failed','not_applicable')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT compensations_task_fk FOREIGN KEY (workspace_id, task_id)
        REFERENCES tasks (workspace_id, id) ON DELETE CASCADE,
      CONSTRAINT compensations_subtask_fk FOREIGN KEY (workspace_id, subtask_id)
        REFERENCES subtasks (workspace_id, id) ON DELETE SET NULL (subtask_id)
    )
    """,
    # No foreign keys to users or tasks, so the trail survives deletes. Hash chaining and
    # append-only enforcement belong to the governance phase (Phase 07).
    """
    CREATE TABLE audit_log (
      id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      workspace_id UUID NOT NULL,
      user_ref TEXT NOT NULL,
      task_id UUID, subtask_id UUID, approval_id UUID,
      server_name VARCHAR(100), tool_name VARCHAR(150),
      permission_level VARCHAR(30),
      input_redacted JSONB, output_redacted JSONB,
      status VARCHAR(30) NOT NULL,
      duration_ms INTEGER,
      prev_hash BYTEA,
      row_hash BYTEA NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE mcp_servers (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id UUID NOT NULL,
      name VARCHAR(100) NOT NULL,
      transport VARCHAR(20) NOT NULL
        CHECK (transport IN ('stdio','streamable_http','sse_legacy')),
      command VARCHAR(512), args JSONB NOT NULL DEFAULT '[]', url VARCHAR(512),
      pinned_version VARCHAR(100), schema_hash CHAR(64),
      env_vars_encrypted BYTEA, key_id VARCHAR(100),
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT mcp_servers_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE file_artifacts (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      task_id UUID NOT NULL,
      user_id UUID NOT NULL,
      file_name VARCHAR(255) NOT NULL,
      file_type VARCHAR(20) NOT NULL
        CHECK (file_type IN ('pdf','xlsx','docx','pptx','md','csv','other')),
      storage_uri VARCHAR(512) NOT NULL,
      size_bytes BIGINT NOT NULL,
      sha256 CHAR(64) NOT NULL,
      verification_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN ('pending','verified','failed')),
      verification_report JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT file_artifacts_task_fk FOREIGN KEY (workspace_id, task_id)
        REFERENCES tasks (workspace_id, id) ON DELETE CASCADE,
      CONSTRAINT file_artifacts_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE memory_items (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      user_id UUID NOT NULL,
      category VARCHAR(20) NOT NULL
        CHECK (category IN ('preference','project','episodic','knowledge')),
      content TEXT NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}',
      embedding vector(768),
      embedding_model VARCHAR(100) NOT NULL,
      tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
      permanent BOOLEAN NOT NULL DEFAULT FALSE,
      access_count INTEGER NOT NULL DEFAULT 1,
      last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      expires_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT memory_items_user_fk FOREIGN KEY (workspace_id, user_id)
        REFERENCES users (workspace_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX memory_embedding_hnsw ON memory_items USING hnsw (embedding vector_cosine_ops) "
    "WITH (m = 16, ef_construction = 64)",
    "CREATE INDEX memory_tsv_gin ON memory_items USING gin (tsv)",
    "CREATE INDEX memory_content_trgm ON memory_items USING gin (content gin_trgm_ops)",
    "CREATE INDEX memory_scope_idx ON memory_items (workspace_id, user_id, category)",
)

# Dependents first.
DROP_ORDER = (
    "memory_items",
    "file_artifacts",
    "mcp_servers",
    "audit_log",
    "compensations",
    "approval_requests",
    "subtasks",
    "tasks",
    "messages",
    "sessions",
)


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)


def downgrade() -> None:
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE {table}")
