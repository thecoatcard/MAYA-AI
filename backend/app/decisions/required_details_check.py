"""Advisory decision-model questions for the required-details gate (ADR-0029).

The deterministic gate (``app.services.required_details``) decides. These helpers only add an
opinion where the rules are ambiguous, and every one returns "no opinion" (None or the input
order) when the decision service is off, unavailable or unsure: the gate then keeps its own
answer. Nothing here may pick a Notion page silently: a ranking only orders the options.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.decisions import runtime as decisions
from app.decisions.questions import choice, noul, untrusted_block

logger = logging.getLogger("maya")

THRESHOLD = 0.85
MAX_CANDIDATES = 8


async def has_topic(request: str) -> bool | None:
    """True or False when the decision model is clearly sure the message names a subject for the
    document or notes (not just 'make notes'); None for no opinion."""
    if not decisions.available("has_topic"):
        return None
    answers = await decisions.decide(
        "has_topic",
        untrusted_block("request", request, limit=2000),
        [
            noul(
                "names_subject",
                "Does the message say what the notes, document, deck or research should be "
                "ABOUT (a concrete subject)? A message that only asks to make one, with no "
                "subject, is no.",
            )
        ],
    )
    if answers is None:
        return None
    if answers.yes("names_subject", THRESHOLD):
        return True
    from app.decisions.questions import is_no

    return False if is_no(answers.noul("names_subject"), THRESHOLD) else None


async def rank_notion_pages(topic: str, titles: Sequence[str]) -> list[str]:
    """``titles`` with the page that best fits ``topic`` first (as given when there is no clear
    winner or no decision service). Titles are never dropped or invented."""
    candidates = list(dict.fromkeys(t for t in titles if t.strip()))[:MAX_CANDIDATES]
    if len(candidates) < 2 or not topic.strip() or not decisions.available("notion_parent"):
        return candidates
    options = {f"p{i}": title[:120] for i, title in enumerate(candidates)}
    answers = await decisions.decide(
        "notion_parent",
        untrusted_block("topic", topic, limit=500),
        [
            choice(
                "best_parent",
                "Which Notion page is the best parent for new notes about the topic?",
                options,
            )
        ],
    )
    picked = answers.pick("best_parent") if answers is not None else None
    if picked is None or picked not in options:
        return candidates
    best = options[picked]
    return [t for t in candidates if t[:120] == best][:1] + [
        t for t in candidates if t[:120] != best
    ]


async def run_startup_check(service: object) -> dict[str, str]:
    """One tiny decision call at startup. Never raises, never logs a key; the result is logged as
    ``decisions_startup_check`` and kept for the status endpoint."""
    outcome = {"state": "failed", "reason": "decision service is not running"}
    try:
        decide = getattr(service, "decide", None)
        if decide is None:
            raise RuntimeError("decision service is not running")
        answers = await decide(
            "startup_check",
            "Is the sky usually blue on a clear day?",
            [noul("check", "Answer the yes/no question in the input.")],
            timeout=8.0,
            background=True,
        )
        if answers is None:
            outcome = {"state": "failed", "reason": "no answer (off, circuit open or error)"}
        else:
            outcome = {"state": "ok", "reason": "answered"}
    except Exception as error:  # noqa: BLE001 - startup must never fail on this
        outcome = {"state": "failed", "reason": type(error).__name__}
    outcome["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    logger.info("decisions_startup_check state=%s reason=%s", outcome["state"], outcome["reason"])
    return outcome
