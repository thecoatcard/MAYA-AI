"""Phase 2 of the chat tool loop (ADR-0026): the pure parts of web_search, send_email and
recall, the router reasons that send a request to the loop, and the research agent's shared
grounded call.

Fakes only: the grounded search runs the real ResearchAgent over a fake model gateway.
"""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agents.schemas import AgentExecutionStatus, SpecialistAgentResult, SpecialistAgentRole
from app.agents.specialized.research import ResearchAgent
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.services.chat_router import Route, route_message
from app.services.document_tools import loop_applies
from app.services.loop_tools import (
    MAX_WEB_SEARCHES_PER_REPLY,
    EmailDraft,
    email_instruction,
    email_request,
    web_search_result,
)


def success(citations: list[dict[str, str]]) -> SpecialistAgentResult:
    return SpecialistAgentResult(
        agent_role=SpecialistAgentRole.RESEARCH,
        status=AgentExecutionStatus.SUCCESS,
        summary="Solar output rose [1].\n\n## Sources\n1. Solar report - https://example.org/solar",
        verification_proof={},
        citations=citations,
    )


# ------------------------------------------------------------------------------ web_search


def test_a_grounded_result_is_returned_as_numbered_sources() -> None:
    result = web_search_result(success([{"title": "Solar report", "url": "https://example.org/s"}]))

    assert result["status"] == "ok"
    assert result["sources"] == [{"n": 1, "title": "Solar report", "url": "https://example.org/s"}]
    assert "Solar output rose [1]." in result["answer"]
    assert "Cite only these numbers" in result["message"]


def test_a_missing_or_failed_search_is_plain_unavailable_with_no_sources() -> None:
    failed = SpecialistAgentResult(
        agent_role=SpecialistAgentRole.RESEARCH,
        status=AgentExecutionStatus.PARTIAL,
        summary="",
        verification_proof={"sources_unavailable": True},
    )
    for result in (None, failed, success([])):
        outcome = web_search_result(result)
        assert outcome["status"] == "unavailable"
        assert "Web search FAILED" in outcome["message"] and outcome["user_reason"]
        assert "sources" not in outcome


def test_the_sources_are_capped_and_titles_are_cut() -> None:
    many = [{"title": "t" * 500, "url": f"https://example.org/{i}"} for i in range(15)]
    sources = web_search_result(success(many))["sources"]

    assert len(sources) == 10
    assert len(sources[0]["title"]) == 200
    assert [s["n"] for s in sources] == list(range(1, 11))


def test_the_per_reply_web_search_cap_is_three() -> None:
    assert MAX_WEB_SEARCHES_PER_REPLY == 3


# ------------------------------------------------------------------------------ send_email


def test_a_valid_email_is_prepared_only_when_the_recipient_is_in_the_user_message() -> None:
    draft = email_request(
        {"to": "rahul@example.com", "subject": " Meeting  moved ", "body": "5 pm."},
        user_text="Email rahul@example.com that the meeting moved",
    )

    assert draft == EmailDraft("rahul@example.com", "Meeting moved", "5 pm.")


def test_a_recipient_that_is_not_in_the_user_message_is_refused() -> None:
    problem = email_request(
        {"to": "boss@example.com", "subject": "Hi", "body": "Hello"},
        user_text="Email my boss about the meeting",
    )

    assert isinstance(problem, str) and "not written in the user's message" in problem


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"to": "a@example.com, b@example.com", "subject": "Hi", "body": "x"}, "single"),
        ({"to": "", "subject": "Hi", "body": "x"}, "no recipient"),
        ({"to": "a@example.com", "subject": "", "body": "x"}, "subject and a body"),
        ({"to": "a@example.com", "subject": "Hi", "body": "   "}, "subject and a body"),
        ({"to": "a@example.com", "subject": "S" * 201, "body": "x"}, "too long"),
    ],
)
def test_an_invalid_email_is_refused_with_a_plain_reason(
    arguments: dict[str, Any], fragment: str
) -> None:
    user_text = "email a@example.com " + " ".join(str(v) for v in arguments.values())
    problem = email_request(arguments, user_text=user_text)

    assert isinstance(problem, str) and fragment in problem


def test_the_email_task_says_nothing_is_sent_without_approval() -> None:
    text = email_instruction(EmailDraft("rahul@example.com", "Hi", "Hello there."))

    assert "rahul@example.com" in text
    assert "nothing may be sent without that approval" in text


# ------------------------------------------------------------------------- router and gating


@pytest.mark.parametrize(
    ("message", "route", "reason"),
    [
        ("What is the latest news on solar power?", Route.ANSWER, "web_lookup"),
        ("What is solar power?", Route.ANSWER, "question"),
        ("Send an email to rahul@example.com saying hi", Route.APPROVAL, "approval_email"),
        ("Delete the old report file", Route.APPROVAL, "approval_verb"),
        ("Draw a picture of a red fox", Route.IMAGE, "image_request"),
        ("Research solar power with sources", Route.TASK, "research"),
    ],
)
def test_the_router_names_the_phase_two_reasons(message: str, route: Route, reason: str) -> None:
    decision = route_message(message)

    assert (decision.route, decision.reason) == (route, reason)


def test_a_document_with_a_web_word_is_not_a_web_lookup() -> None:
    decision = route_message("Make a report with the latest news on solar")

    assert decision.reason != "web_lookup"


@pytest.mark.parametrize(
    ("route", "reason", "connectors", "expected"),
    [
        ("answer", "web_lookup", False, True),
        ("image", "image_request", False, True),
        ("approval", "approval_email", False, True),
        ("approval", "approval_verb", False, False),
        ("answer", "question", False, False),
        ("answer", "question", True, True),
        ("model", "file_candidate", False, True),
        ("task", "repository_review", True, False),
        ("task", "research", False, False),
    ],
)
def test_loop_applies_takes_the_phase_two_reasons_and_connected_reads(
    route: str, reason: str, connectors: bool, expected: bool
) -> None:
    assert loop_applies(route, reason, connectors=connectors) is expected


# --------------------------------------------------- the shared grounded call (research agent)


class FakeGateway:
    """The model gateway's grounded call: one canned answer, or one error on every attempt."""

    def __init__(self, text: str = "", citations: list[dict[str, str]] | None = None) -> None:
        self.text = text
        self.citations = citations or []
        self.error: Exception | None = None
        self.calls = 0

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, citations=self.citations)


async def test_grounded_answer_cites_only_real_sources_and_numbers_them() -> None:
    gateway = FakeGateway(
        text="Solar rose [1] and [7], see https://invented.example/x.",
        citations=[{"title": "Solar report", "url": "https://example.org/solar"}],
    )
    agent = ResearchAgent(model_gateway=gateway)

    result = await agent.grounded_answer("solar news", workspace_id=uuid4())

    assert result.status is AgentExecutionStatus.SUCCESS
    assert "[7]" not in result.summary and "invented.example" not in result.summary
    assert "1. Solar report - https://example.org/solar" in result.summary
    assert [c["url"] for c in result.citations] == ["https://example.org/solar"]


async def test_grounded_answer_quota_is_degraded_not_failed() -> None:
    gateway = FakeGateway()
    gateway.error = GatewayFallbackExhaustedError("quota")
    agent = ResearchAgent(model_gateway=gateway)

    result = await agent.grounded_answer("solar news", workspace_id=uuid4())

    assert result.status is AgentExecutionStatus.PARTIAL
    assert result.verification_proof["sources_unavailable"] is True
    assert result.citations == []


async def test_grounded_answer_with_no_grounding_is_a_failure_with_no_sources() -> None:
    gateway = FakeGateway(text="An answer without any grounding.", citations=[])
    agent = ResearchAgent(model_gateway=gateway)

    result = await agent.grounded_answer("solar news", workspace_id=None)

    assert result.status is AgentExecutionStatus.FAILED
    assert result.citations == []


def test_only_file_routes_force_the_first_round_to_a_document_tool() -> None:
    from app.services.document_tools import file_turn

    for reason in ("binary_deliverable", "project", "file_candidate", "text_deliverable"):
        assert file_turn("model", reason) and file_turn("task", reason)
    assert file_turn("task", "over_limit_12_slides")
    assert not file_turn("answer", "question")
    assert not file_turn("task", "research")  # research keeps its own path
    assert not file_turn("model", "file_delivery")  # not one of the forced file routes
