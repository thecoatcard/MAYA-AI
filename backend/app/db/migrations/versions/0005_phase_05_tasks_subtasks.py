"""Phase 05: Tasks and subtasks schema updates for LangGraph orchestrator state.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: None = None
depends_on: None = None

UPGRADE_TASKS = (
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS user_instruction TEXT",
    (
        "UPDATE tasks SET user_instruction = instruction "
        "WHERE user_instruction IS NULL AND instruction IS NOT NULL"
    ),
    "ALTER TABLE tasks ALTER COLUMN instruction DROP NOT NULL",
    (
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS plan_status "
        "VARCHAR(30) NOT NULL DEFAULT 'planning'"
    ),
    """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tasks_plan_status_check'
      ) THEN
        ALTER TABLE tasks ADD CONSTRAINT tasks_plan_status_check
          CHECK (plan_status IN ('planning','executing','paused','completed','failed'));
      END IF;
    END $$;
    """,
    "ALTER TABLE tasks ALTER COLUMN status DROP NOT NULL",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS active_plan JSONB NOT NULL DEFAULT '[]'",
    (
        "UPDATE tasks SET active_plan = dag_plan "
        "WHERE active_plan = '[]'::jsonb AND dag_plan IS NOT NULL"
    ),
    "ALTER TABLE tasks ALTER COLUMN dag_plan DROP NOT NULL",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS shared_context JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_trace JSONB NOT NULL DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE tasks ALTER COLUMN idempotency_key DROP NOT NULL",
    "ALTER TABLE tasks ALTER COLUMN title DROP NOT NULL",
)

UPGRADE_SUBTASKS = (
    "ALTER TABLE subtasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'subtasks_assigned_agent_check'
      ) THEN
        ALTER TABLE subtasks ADD CONSTRAINT subtasks_assigned_agent_check
          CHECK (assigned_agent IN ('research','coding','document','email','pm','browser','file'));
      END IF;
    END $$;
    """,
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS tasks_workspace_idx ON tasks (workspace_id)",
    "CREATE INDEX IF NOT EXISTS tasks_session_idx ON tasks (workspace_id, session_id)",
    "CREATE INDEX IF NOT EXISTS tasks_plan_status_idx ON tasks (workspace_id, plan_status)",
    "CREATE INDEX IF NOT EXISTS subtasks_workspace_idx ON subtasks (workspace_id)",
    "CREATE INDEX IF NOT EXISTS subtasks_task_idx ON subtasks (workspace_id, task_id)",
    "CREATE INDEX IF NOT EXISTS subtasks_status_idx ON subtasks (workspace_id, status)",
)

GRANTS = ("GRANT SELECT, INSERT, UPDATE, DELETE ON tasks, subtasks TO maya_app",)

DOWNGRADE_INDEXES = (
    "DROP INDEX IF EXISTS subtasks_status_idx",
    "DROP INDEX IF EXISTS subtasks_task_idx",
    "DROP INDEX IF EXISTS subtasks_workspace_idx",
    "DROP INDEX IF EXISTS tasks_plan_status_idx",
    "DROP INDEX IF EXISTS tasks_session_idx",
    "DROP INDEX IF EXISTS tasks_workspace_idx",
)

DOWNGRADE_SUBTASKS = (
    "ALTER TABLE subtasks DROP CONSTRAINT IF EXISTS subtasks_assigned_agent_check",
    "ALTER TABLE subtasks DROP COLUMN IF EXISTS updated_at",
)

DOWNGRADE_TASKS = (
    "ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_plan_status_check",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS updated_at",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS execution_trace",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS shared_context",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS active_plan",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS plan_status",
    "ALTER TABLE tasks DROP COLUMN IF EXISTS user_instruction",
)


def upgrade() -> None:
    for statement in UPGRADE_TASKS:
        op.execute(statement)
    for statement in UPGRADE_SUBTASKS:
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_INDEXES:
        op.execute(statement)
    for statement in DOWNGRADE_SUBTASKS:
        op.execute(statement)
    for statement in DOWNGRADE_TASKS:
        op.execute(statement)
