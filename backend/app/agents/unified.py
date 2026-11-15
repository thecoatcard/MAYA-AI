"""The three unified agents (ADR-0023): Research, Builder, Operator.

The planner, the Agents page and the capability table speak about THREE agents, each with a clear
capability list, instead of one agent per tool. The concrete executors (document, project writer,
file editor, email, briefing, ...) are unchanged "members": they keep the immutable tool
allowlists and permission ceilings that make each of them safe, and stored tasks, the database
enum and the worker still use their names. A unified role is resolved to ONE member in code
(:func:`resolve_member`) when a plan is built, so nothing downstream needs to know the new names.

Old role names stay valid forever as aliases (:data:`ALIASES`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RESEARCH = "research"
BUILDER = "builder"
OPERATOR = "operator"
EXECUTOR = "executor"  # a plain LLM text step; not an agent with tools, kept for the planner

UNIFIED_IDS: tuple[str, ...] = (RESEARCH, BUILDER, OPERATOR)
# What the planner may assign: the three agents plus the tool-less text step.
PLANNER_FACING_ROLES: tuple[str, ...] = (RESEARCH, BUILDER, OPERATOR, EXECUTOR)


@dataclass(frozen=True, slots=True)
class UnifiedAgent:
    """One user-visible agent: its job, what it can do, and which concrete executors it owns."""

    id: str
    label: str
    summary: str
    capabilities: tuple[str, ...]
    members: tuple[str, ...]
    # Side effects this agent never performs without the user's approval (informational).
    needs_approval: tuple[str, ...] = ()


UNIFIED_AGENTS: dict[str, UnifiedAgent] = {
    RESEARCH: UnifiedAgent(
        id=RESEARCH,
        label="Research",
        summary="Finds facts on the web with citations and reads pages.",
        capabilities=(
            "web search with citations",
            "read web pages",
            "list your GitHub repositories",
        ),
        members=("research", "browser"),
    ),
    BUILDER: UnifiedAgent(
        id=BUILDER,
        label="Builder",
        summary=(
            "Creates and edits deliverables: documents, decks, sheets, websites and the files "
            "in your connected folder."
        ),
        capabilities=(
            "create pdf, docx, pptx, xlsx and markdown files",
            "build a static website",
            "edit an attached or folder file in place",
            "write code files (nothing is executed)",
        ),
        members=("document", "project", "file_edit", "coding"),
        needs_approval=("replace a file with another version",),
    ),
    OPERATOR: UnifiedAgent(
        id=OPERATOR,
        label="Operator",
        summary="Acts in your accounts: email, calendar and Notion briefings, GitHub.",
        capabilities=(
            "draft and send an email (always asks your approval)",
            "daily briefing from Google Calendar and Notion",
            "GitHub repositories and pull requests",
        ),
        members=("email", "pm"),
        needs_approval=("send an email", "open a pull request"),
    ),
}

# Every old and new name to the unified agent that owns it.
ALIASES: dict[str, str] = {
    "research": RESEARCH,
    "browser": RESEARCH,
    "document": BUILDER,
    "project": BUILDER,
    "project_writer": BUILDER,
    "file_edit": BUILDER,
    "file": BUILDER,
    "coding": BUILDER,
    "builder": BUILDER,
    "email": OPERATOR,
    "pm": OPERATOR,
    "calendar": OPERATOR,
    "operator": OPERATOR,
}


def unified_for(role: str | None) -> str | None:
    """The unified agent id that owns ``role`` (an old or new name), or ``None``."""
    return ALIASES.get((role or "").strip().lower())


_WEBSITE = re.compile(
    r"\b(web\s?site|web\s?page|landing\s+page|portfolio|html|front-?end|web\s?app)\b", re.I
)
_EMAIL = re.compile(r"\b(e-?mails?|mail|inbox|send|reply|draft(?:ing)?\s+(?:a|an|the)\b)", re.I)
_BUILDER_KINDS = frozenset({"document", "file_edit", "project", "coding"})
_OPERATOR_KINDS = frozenset({"email", "pm"})
_KIND_ALIASES = {"briefing": "pm", "calendar": "pm", "notion": "pm", "project_writer": "project"}


def resolve_member(
    role: str, description: str = "", input_data: dict[str, Any] | None = None
) -> str:
    """The concrete executor role for a planner role.

    Old role names (``document``, ``email`` ...) and ``research``/``executor`` pass through
    unchanged. ``builder`` and ``operator`` pick a member from ``input_data.kind`` when the
    planner gave a valid one, else from the step (a path or attached files mean an edit, website
    words mean a project, email words mean an email), else the default member (``document``,
    ``pm``).
    """
    name = (role or "").strip().lower()
    data = input_data or {}
    kind = str(data.get("kind") or "").strip().lower()
    kind = _KIND_ALIASES.get(kind, kind)
    if name == BUILDER:
        if kind in _BUILDER_KINDS:
            return kind
        if data.get("path") or data.get("input_files"):
            return "file_edit"
        from app.automation.filetype import needs_project  # local: keeps imports light

        if needs_project(description) or _WEBSITE.search(description or ""):
            return "project"
        return "document"
    if name == OPERATOR:
        if kind in _OPERATOR_KINDS:
            return kind
        return "email" if _EMAIL.search(description or "") else "pm"
    return name


def catalogue(available: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    """The planner/UI view: one entry per unified agent, with member availability."""
    out: list[dict[str, Any]] = []
    for agent in UNIFIED_AGENTS.values():
        entry: dict[str, Any] = {
            "id": agent.id,
            "label": agent.label,
            "summary": agent.summary,
            "capabilities": list(agent.capabilities),
            "members": list(agent.members),
            "needs_approval": list(agent.needs_approval),
            "kind": "builtin",
        }
        if available is not None:
            entry["available"] = any(available.get(m, False) for m in agent.members)
        out.append(entry)
    return out
