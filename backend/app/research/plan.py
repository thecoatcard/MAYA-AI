"""Turn a planned task into a deep-research task (ADR-0032).

The normal planner still decides what the user asked for (an answer, a PDF, a DOCX, a deck, a Notion
page). Only the research step changes: it carries ``input_data["deep_research"]`` and the research
agent then runs the wide, cited run instead of one grounded search. The steps after it read its
cited report as their dependency output, so the existing document pipelines do the rest. When the
planner made no research step, one is added in front of the others.

Two rules keep the run honest:

* The user's own words decide the deliverable (``deliverable_intent``). Unless they name a file or
  a destination, the answer is the report in chat: every other step the planner invented (a File
  or Builder step) is dropped, so a chat-only deep research has ONLY the research step.
* Exactly one research step remains. A second plain research step would run the old grounded
  single search (Gemini grounding), so it is merged into the deep-research step instead.
"""

from __future__ import annotations

from typing import Any

from app.agents.state import SubTask
from app.services.deliverable_intent import CHAT, requested_deliverable

STEP_ID = "step_deep_research"


def _is_plain_research(node: SubTask) -> bool:
    # The planner often hints ``web_search`` on a research step; that step is still the plain
    # grounded single search (Gemini google_search), so it is converted or merged too.
    return str(node.assigned_agent) == "research" and node.tool in (None, "", "web_search")


def apply_deep_research(
    nodes: list[SubTask], request: str, spec: dict[str, Any] | None = None
) -> list[SubTask]:
    data = {"request": request[:4000], **{k: v for k, v in (spec or {}).items() if v is not None}}
    if requested_deliverable(request) == CHAT:
        # Chat-only: the report itself is the answer; no file step, no second research step.
        return [
            SubTask(
                id=STEP_ID,
                assigned_agent="research",
                description=request[:2000],
                dependencies=[],
                status="pending",
                input_data={"deep_research": data},
            )
        ]
    chosen: SubTask | None = None
    merged: dict[str, str] = {}
    rest: list[SubTask] = []
    for node in nodes:
        if _is_plain_research(node):
            if chosen is None:
                chosen = node
            else:
                merged[str(node.id)] = str(chosen.id)
                continue
        rest.append(node)
    if chosen is not None:
        chosen.tool = None
        chosen.tool_args = {}
        chosen.input_data = {**(chosen.input_data or {}), "deep_research": data}
        chosen.description = request[:2000]
        for node in rest:
            node.dependencies = list(
                dict.fromkeys(merged.get(str(dep), str(dep)) for dep in node.dependencies)
            )
        return rest
    first = SubTask(
        id=STEP_ID,
        assigned_agent="research",
        description=request[:2000],
        dependencies=[],
        status="pending",
        input_data={"deep_research": data},
    )
    for node in rest:
        if not node.dependencies:
            node.dependencies = [STEP_ID]
    return [first, *rest]
