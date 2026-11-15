"""Mail triage with the decision model (ADR-0029): urgency, category and needs-reply.

Advisory labels added next to a mail tool result; they never change what is read, sent or
deleted. Any failure returns None and the result is exactly what it was before.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.decisions import runtime as decisions
from app.decisions.questions import Answers, choice, noul, untrusted_block

URGENCY = {
    "urgent": "Needs attention today: a deadline, an outage, a problem or an explicit ask for now.",
    "normal": "A normal message with no time pressure.",
    "low": "Can wait: informational, promotional or automated.",
}
CATEGORY = {
    "personal": "From a friend or family member, or a personal matter.",
    "work": "Work, a client, a colleague, a project or a meeting.",
    "finance": "A bill, an invoice, a payment, a bank or tax matter.",
    "newsletter": "A newsletter, marketing or promotional mail.",
    "notification": "An automated notification from a service or an app.",
    "security": "A security alert, sign-in code, password or verification mail.",
}
MIN_PROBABILITY = 0.55
REPLY_THRESHOLD = 0.7
MAX_ITEMS = 25
NOTE = "Estimated by a model from the visible text; check the mail itself before acting."


def _labels(answers: Answers, suffix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    urgency = answers.pick(f"u{suffix}", MIN_PROBABILITY)
    if urgency:
        out["urgency"] = urgency
    category = answers.pick(f"c{suffix}", MIN_PROBABILITY)
    if category:
        out["category"] = category
    reply = answers.noul(f"r{suffix}")
    if reply is not None and (reply.probability >= REPLY_THRESHOLD or reply.probability <= 0.3):
        out["needs_reply"] = reply.probability >= REPLY_THRESHOLD
    return out


def _questions(suffix: str, what: str) -> list[Any]:
    return [
        choice(f"u{suffix}", f"How urgent is {what}?", URGENCY),
        choice(f"c{suffix}", f"Which category fits {what} best?", CATEGORY),
        noul(f"r{suffix}", f"Does {what} expect a reply from the recipient?"),
    ]


async def triage_list(lines: Sequence[str]) -> list[dict[str, Any]] | None:
    """Labels for the numbered header lines of a mail list; None when there is no answer."""
    items = list(lines)[:MAX_ITEMS]
    if not items or not decisions.available("mail_triage"):
        return None
    questions: list[Any] = []
    for i in range(1, len(items) + 1):
        questions.extend(_questions(str(i), f"message [{i}]"))
    body = untrusted_block("mail list", "\n".join(items), limit=20_000)
    answers = await decisions.decide("mail_triage", body, questions, background=False)
    if answers is None:
        return None
    labelled = [{"n": i, **_labels(answers, str(i))} for i in range(1, len(items) + 1)]
    return [x for x in labelled if len(x) > 1] or None


async def triage_message(text: str) -> dict[str, Any] | None:
    """Labels for one opened mail; None when there is no answer."""
    if not text.strip() or not decisions.available("mail_triage"):
        return None
    answers = await decisions.decide(
        "mail_triage", untrusted_block("mail", text), _questions("", "this message")
    )
    if answers is None:
        return None
    return _labels(answers, "") or None
