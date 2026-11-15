"""The deep-research run with a fake gateway, fake search and fake pages. Fully offline."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.research import pipeline as pipeline_module
from app.research.limits import ResearchLimits
from app.research.models import Skip, Source
from app.research.pipeline import DeepResearch
from app.research.schemas import (
    Contradiction,
    Finding,
    Position,
    ResearchPlan,
    SourceNotes,
    SubQuestion,
    Synthesis,
)
from app.search.models import SearchHit

QUOTE = "the figure was measured across all regions"
PAGE = (
    "Analysts reported that revenue reached 57 billion dollars in the latest quarter, driven by "
    "demand. In their words, " + QUOTE + " and then reviewed twice. " * 8
)


def make_source(url: str, *, text: str = PAGE, flagged: bool = False) -> Source:
    domain = re.sub(r"^https?://([^/]+)/.*$", r"\1", url).removeprefix("www.")
    return Source(url, url, f"Title {domain}", domain, "2026-10-01", text, text, flagged)


class FakeFetcher:
    def __init__(
        self,
        skips: dict[str, str] | None = None,
        flagged: set[str] | None = None,
        on_fetch: Any = None,
    ) -> None:
        self.skips = skips or {}
        self.flagged = flagged or set()
        self.on_fetch = on_fetch
        self.urls: list[str] = []

    async def fetch(self, url: str) -> Source | Skip:
        self.urls.append(url)
        if self.on_fetch is not None:
            await self.on_fetch(url)
        domain = re.sub(r"^https?://([^/]+)/.*$", r"\1", url)
        for needle, code in self.skips.items():
            if needle in url:
                return Skip(url, domain, code)
        return make_source(url, flagged=any(f in url for f in self.flagged))


class FakeSearch:
    """Every query returns 3 pages on distinct sites; site numbers keep growing."""

    def __init__(self, sites: int = 36, limited_first: int = 0) -> None:
        self.sites = sites
        self.queries: list[str] = []
        self.limited_left = limited_first

    async def __call__(self, query: str) -> tuple[list[SearchHit], bool]:
        if self.limited_left > 0:
            self.limited_left -= 1
            return [], True
        self.queries.append(query)
        n = len(self.queries)
        hits = []
        for k in range(3):
            site = (n * 3 + k) % self.sites
            hits.append(
                SearchHit(
                    f"Page {n}-{k} on topic",
                    f"https://site{site}.example/a{n}-{k}",
                    "topic",
                )
            )
        # the same site again with a second and a third page: the per-site cap must hold
        hits.append(SearchHit("More", f"https://site0.example/extra{n}", "topic"))
        return hits, False


class FakeGateway:
    def __init__(
        self,
        *,
        body: str | None = None,
        synthesis: Synthesis | None = None,
        plan_error: bool = False,
        follow_ups: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.body = body
        self.synthesis = synthesis
        self.plan_error = plan_error
        self.follow_ups = follow_ups

    async def generate_structured(self, **kwargs: Any) -> Any:
        schema = kwargs["response_schema"]
        prompt = str(kwargs["contents"][0])
        self.calls.append(schema.__name__)
        if schema is ResearchPlan:
            if self.plan_error:
                raise RuntimeError("model down")
            return ResearchPlan(
                topic="solar batteries",
                language="English",
                questions=[
                    SubQuestion(question=f"Q{i}?", queries=[f"q{i} a", f"q{i} b", f"q{i} c"])
                    for i in range(8)
                ],
            )
        if schema is SourceNotes:
            return SourceNotes(
                facts=["Revenue reached a record level."],
                numbers=["57 billion dollars - revenue", "999 units - invented"],
                quotes=[QUOTE, "a sentence the page never said"],
                stance="neutral report",
                date="2026-10-01",
            )
        indexes = [int(m) for m in re.findall(r"^\[(\d+)\] ", prompt, re.MULTILINE)]
        if self.synthesis is not None:
            return self.synthesis
        return Synthesis(
            findings=[Finding(claim="Revenue reached a record.", sources=indexes[:3])],
            gaps=["pricing"] if self.follow_ups else [],
            follow_up_queries=["pricing details", "cost per kwh"] if self.follow_ups else [],
        )

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append("text")
        text = self.body or "Revenue reached a record [1][2]. Costs fell [3]."
        return SimpleNamespace(text=text)


class Steps:
    def __init__(self, cancel_after: int | None = None) -> None:
        self.steps: list[tuple[str, str, str]] = []
        self.polls = 0
        self.cancel_after = cancel_after

    async def step(
        self, step_id: str, kind: str, label: str, state: str, detail: str | None = None
    ) -> None:
        self.steps.append((step_id, label, state))

    async def cancelled(self) -> bool:
        self.polls += 1
        return self.cancel_after is not None and self.polls >= self.cancel_after

    def labels(self) -> list[str]:
        return [label for _, label, _ in self.steps]


def run_of(
    *,
    gateway: FakeGateway | None = None,
    search: FakeSearch | None = None,
    fetcher: FakeFetcher | None = None,
    limits: ResearchLimits | None = None,
    steps: Steps | None = None,
    clock: Any = None,
) -> tuple[DeepResearch, FakeGateway, FakeSearch, FakeFetcher, Steps]:
    gateway = gateway or FakeGateway()
    search = search or FakeSearch()
    fetcher = fetcher or FakeFetcher()
    steps = steps or Steps()

    async def no_sleep(_: float) -> None:
        return None

    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    research = DeepResearch(
        gateway=gateway,
        search=search,
        fetcher=fetcher,  # type: ignore[arg-type]
        limits=limits or ResearchLimits(),
        progress=steps,
        sleep=no_sleep,
        **kwargs,
    )
    return research, gateway, search, fetcher, steps


def sources_section(summary: str) -> str:
    return summary.split("## Sources", 1)[1]


async def test_a_full_run_reads_many_sites_and_cites_only_what_it_read() -> None:
    research, gateway, search, fetcher, steps = run_of()

    result = await research.run("Research solar batteries")

    assert result.ok
    assert result.proof["sources_read"] == len(result.citations) <= 40
    assert result.proof["domains"] >= 20  # the run reached at least the aimed-for sites
    assert len(fetcher.urls) <= 40
    assert result.summary.startswith(f"Read {result.proof['sources_read']} sources from ")
    listed = sources_section(result.summary)
    assert all(url in listed for url in result.proof["sources"])
    assert set(re.findall(r"https://\S+", listed)) <= set(fetcher.urls)  # nothing invented
    assert "## Evidence" in result.summary and "## Confidence and limits" in result.summary
    # the notes kept the quote and figure the page contains, and dropped the invented ones
    assert "999 units" not in result.summary
    domains = [re.sub(r"^https?://([^/]+)/.*$", r"\1", u) for u in fetcher.urls]
    assert max(domains.count(d) for d in set(domains)) <= 2  # two pages per site at most


async def test_progress_steps_carry_the_documented_labels() -> None:
    research, *_, steps = run_of()
    await research.run("Research solar batteries")

    labels = steps.labels()
    assert "Planned 8 questions" in labels
    assert any(label.startswith("Searching (query ") and " of " in label for label in labels)
    assert any(re.fullmatch(r"Read \d+ of \d+ sources", label) for label in labels)
    assert any(re.fullmatch(r"Read \d+ sources from \d+ sites", label) for label in labels)
    assert "Analysing the sources" in labels and "Writing" in labels
    assert "Checking citations" in labels


async def test_fewer_reachable_sites_than_the_minimum_is_reported_exactly_not_padded() -> None:
    fetcher = FakeFetcher(skips={f"site{i}.example": "paywall" for i in range(6, 36)})
    research, *_ = run_of(fetcher=fetcher)

    result = await research.run("Research solar batteries")

    assert result.ok and result.proof["domains"] == 6
    assert result.proof["sources_read"] == len(result.citations)
    assert f"Read {result.proof['sources_read']} sources from 6 sites" in result.summary
    assert "Fewer than the 20 sites I aimed for could be read" in result.summary
    assert result.proof["skipped_reasons"]["paywall"] >= 1
    assert "paywall" in result.summary  # the reason for the skips is said


async def test_a_prompt_injection_page_is_flagged_ignored_and_not_a_source() -> None:
    fetcher = FakeFetcher(flagged={"site3.example"})
    research, *_ = run_of(fetcher=fetcher)

    result = await research.run("Research solar batteries")

    assert result.proof["flagged_pages"] >= 1
    assert "site3.example" not in sources_section(result.summary)
    assert "tried to give instructions to an AI" in result.summary
    assert result.proof["skipped_reasons"]["injection"] >= 1


async def test_citations_that_point_to_no_read_source_are_removed_and_claims_are_marked() -> None:
    body = "Sales doubled in the third quarter [99]. The market will certainly grow next year."
    research, *_ = run_of(gateway=FakeGateway(body=body))

    result = await research.run("Research solar batteries")

    assert "[99]" not in result.summary
    assert "[inference: no source cited]" in result.summary
    assert "citation(s) pointed to no source I read" in result.summary


async def test_a_figure_the_cited_source_does_not_contain_is_flagged() -> None:
    body = "Revenue reached 88 billion dollars [1]."
    research, *_ = run_of(gateway=FakeGateway(body=body))

    result = await research.run("Research solar batteries")

    assert "[figure not found in the cited source]" in result.summary
    assert result.proof["unmatched_figures"] == 1


async def test_contradictions_are_called_out_with_their_sources() -> None:
    synthesis = Synthesis(
        findings=[Finding(claim="Costs fell.", sources=[1, 2])],
        contradictions=[
            Contradiction(
                topic="Cost trend",
                positions=[
                    Position(claim="Costs fell by half", sources=[1]),
                    Position(claim="Costs stayed flat", sources=[2]),
                ],
            )
        ],
    )
    research, *_ = run_of(gateway=FakeGateway(synthesis=synthesis))

    result = await research.run("Research solar batteries")

    assert "## Where sources disagree" in result.summary
    assert "Costs fell by half [1]" in result.summary and "Costs stayed flat [2]" in result.summary


async def test_a_gap_triggers_exactly_one_more_round_and_never_a_third() -> None:
    research, gateway, search, *_ = run_of(gateway=FakeGateway(follow_ups=True))

    result = await research.run("Research solar batteries")

    assert result.proof["rounds"] == 2
    assert "pricing details" in search.queries
    assert gateway.calls.count("Synthesis") == 2


async def test_the_second_round_is_off_when_the_cap_is_one_round() -> None:
    research, gateway, *_ = run_of(
        gateway=FakeGateway(follow_ups=True), limits=ResearchLimits(max_rounds=1)
    )

    result = await research.run("Research solar batteries")

    assert result.proof["rounds"] == 1 and gateway.calls.count("Synthesis") == 1


async def test_the_model_call_cap_is_never_exceeded() -> None:
    research, gateway, *_ = run_of(limits=ResearchLimits(max_model_calls=14))

    result = await research.run("Research solar batteries")

    assert len(gateway.calls) <= 14 and result.proof["model_calls"] <= 14
    assert result.ok and result.proof["stopped"] == "budget"
    assert "model-call cap" in result.summary


@pytest.mark.parametrize(("asked", "expected"), [(None, 40), (50, 50), (500, 60)])
async def test_the_number_of_pages_follows_the_request_up_to_the_hard_cap(
    asked: int | None, expected: int
) -> None:
    limits = ResearchLimits(max_model_calls=400, candidate_target=200)
    research, _, _, fetcher, _ = run_of(
        limits=limits, search=FakeSearch(sites=300), fetcher=FakeFetcher()
    )

    await research.run("Research solar batteries", requested_sources=asked)

    assert len(fetcher.urls) == expected
    assert len(fetcher.urls) <= 60


async def test_a_cancel_stops_fetching_within_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "CANCEL_POLL_SECONDS", 0.01)
    gate = asyncio.Event()

    async def block(_: str) -> None:
        await gate.wait()  # a page that never answers

    steps = Steps(cancel_after=2)
    research = DeepResearch(
        gateway=FakeGateway(),
        search=FakeSearch(),
        fetcher=FakeFetcher(on_fetch=block),  # type: ignore[arg-type]
        limits=ResearchLimits(),
        progress=steps,
    )

    result = await asyncio.wait_for(research.run("Research solar batteries"), timeout=5)

    assert not result.ok and result.proof["error"] == "cancelled"
    assert "Writing" not in steps.labels()


async def test_the_time_cap_stops_reading_and_the_result_says_so() -> None:
    now = [0.0]

    async def slow_page(_: str) -> None:
        now[0] += 30.0  # each page costs 30 s on the fake clock

    research, *_ = run_of(
        fetcher=FakeFetcher(on_fetch=slow_page),
        limits=ResearchLimits(time_cap_seconds=600),
        clock=lambda: now[0],
    )

    result = await research.run("Research solar batteries")

    assert result.proof["stopped"] == "time_cap" and result.proof["not_reached"] >= 1
    assert "not reached" in result.summary


async def test_a_failed_plan_call_falls_back_to_a_plan_made_by_code() -> None:
    research, gateway, search, *_, steps = run_of(gateway=FakeGateway(plan_error=True))

    result = await research.run("solar batteries")

    assert result.ok and search.queries  # still searched
    assert "Planned 6 questions" in steps.labels()


async def test_the_per_minute_search_limit_waits_and_retries() -> None:
    research, _, search, *_ = run_of(search=FakeSearch(limited_first=2))

    result = await research.run("Research solar batteries")

    assert result.ok and search.queries  # the limited answers were retried, not counted as empty


async def test_no_search_results_is_a_plain_failure_not_an_invented_answer() -> None:
    class Empty:
        async def __call__(self, query: str) -> tuple[list[SearchHit], bool]:
            return [], False

    research = DeepResearch(
        gateway=FakeGateway(),
        search=Empty(),
        fetcher=FakeFetcher(),  # type: ignore[arg-type]
        limits=ResearchLimits(),
    )

    result = await research.run("Research solar batteries")

    assert not result.ok and result.proof["error"] == "no_results"
    assert result.citations == []


async def test_when_every_page_is_blocked_nothing_is_written() -> None:
    research, gateway, *_ = run_of(fetcher=FakeFetcher(skips={"example": "blocked"}))

    result = await research.run("Research solar batteries")

    assert not result.ok and result.proof["error"] == "nothing_read"
    assert "text" not in gateway.calls  # no writing call was made
