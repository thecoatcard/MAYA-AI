"""Repair the approval_requests UPDATE grants for the runtime role.

The approval execution lifecycle (revision 0010) moves a row ``approved`` -> ``claimed`` ->
``executing`` -> ``completed`` / ``failed`` and writes ``claimed_by``, ``claimed_at``,
``started_at``, ``completed_at``, ``failed_at``, ``failure_reason`` and ``execution_output``.
Revision 0004 grants ``maya_app`` full access to the table, but a development database that was
patched by hand (or migrated from an earlier state) can end with column-level UPDATE only on the
decision columns. Then ``UPDATE approval_requests SET status = 'claimed' ...`` fails with
"permission denied for table approval_requests", the approval is never claimed and an approved
task never progresses.

This step grants UPDATE on exactly the lifecycle columns, whatever the starting point. It is
additive and idempotent (a GRANT that is already held changes nothing), adds no DELETE, no
TRUNCATE and no role attribute, and leaves forced row-level security untouched.

Downgrade revokes only the columns this step added; the decision-column grants stay.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: None = None
depends_on: None = None

LIFECYCLE_COLUMNS = (
    "claimed_by",
    "claimed_at",
    "started_at",
    "completed_at",
    "failed_at",
    "failure_reason",
    "execution_output",
)


def upgrade() -> None:
    op.execute(f"GRANT UPDATE ({', '.join(LIFECYCLE_COLUMNS)}) ON approval_requests TO maya_app")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ({', '.join(LIFECYCLE_COLUMNS)}) ON approval_requests FROM maya_app")
