"""The provider chain: order, one retry for a transient failure, then the next provider.

Also here: the 10-minute query cache (successes only), the per-user search limit and the result
clean-up (one URL once, near-duplicate titles once). The chain never invents a hit: when every
provider fails or finds nothing, the outcome has no hits and says which providers failed and why.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.mcp.limiter import LeakyBucket
from app.search.health import EngineHealth
from app.search.models import (
    TRANSIENT_CAUSES,
    AnswerProvider,
    NewsProvider,
    NoResultsError,
    ProviderFailure,
    SearchHit,
    SearchOutcome,
    SearchProvider,
    SearchRequest,
    classify_failure,
)
from app.search.names import engine_label
from app.search.query import clean_query

logger = logging.getLogger(__name__)

CACHE_SECONDS: Final = 600.0
CACHE_ENTRIES: Final = 128
RETRY_DELAY_SECONDS: Final = 0.5
DEFAULT_CONCURRENCY: Final = 2  # provider calls at once, per process
_BUCKETS_KEPT: Final = 512
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|igshid$)", re.IGNORECASE)
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def normalise_url(url: str) -> str:
    """A URL as a comparison key: no fragment, no tracking parameters, no ``www.``, no ``/``."""
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)),
    )
    host = (parts.hostname or "").lower().removeprefix("www.")
    return urlunsplit(("https", host, parts.path.rstrip("/") or "", query, ""))


def _title_key(title: str) -> frozenset[str]:
    return frozenset(w for w in (m.lower() for m in _WORDS.findall(title)) if len(w) > 2)


def dedupe_hits(hits: Sequence[SearchHit], *, max_per_domain: int = 2) -> list[SearchHit]:
    """First-seen order; one hit per URL, no near-identical title, at most 2 per domain."""
    seen_urls: set[str] = set()
    seen_titles: list[frozenset[str]] = []
    per_domain: dict[str, int] = {}
    kept: list[SearchHit] = []
    for hit in hits:
        key = normalise_url(hit.url)
        words = _title_key(hit.title)
        if key in seen_urls:
            continue
        if words and any(
            len(words & other) / max(1, len(words | other)) >= 0.8 for other in seen_titles
        ):
            continue
        if per_domain.get(hit.domain, 0) >= max_per_domain:
            continue
        seen_urls.add(key)
        if words:
            seen_titles.append(words)
        per_domain[hit.domain] = per_domain.get(hit.domain, 0) + 1
        kept.append(hit)
    return kept


class SearchRateLimiter:
    """At most ``per_minute`` searches per user per minute (leaky bucket, in this process)."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = max(1, per_minute)
        self._buckets: OrderedDict[str, LeakyBucket] = OrderedDict()

    def allow(self, user_key: str) -> bool:
        bucket = self._buckets.get(user_key)
        if bucket is None:
            bucket = LeakyBucket(capacity=self._per_minute, leak_rate=self._per_minute / 60.0)
            self._buckets[user_key] = bucket
            while len(self._buckets) > _BUCKETS_KEPT:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(user_key)
        return bucket.try_acquire()


class QueryCache:
    """Small LRU with a time limit. Only successful searches are put in."""

    def __init__(
        self,
        *,
        seconds: float = CACHE_SECONDS,
        entries: int = CACHE_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._seconds = seconds
        self._entries = entries
        self._clock = clock
        self._items: OrderedDict[tuple[object, ...], tuple[float, SearchOutcome]] = OrderedDict()

    @staticmethod
    def key(request: SearchRequest) -> tuple[object, ...]:
        return (
            " ".join(_WORDS.findall(request.query.lower())),  # case, spacing, punctuation ignored
            request.news,
            request.max_results,
            request.recency,
            request.region,
            request.lang,
        )

    def get(self, request: SearchRequest) -> SearchOutcome | None:
        key = self.key(request)
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, outcome = item
        if self._clock() - stored_at > self._seconds:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return outcome

    def put(self, request: SearchRequest, outcome: SearchOutcome) -> None:
        if not outcome.hits and outcome.answer is None:
            return  # a failure or an empty search is never cached
        self._items[self.key(request)] = (self._clock(), outcome)
        self._items.move_to_end(self.key(request))
        while len(self._items) > self._entries:
            self._items.popitem(last=False)


class SearchChain:
    """Providers in order. Per run: clean the query, answer from the cache, join an identical
    search already running, then try the providers. A provider that is cooling down (blocked,
    rate limited) is skipped, one that does not fit the query (``matches``) is skipped, and at most
    ``concurrency`` provider calls run at once in this process."""

    def __init__(
        self,
        providers: Mapping[str, SearchProvider | AnswerProvider],
        order: Sequence[str],
        *,
        cache: QueryCache | None = None,
        limiter: SearchRateLimiter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        health: EngineHealth | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._providers = providers
        self._order = list(order)
        self.cache = cache if cache is not None else QueryCache()
        self._limiter = limiter
        self._sleep = sleep
        self.health = health if health is not None else EngineHealth()
        self.concurrency = max(1, concurrency)
        self._gate = asyncio.Semaphore(self.concurrency)
        self._inflight: dict[tuple[object, ...], asyncio.Task[SearchOutcome]] = {}

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def active_names(self, *, without: str | None = None) -> list[str]:
        """General provider names that would be tried: configured, present and (for keyed ones)
        keyed. Intent providers (``matches``: hn, arxiv, stackexchange) answer only some queries
        and do not count."""
        out: list[str] = []
        for name in self._order:
            provider = self._providers.get(name)
            if provider is None or name == without or hasattr(provider, "matches"):
                continue
            available = getattr(provider, "available", None)
            if callable(available) and not available():
                continue
            out.append(name)
        return out

    # ------------------------------------------------------------------ health

    def _cooling_until(self, name: str, provider: object) -> float | None:
        own = getattr(provider, "cooling_until", None)
        if callable(own):
            result = own()
            return float(result) if result is not None else None
        return self.health.cooling_until(name)

    def _keys(self, name: str, provider: object) -> list[str]:
        keys = getattr(provider, "engine_keys", None)
        return list(keys()) if callable(keys) else [name]

    def cooling_count(self) -> int:
        """How many engines are cooling down now (deep research uses fewer parallel queries)."""
        total = 0
        for name in self._order:
            provider = self._providers.get(name)
            if provider is None:
                continue
            total += sum(1 for key in self._keys(name, provider) if self.health.cooling_until(key))
        return total

    def status(self) -> dict[str, Any]:
        """Per-engine state for the status route: ok, cooling until a time, or blocked. No keys."""
        providers: list[dict[str, Any]] = []
        for name in self._order:
            provider = self._providers.get(name)
            if provider is None:
                continue
            available = getattr(provider, "available", None)
            providers.append(
                {
                    "name": name,
                    "available": bool(available()) if callable(available) else True,
                    "intent_only": hasattr(provider, "matches"),
                    "engines": [
                        {**self.health.snapshot(key), "label": engine_label(key)}
                        for key in self._keys(name, provider)
                    ],
                }
            )
        return {
            "order": list(self._order),
            "google_grounding": "gemini" in self._order,
            "concurrency": self.concurrency,
            "providers": providers,
        }

    # ------------------------------------------------------------------ run

    async def run(
        self,
        request: SearchRequest,
        *,
        user_key: str = "anonymous",
        workspace_id: UUID | None = None,
        skip: frozenset[str] = frozenset(),
    ) -> SearchOutcome:
        clean = clean_query(request.query)
        if clean and clean != request.query:
            request = replace(request, query=clean)
        cached = self.cache.get(request)
        if cached is not None:
            return _copy(cached, request, cached=True)
        if self._limiter is not None and not self._limiter.allow(user_key):
            return SearchOutcome(request=request, limited=True)
        key = (*QueryCache.key(request), tuple(sorted(skip)))
        running = self._inflight.get(key)
        if running is None:
            running = asyncio.ensure_future(self._execute(request, workspace_id, skip))
            self._inflight[key] = running
            running.add_done_callback(lambda _task: self._inflight.pop(key, None))
        return _copy(await asyncio.shield(running), request)

    async def _execute(
        self, request: SearchRequest, workspace_id: UUID | None, skip: frozenset[str]
    ) -> SearchOutcome:
        outcome = SearchOutcome(request=request)
        tried: list[str] = []
        for name in self._order:
            provider = self._providers.get(name)
            if provider is None or name in skip:
                continue
            available = getattr(provider, "available", None)
            if callable(available) and not available():
                continue  # no key: skipped silently
            fits = getattr(provider, "matches", None)
            if callable(fits) and not fits(request):
                continue  # an intent provider and this query is not its kind
            tried.append(name)
            if self._cooling_until(name, provider) is not None:
                continue  # cooling down after blocks: not asked again yet
            managed = bool(getattr(provider, "manages_health", False))
            try:
                async with self._gate:
                    await self._try(provider, request, outcome, workspace_id)
            except Exception as error:
                cause = classify_failure(error)
                if not isinstance(error, NoResultsError) and cause not in ("no_results", "cooling"):
                    logger.warning("search_provider_failed provider=%s cause=%s", name, cause)
                    if not managed:
                        self.health.failed(name, cause)
                outcome.failures.append(ProviderFailure(name, cause))
                continue
            if not managed:
                self.health.succeeded(name)
            outcome.provider = name
            outcome.engine = (outcome.hits[0].engine if outcome.hits else None) or name
            break
        if not outcome.hits and outcome.answer is None:
            self._mark_cooling(outcome, tried)
        self.cache.put(request, outcome)
        return outcome

    def _mark_cooling(self, outcome: SearchOutcome, tried: list[str]) -> None:
        """When every provider that fits this query is cooling down, say when to retry."""
        untils: list[float] = []
        for name in tried:
            provider = self._providers.get(name)
            until = self._cooling_until(name, provider) if provider is not None else None
            if until is None:
                return
            untils.append(until)
        if untils:
            outcome.retry_in_seconds = max(1, math.ceil(min(untils) - self.health.now()))

    async def _try(
        self,
        provider: SearchProvider | AnswerProvider,
        request: SearchRequest,
        outcome: SearchOutcome,
        workspace_id: UUID | None,
    ) -> None:
        """Call one provider; a transient failure is retried once, anything else is raised."""
        try:
            await self._call(provider, request, outcome, workspace_id)
        except Exception as error:
            if not getattr(provider, "retry_transient", True):
                raise
            if classify_failure(error) not in TRANSIENT_CAUSES:
                raise
            await self._sleep(RETRY_DELAY_SECONDS)
            await self._call(provider, request, outcome, workspace_id)

    @staticmethod
    async def _call(
        provider: SearchProvider | AnswerProvider,
        request: SearchRequest,
        outcome: SearchOutcome,
        workspace_id: UUID | None,
    ) -> None:
        if isinstance(provider, AnswerProvider):
            answer = await provider.answer(request.query, workspace_id=workspace_id)
            outcome.answer, outcome.hits = answer.text, list(answer.hits)
            return
        if request.news and isinstance(provider, NewsProvider):
            hits = await provider.news(
                request.query,
                max_results=request.max_results,
                recency=request.recency,
                region=request.region,
                lang=request.lang,
            )
        elif isinstance(provider, SearchProvider):
            hits = await provider.search(
                request.query,
                max_results=request.max_results,
                recency=request.recency,
                region=request.region,
                lang=request.lang,
            )
        else:  # pragma: no cover - a provider with neither interface is a wiring error
            raise NoResultsError
        hits = dedupe_hits(hits)
        if not hits:
            raise NoResultsError
        outcome.hits = hits


def _copy(source: SearchOutcome, request: SearchRequest, *, cached: bool = False) -> SearchOutcome:
    """A private copy: callers add hits and set fields, and a shared run must not see that."""
    return SearchOutcome(
        request=request,
        hits=list(source.hits),
        provider=source.provider,
        answer=source.answer,
        failures=list(source.failures),
        cached=cached,
        widened_to=source.widened_to,
        limited=source.limited,
        engine=source.engine,
        retry_in_seconds=source.retry_in_seconds,
    )
