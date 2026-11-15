"""Self-healing rules for subtask execution (pure decisions; the worker applies them).

When a step fails for a RECOVERABLE reason the worker tries the next route before failing the
task. This module only DECIDES the route; it never runs anything and never touches the
database. The rules (see ``docs/architecture/AGENT_REQUEST_FLOW.md`` and ADR 0019):

1. An invented or unavailable tool: skip the step when it only saves a file a predecessor
   already produced, otherwise run it again without the tool.
2. An unsuitable or unavailable agent (for example ``pm`` asked to outline slides): run the
   generic text executor, only when the instruction is content work an LLM can do and does not
   need an integration (email, calendar, Notion, GitHub).
3. A non-critical step that still fails does not kill the task (see ``is_critical``).

NEVER healed: approval gates and approved actions, RESTRICTED tools, credential errors of an
action with side effects, budget exhaustion, a user abort. At most ``MAX_HEALING_HOPS`` hops
per subtask, so a recovery can never loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_HEALING_HOPS = 2

CODE_UNCLASSIFIED_TOOL = "unclassified_tool"
CODE_TOOL_UNAVAILABLE = "tool_unavailable"
CODE_AGENT_UNAVAILABLE = "agent_unavailable"
CODE_ALL_SOURCES_FAILED = "all_sources_failed"
CODE_RESTRICTED_TOOL = "restricted_tool"
CODE_MISSING_ARGUMENTS = "missing_arguments"

# Roles whose steps have a side effect or need an integration: never rerouted to a text step.
NEVER_REROUTE_ROLES = frozenset({"email", "coding", "document"})
# Roles whose steps are always critical (a side effect must never be silently skipped).
ALWAYS_CRITICAL_ROLES = frozenset({"email", "coding"})

_SAVE_STEP = re.compile(
    r"^\s*(?:please\s+)?(?:save|store|export|deliver|upload|persist|archive|place|put|copy|move)\b",
    re.IGNORECASE,
)
_SAVE_TARGET = re.compile(
    r"\b(workspace|storage|folder|directory|drive|artifact|designated|location|path|disk|"
    r"pdf|pptx|docx|xlsx|file|report|deck|document|presentation)\b",
    re.IGNORECASE,
)
_NEEDS_INTEGRATION = re.compile(
    r"\b(send|e-?mail|gmail|inbox|calendar|schedule[sd]?|meetings?|invites?|notion|"
    r"pull request|github|post to|publish|deploy|push)\b",
    re.IGNORECASE,
)
_CONTENT_WORK = re.compile(
    r"\b(outline|draft|write|summari[sz]e|summary|analy[sz]e|analysis|compare|comparison|"
    r"structure|plan|list|explain|describe|compile|prepare|organi[sz]e|brainstorm|rank|"
    r"evaluate|review|identify|determine|define|design|create|generate|propose|recommend)\b",
    re.IGNORECASE,
)
PM_TOPIC = re.compile(
    r"\b(calendar|schedule[sd]?|meetings?|agenda|appointments?|notion|briefing|daily brief|"
    r"stand-?up|to-?do|task list|my tasks|deadlines?|reminders?|events?)\b",
    re.IGNORECASE,
)


def is_save_step(description: str, tool: str | None, save_tools: frozenset[str]) -> bool:
    """True for a step whose only job is to save, export or deliver an existing file."""
    if tool and tool in save_tools:
        return True
    text = description or ""
    return bool(_SAVE_STEP.match(text) and _SAVE_TARGET.search(text))


def needs_integration(description: str) -> bool:
    return bool(_NEEDS_INTEGRATION.search(description or ""))


def is_content_work(description: str) -> bool:
    """An instruction a plain LLM text step can do (and that needs no integration)."""
    text = description or ""
    return bool(_CONTENT_WORK.search(text)) and not needs_integration(text)


def pm_topic(description: str) -> bool:
    """True when a ``pm`` instruction is really about calendar / Notion / task briefing."""
    return bool(PM_TOPIC.search(description or ""))


def predecessor_artifact(dep_outputs: dict[str, Any]) -> tuple[str, str] | None:
    """``(dependency id, file name)`` of a predecessor that already produced a file artifact."""
    for dep_id, output in (dep_outputs or {}).items():
        if isinstance(output, dict) and isinstance(output.get("artifact"), dict):
            name = str(output["artifact"].get("file_name") or "the file")
            return str(dep_id), name
    return None


def entry(attempt: int, source: str, target: str, reason: str) -> dict[str, Any]:
    """One healing-trail entry, stored in the subtask's input, events and proof."""
    return {"attempt": attempt, "from": source, "to": target, "reason": reason[:300]}


def recovered_text(trail: list[dict[str, Any]]) -> str:
    """The honest user-facing sentence for a healing trail."""
    parts: list[str] = []
    for item in trail:
        if item.get("to") == "skipped":
            parts.append(f"skipped the step ({item.get('reason')})")
        elif item.get("to") == "arguments repaired":
            parts.append(
                f"filled in a missing argument for '{item.get('from')}' ({item.get('reason')})"
            )
        elif item.get("to") == "no tool":
            parts.append(f"ran without the tool '{item.get('from')}' ({item.get('reason')})")
        else:
            parts.append(f"ran as {item.get('to')} instead of {item.get('from')}")
    return "Recovered: " + "; ".join(parts) if parts else ""


@dataclass(frozen=True)
class HealingDecision:
    """What the worker should do instead of failing: ``skip``, ``rerun_without_tool`` or
    ``reroute_executor``."""

    action: str
    target: str
    reason: str
    predecessor: str | None = None


def decide(
    *,
    code: str,
    role: str,
    description: str,
    tool: str | None,
    dep_outputs: dict[str, Any],
    save_tools: frozenset[str],
    hops: int,
) -> HealingDecision | None:
    """Pick a recovery route for a recoverable failure, or ``None`` to fail honestly."""
    if hops >= MAX_HEALING_HOPS:
        return None
    if tool and role in ("coding", "email"):
        return None  # a tool step of a side-effect role is never silently rewritten
    if code in (CODE_UNCLASSIFIED_TOOL, CODE_TOOL_UNAVAILABLE):
        label = tool or "tool"
        if is_save_step(description, tool, save_tools):
            produced = predecessor_artifact(dep_outputs)
            if produced is not None:
                return HealingDecision(
                    action="skip",
                    target="skipped",
                    reason=f"artifact already saved by {produced[0]} ({produced[1]})",
                    predecessor=produced[0],
                )
        return HealingDecision(
            action="rerun_without_tool",
            target=f"{role} agent without tool '{label}'",
            reason=f"tool '{label}' is not available or not classified",
        )
    if code == CODE_MISSING_ARGUMENTS:
        # The tool needs a value that cannot be derived. Run the step agent-only when its agent
        # can do the instruction by itself; otherwise fail with the precise message.
        if role in NEVER_REROUTE_ROLES:
            return None
        if role == "research" or is_content_work(description):
            return HealingDecision(
                action="rerun_without_tool",
                target=f"{role} agent without tool '{tool or 'tool'}'",
                reason=f"tool '{tool or 'tool'}' was missing a required value",
            )
        return None
    if code in (CODE_AGENT_UNAVAILABLE, CODE_ALL_SOURCES_FAILED):
        if role in NEVER_REROUTE_ROLES or tool:
            return None
        if role == "pm" and pm_topic(description):
            return None  # it really needs the calendar / Notion: fail with the actionable text
        if not is_content_work(description):
            return None
        return HealingDecision(
            action="reroute_executor",
            target="executor",
            reason=f"the {role} agent could not run this step",
        )
    return None


def is_critical(input_data: dict[str, Any] | None, role: str, requires_approval: bool) -> bool:
    """Whether a failure of this step must fail the task.

    The planner may mark a step ``critical: false``. That is ignored for a step with a side
    effect (an approval, an email, code): those are always critical. Plans without the flag
    behave as before (critical).
    """
    data = input_data or {}
    if requires_approval or role in ALWAYS_CRITICAL_ROLES or data.get("requires_approval"):
        return True
    return data.get("critical") is not False
