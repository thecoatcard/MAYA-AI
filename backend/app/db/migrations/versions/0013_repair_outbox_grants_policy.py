"""Repair the task_outbox_events grants and row-level security policy.

Revision 0008 as first committed created ``task_outbox_events`` with a policy named
``task_outbox_events_tenant_isolation`` and no privileges for the runtime role, so the first
outbox insert by ``maya_app`` failed with "permission denied". Alembic never re-runs an applied
revision, so databases already migrated from that 0008 need this repair step. It is idempotent
and ends in one state whatever the starting point (committed 0008, an edited 0008, or a database
patched by hand):

* forced RLS stays on; the single policy is ``ws_isolation`` (the plan's pattern) with the same
  expression for USING and WITH CHECK, so a write cannot target another workspace;
* ``maya_app`` holds exactly SELECT, INSERT and UPDATE (status, dispatched_at). That is all the
  outbox code needs (insert events, ``SELECT ... FOR UPDATE SKIP LOCKED``, mark dispatched or
  failed); no DELETE, TRUNCATE or other column updates;
* ownership is unchanged and no role gets BYPASSRLS.

Downgrade returns to the state that the committed 0008 created (no privileges, the old policy
name), which is safe: it only removes access.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: None = None
depends_on: None = None

WORKSPACE_SETTING = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"
OLD_POLICY = "task_outbox_events_tenant_isolation"
POLICY = "ws_isolation"


def upgrade() -> None:
    op.execute("ALTER TABLE task_outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_outbox_events FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {OLD_POLICY} ON task_outbox_events")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON task_outbox_events")
    op.execute(
        f"""
        CREATE POLICY {POLICY} ON task_outbox_events
          USING (workspace_id = {WORKSPACE_SETTING})
          WITH CHECK (workspace_id = {WORKSPACE_SETTING})
        """
    )
    # Start from nothing so that stray wider grants are removed, then grant the minimum.
    op.execute("REVOKE ALL PRIVILEGES ON task_outbox_events FROM maya_app")
    op.execute("GRANT SELECT, INSERT ON task_outbox_events TO maya_app")
    op.execute("GRANT UPDATE (status, dispatched_at) ON task_outbox_events TO maya_app")


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON task_outbox_events FROM maya_app")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON task_outbox_events")
    op.execute(f"DROP POLICY IF EXISTS {OLD_POLICY} ON task_outbox_events")
    op.execute(
        f"""
        CREATE POLICY {OLD_POLICY} ON task_outbox_events
          USING (workspace_id = {WORKSPACE_SETTING})
        """
    )
