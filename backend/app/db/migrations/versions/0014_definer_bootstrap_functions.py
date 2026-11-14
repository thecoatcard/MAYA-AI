"""Two SECURITY DEFINER functions that answer cross-tenant questions without exposing rows.

* ``auth_any_account_exists()``: is there at least one user (local-owner bootstrap, ADR-0014).
* ``list_workspaces_with_open_work()``: ids of workspaces with open tasks, open subtasks or
  pending outbox events (background recovery). Ids only, nothing else.

Both are owned by the existing ``maya_auth_definer`` role, pin ``search_path``, are not
executable by PUBLIC and are executable by ``maya_app`` only. The definer role gets SELECT on
the few columns they read, plus a SELECT-only policy scoped to that role on each table (the
runtime role keeps the workspace policy and gains nothing). Idempotent; the downgrade drops the
functions, the policies and those column grants.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: None = None
depends_on: None = None

POLICY = "definer_open_work_lookup"
# table -> columns the definer role may read (users already has its 0004 lookup grant).
DEFINER_COLUMNS = {
    "tasks": "workspace_id, status, plan_status",
    "subtasks": "workspace_id, status",
    "task_outbox_events": "workspace_id, status",
}
POLICY_TABLES = ("tasks", "subtasks", "task_outbox_events")
FUNCTIONS = ("auth_any_account_exists()", "list_workspaces_with_open_work()")

ANY_ACCOUNT_EXISTS = """
    CREATE OR REPLACE FUNCTION public.auth_any_account_exists()
    RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $body$
      SELECT EXISTS (SELECT 1 FROM public.users)
    $body$
"""
OPEN_WORK = """
    CREATE OR REPLACE FUNCTION public.list_workspaces_with_open_work()
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $body$
      SELECT t.workspace_id FROM public.tasks AS t
      WHERE t.status IN ('pending', 'executing', 'awaiting_approval', 'compensating')
         OR t.plan_status IN ('planning', 'executing', 'paused')
      UNION
      SELECT s.workspace_id FROM public.subtasks AS s
      WHERE s.status IN ('pending', 'in_progress', 'awaiting_approval')
      UNION
      SELECT o.workspace_id FROM public.task_outbox_events AS o
      WHERE o.status = 'pending'
    $body$
"""


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maya_auth_definer') THEN
            RAISE EXCEPTION 'Role maya_auth_definer is missing; revision 0004 creates it';
          END IF;
          IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
             AND NOT pg_has_role(current_user, 'maya_auth_definer', 'SET') THEN
            RAISE EXCEPTION 'Grant maya_auth_definer to the migration role WITH SET TRUE first';
          END IF;
        END $$
        """
    )
    for table in POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} FOR SELECT TO maya_auth_definer USING (true)"
        )
    for table, columns in DEFINER_COLUMNS.items():
        op.execute(f"GRANT SELECT ({columns}) ON {table} TO maya_auth_definer")
    # Handing a function to a role needs CREATE on its schema for that step only (see 0004).
    op.execute("GRANT CREATE ON SCHEMA public TO maya_auth_definer")
    for statement, signature in zip((ANY_ACCOUNT_EXISTS, OPEN_WORK), FUNCTIONS, strict=True):
        op.execute(statement)
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO maya_auth_definer")
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO maya_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC, maya_app, maya_auth_definer")


def downgrade() -> None:
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS public.{signature}")
    for table in POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    for table, columns in DEFINER_COLUMNS.items():
        op.execute(f"REVOKE SELECT ({columns}) ON {table} FROM maya_auth_definer")
