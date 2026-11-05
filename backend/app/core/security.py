"""Password policy and Argon2id hashing."""

import asyncio
import re
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from argon2 import Parameters, PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
# Raw input is capped before normalization so a huge body cannot cost CPU.
PASSWORD_INPUT_LIMIT = 256
USERNAME_PATTERN = re.compile(r"[a-z0-9_.-]{3,32}")
DEFAULT_HASH_CONCURRENCY = 4
DEFAULT_HASH_QUEUE = 64
T = TypeVar("T")
_PLACEHOLDER_INPUT = "maya-equal-work-placeholder"


class Overloaded(RuntimeError):
    """Too many password operations are already waiting; the caller should answer 503."""

    def __init__(self) -> None:
        super().__init__("Too many password operations are waiting")


class PasswordPolicyViolation(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def validate_password_policy(password: str, *, forbidden: Iterable[str] = ()) -> None:
    """Length-based policy: no composition rules; reject only the supplied identifiers."""
    normalized = normalize_password(password)
    if len(normalized) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyViolation("password_too_short")
    if len(normalized) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyViolation("password_too_long")
    folded = normalized.casefold()
    for value in forbidden:
        if value and folded == normalize_password(value).casefold():
            raise PasswordPolicyViolation("password_matches_identifier")


class PasswordService:
    """Argon2id hashing on a dedicated thread pool with a bounded queue.

    The pool is separate from the event loop's default executor, so slow hashing (or a hung
    SMTP server) cannot starve the other. At most ``concurrency`` hashes run at once and at most
    ``queue`` more wait; beyond that callers get ``Overloaded`` instead of unbounded memory use.
    """

    def __init__(
        self,
        parameters: Parameters = profiles.RFC_9106_LOW_MEMORY,
        *,
        concurrency: int = DEFAULT_HASH_CONCURRENCY,
        queue: int = DEFAULT_HASH_QUEUE,
    ) -> None:
        self._parameters = parameters
        self._hasher = PasswordHasher.from_parameters(parameters)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="maya-hash")
        self._capacity = concurrency + queue
        self._pending = 0
        self._placeholder_hash: str | None = None

    @property
    def parameters(self) -> Parameters:
        return self._parameters

    async def _run(self, function: Callable[..., T], *arguments: object) -> T:
        if self._pending >= self._capacity:
            raise Overloaded
        self._pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(
                self._executor, function, *arguments
            )
        finally:
            self._pending -= 1

    async def hash(self, password: str) -> str:
        return await self._run(self._hasher.hash, normalize_password(password))

    async def verify(self, hashed: str, password: str) -> bool:
        return await self._run(self._verify, hashed, normalize_password(password))

    async def verify_unknown_account(self, password: str) -> None:
        """Spend the same work as a real verification when the account does not exist."""
        if self._placeholder_hash is None:
            self._placeholder_hash = await self.hash(_PLACEHOLDER_INPUT)
        await self.verify(self._placeholder_hash, password)

    def needs_rehash(self, hashed: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(hashed)
        except InvalidHashError:
            return True

    def _verify(self, hashed: str, password: str) -> bool:
        try:
            return self._hasher.verify(hashed, password)
        except (VerificationError, InvalidHashError):
            return False
