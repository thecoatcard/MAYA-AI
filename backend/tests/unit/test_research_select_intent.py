"""Candidate selection (dedupe, per-site caps, spam), the request reading and the plan hook."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.state import SubTask
from app.research import runtime
from app.research.intent import (
    needs_topic,
    refusal_text,
    requested_sources,
    start_text,
    topic_of,
)
from app.research.limits import ResearchLimits
from app.research.models import Candidate
from app.research.plan import STEP_ID, apply_deep_research
from app.research.select import merge_hits, pick, rank, usable
from app.search.models import SearchHit


def hit(
    url: str, title: str = "Solar battery storage report", published: str | None = None
) -> SearchHit:
    return SearchHit(title, url, "solar battery storage", published)


def test_the_same_page_in_other_spellings_is_one_candidate_and_counts_as_repeated() -> None:
    found: dict[str, Candidate] = {}
    added = merge_hits(
        found,
        [
            hit("https://www.a.example/x/?utm_source=t"),
            hit("http://a.example/x#top"),
            hit("https://b.example/y"),
        ],
    )

    assert added == 2 and len(found) == 2
    assert max(c.hits for c in found.values()) == 2


def test_spam_social_and_unreadable_file_types_never_become_candidates() -> None:
    found: dict[str, Candidate] = {}
    merge_hits(
        found,
        [
            hit("https://www.youtube.com/watch?v=1"),
            hit("https://x.com/user/status/1"),
            hit("https://a.example/file.zip"),
            hit("https://a.example/deck.pptx"),
            hit("ftp://a.example/x"),
            hit("https://a.example/report.pdf"),
        ],
    )

    assert [c.url for c in found.values()] == ["https://a.example/report.pdf"]
    assert not usable("javascript:alert(1)")


def test_the_pick_takes_one_page_per_site_first_and_never_more_than_two() -> None:
    found: dict[str, Candidate] = {}
    for site in range(25):
        for page in range(4):
            merge_hits(found, [hit(f"https://s{site}.example/p{page}", f"Solar storage {page}")])

    chosen = pick(rank(found.values(), "solar battery storage"), 50)

    domains = [c.domain for c in chosen]
    assert len(set(domains)) == 25  # every site is reached
    assert max(domains.count(d) for d in set(domains)) == 2
    first_pass = domains[:25]
    assert len(set(first_pass)) == 25  # the first 25 are 25 different sites


def test_a_pick_respects_pages_already_tried_per_site() -> None:
    found: dict[str, Candidate] = {}
    for page in range(3):
        merge_hits(found, [hit(f"https://s.example/p{page}", f"Solar storage {page}")])

    chosen = pick(rank(found.values(), "solar"), 10, domain_counts={"s.example": 1})

    assert len(chosen) == 1  # one page was already read: only one more is allowed


def test_authority_recency_and_thin_listing_pages_move_the_score() -> None:
    found: dict[str, Candidate] = {}
    merge_hits(
        found,
        [
            hit("https://energy.gov/solar", "Solar battery storage", "2026-05-01"),
            hit("https://blog.example/tag/solar", "Solar battery storage"),
            hit("https://shop.example/a", "Top 10 best coupon solar deals"),
        ],
    )

    ranked = rank(found.values(), "solar battery storage")

    assert ranked[0].url == "https://energy.gov/solar"
    assert pick(ranked, 10) and all(c.score >= -2.0 for c in pick(ranked, 10))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("read 50 sites about batteries", 50), ("use 100 websites", 100), ("2 sources please", None)],
)
def test_the_requested_number_of_sites_is_read_from_the_message(
    text: str, expected: int | None
) -> None:
    assert requested_sources(text) == expected


@pytest.mark.parametrize("text", ["deep research", "research karo", "please do research", "  "])
def test_a_message_without_a_subject_needs_a_topic(text: str) -> None:
    assert needs_topic(text)


@pytest.mark.parametrize("text", ["research solar batteries", "bitcoin ETF kar research"])
def test_a_message_with_a_subject_does_not(text: str) -> None:
    assert not needs_topic(text)


def test_the_start_text_promises_effort_and_never_results() -> None:
    limits = ResearchLimits()
    text = start_text(topic_of("solar batteries"), limits)

    assert text.startswith("Started: deep research on solar batteries.")
    assert "at least 20 different sites" in text and "up to 40" in text
    assert "will not share any findings until it finishes" in text
    assert "10 minutes" in text
    assert "at least 20" in start_text("x", limits, 50) and "up to 50" in start_text(
        "x", limits, 50
    )
    assert "up to 60" in start_text("x", limits, 500)  # the hard cap
    assert refusal_text("it is off").startswith("Deep research did not start")


def settings(**over: object) -> SimpleNamespace:
    base = {
        "deep_research_enabled": True,
        "deep_research_min_domains": 20,
        "deep_research_max_sources": 40,
        "deep_research_hard_max_sources": 60,
        "deep_research_time_cap_seconds": 600,
        "deep_research_max_model_calls": 60,
        "deep_research_max_rounds": 2,
        "deep_research_fetch_concurrency": 6,
    }
    return SimpleNamespace(**{**base, **over})


def search_service(names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        chain=SimpleNamespace(active_names=lambda without=None: [n for n in names if n != without])
    )


def test_the_kill_switch_and_missing_search_or_model_fail_closed_with_a_plain_reason() -> None:
    gateway = SimpleNamespace(client=object())
    ok = search_service(["ddgs"])

    assert runtime.unavailable_reason(settings(), ok, gateway) is None
    assert "switched off" in (
        runtime.unavailable_reason(settings(deep_research_enabled=False), ok, gateway) or ""
    )
    assert "no model" in (
        runtime.unavailable_reason(settings(), ok, SimpleNamespace(client=None)) or ""
    )
    assert "web search" in (
        runtime.unavailable_reason(settings(), search_service(["gemini"]), gateway) or ""
    )
    assert "web search" in (runtime.unavailable_reason(settings(), None, gateway) or "")


def test_the_status_reports_the_caps_and_availability() -> None:
    out = runtime.status(settings(), search_service(["ddgs"]), SimpleNamespace(client=object()))

    assert out["enabled"] and out["available"] and out["max_sources"] == 40
    assert out["time_cap_seconds"] == 600 and out["hard_max_sources"] == 60


def test_the_limits_default_to_the_approved_values_and_clamp_user_requests() -> None:
    limits = ResearchLimits.from_settings(settings())

    assert (limits.min_domains, limits.max_sources, limits.time_cap_seconds) == (20, 40, 600)
    assert limits.max_model_calls == 60
    assert limits.sources_for(None) == 40 and limits.sources_for(50) == 50
    assert limits.sources_for(999) == 60


def node(step: str, agent: str, deps: list[str] | None = None) -> SubTask:
    return SubTask(id=step, assigned_agent=agent, description=step, dependencies=deps or [])


def test_an_existing_research_step_becomes_the_deep_run() -> None:
    nodes = [node("step_1", "research"), node("step_2", "document", ["step_1"])]

    out = apply_deep_research(nodes, "research solar and make a pdf", {"sources": 50})

    assert [n.id for n in out] == ["step_1", "step_2"]
    assert out[0].input_data["deep_research"]["sources"] == 50
    assert out[0].input_data["deep_research"]["request"].startswith("research solar")
    assert "deep_research" not in out[1].input_data


def test_a_plan_without_research_gets_one_in_front() -> None:
    nodes = [node("step_1", "document")]

    out = apply_deep_research(nodes, "make a pdf on solar", {})

    assert out[0].id == STEP_ID and "deep_research" in out[0].input_data
    assert out[1].dependencies == [STEP_ID]


def test_chat_only_deep_research_keeps_only_the_research_step() -> None:
    # Owner bug: "find best book ..." showed a File Step and a grounded search. No file was asked,
    # so the planner's file step is dropped and the plain research step becomes the deep run.
    nodes = [
        node("step_1", "research"),
        node("step_2", "research"),
        node("step_3", "document", ["step_1", "step_2"]),
    ]
    nodes[0].tool = "web_search"

    out = apply_deep_research(nodes, "find best book i must read for self improvement", {})

    assert [str(n.assigned_agent) for n in out] == ["research"]
    assert "deep_research" in out[0].input_data and out[0].tool is None


def test_a_file_request_merges_extra_research_steps_and_clears_the_search_tool() -> None:
    nodes = [
        node("step_1", "research"),
        node("step_2", "research"),
        node("step_3", "document", ["step_1", "step_2"]),
    ]
    nodes[0].tool = "web_search"

    out = apply_deep_research(nodes, "research books and make a pdf", {})

    assert [n.id for n in out] == ["step_1", "step_3"]
    assert out[0].tool is None and "deep_research" in out[0].input_data
    assert out[1].dependencies == ["step_1"]  # the merged step no longer exists
