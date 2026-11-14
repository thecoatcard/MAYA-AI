"""Native search: provider mapping, the provider chain, cache, limit and dedupe. Offline only:
the ddgs library object is a fake, Brave and Tavily run over ``httpx.MockTransport``."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.search.chain import (
    QueryCache,
    SearchChain,
    SearchRateLimiter,
    dedupe_hits,
    normalise_url,
)
from app.search.health import EngineHealth
from app.search.models import (
    NoResultsError,
    ProviderAnswer,
    ProviderUnavailableError,
    SearchHit,
    SearchRequest,
    classify_failure,
)
from app.search.names import DEFAULT_PROVIDERS, parse_provider_list
from app.search.providers import BraveProvider, DdgsProvider, GeminiProvider, TavilyProvider
from tests.support.gateway_factory import make_settings


async def no_sleep(_: float) -> None:
    return None


class FakeDdgs:
    def __init__(self, text: list[dict[str, Any]], news: list[dict[str, Any]]) -> None:
        self.text_rows, self.news_rows = text, news
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("text", query, kwargs))
        return self.text_rows

    def news(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("news", query, kwargs))
        return self.news_rows


class Scripted:
    """A provider that fails or answers as told, and counts its calls."""

    def __init__(self, name: str, *, keyed: bool = True, outcomes: list[Any] | None = None) -> None:
        self.name = name
        self._keyed = keyed
        self._outcomes = list(outcomes or [])
        self.calls = 0

    def available(self) -> bool:
        return self._keyed

    async def search(self, query: str, **_: Any) -> list[SearchHit]:
        return self._next()

    async def news(self, query: str, **_: Any) -> list[SearchHit]:
        return self._next()

    def _next(self) -> list[SearchHit]:
        self.calls += 1
        item = self._outcomes.pop(0) if self._outcomes else []
        if isinstance(item, BaseException):
            raise item
        return list(item)


def hit(title: str, url: str, **extra: Any) -> SearchHit:
    return SearchHit(title=title, url=url, snippet="s", **extra)


REQUEST = SearchRequest("nvidia news today", news=True, recency="day")


# ----------------------------------------------------------------------------- the setting


def test_provider_list_default_order_and_unknown_names_are_refused() -> None:
    # Deliberately changed: Gemini (Google Search grounding) is no longer in the default chain; it
    # is an explicit opt-in. The default is the free engines plus the intent providers.
    default = parse_provider_list(DEFAULT_PROVIDERS)
    assert default == [
        "hn",
        "arxiv",
        "stackexchange",
        "ddgs",
        "searxng",
        "brave",
        "tavily",
        "wikipedia",
    ]
    assert "gemini" not in default
    assert parse_provider_list(" Tavily , ddgs ,ddgs") == ["tavily", "ddgs"]
    with pytest.raises(ValueError, match="Unknown search provider 'bing'"):
        parse_provider_list("ddgs,bing")
    with pytest.raises(ValueError, match="empty"):
        parse_provider_list(" , ")


def test_settings_reject_an_unknown_provider_and_treat_a_blank_key_as_unset() -> None:
    with pytest.raises(ValueError, match="Unknown search provider"):
        make_settings(search_providers="ddgs,altavista")
    settings = make_settings(brave_api_key=" ", tavily_api_key="")
    assert settings.brave_api_key is None and settings.tavily_api_key is None
    # Deliberately changed: no gemini (Google grounding) unless the owner opts in.
    assert settings.search_providers == DEFAULT_PROVIDERS
    assert "gemini" not in settings.search_providers.split(",")
    assert make_settings(search_providers="ddgs,gemini").search_providers == "ddgs,gemini"


# ----------------------------------------------------------------------------- ddgs mapping


async def test_ddgs_maps_text_results_and_passes_region_without_a_time_limit() -> None:
    fake = FakeDdgs(
        text=[{"title": " A  title ", "href": "https://a.example/x", "body": "Body\ntext"}],
        news=[],
    )
    provider = DdgsProvider(lambda: fake)

    hits = await provider.search("solar", max_results=5, region="in", lang="en")

    # The hit now names the real engine; the library is asked for that explicit engine.
    assert hits == [
        SearchHit("A title", "https://a.example/x", "Body text", None, None, "duckduckgo")
    ]
    kind, query, kwargs = fake.calls[0]
    assert (kind, query) == ("text", "solar") and kwargs["backend"] == "duckduckgo"
    assert kwargs["region"] == "in-en" and kwargs["max_results"] == 5 and "timelimit" not in kwargs


async def test_ddgs_news_maps_date_and_source_and_passes_the_day_time_limit() -> None:
    fake = FakeDdgs(
        text=[],
        news=[
            {
                "date": "2026-11-12T05:00:00+00:00",
                "title": "Nvidia beats",
                "body": "Earnings",
                "url": "https://news.example/n1",
                "source": "Example News",
            }
        ],
    )
    hits = await DdgsProvider(lambda: fake).news("nvidia", recency="day")

    assert hits[0].published == "2026-11-12T05:00:00+00:00"
    assert hits[0].source_name == "Example News" and hits[0].domain == "news.example"
    assert fake.calls[0][0] == "news" and fake.calls[0][2]["timelimit"] == "d"


async def test_ddgs_with_no_rows_raises_no_results() -> None:
    with pytest.raises(NoResultsError):
        await DdgsProvider(lambda: FakeDdgs([], [])).search("nothing")


# ----------------------------------------------------------------------------- brave, tavily


async def test_brave_sends_its_key_header_and_freshness_and_maps_news() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Chip news",
                        "url": "https://b.example/1",
                        "description": "d",
                        "page_age": "2026-11-12T01:00:00",
                        "meta_url": {"hostname": "b.example"},
                    }
                ]
            },
        )

    provider = BraveProvider(
        SecretStr("brave-key-not-real"), transport=httpx.MockTransport(handler)
    )
    hits = await provider.news("chips", recency="day")

    assert hits[0].source_name == "b.example" and hits[0].published == "2026-11-12T01:00:00"
    assert seen[0].url.path.endswith("/news/search") and seen[0].url.params["freshness"] == "pd"
    assert seen[0].headers["x-subscription-token"] == "brave-key-not-real"


async def test_tavily_posts_topic_news_and_maps_content() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer tavily-key-not-real"
        return httpx.Response(
            200,
            json={"results": [{"title": "T", "url": "https://t.example/1", "content": "c"}]},
        )

    provider = TavilyProvider(
        SecretStr("tavily-key-not-real"), transport=httpx.MockTransport(handler)
    )
    hits = await provider.news("q", recency="week")

    assert hits[0].snippet == "c"
    assert bodies[0]["topic"] == "news" and bodies[0]["time_range"] == "week"


async def test_a_429_from_brave_is_classified_as_rate_limited() -> None:
    provider = BraveProvider(
        SecretStr("k-not-real"),
        transport=httpx.MockTransport(lambda r: httpx.Response(429, json={})),
    )
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await provider.search("q")
    assert classify_failure(caught.value) == "rate_limited"


def test_providers_without_a_key_are_unavailable() -> None:
    assert not BraveProvider(None).available() and not TavilyProvider(None).available()
    assert BraveProvider(SecretStr("x-not-real")).available()


# ----------------------------------------------------------------------------- the chain


async def test_chain_falls_through_ddgs_failure_unkeyed_brave_to_tavily() -> None:
    ddgs = Scripted("ddgs", outcomes=[httpx.ConnectError("dns"), httpx.ConnectError("dns")])
    brave = Scripted("brave", keyed=False)
    tavily = Scripted("tavily", outcomes=[[hit("Tavily item", "https://t.example/a")]])
    gemini = Scripted("gemini")
    chain = SearchChain(
        {"ddgs": ddgs, "brave": brave, "tavily": tavily, "gemini": gemini},
        ["ddgs", "brave", "tavily", "gemini"],
        sleep=no_sleep,
    )

    outcome = await chain.run(REQUEST)

    assert outcome.provider == "tavily" and [h.title for h in outcome.hits] == ["Tavily item"]
    assert ddgs.calls == 2  # one retry for a transient (network) failure
    assert brave.calls == 0 and gemini.calls == 0  # no key: skipped silently; later one not used
    assert [(f.provider, f.cause) for f in outcome.failures] == [("ddgs", "network")]


async def test_a_rate_limit_is_not_retried_and_goes_to_the_next_provider() -> None:
    limited = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "https://x"), response=httpx.Response(429)
    )
    first = Scripted("ddgs", outcomes=[limited])
    second = Scripted("tavily", outcomes=[[hit("ok", "https://t.example/a")]])
    chain = SearchChain({"ddgs": first, "tavily": second}, ["ddgs", "tavily"], sleep=no_sleep)

    outcome = await chain.run(REQUEST)

    assert first.calls == 1 and outcome.provider == "tavily"
    assert outcome.failures[0].cause == "rate_limited"


async def test_the_order_setting_decides_who_is_tried_first() -> None:
    a = Scripted("ddgs", outcomes=[[hit("from ddgs", "https://a.example/1")]])
    b = Scripted("tavily", outcomes=[[hit("from tavily", "https://b.example/1")]])
    chain = SearchChain({"ddgs": a, "tavily": b}, ["tavily", "ddgs"], sleep=no_sleep)

    outcome = await chain.run(REQUEST)

    assert outcome.provider == "tavily" and a.calls == 0


class FakeGemini:
    name = "gemini"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def available(self) -> bool:
        return True

    async def answer(self, query: str, *, workspace_id: Any = None) -> ProviderAnswer:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ProviderAnswer("Grounded text [1]", [hit("G", "https://g.example/1")])


async def test_gemini_is_used_only_after_every_native_provider_failed() -> None:
    ddgs = Scripted("ddgs", outcomes=[NoResultsError()])
    gemini = FakeGemini()
    chain = SearchChain({"ddgs": ddgs, "gemini": gemini}, ["ddgs", "gemini"], sleep=no_sleep)
    outcome = await chain.run(REQUEST)
    assert outcome.provider == "gemini" and outcome.answer == "Grounded text [1]"
    assert gemini.calls == 1

    working = Scripted("ddgs", outcomes=[[hit("fine", "https://a.example/1")]])
    gemini2 = FakeGemini()
    chain2 = SearchChain({"ddgs": working, "gemini": gemini2}, ["ddgs", "gemini"], sleep=no_sleep)
    await chain2.run(REQUEST)
    assert gemini2.calls == 0


async def test_every_provider_failing_gives_no_hits_and_the_reasons() -> None:
    chain = SearchChain(
        {
            "ddgs": Scripted("ddgs", outcomes=[ProviderUnavailableError("rate_limited")]),
            "gemini": FakeGemini(ProviderUnavailableError("rate_limited")),
        },
        ["ddgs", "gemini"],
        sleep=no_sleep,
    )
    outcome = await chain.run(REQUEST)
    assert outcome.hits == [] and outcome.answer is None and outcome.provider is None
    assert [(f.provider, f.cause) for f in outcome.failures] == [
        ("ddgs", "rate_limited"),
        ("gemini", "rate_limited"),
    ]


# ----------------------------------------------------------------------------- cache and limit


async def test_identical_queries_hit_the_cache_and_failures_are_never_cached() -> None:
    provider = Scripted("ddgs", outcomes=[[hit("one", "https://a.example/1")]])
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep)

    first = await chain.run(REQUEST)
    second = await chain.run(SearchRequest("  NVIDIA news   today", news=True, recency="day"))
    assert provider.calls == 1 and second.cached and not first.cached
    assert [h.url for h in second.hits] == ["https://a.example/1"]

    other = await chain.run(SearchRequest("nvidia news today", news=False))  # a different request
    assert provider.calls == 2 and other.hits == []

    flaky = Scripted(
        "ddgs",
        outcomes=[ProviderUnavailableError("rate_limited"), [hit("late", "https://a.example/9")]],
    )
    # Deliberately changed: a failed provider now cools down (2 minutes), so the "retry" happens
    # after the cooldown, on a fake clock.
    now = [1000.0]
    chain2 = SearchChain(
        {"ddgs": flaky}, ["ddgs"], sleep=no_sleep, health=EngineHealth(clock=lambda: now[0])
    )
    assert (await chain2.run(REQUEST)).hits == []
    now[0] += 200.0
    retried = await chain2.run(REQUEST)
    assert flaky.calls == 2 and not retried.cached  # the failure was not cached
    assert [h.title for h in retried.hits] == ["late"]


def test_cache_entries_expire_after_five_minutes_and_the_lru_is_bounded() -> None:
    now = [0.0]
    cache = QueryCache(seconds=300, entries=2, clock=lambda: now[0])
    from app.search.models import SearchOutcome

    for word in ("a", "b", "c"):
        request = SearchRequest(word)
        cache.put(request, SearchOutcome(request, hits=[hit(word, f"https://{word}.example/")]))
    assert cache.get(SearchRequest("a")) is None  # evicted: only 2 entries kept
    assert cache.get(SearchRequest("c")) is not None
    now[0] = 301.0
    assert cache.get(SearchRequest("c")) is None


async def test_the_per_user_limit_stops_a_search_loop() -> None:
    provider = Scripted(
        "ddgs", outcomes=[[hit(f"t{i}", f"https://a.example/{i}")] for i in range(9)]
    )
    chain = SearchChain({"ddgs": provider}, ["ddgs"], limiter=SearchRateLimiter(2), sleep=no_sleep)
    results = [
        await chain.run(SearchRequest(f"q{i}"), user_key="user-1", workspace_id=uuid4())
        for i in range(3)
    ]
    assert [r.limited for r in results] == [False, False, True]
    other_user = await chain.run(SearchRequest("q9"), user_key="user-2")
    assert not other_user.limited


# ----------------------------------------------------------------------------- dedupe


def test_dedupe_by_url_tracking_params_near_duplicate_titles_and_domain_cap() -> None:
    hits = [
        hit("Nvidia stock jumps after earnings report", "https://www.a.example/p?utm_source=x"),
        hit("Nvidia stock jumps after earnings report!", "https://b.example/other"),
        hit("A different story entirely here", "https://a.example/p/"),
        hit("Another unrelated headline about chips", "https://c.example/1"),
        hit("Third story from c about memory", "https://c.example/2"),
        hit("Fourth story from c about networking", "https://c.example/3"),
    ]
    kept = dedupe_hits(hits)
    assert [h.url for h in kept] == [
        "https://www.a.example/p?utm_source=x",
        "https://c.example/1",
        "https://c.example/2",
    ]
    assert normalise_url("https://www.a.example/p/?utm_source=x#f") == normalise_url(
        "http://a.example/p"
    )


# ----------------------------------------------------------------------------- gemini adapter


async def test_the_gemini_provider_uses_the_grounded_call_and_names_the_cause_on_failure() -> None:
    from types import SimpleNamespace

    from app.agents.schemas import AgentExecutionStatus

    ok = SimpleNamespace(
        status=AgentExecutionStatus.SUCCESS,
        summary="text",
        citations=[{"title": "S", "url": "https://s.example/1"}],
        verification_proof={},
    )

    class Agent:
        def __init__(self, result: Any) -> None:
            self.result = result

        async def gemini_grounded_answer(self, query: str, *, workspace_id: Any) -> Any:
            return self.result

    answer = await GeminiProvider(Agent(ok)).answer("q")
    assert answer.text == "text" and answer.hits[0].url == "https://s.example/1"

    busy = SimpleNamespace(
        status=AgentExecutionStatus.PARTIAL,
        summary="",
        citations=[],
        verification_proof={"cause": "rate_limited"},
    )
    with pytest.raises(ProviderUnavailableError) as caught:
        await GeminiProvider(Agent(busy)).answer("q")
    assert caught.value.cause == "rate_limited"
    assert not GeminiProvider(None).available()
