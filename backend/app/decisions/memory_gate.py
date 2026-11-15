"""Memory gate with the decision model (purpose ``memory_gate``, ADR-0029).

Advisory only, fail-open, on the chat hot path:

* ``needs_memory``: does answering this request need the user's saved memories at all (personal
  facts, preferences, past decisions, "my", "remember", "as usual", follow-ups)? A clear NO skips
  recall and injects nothing, so memory cannot leak into a general-knowledge, coding, translation
  or news question and the turn is faster.
* ``rank``: of the memories recall already fetched, which are relevant to this request? Only
  those above a threshold are injected (at most ``MAX_INJECTED``), still tagged as context and
  never as the topic (the block header says so).

Any error, timeout, open circuit or missing answer returns None, and the caller keeps today's
behaviour. Standing preferences (language, format) never pass through here. Nothing is logged
but the outcome and counts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Final, Protocol

from app.decisions import runtime as decisions
from app.decisions.questions import is_no, noul, score, untrusted_block

PURPOSE: Final = "memory_gate"
GATE_TIMEOUT_SECONDS: Final = 1.2
NOT_NEEDED_LABEL: Final = "No memory needed"
NOT_NEEDED_THRESHOLD: Final = 0.85
# Relevance levels, lowest to highest; a memory is kept at RELEVANT_LEVEL or above.
LEVELS: Final = ("unrelated", "loosely related", "relevant", "needed")
RELEVANT_LEVEL: Final = 2
MAX_INJECTED: Final = 6
MAX_CANDIDATES: Final = 12
_MEMORY_CHARS: Final = 300

logger = logging.getLogger("maya.decisions")


class _Candidate(Protocol):
    @property
    def text(self) -> str: ...


async def needs_memory(request: str) -> bool | None:
    """False only when the model clearly says no memory is needed; True when it clearly says yes;
    None (no opinion) otherwise or on any failure."""
    if not decisions.available(PURPOSE):
        return None
    answers = await decisions.decide(
        PURPOSE,
        untrusted_block("request", request, limit=2000),
        [
            noul(
                "needs_memory",
                "Does answering this request require the user's own saved memories: personal "
                "facts about the user, their preferences, their past decisions or earlier "
                "conversations, words like 'my', 'remember', 'as usual', or a follow-up that "
                "only makes sense with that history? Answer no for general knowledge, coding "
                "questions, translation, math, news or web research that does not depend on who "
                "the user is.",
                true_description="The answer depends on what MAYA knows about this user.",
                false_description="The request can be answered fully without any user memory.",
            )
        ],
        timeout=GATE_TIMEOUT_SECONDS,
    )
    if answers is None:
        logger.info("memory_gate outcome=no_answer")
        return None
    answer = answers.noul("needs_memory")
    if is_no(answer, NOT_NEEDED_THRESHOLD):
        logger.info("memory_gate outcome=not_needed")
        return False
    logger.info("memory_gate outcome=needed")
    return True


async def rank(request: str, memories: Sequence[_Candidate]) -> list[int] | None:
    """Indexes (best first, at most ``MAX_INJECTED``) of the memories relevant to the request, or
    None for no opinion (the caller keeps every candidate, as before)."""
    if len(memories) < 2 or not decisions.available(PURPOSE):
        return None
    candidates = list(memories)[:MAX_CANDIDATES]
    listing = "\n".join(
        f"[{index}] {' '.join(candidate.text.split())[:_MEMORY_CHARS]}"
        for index, candidate in enumerate(candidates)
    )
    questions = [
        score(
            f"m{index}",
            f"How relevant is memory [{index}] to answering the request? It may shape language, "
            "tone, names or preferences, but a memory that is only about another topic is "
            "unrelated.",
            LEVELS,
        )
        for index in range(len(candidates))
    ]
    answers = await decisions.decide(
        PURPOSE,
        untrusted_block("request", request, limit=2000)
        + "\n"
        + untrusted_block("memories", listing, limit=6000),
        questions,
        timeout=GATE_TIMEOUT_SECONDS,
    )
    if answers is None:
        logger.info("memory_gate outcome=rank_no_answer candidates=%d", len(candidates))
        return None
    scored: list[tuple[float, int]] = []
    for index in range(len(candidates)):
        answer = answers.score(f"m{index}")
        if answer is not None and answer.score >= RELEVANT_LEVEL:
            scored.append((answer.score, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    kept = [index for _, index in scored[:MAX_INJECTED]]
    logger.info("memory_gate outcome=ranked candidates=%d kept=%d", len(candidates), len(kept))
    return kept
