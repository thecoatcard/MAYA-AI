"""Cross-workspace and same-workspace/different-user isolation of the authentication store."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.postgres import PostgresAuthStore
from app.auth.store import AccountConflict, ChallengeConflict
from tests.support.clock import ManualClock
from tests.support.postgres import SeededTenant, sqlstate

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    """This module commits rows, so it works on its own workspaces."""
    return fresh_tenants


CLOCK = ManualClock()


async def new_challenge(store: PostgresAuthStore, tenant: SeededTenant, user_id: object) -> object:
    challenge_id = uuid4()
    async with store.transaction(tenant.workspace_id) as tx:
        await tx.create_challenge(
            challenge_id=challenge_id,
            workspace_id=tenant.workspace_id,
            user_id=user_id,  # type: ignore[arg-type]
            purpose="email_verification",
            code_hmac=b"\x01" * 32,
            expires_at=CLOCK.now() + timedelta(minutes=10),
            now=CLOCK.now(),
        )
    return challenge_id


async def password_version(store: PostgresAuthStore, tenant: SeededTenant) -> datetime:
    async with store.transaction(None) as tx:
        account = await tx.find_account_by_username(tenant.username)
    assert account is not None
    return account.password_changed_at


async def new_session(store: PostgresAuthStore, tenant: SeededTenant) -> tuple[UUID, UUID]:
    session_id, jti = uuid4(), uuid4()
    version = await password_version(store, tenant)
    async with store.transaction(tenant.workspace_id) as tx:
        opened = await tx.create_session(
            session_id=session_id,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            refresh_jti=jti,
            refresh_expires_at=CLOCK.now() + timedelta(days=7),
            absolute_expires_at=CLOCK.now() + timedelta(days=30),
            expected_password_changed_at=version,
            now=CLOCK.now(),
        )
    assert opened
    return session_id, jti


async def test_a_code_issued_to_one_user_cannot_be_used_by_a_colleague(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store, first = PostgresAuthStore(app_engine), tenants[0]
    # Remove the seeded live challenge so the unique index lets us create our own.
    async with store.transaction(first.workspace_id) as tx:
        await tx.invalidate_live_challenges(first.user_id, "email_verification", CLOCK.now())
    await new_challenge(store, first, first.user_id)
    async with store.transaction(first.workspace_id) as tx:
        assert (
            await tx.record_challenge_attempt(
                first.other_user_id, "email_verification", CLOCK.now(), 5
            )
            is None
        )
        mine = await tx.record_challenge_attempt(
            first.user_id, "email_verification", CLOCK.now(), 5
        )
    assert mine is not None and mine.user_id == first.user_id


async def test_a_session_belongs_to_exactly_one_user_even_inside_one_workspace(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store, first = PostgresAuthStore(app_engine), tenants[0]
    session_id, jti = await new_session(store, first)
    colleague = first.other_user_id
    async with store.transaction(first.workspace_id) as tx:
        assert await tx.get_live_session(session_id, colleague, CLOCK.now()) is None
        assert (
            await tx.rotate_refresh(
                session_id=session_id,
                user_id=colleague,
                current_jti=jti,
                new_jti=uuid4(),
                sliding_expires_at=CLOCK.now() + timedelta(days=7),
                now=CLOCK.now(),
            )
            is None
        )
        assert await tx.refresh_generation_of(session_id, colleague) is None
        assert await tx.revoke_session(session_id, colleague, "logout", CLOCK.now()) is False
        assert await tx.revoke_user_sessions(colleague, "password_reset", CLOCK.now()) == 0
        live = await tx.get_live_session(session_id, first.user_id, CLOCK.now())
    assert live is not None and live.refresh_jti == jti


async def test_nothing_is_reachable_from_another_workspace_context(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store = PostgresAuthStore(app_engine)
    first, second = tenants
    session_id, jti = await new_session(store, first)
    for context in (second.workspace_id, None):
        async with store.transaction(context) as tx:
            assert await tx.get_live_session(session_id, first.user_id, CLOCK.now()) is None
            assert (
                await tx.rotate_refresh(
                    session_id=session_id,
                    user_id=first.user_id,
                    current_jti=jti,
                    new_jti=uuid4(),
                    sliding_expires_at=CLOCK.now() + timedelta(days=7),
                    now=CLOCK.now(),
                )
                is None
            )
            assert (
                await tx.revoke_session(session_id, first.user_id, "logout", CLOCK.now()) is False
            )
            assert await tx.latest_challenge_created_at(first.user_id, "password_reset") is None
            assert (
                await tx.record_challenge_attempt(first.user_id, "password_reset", CLOCK.now(), 5)
                is None
            )
            await tx.mark_email_verified(first.user_id, CLOCK.now())
            await tx.set_password(first.user_id, "attacker-chosen-hash", CLOCK.now())
    async with store.transaction(None) as tx:
        account = await tx.find_account_by_username(first.username)
    assert account is not None
    assert account.hashed_password == "seed-hash"  # untouched
    assert account.email_verified_at is None


async def test_rows_cannot_be_written_into_a_workspace_other_than_the_context(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store = PostgresAuthStore(app_engine)
    first, second = tenants
    with pytest.raises(DBAPIError) as caught:
        async with store.transaction(second.workspace_id) as tx:
            await tx.create_challenge(
                challenge_id=uuid4(),
                workspace_id=first.workspace_id,
                user_id=first.other_user_id,
                purpose="password_reset",
                code_hmac=b"\x02" * 32,
                expires_at=CLOCK.now(),
                now=CLOCK.now(),
            )
    assert sqlstate(caught.value) == "42501"
    # A session is only opened for a user the context workspace can see, so a foreign user
    # simply yields nothing (and writes nothing) instead of an error.
    foreign_session = uuid4()
    async with store.transaction(second.workspace_id) as tx:
        opened = await tx.create_session(
            session_id=foreign_session,
            workspace_id=first.workspace_id,
            user_id=first.user_id,
            refresh_jti=uuid4(),
            refresh_expires_at=CLOCK.now(),
            absolute_expires_at=CLOCK.now(),
            expected_password_changed_at=await password_version(store, first),
            now=CLOCK.now(),
        )
    assert opened is False
    async with store.transaction(first.workspace_id) as tx:
        assert await tx.get_live_session(foreign_session, first.user_id, CLOCK.now()) is None


async def test_a_second_live_challenge_is_a_conflict_not_a_crash(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store, second = PostgresAuthStore(app_engine), tenants[1]
    async with store.transaction(second.workspace_id) as tx:
        await tx.invalidate_live_challenges(second.user_id, "password_reset", CLOCK.now())
    await new_challenge_for(store, second, "password_reset")
    with pytest.raises(ChallengeConflict):
        await new_challenge_for(store, second, "password_reset")


async def new_challenge_for(store: PostgresAuthStore, tenant: SeededTenant, purpose: str) -> None:
    async with store.transaction(tenant.workspace_id) as tx:
        await tx.create_challenge(
            challenge_id=uuid4(),
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            purpose=purpose,
            code_hmac=b"\x03" * 32,
            expires_at=CLOCK.now() + timedelta(minutes=10),
            now=CLOCK.now(),
        )


@pytest.mark.parametrize("clash", ["email", "username"])
async def test_registration_conflicts_see_rows_hidden_by_row_level_security(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants, clash: str
) -> None:
    """Unique indexes ignore row-level security, so a duplicate in ANOTHER workspace is still
    detected, and the half-created workspace is rolled back."""
    store, existing = PostgresAuthStore(app_engine), tenants[0]
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT username, email FROM users WHERE id = :id"), {"id": existing.user_id}
            )
        ).one()
    workspace_id, unique = uuid4(), uuid4().hex[:10]
    with pytest.raises(AccountConflict):
        async with store.transaction(workspace_id) as tx:
            await tx.create_account(
                workspace_id=workspace_id,
                user_id=uuid4(),
                workspace_name="Clash",
                slug=f"ws-{unique}",
                username=row[0] if clash == "username" else f"fresh_{unique}",
                email=row[1] if clash == "email" else f"fresh.{unique}@example.com",
                full_name="Clash",
                hashed_password="hash",
                role="admin",
                now=CLOCK.now(),
            )
    async with admin_engine.connect() as connection:
        leftovers = (
            await connection.execute(
                text("SELECT count(*) FROM workspaces WHERE id = :id"), {"id": workspace_id}
            )
        ).scalar_one()
    assert leftovers == 0


async def test_an_account_cannot_be_created_inside_someone_elses_workspace(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    store = PostgresAuthStore(app_engine)
    first, second = tenants
    unique = uuid4().hex[:10]
    with pytest.raises(DBAPIError) as caught:
        async with store.transaction(second.workspace_id) as tx:
            await tx.create_account(
                workspace_id=first.workspace_id,  # not the context workspace
                user_id=uuid4(),
                workspace_name="Takeover",
                slug=f"ws-{unique}",
                username=f"intruder_{unique}",
                email=f"intruder.{unique}@example.com",
                full_name="Intruder",
                hashed_password="hash",
                role="admin",
                now=CLOCK.now(),
            )
    assert sqlstate(caught.value) == "42501"
