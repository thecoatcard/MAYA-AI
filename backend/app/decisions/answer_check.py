"""Anti-hallucination checks for chat answers with the decision model (ADR-0029).

* ``needs_web_search``: a factual or "current" question answered without any web search.
* ``unsupported_by_sources``: after a web-search turn, is the answer backed by the sources found.

A positive flag only makes the reply MORE honest: a short note says the answer is unverified and
offers to search. It never deletes or rewrites the reply, and no answer adds nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.decisions import runtime as decisions
from app.decisions.questions import is_no, noul, untrusted_block

NEEDS_SEARCH_THRESHOLD = 0.85
UNSUPPORTED_THRESHOLD = 0.85
MIN_ANSWER_CHARS = 60
MAX_EVIDENCE_CHARS = 10_000

UNVERIFIED_NOTE = (
    "Note: this answer comes from my general knowledge and was not checked against the web, so "
    "it may be out of date. Ask me to search the web if you want it verified."
)
UNSUPPORTED_NOTE = (
    "Note: some details above may not be backed by the sources I found, so treat them as "
    "unverified."
)


def evidence_text(outcome: Mapping[str, Any]) -> str:
    """Bounded text of a web-search tool result (answer, passages, source titles)."""
    keep = {k: outcome.get(k) for k in ("answer", "passages", "sources") if outcome.get(k)}
    return json.dumps(keep, default=str, ensure_ascii=False)[:MAX_EVIDENCE_CHARS]


async def needs_web_search(question: str, answer: str) -> bool:
    """True only for a clear "this needs current facts from the web"."""
    if len(answer) < MIN_ANSWER_CHARS or not decisions.available("search_need"):
        return False
    answers = await decisions.decide(
        "search_need",
        untrusted_block("user question", question, limit=3000),
        [
            noul(
                "needs_web_search",
                "Does answering this question correctly require current or fast-changing facts "
                "(news, prices, versions, recent events, people in current roles) that general "
                "knowledge may have wrong or out of date?",
            )
        ],
    )
    return answers is not None and answers.yes("needs_web_search", NEEDS_SEARCH_THRESHOLD)


async def unsupported_by_sources(question: str, answer: str, evidence: str) -> bool:
    """True only for a clear "the answer is NOT supported by the sources"."""
    if len(answer) < MIN_ANSWER_CHARS or not evidence or not decisions.available("grounding"):
        return False
    answers = await decisions.decide(
        "grounding",
        untrusted_block("user question", question, limit=2000)
        + "\n"
        + untrusted_block("answer", answer, limit=5000)
        + "\n"
        + untrusted_block("sources", evidence, limit=MAX_EVIDENCE_CHARS),
        [
            noul(
                "supported",
                "Is every factual claim in the answer supported by the sources provided?",
            )
        ],
        background=True,
    )
    return answers is not None and is_no(answers.noul("supported"), UNSUPPORTED_THRESHOLD)
