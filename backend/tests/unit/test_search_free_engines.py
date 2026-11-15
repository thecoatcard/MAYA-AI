"""Free engines, rotation, cooldowns and burst control of the native search. Offline only: the ddgs
library object is a fake, the HTTP providers run over ``httpx.MockTransport``, time is a fake."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.api.v1.search import search_status
from app.search.chain import SearchChain
from app.search.factory import build_web_search
from app.search.health import BASE_COOLDOWN_SECONDS, MAX_COOLDOWN_SECONDS, EngineHealth, Pacer
from app.search.models import (
    NoResultsError,
    ProviderUnavailableError,
    SearchHit,
    SearchOutcome,
    SearchRequest,
)
from app.search.names import engine_label, parse_engine_list
from app.search.providers import DdgsProvider
from app.search.providers_free import (
    ArxivProvider,
    HackerNewsProvider,
    SearxngProvider,
    StackExchangeProvider,
    WikipediaProvider,
)
from app.search.query import clean_query
from app.search.service import WebSearchService, failure_sentence
from app.services.loop_tools import WEB_SEARCH_TOOL
from tests.support.gateway_factory import make_settings


class DDGSException(Exception):  # noqa: N818 - the library's own class name (classified by name)
    pass


async def no_sleep(_: float) -> None:
    return None


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class EngineFake:
    """A ddgs client: each backend answers from its own script (rows or an exception)."""

    def __init__(self, scripts: dict[str, Any]) -> None:
        self.scripts = scripts
        self.calls: list[str] = []

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        backend = kwargs["backend"]
        self.calls.append(backend)
        result = self.scripts[backend]
        if isinstance(result, Exception):
            raise result
        return list(result)

    news = text


def rows(host: str) -> list[dict[str, Any]]:
    return [{"title": f"T {host}", "href": f"https://{host}/a", "body": "b"}]


def ddgs(
    fake: EngineFake, clock: Clock, engines: tuple[str, ...] = ("duckduckgo", "bing", "brave")
) -> DdgsProvider:
    return DdgsProvider(
        lambda: fake,
        engines=engines,
        health=EngineHealth(clock=clock),
        pacer=Pacer(sleep=no_sleep),
    )


# ----------------------------------------------------------------------------- rotation


async def test_a_blocked_engine_is_skipped_and_the_next_engine_answers() -> None:
    clock = Clock()
    fake = EngineFake({"duckduckgo": DDGSException("blocked"), "bing": rows("b.example")})
    provider = ddgs(fake, clock)

    hits = await provider.search("solar")

    assert [h.engine for h in hits] == ["bing"] and fake.calls == ["duckduckgo", "bing"]
    again = await provider.search("solar panels")  # duckduckgo is cooling: not asked again
    assert fake.calls == ["duckduckgo", "bing", "bing"] and again[0].engine == "bing"
    assert provider.cooling_until() is None  # one engine is still ready


async def test_every_engine_cooling_is_reported_with_the_earliest_retry() -> None:
    clock = Clock()
    fake = EngineFake({"duckduckgo": DDGSException("x"), "bing": DDGSException("y")})
    provider = ddgs(fake, clock, engines=("duckduckgo", "bing"))
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep, health=provider._health)

    first = await chain.run(SearchRequest("solar energy"))
    assert first.hits == [] and first.retry_in_seconds == 120  # both just got a 2 minute cooldown
    second = await chain.run(SearchRequest("wind energy"))
    assert second.retry_in_seconds == 120 and fake.calls == ["duckduckgo", "bing"]  # not asked
    sentence = failure_sentence(second)
    assert "temporarily limiting requests; retry in about 2 minutes" in sentence
    assert "general knowledge" in sentence

    clock.now += 125  # the cooldown is over: the engines are asked again
    fake.scripts["bing"] = rows("b.example")
    third = await chain.run(SearchRequest("tidal energy"))
    assert third.hits and third.engine == "bing" and third.retry_in_seconds is None


def test_cooldown_doubles_to_thirty_minutes_and_a_success_resets_it() -> None:
    clock = Clock()
    health = EngineHealth(clock=clock)
    seconds = [health.failed("duckduckgo", "blocked") for _ in range(7)]
    assert seconds[:4] == [120, 240, 480, 960] and seconds[-1] == MAX_COOLDOWN_SECONDS
    assert health.snapshot("duckduckgo")["state"] == "blocked"
    clock.now += MAX_COOLDOWN_SECONDS + 1
    assert health.cooling_until("duckduckgo") is None  # recovered by time
    assert health.failed("duckduckgo", "blocked") == MAX_COOLDOWN_SECONDS  # count kept
    health.succeeded("duckduckgo")
    assert health.failed("duckduckgo", "timeout") == BASE_COOLDOWN_SECONDS  # a success resets
    assert health.snapshot("duckduckgo")["state"] == "cooling"
    assert health.failed("duckduckgo", "no_results") == 0  # an empty answer is not a failure


async def test_a_timeout_cools_the_engine_like_a_block() -> None:
    clock = Clock()
    fake = EngineFake({"duckduckgo": TimeoutError(), "bing": rows("b.example")})
    provider = ddgs(fake, clock)
    assert (await provider.search("q one"))[0].engine == "bing"
    assert provider._health.snapshot("duckduckgo")["last_cause"] == "timeout"


def test_the_rotation_never_contains_google() -> None:
    assert "google" not in parse_engine_list("duckduckgo,bing,brave,yahoo,mojeek")
    with pytest.raises(ValueError, match="Unknown search engine 'google'"):
        parse_engine_list("duckduckgo,google")
    assert "google" not in DdgsProvider(engines=("duckduckgo", "google")).engine_keys()


# ----------------------------------------------------------------------------- no gemini by default


class Hits:
    """A provider that returns hits (optionally naming an engine) and counts calls."""

    def __init__(self, name: str, engine: str | None = None, delay: float = 0.0) -> None:
        self.name = name
        self.engine = engine
        self.delay = delay
        self.calls = 0
        self.running = 0
        self.peak = 0

    def available(self) -> bool:
        return True

    async def search(self, query: str, **_: Any) -> list[SearchHit]:
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return [
                SearchHit(
                    "Title " + query,
                    f"https://x.example/{self.calls}",
                    "s",
                    None,
                    None,
                    self.engine,
                )
            ]
        finally:
            self.running -= 1


def test_the_default_chain_has_no_gemini_and_an_explicit_opt_in_adds_it() -> None:
    gateway = SimpleNamespace(client=object())
    default_service, _ = build_web_search(make_settings(), gateway)
    assert "gemini" not in default_service.chain.order
    assert default_service.chain.status()["google_grounding"] is False

    opted, _ = build_web_search(make_settings(search_providers="ddgs,gemini"), gateway)
    assert opted.chain.order == ["ddgs", "gemini"]
    assert opted.chain.status()["google_grounding"] is True


async def test_the_label_names_the_engine_that_answered_never_google() -> None:
    chain = SearchChain({"ddgs": Hits("ddgs", engine="bing")}, ["ddgs"], sleep=no_sleep)
    service = WebSearchService(chain, SimpleNamespace(fetch_page=_no_page))  # type: ignore[arg-type]
    result = await service.web_search("rust news", user_key="u", topic="web")
    assert result["step_detail"].startswith("Bing web, 1 results")
    assert "Google" not in json.dumps(result)

    assert engine_label("searxng:search.example") == "SearXNG"
    assert engine_label("gemini") == "Google Search"  # only when grounding really answered
    assert engine_label("duckduckgo") == "DuckDuckGo" and engine_label("hn") == "Hacker News"


async def _no_page(url: str, limit: int) -> Any:
    return SimpleNamespace(ok=False)


# ----------------------------------------------------------------------------- SearXNG


def searxng_transport(seen: list[httpx.Request], statuses: dict[str, int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = statuses.get(request.url.host, 200)
        if status != 200:
            return httpx.Response(status)
        body = {
            "results": [
                {"title": "Hit", "url": "https://r.example/1", "content": "<b>text</b>"},
                {"title": "no url"},
            ]
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def test_searxng_uses_the_json_api_and_skips_an_instance_that_answers_403() -> None:
    seen: list[httpx.Request] = []
    clock = Clock()
    provider = SearxngProvider(
        ["https://a.example", "https://b.example/", "ftp://bad.example", "http://u:p@x.example"],
        transport=searxng_transport(seen, {"a.example": 403}),
        health=EngineHealth(clock=clock),
        pacer=Pacer(sleep=no_sleep),
    )
    assert provider.engine_keys() == ["searxng:a.example", "searxng:b.example"]  # invalid dropped

    hits = await provider.search("solar", recency="week", lang="en")

    assert [r.url.host for r in seen] == ["a.example", "b.example"]
    assert seen[1].url.params["format"] == "json" and seen[1].url.params["time_range"] == "week"
    assert [(h.title, h.snippet, h.engine) for h in hits] == [("Hit", "text", "searxng")]
    assert provider._health.snapshot("searxng:a.example")["state"] == "blocked"
    await provider.search("solar again")  # the blocked instance is not asked again
    assert [r.url.host for r in seen][2:] == ["b.example"]


async def test_searxng_without_instances_is_not_available_and_429_cools_the_instance() -> None:
    assert SearxngProvider([]).available() is False
    provider = SearxngProvider(
        ["https://a.example"],
        transport=searxng_transport([], {"a.example": 429}),
        health=EngineHealth(clock=Clock()),
        pacer=Pacer(sleep=no_sleep),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("q")
    assert provider.cooling_until() is not None
    with pytest.raises(ProviderUnavailableError):
        await provider.search("q")  # every instance cooling


# ----------------------------------------------------------------------------- Wikipedia


async def test_wikipedia_provider_maps_pages_and_skips_news_queries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "en.wikipedia.org" and request.url.params["q"] == "ada lovelace"
        page = {
            "key": "Ada_Lovelace",
            "title": "Ada Lovelace",
            "description": "English mathematician",
            "excerpt": '<span class="searchmatch">Ada</span> wrote notes',
        }
        return httpx.Response(200, json={"pages": [page, {"title": "no key"}]})

    provider = WikipediaProvider(
        transport=httpx.MockTransport(handler), pacer=Pacer(sleep=no_sleep)
    )
    hits = await provider.search("ada lovelace")
    assert hits == [
        SearchHit(
            "Ada Lovelace",
            "https://en.wikipedia.org/wiki/Ada_Lovelace",
            "English mathematician - Ada wrote notes",
            None,
            None,
            "wikipedia",
        )
    ]
    assert provider.matches(SearchRequest("x", news=False)) and not provider.matches(
        SearchRequest("x", news=True)
    )


async def test_wikipedia_with_no_pages_is_no_results() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"pages": []}))
    with pytest.raises(NoResultsError):
        await WikipediaProvider(transport=transport, pacer=Pacer(sleep=no_sleep)).search("zzzz")


# ----------------------------------------------------------------------------- intent providers


ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2401.00001v1</id><title> Attention  is more </title>
<summary>We study attention.</summary><published>2024-01-01T00:00:00Z</published></entry></feed>"""


def test_intent_providers_match_only_a_clear_signal() -> None:
    hn, arxiv, se = HackerNewsProvider(), ArxivProvider(), StackExchangeProvider()
    assert hn.matches(SearchRequest("hacker news discussion about rust"))
    assert not hn.matches(SearchRequest("tech news today"))
    assert arxiv.matches(SearchRequest("latest arxiv papers on diffusion models"))
    assert arxiv.matches(SearchRequest("research paper about retrieval"))
    assert not arxiv.matches(SearchRequest("paper towels"))
    assert se.matches(SearchRequest("python KeyError traceback when reading dict"))
    assert se.matches(SearchRequest("how do I reverse a list in python"))
    assert not se.matches(SearchRequest("best pizza in delhi"))


async def test_hn_arxiv_and_stackexchange_parse_their_documented_apis() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "hn.algolia.com":
            hit = {"title": "Show HN: X", "url": None, "objectID": "42", "points": 9}
            return httpx.Response(200, json={"hits": [hit]})
        if host == "export.arxiv.org":
            return httpx.Response(200, text=ATOM)
        item = {
            "title": "How to &quot;reverse&quot; a list",
            "link": "https://stackoverflow.com/q/1",
            "is_answered": True,
            "score": 5,
            "tags": ["python"],
            "creation_date": 1_700_000_000,
        }
        return httpx.Response(200, json={"items": [item]})

    transport, pacer = httpx.MockTransport(handler), Pacer(sleep=no_sleep)
    hn = await HackerNewsProvider(transport=transport, pacer=pacer).search("hacker news rust")
    assert hn[0].url == "https://news.ycombinator.com/item?id=42" and hn[0].engine == "hn"
    paper = await ArxivProvider(transport=transport, pacer=pacer).search("arxiv attention")
    assert (
        paper[0].url == "https://arxiv.org/abs/2401.00001v1"
        and paper[0].title == "Attention is more"
    )
    answer = await StackExchangeProvider(transport=transport, pacer=pacer).search("python list")
    assert answer[0].title == 'How to "reverse" a list' and answer[0].engine == "stackexchange"


async def test_arxiv_refuses_a_feed_with_entities_and_stackexchange_honours_backoff() -> None:
    bad = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed/>'
    with pytest.raises(ProviderUnavailableError):
        ArxivProvider._parse(bad)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"items": [], "backoff": 10})
    )
    with pytest.raises(ProviderUnavailableError):
        await StackExchangeProvider(transport=transport, pacer=Pacer(sleep=no_sleep)).search("q")


async def test_the_chain_asks_an_intent_provider_first_and_only_for_its_kind() -> None:
    hn, general = Hits("hn", engine="hn"), Hits("ddgs", engine="duckduckgo")
    hn.matches = lambda request: "hacker news" in request.query  # type: ignore[attr-defined]
    chain = SearchChain({"hn": hn, "ddgs": general}, ["hn", "ddgs"], sleep=no_sleep)

    first = await chain.run(SearchRequest("hacker news rust"))
    assert first.engine == "hn" and hn.calls == 1 and general.calls == 0
    second = await chain.run(SearchRequest("rust ownership"))
    assert second.engine == "duckduckgo" and hn.calls == 1
    assert chain.active_names() == ["ddgs"]  # an intent provider is not a general one


# ----------------------------------------------------------------------------- burst control


async def test_the_pacer_spaces_requests_to_one_engine_and_not_across_engines() -> None:
    clock = Clock(10.0)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(round(seconds, 3))
        clock.now += seconds

    pacer = Pacer(spacing=0.4, jitter=0.0, clock=clock, sleep=sleep, rand=lambda: 0.0)
    await pacer.wait("bing")
    await pacer.wait("bing")
    await pacer.wait("bing")
    await pacer.wait("brave")  # another engine: no wait
    assert slept == [0.4, 0.4]
    jittery = Pacer(spacing=0.4, jitter=0.25, clock=clock, sleep=sleep, rand=lambda: 1.0)
    await jittery.wait("x")
    slept.clear()
    await jittery.wait("x")
    assert slept == [0.65]  # the gap is at least 0.4 s and carries jitter


async def test_at_most_two_provider_calls_run_at_once_in_a_process() -> None:
    provider = Hits("ddgs", delay=0.02)
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep, concurrency=2)
    await asyncio.gather(*(chain.run(SearchRequest(f"query number {i}")) for i in range(6)))
    assert provider.calls == 6 and provider.peak == 2
    assert make_settings().search_concurrency == 2  # the default


async def test_identical_searches_in_flight_share_one_call_and_then_the_cache() -> None:
    provider = Hits("ddgs", delay=0.02)
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep)

    outcomes = await asyncio.gather(
        *(chain.run(SearchRequest(q)) for q in ("Solar  power?", "solar power", "SOLAR POWER!"))
    )
    assert provider.calls == 1 and all(o.hits for o in outcomes)
    outcomes[0].hits.clear()  # a caller changing its copy does not touch the others
    assert outcomes[1].hits
    cached = await chain.run(SearchRequest("solar power"))
    assert provider.calls == 1 and cached.cached and cached.hits


def test_the_cache_lasts_ten_minutes() -> None:
    from app.search.chain import CACHE_SECONDS

    assert CACHE_SECONDS == 600.0


# ----------------------------------------------------------------------------- short queries


def test_a_pasted_prompt_becomes_a_short_keyword_query() -> None:
    long = (
        "What were the LeetCode problems for each day this week? Provide a day-wise breakdown "
        "including the title, difficulty, topics and a link for every problem."
    )
    assert clean_query(long) == "What were the LeetCode problems for each day this week?"
    assert clean_query("solar   panels  ") == "solar panels"
    assert len(clean_query("word " * 100)) <= 200
    assert clean_query("Please nvidia earnings provide a table including eps") == "nvidia earnings"


async def test_the_chain_searches_with_the_cleaned_query() -> None:
    provider = Hits("ddgs")
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep)
    outcome = await chain.run(
        SearchRequest("LeetCode daily problems this week. Provide a breakdown including links")
    )
    assert outcome.request.query == "LeetCode daily problems this week."
    assert outcome.hits[0].title == "Title LeetCode daily problems this week."


def test_the_tool_description_asks_for_a_short_keyword_query() -> None:
    description = WEB_SEARCH_TOOL.parameters["properties"]["query"]["description"]
    assert "SHORT keyword query" in description and "Never paste" in description


# ----------------------------------------------------------------------------- status


async def test_the_status_route_lists_each_engine_state_without_secrets() -> None:
    clock = Clock()
    fake = EngineFake({"duckduckgo": DDGSException("x"), "bing": rows("b.example")})
    provider = ddgs(fake, clock, engines=("duckduckgo", "bing"))
    chain = SearchChain(
        {"ddgs": provider, "wikipedia": WikipediaProvider()},
        ["ddgs", "wikipedia"],
        sleep=no_sleep,
        health=provider._health,
    )
    await chain.run(SearchRequest("solar energy"))
    service = SimpleNamespace(chain=chain)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(web_search_service=service))
    )

    body = await search_status(request, principal=object())  # type: ignore[arg-type]

    assert body["available"] is True and body["order"] == ["ddgs", "wikipedia"]
    engines = {e["engine"]: e for p in body["providers"] for e in p["engines"]}
    assert engines["duckduckgo"]["state"] == "blocked" and engines["duckduckgo"]["cooling_until"]
    assert engines["duckduckgo"]["label"] == "DuckDuckGo" and engines["bing"]["state"] == "ok"
    assert "key" not in json.dumps(body).lower().replace("engine", "")
    empty = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert (await search_status(empty, principal=object()))["available"] is False  # type: ignore[arg-type]


async def test_deep_research_searcher_reports_engines_and_throttles_while_cooling() -> None:
    from app.research.runtime import ChainSearcher

    clock = Clock()
    fake = EngineFake({"duckduckgo": DDGSException("x"), "bing": rows("b.example")})
    provider = ddgs(fake, clock, engines=("duckduckgo", "bing"))
    chain = SearchChain({"ddgs": provider}, ["ddgs"], sleep=no_sleep, health=provider._health)
    searcher = ChainSearcher(chain, user_key="deep:1", workspace_id=None)
    assert searcher.parallelism() == 3  # nothing is cooling yet

    hits, limited = await searcher("solar storage costs")

    assert hits and not limited and dict(searcher.engines_used) == {"Bing": 1}
    assert searcher.parallelism() == 1  # duckduckgo is cooling: fewer parallel queries


def test_all_engine_cooling_outcome_without_a_retry_time_keeps_the_plain_reason() -> None:
    outcome = SearchOutcome(request=SearchRequest("q"))
    assert "No web search provider is set up" in failure_sentence(outcome)
