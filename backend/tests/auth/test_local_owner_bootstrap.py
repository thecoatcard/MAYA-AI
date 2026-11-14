"""Local-owner bootstrap (ADR-0014): every refusal path and the one success path."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from dotenv import dotenv_values
from redis.asyncio import Redis

from app.auth.errors import BootstrapUnavailable, InvalidCode, RateLimited
from app.auth.models import TokenPair
from app.auth.pending import PendingSignup, RedisPendingSignups
from app.auth.service import BootstrapCommand, CompleteSignupCommand, is_loopback_address
from app.auth.store import AuthTransaction
from app.core.security import PasswordPolicyViolation
from tests.support.auth import ADDRESS, GOOD_PASSWORD, Harness, build_harness
from tests.support.fake_store import FakeAuthStore

LOCAL = "127.0.0.1"


def command(**overrides: str) -> BootstrapCommand:
    values = {"full_name": "Local Owner", "email": "owner@example.com", "password": GOOD_PASSWORD}
    values.update(overrides)
    return BootstrapCommand(**values)


def enabled() -> Harness:
    return build_harness(FakeAuthStore(), local_owner_bootstrap=True)


def fake(harness: Harness) -> FakeAuthStore:
    assert isinstance(harness.store, FakeAuthStore)
    return harness.store


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("203.0.113.7", False),
        ("192.168.1.10", False),
        ("0.0.0.0", False),
        ("::", False),
        ("unknown", False),
        ("testclient", False),
        ("localhost", False),
        ("", False),
    ],
)
def test_only_literal_loopback_addresses_count_as_local(address: str, expected: bool) -> None:
    assert is_loopback_address(address) is expected


async def test_the_flag_defaults_to_off_and_then_nothing_is_available() -> None:
    harness = build_harness(FakeAuthStore())
    assert await harness.service.bootstrap_status(LOCAL) == (False, "disabled")
    with pytest.raises(BootstrapUnavailable):
        await harness.service.bootstrap(command(), LOCAL)
    assert fake(harness).users == {}


async def test_a_non_loopback_peer_is_refused_even_when_enabled() -> None:
    harness = enabled()
    assert await harness.service.bootstrap_status(ADDRESS) == (False, "disabled")
    with pytest.raises(BootstrapUnavailable):
        await harness.service.bootstrap(command(), ADDRESS)
    assert fake(harness).users == {}


async def test_first_call_creates_a_verified_admin_and_a_working_session() -> None:
    harness = enabled()
    assert await harness.service.bootstrap_status(LOCAL) == (True, None)
    pair = await harness.service.bootstrap(command(), LOCAL)
    assert isinstance(pair, TokenPair)
    principal = await harness.service.authenticate(pair.access_token)
    assert principal.role == "admin"
    user = next(iter(fake(harness).users.values()))
    assert user.email == "owner@example.com"
    assert user.email_verified_at is not None
    assert harness.email.sent == []  # no code, no mail
    login = await harness.service.login("owner@example.com", GOOD_PASSWORD, LOCAL)
    assert login.access_token


async def test_a_second_call_after_the_first_user_exists_is_refused() -> None:
    harness = enabled()
    await harness.service.bootstrap(command(), LOCAL)
    assert await harness.service.bootstrap_status(LOCAL) == (False, "already_initialised")
    with pytest.raises(BootstrapUnavailable):
        await harness.service.bootstrap(command(email="second@example.com"), LOCAL)
    assert len(fake(harness).users) == 1


async def test_any_existing_account_blocks_it_including_a_registered_one() -> None:
    harness = enabled()
    await harness.register()  # a normal, not yet verified account
    with pytest.raises(BootstrapUnavailable):
        await harness.service.bootstrap(command(), LOCAL)


async def test_the_same_password_policy_as_registration_applies() -> None:
    harness = enabled()
    with pytest.raises(PasswordPolicyViolation) as short:
        await harness.service.bootstrap(command(password="short"), LOCAL)
    with pytest.raises(PasswordPolicyViolation) as reuse:
        await harness.service.bootstrap(command(password="owner@example.com"), LOCAL)
    assert short.value.code == "password_too_short"
    assert reuse.value.code
    assert fake(harness).users == {}


async def test_attempts_are_rate_limited_per_address() -> None:
    harness = enabled()
    for _ in range(5):
        with pytest.raises(PasswordPolicyViolation):
            await harness.service.bootstrap(command(password="short"), LOCAL)
    with pytest.raises(RateLimited):
        await harness.service.bootstrap(command(), LOCAL)
    assert fake(harness).users == {}


async def test_an_unreadable_user_count_fails_closed() -> None:
    harness = enabled()
    store = fake(harness)

    @asynccontextmanager
    async def broken(workspace_id: UUID | None) -> AsyncIterator[AuthTransaction]:
        raise RuntimeError("function public.auth_any_account_exists() does not exist")
        yield  # pragma: no cover

    store.transaction = broken  # type: ignore[method-assign]
    assert await harness.service.bootstrap_status(LOCAL) == (False, "not_ready")
    with pytest.raises(BootstrapUnavailable):
        await harness.service.bootstrap(command(), LOCAL)


async def test_signup_state_expires_with_the_code() -> None:
    harness = build_harness(FakeAuthStore())
    message = await harness.service.send_signup_otp("new@example.com", ADDRESS)
    assert message is not None
    await harness.service.deliver(message)
    code = harness.email.codes_for("new@example.com")[-1]
    harness.clock.advance(seconds=11 * 60)  # past the ten-minute lifetime
    with pytest.raises(InvalidCode):
        await harness.service.complete_signup(
            CompleteSignupCommand("new@example.com", code, "newuser", GOOD_PASSWORD, "New"),
            ADDRESS,
        )


@pytest.fixture
async def real_redis() -> AsyncIterator[Redis]:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")
    assert url.startswith(("redis://127.0.0.1", "redis://localhost"))
    client = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.redis
async def test_pending_signups_in_redis_survive_a_new_store_instance_and_expire(
    real_redis: Redis,
) -> None:
    key = b"k" * 32
    email = f"{uuid4().hex}@example.com"
    now = build_harness(FakeAuthStore()).clock.now()
    pending = PendingSignup(uuid4(), email, b"\x01" * 32, now, now + timedelta(minutes=10))
    store = RedisPendingSignups(real_redis, key)
    await store.put(pending)
    try:
        restarted = RedisPendingSignups(real_redis, key)  # a new process, same Redis
        found = await restarted.get(email)
        assert found is not None
        assert (found.challenge_id, found.code_hmac, found.attempts) == (
            pending.challenge_id,
            pending.code_hmac,
            0,
        )
        await restarted.record_attempt(email)
        await restarted.record_attempt(email)
        reread = await store.get(email)
        assert reread is not None and reread.attempts == 2
        names = [name async for name in real_redis.scan_iter("maya:signup:*")]
        assert all(email not in name for name in names)  # keyed digest, never the address
        ttls = [await real_redis.ttl(name) for name in names]
        assert any(0 < ttl <= 600 for ttl in ttls)
        await store.delete(email)
        assert await store.get(email) is None
        await store.record_attempt(email)  # a missing key is not recreated
        assert await store.get(email) is None
    finally:
        await store.delete(email)
