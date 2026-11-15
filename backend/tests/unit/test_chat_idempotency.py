"""Deterministic interleavings for the Redis-backed chat idempotency store."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.chat_idempotency import (
    IN_PROGRESS_TTL_SECONDS,
    TERMINAL_TTL_SECONDS,
    IdempotencyRecord,
    IdempotencyScope,
    IdempotencyUnavailable,
    RedisIdempotencyStore,
    State,
    idempotency_key_digest,
)

SCOPE = IdempotencyScope(
    UUID("9db2be48-5c0d-4c71-8a67-9e45cb3bef81"),
    UUID("3c6677fd-a48d-4913-9d0d-b5de10b82301"),
    "abcdefgh12345678",
)
FIRST = IdempotencyRecord(
    state="in_progress",
    request_hash="0123456789abcdef" * 4,
    session_id=None,
    user_message_id=UUID("cc47f302-8aa4-4db4-b0aa-df4b997e2101"),
    assistant_message_id=UUID("cc47f302-8aa4-4db4-b0aa-df4b997e2102"),
)
SECOND = replace(
    FIRST,
    user_message_id=UUID("cc47f302-8aa4-4db4-b0aa-df4b997e2201"),
    assistant_message_id=UUID("cc47f302-8aa4-4db4-b0aa-df4b997e2202"),
)
SESSION = UUID("519f649f-ae09-4412-b9d3-183f7f03e405")

# The fake executes only these specific scripts. Checking the script as well as simulating the
# Redis comparison keeps an unconditional SET/DEL from masquerading as a successful atomic test.
COMPARE_SET = """\
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0"""
COMPARE_DELETE = """\
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0"""


class InterleavingRedis:
    """A single-key fake; the armed callback runs immediately before the next mutation."""

    def __init__(self, *, return_bytes: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.before_mutation: Callable[[], None] | None = None
        self.return_bytes = return_bytes
        self.get_error: Exception | None = None
        self.eval_error: Exception | None = None

    def install(self, record: IdempotencyRecord, ttl: int = IN_PROGRESS_TTL_SECONDS) -> None:
        self.values[SCOPE.redis_key] = record.to_json()
        self.ttls[SCOPE.redis_key] = ttl

    def record(self) -> IdempotencyRecord | None:
        raw = self.values.get(SCOPE.redis_key)
        return None if raw is None else IdempotencyRecord.from_json(raw)

    def _interleave(self) -> None:
        callback, self.before_mutation = self.before_mutation, None
        if callback is not None:
            callback()

    async def get(self, name: str) -> str | bytes | None:
        if self.get_error is not None:
            raise self.get_error
        raw = self.values.get(name)
        return raw.encode() if raw is not None and self.return_bytes else raw

    async def set(self, name: str, value: str, *, nx: bool = False, ex: int) -> bool | None:
        self._interleave()
        if nx and name in self.values:
            return None
        self.values[name] = value
        self.ttls[name] = ex
        return True

    async def delete(self, name: str) -> int:
        self._interleave()
        existed = name in self.values
        self.values.pop(name, None)
        self.ttls.pop(name, None)
        return int(existed)

    async def eval(
        self, script: str, key_count: int, name: str, expected: str | bytes, *args: object
    ) -> int:
        self._interleave()
        if self.eval_error is not None:
            raise self.eval_error
        assert key_count == 1
        assert script.strip() in (COMPARE_SET, COMPARE_DELETE)
        expected_text = expected.decode() if isinstance(expected, bytes) else expected
        if self.values.get(name) != expected_text:
            return 0
        if script.strip() == COMPARE_DELETE:
            assert args == ()
            self.values.pop(name)
            self.ttls.pop(name)
        else:
            value, ttl = args
            assert isinstance(value, str) and isinstance(ttl, int)
            self.values[name] = value
            self.ttls[name] = ttl
        return 1


class ExpiryBarrierRedis(InterleavingRedis):
    """A fake Redis clock and a barrier between the read and atomic mutation."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0
        self.expires: dict[str, float] = {}
        self.read_captured = asyncio.Event()
        self.continue_read = asyncio.Event()
        self.pause_after_read = False

    def install(self, record: IdempotencyRecord, ttl: int = IN_PROGRESS_TTL_SECONDS) -> None:
        super().install(record, ttl)
        self.expires[SCOPE.redis_key] = self.now + ttl

    def _expire(self, name: str) -> None:
        if self.expires.get(name, float("inf")) <= self.now:
            self.values.pop(name, None)
            self.ttls.pop(name, None)
            self.expires.pop(name, None)

    async def get(self, name: str) -> str | bytes | None:
        self._expire(name)
        raw = await super().get(name)
        if self.pause_after_read:
            self.pause_after_read = False
            self.read_captured.set()
            await self.continue_read.wait()
        return raw

    async def set(self, name: str, value: str, *, nx: bool = False, ex: int) -> bool | None:
        self._interleave()
        self._expire(name)
        if nx and name in self.values:
            return None
        self.values[name] = value
        self.ttls[name] = ex
        self.expires[name] = self.now + ex
        return True

    async def eval(
        self, script: str, key_count: int, name: str, expected: str | bytes, *args: object
    ) -> int:
        self._interleave()
        self._expire(name)
        return await super().eval(script, key_count, name, expected, *args)


def store(fake: InterleavingRedis) -> RedisIdempotencyStore:
    return RedisIdempotencyStore(cast(Redis, fake))


def test_durable_key_digest_is_keyed_and_scoped_without_retaining_the_raw_key() -> None:
    secret = b"unit-test-only-secret"
    digest = idempotency_key_digest(secret, SCOPE)

    assert len(digest) == 64
    assert digest != SCOPE.key
    assert idempotency_key_digest(secret, SCOPE) == digest
    assert (
        idempotency_key_digest(
            secret, replace(SCOPE, user_id=UUID("c9c95b04-baa8-4f82-94e1-17e1ab13c243"))
        )
        != digest
    )
    assert (
        idempotency_key_digest(
            secret, replace(SCOPE, workspace_id=UUID("c9c95b04-baa8-4f82-94e1-17e1ab13c243"))
        )
        != digest
    )
    assert idempotency_key_digest(b"different-key-secret", SCOPE) != digest


def result(state: State) -> IdempotencyRecord:
    return replace(
        FIRST,
        state=state,
        session_id=SESSION,
        finish_reason="stop" if state in ("completed", "interrupted") else None,
        error_code="internal_error" if state == "failed" else None,
    )


@pytest.mark.parametrize("return_bytes", [False, True])
async def test_lookup_reads_without_claiming_or_refreshing_the_key(return_bytes: bool) -> None:
    fake = InterleavingRedis(return_bytes=return_bytes)
    subject = store(fake)
    assert await subject.lookup(SCOPE) is None
    fake.install(FIRST, ttl=37)

    assert await subject.lookup(SCOPE) == FIRST
    assert fake.ttls[SCOPE.redis_key] == 37


async def test_lookup_fails_closed_for_an_unreadable_or_unavailable_record() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    fake.values[SCOPE.redis_key] = "not a record"
    with pytest.raises(IdempotencyUnavailable):
        await subject.lookup(SCOPE)
    fake.get_error = RedisError("simulated failure")
    with pytest.raises(IdempotencyUnavailable):
        await subject.lookup(SCOPE)


@pytest.mark.parametrize("state", ["in_progress", "completed", "interrupted", "failed"])
@pytest.mark.parametrize("return_bytes", [False, True])
async def test_late_save_cannot_replace_a_claim_reacquired_after_the_read(
    state: State, return_bytes: bool
) -> None:
    fake = InterleavingRedis(return_bytes=return_bytes)
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    fake.before_mutation = lambda: fake.install(SECOND)

    await subject.save(SCOPE, result(state))

    assert fake.record() == SECOND
    assert fake.ttls[SCOPE.redis_key] == IN_PROGRESS_TTL_SECONDS


async def test_save_does_not_recreate_a_claim_after_expiry() -> None:
    fake = InterleavingRedis()
    fake.install(SECOND)

    assert not await store(fake).save(SCOPE, result("completed"))

    assert fake.record() == SECOND
    assert fake.ttls[SCOPE.redis_key] == IN_PROGRESS_TTL_SECONDS


@pytest.mark.parametrize("return_bytes", [False, True])
async def test_release_cannot_delete_a_claim_reacquired_after_the_read(return_bytes: bool) -> None:
    fake = InterleavingRedis(return_bytes=return_bytes)
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    fake.before_mutation = lambda: fake.install(SECOND)

    await subject.release(SCOPE, FIRST)

    assert fake.record() == SECOND
    assert fake.ttls[SCOPE.redis_key] == IN_PROGRESS_TTL_SECONDS


async def test_release_cannot_delete_a_result_finalized_after_the_read() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    completed = result("completed")
    fake.before_mutation = lambda: fake.install(completed, TERMINAL_TTL_SECONDS)

    await subject.release(SCOPE, FIRST)

    assert fake.record() == completed
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS


async def test_a_late_refresh_cannot_turn_a_finished_result_back_into_a_claim() -> None:
    fake = InterleavingRedis()
    completed = result("completed")
    fake.install(completed, TERMINAL_TTL_SECONDS)

    await store(fake).save(SCOPE, result("in_progress"))

    assert fake.record() == completed
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS


async def test_a_refresh_racing_finalization_cannot_revert_the_result() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    completed = result("completed")
    fake.before_mutation = lambda: fake.install(completed, TERMINAL_TTL_SECONDS)

    await subject.save(SCOPE, result("in_progress"))

    assert fake.record() == completed
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS


async def test_a_late_save_cannot_replace_a_new_attempts_finished_result() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    successor_result = replace(SECOND, state="completed", session_id=SESSION, finish_reason="stop")
    fake.before_mutation = lambda: fake.install(successor_result, TERMINAL_TTL_SECONDS)

    await subject.save(SCOPE, result("completed"))

    assert fake.record() == successor_result
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS


async def test_a_same_attempt_refresh_racing_a_final_save_is_retried() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    fake.before_mutation = lambda: fake.install(replace(FIRST, session_id=SESSION))

    await subject.save(SCOPE, result("completed"))

    assert fake.record() == result("completed")
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS


async def test_release_retries_if_the_same_attempt_was_refreshed_after_the_read() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    fake.before_mutation = lambda: fake.install(replace(FIRST, session_id=SESSION))

    await subject.release(SCOPE, FIRST)

    assert fake.record() is None


async def test_expiry_between_save_read_and_compare_cannot_replace_the_new_claim() -> None:
    fake = ExpiryBarrierRedis()
    subject = store(fake)
    fake.install(FIRST, ttl=1)
    fake.pause_after_read = True

    saving = asyncio.create_task(subject.save(SCOPE, result("completed")))
    await fake.read_captured.wait()
    fake.now = 1
    fake.install(SECOND)
    fake.continue_read.set()
    await saving

    assert fake.record() == SECOND


async def test_expiry_between_release_read_and_compare_cannot_delete_the_new_claim() -> None:
    fake = ExpiryBarrierRedis()
    subject = store(fake)
    fake.install(FIRST, ttl=1)
    fake.pause_after_read = True

    releasing = asyncio.create_task(subject.release(SCOPE, FIRST))
    await fake.read_captured.wait()
    fake.now = 1
    fake.install(SECOND)
    fake.continue_read.set()
    await releasing

    assert fake.record() == SECOND


async def test_owned_updates_keep_ttls_but_expired_keys_cannot_be_resurrected() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    assert await subject.save(SCOPE, result("in_progress"))
    assert fake.record() == result("in_progress")
    assert fake.ttls[SCOPE.redis_key] == IN_PROGRESS_TTL_SECONDS
    assert await subject.save(SCOPE, result("completed"))
    assert fake.record() == result("completed")
    assert fake.ttls[SCOPE.redis_key] == TERMINAL_TTL_SECONDS
    await subject.release(SCOPE, FIRST)
    assert fake.record() == result("completed")

    fake.values.clear()  # the result expired; the existing behavior permits a late save
    fake.ttls.clear()
    assert not await subject.save(SCOPE, result("completed"))
    assert fake.record() is None


async def test_malformed_records_fail_closed_and_are_not_deleted() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    fake.values[SCOPE.redis_key] = "not a record"

    with pytest.raises(IdempotencyUnavailable):
        await subject.claim(SCOPE, FIRST)
    with pytest.raises(IdempotencyUnavailable):
        await subject.save(SCOPE, result("completed"))
    await subject.release(SCOPE, FIRST)
    assert fake.values[SCOPE.redis_key] == "not a record"


async def test_repeated_same_owner_contention_fails_closed_instead_of_overwriting() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None

    def refresh_again() -> None:
        current = fake.record()
        assert current is not None
        session_id = SESSION if current.session_id is None else None
        fake.install(replace(current, session_id=session_id))
        fake.before_mutation = refresh_again

    fake.before_mutation = refresh_again
    with pytest.raises(IdempotencyUnavailable):
        await subject.save(SCOPE, result("completed"))
    current = fake.record()
    assert current is not None and current.state == "in_progress"


async def test_atomic_save_errors_fail_closed_and_release_remains_best_effort() -> None:
    fake = InterleavingRedis()
    subject = store(fake)
    assert await subject.claim(SCOPE, FIRST) is None
    fake.eval_error = RedisError("simulated failure")

    with pytest.raises(IdempotencyUnavailable):
        await subject.save(SCOPE, result("completed"))
    await subject.release(SCOPE, FIRST)
    assert fake.record() == FIRST
