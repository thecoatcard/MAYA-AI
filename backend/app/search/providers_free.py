"""More free search providers that need no key: SearXNG, Wikipedia, Hacker News, arXiv and Stack
Exchange. Only documented JSON/Atom APIs are used (no scraping of any results page, never Google).

Same shape as the others: ``search`` returns ``SearchHit`` lists (each with its ``engine``) and
raises on failure. Every request is spaced by the shared ``Pacer`` and has a short timeout. The
specialised ones (``hn``, ``arxiv``, ``stackexchange``) answer only a query with a clear signal for
them (``matches``); Wikipedia skips news queries.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET  # noqa: S405 - arXiv only, DOCTYPE/ENTITY is refused below
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import quote, urlsplit

import httpx

from app.search.health import EngineHealth, Pacer
from app.search.models import (
    NoResultsError,
    ProviderUnavailableError,
    SearchHit,
    SearchRequest,
    classify_failure,
)
from app.search.query import wants_arxiv, wants_hacker_news, wants_stackexchange

_TIMEOUT: Final = 8.0
_UA: Final = (
    "MAYA-AI/0.1 (https://maya.ai; dev@maya.ai) "
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)
_TAGS = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_LANG = re.compile(r"^[a-z]{2,3}$")
_SEARXNG_RANGE: Final = {"day": "day", "week": "week", "month": "month", "year": "year"}
_ARXIV_STOP: Final = frozenset(
    "the and for with that this from what how why who are was about into on of in a an to is "
    "arxiv paper papers preprint preprints research scientific latest recent".split()
)
_ATOM: Final = "{http://www.w3.org/2005/Atom}"


def _plain(value: Any, limit: int = 500) -> str:
    text = html.unescape(_TAGS.sub("", str(value or "")))
    return " ".join(text.split())[:limit]


class _Http:
    """Shared HTTP plumbing: spacing, a timeout, the identifying User-Agent."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        pacer: Pacer | None = None,
        timeout: float = _TIMEOUT,
    ) -> None:
        self._transport = transport
        self._pacer = pacer if pacer is not None else Pacer()
        self._timeout = timeout

    async def _get(
        self, key: str, url: str, params: dict[str, Any], *, accept: str = "application/json"
    ) -> httpx.Response:
        await self._pacer.wait(key)
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=True
        ) as client:
            response = await client.get(
                url, params=params, headers={"User-Agent": _UA, "Accept": accept}
            )
        response.raise_for_status()
        return response


class WikipediaProvider(_Http):
    """Wikipedia REST search (``/w/rest.php/v1/search/page``): encyclopedic queries and the last
    native fallback. Not used for news queries."""

    name = "wikipedia"

    def available(self) -> bool:
        return True

    def matches(self, request: SearchRequest) -> bool:
        return not request.news

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        language = lang.lower() if lang and _LANG.match(lang.lower()) else "en"
        response = await self._get(
            self.name,
            f"https://{language}.wikipedia.org/w/rest.php/v1/search/page",
            {"q": query, "limit": min(max_results, 10)},
        )
        payload = response.json()
        pages = payload.get("pages") if isinstance(payload, dict) else None
        hits: list[SearchHit] = []
        for page in pages or []:
            if not isinstance(page, dict) or not page.get("key"):
                continue
            snippet = " - ".join(
                part
                for part in (_plain(page.get("description"), 120), _plain(page.get("excerpt")))
                if part
            )
            hits.append(
                SearchHit(
                    title=_plain(page.get("title"), 200),
                    url=f"https://{language}.wikipedia.org/wiki/{quote(str(page['key']))}",
                    snippet=snippet[:500],
                    engine="wikipedia",
                )
            )
        if not hits:
            raise NoResultsError
        return hits


class HackerNewsProvider(_Http):
    """Hacker News through the Algolia API; only for a query that names Hacker News."""

    name = "hn"

    def available(self) -> bool:
        return True

    def matches(self, request: SearchRequest) -> bool:
        return wants_hacker_news(request.query)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        terms = re.sub(r"(?i)\b(?:hacker\s?news|show hn|ask hn|on|from|at)\b", " ", query)
        params: dict[str, Any] = {
            "query": " ".join(terms.split()) or query,
            "tags": "story",
            "hitsPerPage": min(max_results, 20),
        }
        path = "search"
        spans = {"day": 1, "week": 7, "month": 31, "year": 365}
        if recency in spans:
            path = "search_by_date"
            since = datetime.now(UTC) - timedelta(days=spans[recency])
            params["numericFilters"] = f"created_at_i>{int(since.timestamp())}"
        response = await self._get(self.name, f"https://hn.algolia.com/api/v1/{path}", params)
        payload = response.json()
        hits: list[SearchHit] = []
        for row in (payload.get("hits") if isinstance(payload, dict) else None) or []:
            if not isinstance(row, dict) or not row.get("title"):
                continue
            discussion = f"https://news.ycombinator.com/item?id={row.get('objectID')}"
            points = row.get("points")
            hits.append(
                SearchHit(
                    title=_plain(row.get("title"), 200),
                    url=str(row.get("url") or discussion),
                    snippet=f"{points} points on Hacker News. Discussion: {discussion}"
                    if points is not None
                    else f"Discussion: {discussion}",
                    published=str(row.get("created_at") or "") or None,
                    source_name="Hacker News",
                    engine="hn",
                )
            )
        if not hits:
            raise NoResultsError
        return hits


class ArxivProvider(_Http):
    """arXiv Atom API; only for a query about papers, preprints or arXiv."""

    name = "arxiv"

    def available(self) -> bool:
        return True

    def matches(self, request: SearchRequest) -> bool:
        return wants_arxiv(request.query)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        words = [w for w in (m.lower() for m in _WORD.findall(query)) if w not in _ARXIV_STOP]
        if not words:
            raise NoResultsError
        search_query = " AND ".join(f"all:{w}" for w in words[:8])
        response = await self._get(
            self.name,
            "https://export.arxiv.org/api/query",
            {
                "search_query": search_query,
                "start": 0,
                "max_results": min(max_results, 20),
                "sortBy": "submittedDate" if recency else "relevance",
                "sortOrder": "descending",
            },
            accept="application/atom+xml",
        )
        return self._parse(response.text)

    @staticmethod
    def _parse(text: str) -> list[SearchHit]:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ProviderUnavailableError("blocked")  # never parse a feed that declares entities
        try:
            root = ET.fromstring(text)  # noqa: S314 - DOCTYPE/ENTITY refused above
        except ET.ParseError as error:
            raise ProviderUnavailableError("server") from error
        hits: list[SearchHit] = []
        for entry in root.findall(f"{_ATOM}entry"):
            link = (entry.findtext(f"{_ATOM}id") or "").strip()
            title = _plain(entry.findtext(f"{_ATOM}title"), 200)
            if not link or not title:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=link.replace("http://", "https://", 1),
                    snippet=_plain(entry.findtext(f"{_ATOM}summary"), 500),
                    published=(entry.findtext(f"{_ATOM}published") or "").strip() or None,
                    source_name="arXiv",
                    engine="arxiv",
                )
            )
        if not hits:
            raise NoResultsError
        return hits


class StackExchangeProvider(_Http):
    """Stack Overflow questions through the keyless Stack Exchange API (small daily quota, so
    only for a programming question). A ``backoff`` in the answer is honoured as rate limiting."""

    name = "stackexchange"

    def available(self) -> bool:
        return True

    def matches(self, request: SearchRequest) -> bool:
        return wants_stackexchange(request.query)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        cleaned = re.sub(r"(?i)\bstack\s?(?:overflow|exchange)\b", " ", query)
        response = await self._get(
            self.name,
            "https://api.stackexchange.com/2.3/search/advanced",
            {
                "q": " ".join(cleaned.split()) or query,
                "site": "stackoverflow",
                "order": "desc",
                "sort": "relevance",
                "pagesize": min(max_results, 15),
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise NoResultsError
        if payload.get("backoff"):
            raise ProviderUnavailableError("rate_limited")
        hits: list[SearchHit] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict) or not item.get("link"):
                continue
            tags = ", ".join(str(t) for t in (item.get("tags") or [])[:5])
            answered = "answered" if item.get("is_answered") else "not answered"
            created = item.get("creation_date")
            hits.append(
                SearchHit(
                    title=_plain(item.get("title"), 200),
                    url=str(item["link"]),
                    snippet=(
                        f"Stack Overflow, {answered}, score {item.get('score', 0)}. Tags: {tags}"
                    ),
                    published=datetime.fromtimestamp(int(created), tz=UTC).date().isoformat()
                    if isinstance(created, int)
                    else None,
                    source_name="Stack Overflow",
                    engine="stackexchange",
                )
            )
        if not hits:
            raise NoResultsError
        return hits


class SearxngProvider(_Http):
    """Configured SearXNG instances (``MAYA_SEARXNG_URLS``, empty by default) over their JSON API
    (``format=json``). An instance that answers 403/429 (JSON disabled or rate limited) or fails
    is skipped and cools down on its own; the next instance is tried."""

    name = "searxng"
    manages_health = True
    retry_transient = False

    def __init__(
        self,
        urls: Sequence[str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        health: EngineHealth | None = None,
        pacer: Pacer | None = None,
        timeout: float = 6.0,
        max_instances: int = 2,
    ) -> None:
        super().__init__(transport=transport, pacer=pacer, timeout=timeout)
        self._urls = [u.rstrip("/") for u in urls if self._valid(u)]
        self._health = health if health is not None else EngineHealth()
        self._max_instances = max(1, max_instances)

    @staticmethod
    def _valid(url: str) -> bool:
        parts = urlsplit(url.strip())
        return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username

    def _key(self, url: str) -> str:
        return f"searxng:{urlsplit(url).netloc}"

    def available(self) -> bool:
        return bool(self._urls)

    def engine_keys(self) -> list[str]:
        return [self._key(u) for u in self._urls]

    def cooling_until(self) -> float | None:
        return self._health.earliest(self.engine_keys())

    async def news(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        return await self._query(query, "news", max_results, recency, lang)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        recency: str | None = None,
        region: str | None = None,
        lang: str | None = None,
    ) -> list[SearchHit]:
        return await self._query(query, "general", max_results, recency, lang)

    async def _query(
        self, query: str, category: str, max_results: int, recency: str | None, lang: str | None
    ) -> list[SearchHit]:
        ready = [u for u in self._urls if self._health.cooling_until(self._key(u)) is None]
        if not ready:
            raise ProviderUnavailableError("cooling")
        params: dict[str, Any] = {"q": query, "format": "json", "categories": category}
        if lang:
            params["language"] = lang.lower()
        if recency in _SEARXNG_RANGE:
            params["time_range"] = _SEARXNG_RANGE[recency]
        last: Exception | None = None
        empty = False
        for url in ready[: self._max_instances]:
            key = self._key(url)
            try:
                response = await self._get(key, f"{url}/search", params)
                payload = response.json()
            except Exception as error:  # noqa: BLE001 - classified, never shown raw
                cause = classify_failure(error)
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (
                    403,
                    429,
                ):
                    cause = "blocked" if error.response.status_code == 403 else "rate_limited"
                self._health.failed(key, cause)
                last = error
                continue
            rows = payload.get("results") if isinstance(payload, dict) else None
            hits = [
                SearchHit(
                    title=_plain(row.get("title"), 200),
                    url=str(row["url"]),
                    snippet=_plain(row.get("content")),
                    published=str(row.get("publishedDate") or "") or None,
                    engine="searxng",
                )
                for row in rows or []
                if isinstance(row, dict) and row.get("url")
            ][:max_results]
            self._health.succeeded(key)
            if hits:
                return hits
            empty = True
        if empty or last is None:
            raise NoResultsError
        raise last
