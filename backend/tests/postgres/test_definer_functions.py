"""The two pre-login lookup functions: narrow, fixed search path, exact match only."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support.postgres import SeededTenant, scoped, sqlstate

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]
FUNCTIONS = {
    "auth_find_account_by_username": "username",
    "auth_find_account_by_email": "email",
}
IDENTITY_COLUMNS = ["user_id", "workspace_id", "username", "email", "email_verified_at", "role"]
EXPECTED_COLUMNS = {
    # Login needs the hash and the password version; the e-mail lookup returns neither.
    "auth_find_account_by_username": [*IDENTITY_COLUMNS, "hashed_password", "password_changed_at"],
    "auth_find_account_by_email": IDENTITY_COLUMNS,
}


async def seeded_identity(admin: AsyncEngine, tenant: SeededTenant) -> dict[str, Any]:
    async with admin.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT username, email, hashed_password FROM users WHERE id = :id"),
                {"id": tenant.user_id},
            )
        ).one()
    return {"username": row[0], "email": row[1], "hash": row[2]}


async def call(
    engine: AsyncEngine, workspace: object, function: str, value: str
) -> list[dict[str, Any]]:
    async with scoped(engine, workspace) as connection:  # type: ignore[arg-type]
        result = await connection.execute(
            text(f"SELECT * FROM public.{function}(:value)"), {"value": value}
        )
        return [dict(row._mapping) for row in result]


@pytest.mark.parametrize("function", FUNCTIONS)
async def test_function_is_a_locked_down_security_definer(
    admin_engine: AsyncEngine, function: str
) -> None:
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT prosecdef, proconfig, pg_get_userbyid(proowner), "
                    "has_function_privilege('maya_app', p.oid, 'EXECUTE'), "
                    "EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0) "
                    "FROM pg_proc p WHERE proname = :name"
                ),
                {"name": function},
            )
        ).one()
    secdef, config, owner, app_may_execute, public_may_execute = row
    assert secdef is True
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert owner == "maya_auth_definer"
    assert app_may_execute is True
    assert public_may_execute is False


@pytest.mark.parametrize(("function", "column"), FUNCTIONS.items())
async def test_lookup_returns_exactly_the_documented_columns(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants, function: str, column: str
) -> None:
    first, _ = tenants
    identity = await seeded_identity(admin_engine, first)
    rows = await call(app_engine, None, function, identity[column])
    assert len(rows) == 1
    assert list(rows[0]) == EXPECTED_COLUMNS[function]
    assert rows[0]["user_id"] == first.user_id
    assert rows[0]["workspace_id"] == first.workspace_id
    if function == "auth_find_account_by_username":
        assert rows[0]["hashed_password"] == identity["hash"]
    else:
        assert "hashed_password" not in rows[0]


@pytest.mark.parametrize(("function", "column"), FUNCTIONS.items())
async def test_lookup_works_before_a_workspace_is_known_and_ignores_the_context(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants, function: str, column: str
) -> None:
    first, second = tenants
    identity = await seeded_identity(admin_engine, first)
    for context in (None, "", uuid4(), second.workspace_id):
        rows = await call(app_engine, context, function, identity[column])
        assert [row["user_id"] for row in rows] == [first.user_id]


@pytest.mark.parametrize(("function", "column"), FUNCTIONS.items())
@pytest.mark.parametrize(
    "payload",
    [
        "",
        "nobody",
        "' OR '1'='1",
        "x'; DROP TABLE users; --",
        "%",
        "_",
        "a%",
        "NULL",
        "\\",
        "ünï",
    ],
)
async def test_lookup_is_an_exact_match_and_not_injectable(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    tenants: Tenants,
    function: str,
    column: str,
    payload: str,
) -> None:
    del column
    assert await call(app_engine, None, function, payload) == []
    async with admin_engine.connect() as connection:
        count = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count >= 4


@pytest.mark.parametrize(("function", "column"), FUNCTIONS.items())
async def test_lookup_is_case_sensitive_because_the_app_normalizes_first(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants, function: str, column: str
) -> None:
    identity = await seeded_identity(admin_engine, tenants[0])
    value = identity[column]
    assert await call(app_engine, None, function, value.upper()) == []
    assert await call(app_engine, None, function, f" {value}") == []
    assert len(await call(app_engine, None, function, value)) == 1


@pytest.mark.parametrize(("function", "column"), FUNCTIONS.items())
async def test_a_shadow_table_on_the_search_path_cannot_hijack_the_lookup(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants, function: str, column: str
) -> None:
    identity = await seeded_identity(admin_engine, tenants[0])
    async with scoped(app_engine, None) as connection:
        await connection.execute(
            text(
                "CREATE TEMP TABLE users (id uuid, workspace_id uuid, username varchar, "
                "email varchar, hashed_password varchar, email_verified_at timestamptz, "
                "role varchar)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO pg_temp.users VALUES (gen_random_uuid(), gen_random_uuid(), "
                "'shadow_user', 'shadow@example.com', 'attacker-chosen-hash', now(), 'admin')"
            )
        )
        await connection.execute(text("SET LOCAL search_path = pg_temp, public"))
        planted = await connection.execute(
            text(f"SELECT * FROM public.{function}(:value)"),
            {"value": "shadow_user" if column == "username" else "shadow@example.com"},
        )
        assert planted.all() == []
        genuine = await connection.execute(
            text(f"SELECT user_id FROM public.{function}(:value)"), {"value": identity[column]}
        )
        assert len(genuine.all()) == 1


async def test_the_definer_role_is_confined_to_the_lookup_columns(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    async def attempt(statement: str) -> str | None:
        async with admin_engine.connect() as connection:
            await connection.begin()
            try:
                await connection.execute(text("SET LOCAL ROLE maya_auth_definer"))
                await connection.execute(text(statement))
            except DBAPIError as error:
                return sqlstate(error)
            finally:
                await connection.rollback()
        return None

    assert await attempt("SELECT hashed_password FROM users") is None
    assert await attempt("SELECT full_name FROM users") == "42501"
    assert await attempt("SELECT password_changed_at FROM users") is None
    assert await attempt("SELECT created_at FROM users") == "42501"
    assert (
        await attempt("INSERT INTO workspaces (name, slug) VALUES ('x', 'definer-probe')")
        == "42501"
    )
    assert await attempt("UPDATE users SET role = 'admin'") == "42501"
    assert await attempt("DELETE FROM users") == "42501"
    for table in ("sessions", "auth_sessions", "auth_otp_challenges", "workspaces", "audit_log"):
        assert await attempt(f"SELECT id FROM {table}") == "42501", table


async def test_the_definer_policy_applies_to_the_definer_role_only(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    async with admin_engine.connect() as connection:
        roles = (
            (
                await connection.execute(
                    text(
                        "SELECT roles::text FROM pg_policies "
                        "WHERE policyname = 'auth_definer_lookup'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert roles == ["{maya_auth_definer}"]
    async with scoped(app_engine, None) as connection:
        assert (await connection.execute(text("SELECT id FROM users"))).all() == []
