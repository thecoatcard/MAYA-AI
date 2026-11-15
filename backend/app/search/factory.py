"""Build the search stack from settings (one per process)."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.search.chain import QueryCache, SearchChain, SearchRateLimiter
from app.search.fetch import PageFetcher
from app.search.health import EngineHealth, Pacer
from app.search.names import parse_engine_list, parse_provider_list
from app.search.providers import BraveProvider, DdgsProvider, GeminiProvider, TavilyProvider
from app.search.providers_free import (
    ArxivProvider,
    HackerNewsProvider,
    SearxngProvider,
    StackExchangeProvider,
    WikipediaProvider,
)
from app.search.report import NativeResearch
from app.search.service import WebSearchService


def build_web_search(
    settings: Settings, gateway: Any | None
) -> tuple[WebSearchService, NativeResearch]:
    """The chat tool service and the research-step backend, sharing one chain and one cache.

    One ``EngineHealth`` (cooldowns) and one ``Pacer`` (request spacing) are shared by all
    providers. Gemini grounding is built only when the owner names ``gemini`` in
    ``MAYA_SEARCH_PROVIDERS`` and the model gateway has a client."""
    order = parse_provider_list(settings.search_providers)
    gemini_agent: Any | None = None
    if "gemini" in order and gateway is not None and getattr(gateway, "client", None) is not None:
        from app.agents.specialized.research import ResearchAgent

        gemini_agent = ResearchAgent(model_gateway=gateway)  # no native_search: no recursion
    health = EngineHealth()
    pacer = Pacer(spacing=settings.search_engine_spacing_ms / 1000.0)
    searxng_urls = [u for u in settings.searxng_urls.split(",") if u.strip()]
    providers: dict[str, Any] = {
        "ddgs": DdgsProvider(
            engines=parse_engine_list(settings.ddgs_engines), health=health, pacer=pacer
        ),
        "searxng": SearxngProvider(searxng_urls, health=health, pacer=pacer),
        "brave": BraveProvider(settings.brave_api_key),
        "tavily": TavilyProvider(settings.tavily_api_key),
        "wikipedia": WikipediaProvider(pacer=pacer),
        "hn": HackerNewsProvider(pacer=pacer),
        "arxiv": ArxivProvider(pacer=pacer),
        "stackexchange": StackExchangeProvider(pacer=pacer),
        "gemini": GeminiProvider(gemini_agent),
    }
    chain = SearchChain(
        providers,
        order,
        cache=QueryCache(),
        limiter=SearchRateLimiter(settings.search_rate_limit_per_minute),
        health=health,
        concurrency=settings.search_concurrency,
    )
    service = WebSearchService(chain, PageFetcher(), gemini_agent=gemini_agent)
    return service, NativeResearch(service, gateway)
