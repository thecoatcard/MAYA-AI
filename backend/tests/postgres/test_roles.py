"""What the restricted runtime role can and cannot do."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support.postgres import SeededTenant, scoped, sqlstate

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]

FORBIDDEN_STATEMENTS = [
    # Destroying or weakening the isolation itself.
    "TRUNCATE users",
    "TRUNCATE sessions",
    "DROP TABLE sessions",
    "ALTER TABLE sessions DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE sessions NO FORCE ROW LEVEL SECURITY",
    "DROP POLICY ws_isolation ON sessions",
    "CREATE POLICY everything ON sessions USING (true)",
    "ALTER TABLE sessions OWNER TO maya_app",
    # Creating objects, roles and escalating.
    "CREATE TABLE evil (id int)",
    "CREATE SCHEMA evil",
    "CREATE FUNCTION public.evil() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE ROLE evil",
    "ALTER ROLE maya_app BYPASSRLS",
    "ALTER ROLE maya_app SUPERUSER",
    "SET ROLE maya_migrator",
    "SET ROLE maya_auth_definer",
    "SET SESSION AUTHORIZATION maya_migrator",
    "DROP FUNCTION public.auth_find_account_by_username(text)",
    # The authentication tables may only change their state columns.
    "UPDATE auth_sessions SET user_id = user_id",
    "UPDATE auth_sessions SET workspace_id = workspace_id",
    "UPDATE auth_sessions SET absolute_expires_at = absolute_expires_at",
    "UPDATE auth_sessions SET created_at = created_at",
    "UPDATE auth_otp_challenges SET code_hmac = code_hmac",
    "UPDATE auth_otp_challenges SET expires_at = expires_at",
    "UPDATE auth_otp_challenges SET user_id = user_id",
    "UPDATE auth_otp_challenges SET purpose = purpose",
    "UPDATE auth_otp_challenges SET workspace_id = workspace_id",
    "DELETE FROM auth_sessions",
    "DELETE FROM auth_otp_challenges",
    # Credential material and identity columns.
    "SELECT hashed_password FROM users",
    "SELECT * FROM users",
    "UPDATE users SET role = 'admin'",
    "UPDATE users SET username = 'renamed'",
    "UPDATE users SET workspace_id = workspace_id",
    "UPDATE users SET email = email",
    "DELETE FROM users",
    "DELETE FROM workspaces",
    "UPDATE workspaces SET name = name",
    "TRUNCATE workspaces",
    # The audit trail is append-only for the runtime role.
    "UPDATE audit_log SET status = status",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
    # Schema bookkeeping.
    "INSERT INTO alembic_version (version_num) VALUES ('9999')",
    "SELECT version_num FROM alembic_version",
]


async def test_runtime_role_flags(app_engine: AsyncEngine, tenants: Tenants) -> None:
    async with app_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                    "rolreplication, rolinherit FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    assert tuple(row)[:6] == (True, False, False, False, False, False)


async def test_definer_role_flags(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                    "rolreplication FROM pg_roles WHERE rolname = 'maya_auth_definer'"
                )
            )
        ).one()
    assert tuple(row) == (False, False, False, False, False, False)


async def test_runtime_role_owns_nothing_and_has_no_role_memberships(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        owned = (
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relowner = 'maya_app'::regrole "
                        "UNION ALL SELECT count(*) FROM pg_proc "
                        "WHERE proowner = 'maya_app'::regrole "
                        "UNION ALL SELECT count(*) FROM pg_namespace "
                        "WHERE nspowner = 'maya_app'::regrole"
                    )
                )
            )
            .scalars()
            .all()
        )
        memberships = (
            await connection.execute(
                text("SELECT count(*) FROM pg_auth_members WHERE member = 'maya_app'::regrole")
            )
        ).scalar_one()
    assert owned == [0, 0, 0]
    assert memberships == 0


@pytest.mark.parametrize("statement", FORBIDDEN_STATEMENTS)
async def test_forbidden_statements_are_refused(
    app_engine: AsyncEngine, tenants: Tenants, statement: str
) -> None:
    async with scoped(app_engine, tenants[0].workspace_id) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(statement))
    assert sqlstate(caught.value) == "42501", statement


async def test_permitted_user_columns_exclude_the_password_hash(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, _ = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, workspace_id, username, email, full_name, email_verified_at, "
                    "password_changed_at, role, created_at FROM users"
                )
            )
        ).all()
        assert len(rows) == 2
        updated = await connection.execute(
            text(
                "UPDATE users SET hashed_password = :new_hash, "
                "email_verified_at = email_verified_at, "
                "password_changed_at = password_changed_at WHERE id = :id"
            ),
            {"id": first.user_id, "new_hash": "replacement-hash"},
        )
        assert updated.rowcount == 1


async def test_audit_log_accepts_appends_for_the_own_workspace(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, _ = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        inserted = await connection.execute(
            text(
                "INSERT INTO audit_log (workspace_id, user_ref, status, row_hash) "
                "VALUES (:ws, 'appender', 'success', '\\x01') RETURNING id"
            ),
            {"ws": first.workspace_id},
        )
        assert inserted.scalar_one() > 0


async def test_runtime_role_cannot_see_other_databases_roles_passwords(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text("SELECT rolpassword FROM pg_authid"))
    assert sqlstate(caught.value) == "42501"
