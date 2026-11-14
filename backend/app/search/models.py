"""Shared types of the native search stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.core.provider_errors import failure_cause

Recency = Literal["day", "week", "month", "year"]
RECENCY_VALUES: Final = ("day", "week", "month", "year")
RECENCY_HOURS: Final[dict[str, float]] = {"day": 36.0, "week": 24 * 7.5, "month": 24 * 32.0}


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    published: str | None = None
    source_name: str | None = None
    engine: str | None = None  # the real engine that returned this hit (see ENGINE_LABELS)

    @property
    def domain(self) -> str:
        host = (urlsplit(self.url).hostname or "").lower()
        return host.removeprefix("www.")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    news: bool = False
    max_results: int = 8
    recency: str | None = None
    region: str | None = None
    lang: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """A provider that did not answer, and why (a plain cause, never provider text)."""

    provider: str
    cause: str


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    """What an answering provider (Gemini grounding) returns: written text plus its sources."""

    text: str
    hits: list[SearchHit]


@dataclass(slots=True)
class SearchOutcome:
    request: SearchRequest
    hits: list[SearchHit] = field(default_factory=list)
    provider: str | None = None
    answer: str | None = None
    failures: list[ProviderFailure] = field(default_factory=list)
    cached: bool = False
    widened_to: str | None = None  # the wider window used when the first one had no result
    limited: bool = False  # the per-user search limit stopped this search
    engine: str | None = None  # the real engine of the hits (a key of ENGINE_LABELS)
    retry_in_seconds: int | None = None  # every engine is cooling down: the earliest retry


class ProviderUnavailableError(Exception):
    """A provider cannot answer now. ``cause`` is a plain cause (see ``classify_failure``)."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


class NoResultsError(Exception):
    """The provider answered with nothing."""


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]: ...


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    async def news(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]: ...


@runtime_checkable
class AnswerProvider(Protocol):
    """A provider that writes its own answer from its own sources (Gemini grounding)."""

    name: str

    async def answer(self, query: str, *, workspace_id: UUID | None = None) -> ProviderAnswer: ...


TRANSIENT_CAUSES: Final = frozenset({"network", "timeout", "server"})


def classify_failure(error: BaseException) -> str:
    """A coarse plain cause: ``rate_limited``, ``auth``, ``network``, ``timeout``, ``server``,
    ``blocked`` or ``unknown``. Types and status codes only, never message text."""
    if isinstance(error, ProviderUnavailableError):
        return error.cause
    if isinstance(error, NoResultsError):
        return "no_results"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (408, 429, 503):
            return "rate_limited"
        if status in (401, 403):
            return "auth"
        if 500 <= status < 600:
            return "server"
        return "unknown"
    name = type(error).__name__
    if name == "RatelimitException":
        return "rate_limited"
    if name == "TimeoutException" or isinstance(error, TimeoutError):
        return "timeout"
    if name == "DDGSException":
        # The library hides the cause behind one class; "No results found" is an empty answer.
        return "no_results" if "no results" in str(error).lower() else "blocked"
    return failure_cause(error)
