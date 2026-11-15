"""Search providers: ddgs engine rotation (no key), Brave, Tavily and Gemini grounding (opt-in).

More free providers (SearXNG, Wikipedia, Hacker News, arXiv, Stack Exchange) are in
``app/search/providers_free.py``.

Every provider returns ``SearchHit`` lists and raises on failure; the chain decides what to do
about it. Keys are read once from settings and never logged. The ``ddgs`` library is imported when
first used, so the rest of the application starts without it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any, Final
from uuid import UUID

import httpx
from pydantic import SecretStr

from app.search.health import EngineHealth, Pacer
from app.search.models import (
    NoResultsError,
    ProviderAnswer,
    ProviderUnavailableError,
    SearchHit,
    classify_failure,
)
from app.search.names import DDGS_ENGINES

logger = logging.getLogger(__name__)

_DDGS_LIMIT: Final = {"day": "d", "week": "w", "month": "m", "year": "y"}
_BRAVE_FRESHNESS: Final = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
_BRAVE_URL: Final = "https://api.search.brave.com/res/v1"
_TAVILY_URL: Final = "https://api.tavily.com/search"
_HTTP_TIMEOUT: Final = 10.0
_MAX_SNIPPET: Final = 500


def _text(value: Any, limit: int = _MAX_SNIPPET) -> str:
    return " ".join(str(value or "").split())[:limit]


def _region(region: str | None, lang: str | None) -> str:
    """DuckDuckGo region code such as ``in-en``; ``wt-wt`` (worldwide) when not given."""
    if region and lang:
        return f"{region.lower()}-{lang.lower()}"
    return region.lower() if region and "-" in region else "wt-wt"


def _published_text(value: Any) -> str | None:
    text = _text(value, 40)
    return text or None


class DdgsProvider:
    """Free metasearch through the ``ddgs`` library, with an explicit rotation of NON-Google
    engines (``MAYA_DDGS_ENGINES``, default duckduckgo, bing, brave, yahoo, mojeek).

    Each engine has its own health: a blocked, rate-limited or timed-out engine cools down
    (2, 4, ... 30 minutes) and the next engine is asked, so one blocked engine never blocks the
    chain. Requests to one engine are spaced. A hit carries the engine that returned it."""

    name = "ddgs"
    manages_health = True  # the chain must not record this provider's failures itself
    retry_transient = False  # the rotation already moved on to other engines

    def __init__(
        self,
        client_factory: Callable[[], Any] | None = None,
        *,
        timeout_seconds: float = 6.0,
        engines: Sequence[str] = DDGS_ENGINES,
        health: EngineHealth | None = None,
        pacer: Pacer | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._factory = client_factory
        self._timeout = timeout_seconds
        self._engines = [e for e in engines if e != "google"]
        self._health = health if health is not None else EngineHealth()
        self._pacer = pacer if pacer is not None else Pacer()
        self._max_attempts = max(1, max_attempts)

    def available(self) -> bool:
        return True

    def engine_keys(self) -> list[str]:
        return list(self._engines)

    def cooling_until(self) -> float | None:
        """When an engine is ready again, if every engine is cooling; else ``None``."""
        return self._health.earliest(self._engines)

    def _client(self) -> Any:
        if self._factory is not None:
            return self._factory()
        from ddgs import DDGS  # lazy: the library is only needed when a search runs

        return DDGS(timeout=int(self._timeout))

    def _known(self, kind: str) -> list[str]:
        """The configured engines that the installed library knows for this kind of search (an
        unknown backend name would make the library use ALL engines, so it is never passed)."""
        if self._factory is not None:
            return list(self._engines)
        try:
            from ddgs.engines import ENGINES

            present = ENGINES.get("news" if kind == "news" else "text", {})
        except Exception:  # noqa: BLE001 - an unreadable registry means trust the configuration
            return list(self._engines)
        return [engine for engine in self._engines if engine in present]

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        engine, rows = await self._call("text", query, max_results, recency, region, lang)
        hits = [
            SearchHit(
                title=_text(row.get("title"), 200),
                url=str(row.get("href") or row.get("url") or ""),
                snippet=_text(row.get("body")),
                engine=engine,
            )
            for row in rows
        ]
        return [hit for hit in hits if hit.url]

    async def news(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        engine, rows = await self._call("news", query, max_results, recency, region, lang)
        hits = [
            SearchHit(
                title=_text(row.get("title"), 200),
                url=str(row.get("url") or row.get("href") or ""),
                snippet=_text(row.get("body")),
                published=_published_text(row.get("date")),
                source_name=_text(row.get("source"), 80) or None,
                engine=engine,
            )
            for row in rows
        ]
        return [hit for hit in hits if hit.url]

    async def _call(
        self,
        kind: str,
        query: str,
        max_results: int,
        recency: str | None,
        region: str | None,
        lang: str | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        arguments: dict[str, Any] = {
            "region": _region(region, lang),
            "safesearch": "moderate",
            "max_results": max_results,
        }
        if recency in _DDGS_LIMIT:
            arguments["timelimit"] = _DDGS_LIMIT[recency]

        def run(engine: str) -> list[dict[str, Any]]:
            client = self._client()
            method = client.news if kind == "news" else client.text
            return list(method(query, backend=engine, **arguments) or [])

        ready = [e for e in self._known(kind) if self._health.cooling_until(e) is None]
        if not ready:
            raise ProviderUnavailableError("cooling")
        last_error: Exception | None = None
        empty = False
        for engine in ready[: self._max_attempts]:
            await self._pacer.wait(engine)
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(run, engine), timeout=self._timeout + 2
                )
            except Exception as error:  # noqa: BLE001 - classified, never shown raw
                cause = classify_failure(error)
                if cause == "no_results":
                    empty = True
                    self._health.succeeded(engine)
                    continue
                seconds = self._health.failed(engine, cause)
                logger.warning(
                    "search_engine_failed engine=%s cause=%s cooldown_seconds=%d",
                    engine,
                    cause,
                    int(seconds),
                )
                last_error = error
                continue
            self._health.succeeded(engine)
            if rows:
                return engine, rows
            empty = True

        # Fallback to unconstrained default search if all individual engine attempts failed
        try:

            def run_default() -> list[dict[str, Any]]:
                client = self._client()
                method = client.news if kind == "news" else client.text
                return list(method(query, **arguments) or [])

            default_rows = await asyncio.wait_for(
                asyncio.to_thread(run_default), timeout=self._timeout + 2
            )
            if default_rows:
                return "ddgs", default_rows
        except Exception:
            pass

        if empty or last_error is None:
            raise NoResultsError
        raise last_error


class BraveProvider:
    """Brave Search API (web and news). Used only when ``MAYA_BRAVE_API_KEY`` is set."""

    name = "brave"

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._transport = transport

    def available(self) -> bool:
        return self._key is not None and bool(self._key.get_secret_value())

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        data = await self._get("web/search", query, max_results, recency, region, lang)
        rows = (data.get("web") or {}).get("results") or []
        return self._hits(rows)

    async def news(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        data = await self._get("news/search", query, max_results, recency, region, lang)
        return self._hits(data.get("results") or [])

    @staticmethod
    def _hits(rows: list[Any]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            meta = row.get("meta_url")
            hostname = meta.get("hostname") if isinstance(meta, dict) else None
            hits.append(
                SearchHit(
                    title=_text(row.get("title"), 200),
                    url=str(row["url"]),
                    snippet=_text(row.get("description")),
                    published=_published_text(row.get("page_age") or row.get("age")),
                    source_name=_text(hostname, 80) or None,
                    engine="brave",
                )
            )
        if not hits:
            raise NoResultsError
        return hits

    async def _get(
        self,
        path: str,
        query: str,
        max_results: int,
        recency: str | None,
        region: str | None,
        lang: str | None,
    ) -> dict[str, Any]:
        if self._key is None or not self.available():
            raise ProviderUnavailableError("auth")
        params: dict[str, Any] = {"q": query, "count": min(max_results, 20)}
        if recency in _BRAVE_FRESHNESS:
            params["freshness"] = _BRAVE_FRESHNESS[recency]
        if region and "-" not in region:
            params["country"] = region.upper()
        if lang:
            params["search_lang"] = lang.lower()
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=self._transport) as client:
            response = await client.get(
                f"{_BRAVE_URL}/{path}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._key.get_secret_value(),
                },
            )
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}


class TavilyProvider:
    """Tavily search API over plain HTTP (no SDK). Used only when ``MAYA_TAVILY_API_KEY`` is set."""

    name = "tavily"

    def __init__(
        self,
        api_key: SecretStr | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._transport = transport

    def available(self) -> bool:
        return self._key is not None and bool(self._key.get_secret_value())

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        return await self._post(query, max_results, recency, topic="general")

    async def news(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        return await self._post(query, max_results, recency, topic="news")

    async def _post(
        self, query: str, max_results: int, recency: str | None, *, topic: str
    ) -> list[SearchHit]:
        if self._key is None or not self.available():
            raise ProviderUnavailableError("auth")
        body: dict[str, Any] = {
            "query": query,
            "max_results": min(max_results, 20),
            "topic": topic,
            "search_depth": "basic",
        }
        if recency in _DDGS_LIMIT:
            body["time_range"] = recency
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=self._transport) as client:
            response = await client.post(
                _TAVILY_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
            )
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else None
        hits = [
            SearchHit(
                title=_text(row.get("title"), 200),
                url=str(row["url"]),
                snippet=_text(row.get("content")),
                published=_published_text(row.get("published_date")),
                engine="tavily",
            )
            for row in rows or []
            if isinstance(row, dict) and row.get("url")
        ]
        if not hits:
            raise NoResultsError
        return hits


class GeminiProvider:
    """Gemini Google Search grounding. OFF by default: it runs only when the owner names
    ``gemini`` in ``MAYA_SEARCH_PROVIDERS`` (its quota is small and shared with the agents).

    ``agent`` is a research agent used only for its ``gemini_grounded_answer`` (never the native
    path, so the chain cannot call itself)."""

    name = "gemini"

    def __init__(self, agent: Any | None) -> None:
        self._agent = agent

    def available(self) -> bool:
        return self._agent is not None

    async def answer(self, query: str, *, workspace_id: UUID | None = None) -> ProviderAnswer:
        from app.agents.schemas import AgentExecutionStatus
        from app.services.loop_tools import search_failure_cause

        if self._agent is None:
            raise ProviderUnavailableError("auth")
        result = await self._agent.gemini_grounded_answer(query, workspace_id=workspace_id)
        if result.status is not AgentExecutionStatus.SUCCESS or not result.citations:
            raise ProviderUnavailableError(search_failure_cause(result, None))
        hits = [
            SearchHit(title=_text(c.get("title") or c.get("url"), 200), url=str(c["url"]))
            for c in result.citations
            if c.get("url")
        ]
        return ProviderAnswer(text=result.summary, hits=hits)
