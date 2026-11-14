"""Planner tool-selection hint from the decision model (ADR-0029).

A few yes/no questions about which specialist steps the instruction likely needs. The result is
one advisory sentence added to the planner prompt; the planner and its validators stay in
charge, and no answer means the prompt is exactly what it was.
"""

from __future__ import annotations

from app.decisions import runtime as decisions
from app.decisions.questions import noul, untrusted_block

THRESHOLD = 0.85
_NEEDS = {
    "needs_research": ("research", "Does fulfilling the request need current facts from the web?"),
    "needs_document": (
        "document",
        "Does the request ask for a produced file such as a PDF, Word, slides or spreadsheet?",
    ),
    "needs_email": ("email", "Does the request ask to write or send an email?"),
    "needs_code": ("coding", "Does the request ask for source code or a software project?"),
    "needs_notion": ("pm", "Does the request involve the user's Notion workspace or tasks?"),
}


async def planner_hint(instruction: str) -> str:
    """An advisory block for the planner prompt, or ``""``."""
    if not decisions.available("planner_hint"):
        return ""
    answers = await decisions.decide(
        "planner_hint",
        untrusted_block("instruction", instruction, limit=4000),
        [noul(key, question) for key, (_agent, question) in _NEEDS.items()],
        background=False,
    )
    if answers is None:
        return ""
    agents = [agent for key, (agent, _q) in _NEEDS.items() if answers.yes(key, THRESHOLD)]
    if not agents:
        return ""
    return (
        "\n\nAdvisory hint from a classifier (it can be wrong; the instruction above decides): "
        "the request likely needs these specialist steps: " + ", ".join(agents) + "."
    )
