"""Roles, row-level security, least-privilege grants and the pre-login lookup functions.

* Forced row-level security on every table, keyed by the transaction-local setting
  ``app.workspace_id`` (unset or empty means no rows and no writes).
* ``maya_app`` (created by docker/postgres/init.sql) gets only the privileges listed below. It
  cannot read ``users.hashed_password``; only the by-username definer function returns it (login
  needs it), and the by-e-mail function does not. On the two authentication tables it may update
  only the state columns, never ``user_id``, ``workspace_id``, ``code_hmac`` or ``expires_at``.
* ``maya_auth_definer`` is a NOLOGIN role that owns the two lookup functions. It is a
  cluster-level role, so downgrading leaves it in place; creating it is idempotent.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: None = None
depends_on: None = None

# The plan's eleven tenant tables plus the two authentication tables.
TENANT_TABLES = (
    "users",
    "sessions",
    "messages",
    "tasks",
    "subtasks",
    "approval_requests",
    "compensations",
    "audit_log",
    "mcp_servers",
    "file_artifacts",
    "memory_items",
    "auth_otp_challenges",
    "auth_sessions",
)
RLS_TABLES = ("workspaces", *TENANT_TABLES)
FULL_ACCESS_TABLES = (
    "sessions",
    "messages",
    "tasks",
    "subtasks",
    "approval_requests",
    "compensations",
    "mcp_servers",
    "file_artifacts",
    "memory_items",
)
WORKSPACE_SETTING = "NULLIF(current_setting('app.workspace_id', true), '')::uuid"

LOOKUP_COLUMNS = (
    "id, workspace_id, username, email, hashed_password, email_verified_at, role, "
    "password_changed_at"
)
FUNCTIONS = ("auth_find_account_by_username", "auth_find_account_by_email")


# Login needs the hash and the password-change marker; the e-mail lookup (verification and
# recovery) needs neither, so it does not return them.
LOOKUP_BY_USERNAME = """
    CREATE FUNCTION public.auth_find_account_by_username(p_value text)
    RETURNS TABLE (
      user_id uuid, workspace_id uuid, username varchar, email varchar,
      email_verified_at timestamptz, role varchar,
      hashed_password varchar, password_changed_at timestamptz
    )
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $body$
      SELECT u.id, u.workspace_id, u.username, u.email, u.email_verified_at, u.role,
             u.hashed_password, u.password_changed_at
      FROM public.users AS u
      WHERE u.username = p_value
      LIMIT 1
    $body$
"""
LOOKUP_BY_EMAIL = """
    CREATE FUNCTION public.auth_find_account_by_email(p_value text)
    RETURNS TABLE (
      user_id uuid, workspace_id uuid, username varchar, email varchar,
      email_verified_at timestamptz, role varchar
    )
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $body$
      SELECT u.id, u.workspace_id, u.username, u.email, u.email_verified_at, u.role
      FROM public.users AS u
      WHERE u.email = p_value
      LIMIT 1
    $body$
"""


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maya_app') THEN
            RAISE EXCEPTION 'Role maya_app is missing; docker/postgres/init.sql creates it';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'maya_app' AND (rolsuper OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'Role maya_app must not be a superuser or bypass row-level security';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maya_auth_definer') THEN
            CREATE ROLE maya_auth_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
              NOREPLICATION NOBYPASSRLS;
          END IF;
          -- Handing the lookup functions to that role needs the right to SET ROLE to it
          -- (PostgreSQL 16 and later). Fail early and clearly instead of half way through.
          IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
             AND NOT pg_has_role(current_user, 'maya_auth_definer', 'SET') THEN
            RAISE EXCEPTION 'Grant maya_auth_definer to the migration role WITH SET TRUE first';
          END IF;
        END $$
        """
    )

    for table in RLS_TABLES:
        column = "id" if table == "workspaces" else "workspace_id"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY ws_isolation ON {table} "
            f"USING ({column} = {WORKSPACE_SETTING}) WITH CHECK ({column} = {WORKSPACE_SETTING})"
        )

    # The definer role may read users for the two lookups only; the policy is scoped to that
    # role, so maya_app gains no way around the workspace policy.
    op.execute(
        "CREATE POLICY auth_definer_lookup ON users FOR SELECT TO maya_auth_definer USING (true)"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO maya_app, maya_auth_definer")
    op.execute(f"GRANT SELECT ({LOOKUP_COLUMNS}) ON users TO maya_auth_definer")
    # A function can only be given to a role that may CREATE in its schema (a superuser skips
    # this check, a plain migration owner does not), so allow it for exactly this step.
    op.execute("GRANT CREATE ON SCHEMA public TO maya_auth_definer")
    for name, statement in (
        ("auth_find_account_by_username", LOOKUP_BY_USERNAME),
        ("auth_find_account_by_email", LOOKUP_BY_EMAIL),
    ):
        op.execute(statement)
        op.execute(f"ALTER FUNCTION public.{name}(text) OWNER TO maya_auth_definer")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}(text) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}(text) TO maya_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC, maya_app, maya_auth_definer")

    # maya_app: no ownership, no DDL, no TRUNCATE, no REFERENCES/TRIGGER.
    op.execute("GRANT SELECT, INSERT ON workspaces TO maya_app")
    op.execute(
        "GRANT SELECT (id, workspace_id, username, email, full_name, email_verified_at, "
        "password_changed_at, role, created_at) ON users TO maya_app"
    )
    op.execute("GRANT INSERT ON users TO maya_app")
    op.execute(
        "GRANT UPDATE (hashed_password, email_verified_at, password_changed_at) "
        "ON users TO maya_app"
    )
    # Authentication state: insert and read, and update only the columns that change.
    op.execute("GRANT SELECT, INSERT ON auth_otp_challenges, auth_sessions TO maya_app")
    op.execute(
        "GRANT UPDATE (attempts, consumed_at, invalidated_at) ON auth_otp_challenges TO maya_app"
    )
    op.execute(
        "GRANT UPDATE (refresh_jti, refresh_generation, refresh_expires_at, last_used_at, "
        "revoked_at, revoked_reason) ON auth_sessions TO maya_app"
    )
    op.execute("GRANT SELECT, INSERT ON audit_log TO maya_app")
    for table in FULL_ACCESS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO maya_app")


def downgrade() -> None:
    for name in FUNCTIONS:
        op.execute(f"DROP FUNCTION public.{name}(text)")
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM maya_app, maya_auth_definer"
    )
    op.execute("DROP POLICY auth_definer_lookup ON users")
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY ws_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # The schema USAGE grants and the maya_auth_definer role are cluster/schema level and shared
    # with later revisions' tooling, so they are left in place.
