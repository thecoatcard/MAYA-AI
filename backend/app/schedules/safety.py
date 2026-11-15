"""What an unattended scheduled run may do (ADR-0033).

The planner decides the steps; this filter then keeps only the safe ones: research (read-only web
search / deep research) and, when the user named a file format, the document step that writes an
artifact. Everything else (mail, Notion, GitHub, code, browser, project files, any explicit tool
or approval step) is removed. A removed step is never replaced by an auto-approval: if a step
that remains still needs approval, a question or a missing detail, the run ends as
``needs_attention`` and the pending card stays for the user.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.agents.state import SubTask

logger = logging.getLogger("maya.schedules")

SAFE_AGENTS = frozenset({"research", "document"})
ROOT_ID = "step_scheduled_research"


def _agent(node: SubTask) -> str:
    return str(getattr(node.assigned_agent, "value", node.assigned_agent))


def _allowed(node: SubTask, file_format: str) -> bool:
    if _agent(node) not in SAFE_AGENTS or node.tool or node.requires_approval:
        return False
    return not (_agent(node) == "document" and file_format == "chat")


def make_plan_filter(
    file_format: str, instruction: str
) -> Callable[[list[SubTask]], list[SubTask]]:
    def keep_safe(nodes: list[SubTask]) -> list[SubTask]:
        kept = [n for n in nodes if _allowed(n, file_format)]
        removed = len(nodes) - len(kept)
        if removed:
            logger.info("schedule_plan_filtered removed=%d", removed)
        if not any(_agent(n) == "research" for n in kept):
            # Every report needs its facts first: put a read-only research step in front.
            root = SubTask(
                id=ROOT_ID,
                assigned_agent="research",
                description=instruction[:2000],
                dependencies=[],
                status="pending",
                input_data={},
            )
            for node in kept:
                if not node.dependencies:
                    node.dependencies = [ROOT_ID]
            kept = [root, *kept]
        ids = {n.id for n in kept}
        for node in kept:
            node.dependencies = [d for d in node.dependencies if d in ids]
        # A file format asked for but no document step planned: leave it, the planner owns that.
        return kept

    return keep_safe
