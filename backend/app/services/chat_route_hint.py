"""Decision-model tie-breaker for an ambiguous chat route (ADR-0029).

The deterministic router stays the authority. Only when it fell through to a weak, default reason
(no clear file or task signal) AND the user named no count or format, one ``route`` question is
asked. The answer can only ESCALATE: toward an approval-gated send or a background task. It never
lowers an approval route, never removes tools, and any failure keeps the router's decision.
"""

from __future__ import annotations

from app.decisions import runtime as decisions
from app.decisions.questions import choice, untrusted_block
from app.services.chat_router import Decision, Route, states_explicit_spec

# Reasons that mean "no strong signal either way" (see chat_router.route_message).
AMBIGUOUS_REASONS = frozenset({"no_file_intent", "text_deliverable", "file_delivery"})
HINT_REASON_PREFIX = "decision_"

_OPTIONS = {
    "answer": "A conversational answer, explanation, translation or short text in the chat.",
    "file": "A small file (a note, a text or code file) the assistant makes right away.",
    "task": "Big or slow work that needs a background task: long documents, research with "
    "sources, many pages, a website or a multi-step job.",
    "approval": "An action outside the chat that needs the user's approval: sending an email or "
    "message, publishing, deleting, replacing or merging something.",
}
_PICK_MIN_PROBABILITY = 0.8
_PICK_MIN_CONFIDENCE = 0.4


async def refine_route(decision: Decision | None, text: str) -> Decision:
    """The router's decision, possibly escalated by the decision model; never weakened."""
    if decision is None:
        raise ValueError("a decision is required")
    if (
        decision.reason not in AMBIGUOUS_REASONS
        or decision.route not in (Route.ANSWER, Route.MODEL)
        or states_explicit_spec(text)
        or not decisions.available("route")
    ):
        return decision
    question = choice(
        "route",
        "Which kind of handling does the user's message need? Judge only what the user is "
        "asking for in the message.",
        _OPTIONS,
    )
    answers = await decisions.decide("route", untrusted_block("user message", text), [question])
    if answers is None:
        return decision
    picked = answers.pick("route", _PICK_MIN_PROBABILITY, _PICK_MIN_CONFIDENCE)
    if picked == "approval":
        return Decision(Route.APPROVAL, f"{HINT_REASON_PREFIX}approval")
    if picked == "task":
        return Decision(Route.TASK, f"{HINT_REASON_PREFIX}task")
    if picked == "file" and decision.route is Route.ANSWER:
        return Decision(Route.MODEL, f"{HINT_REASON_PREFIX}file")
    return decision
