"""Deterministic repair of a model-written plan before it is stored.

The planner output is untrusted model text and routinely contains small mistakes that used to
kill a whole task at run time: a ``pm`` agent asked to outline slides, an invented ``save_file``
tool, a "save the PDF to the workspace" step that the document agent has already done. These
repairs are pure functions over a list of :class:`PlanNode`; every repair is logged and returned
so it can be shown. Cycles are NOT repaired here (the planner asks the model once to fix them and
otherwise rejects the plan).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.unified import resolve_member
from app.automation.filetype import needs_project
from app.mcp.permissions import SAVE_TOOL_NAMES
from app.tasks.healing import is_save_step, pm_topic

logger = logging.getLogger(__name__)

# What the planner may assign. ``executor`` is a plain text step (outline, summarise, analyse,
# draft copy). It is stored with the ``file`` role (no migration) plus ``input_data.executor``.
# ``file_edit`` and ``project`` (ADR-0022) are stored the same way (``input_data.executor``).
PLANNER_ROLES: tuple[str, ...] = (
    "research",
    "builder",
    "operator",
    "document",
    "email",
    "pm",
    "coding",
    "executor",
    "file_edit",
    "project",
)
FILE_STEP_ROLES: frozenset[str] = frozenset({"file_edit", "project"})
PERSISTED_EXECUTOR_ROLE = "file"
_LEGACY_ROLE_MAP = {"browser": "research", "file": "executor"}
_ALWAYS_CRITICAL = frozenset({"document", "email", "coding", "file_edit", "project"})


MAX_DERIVED_QUERY_CHARS = 300


@dataclass
class PlanNode:
    """A plan step in the planner's own vocabulary (before it becomes a ``SubTask``)."""

    id: str
    role: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    input_data: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    tool: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    critical: bool | None = None


def _ancestors(node_id: str, by_id: dict[str, PlanNode]) -> set[str]:
    seen: set[str] = set()
    stack = list(by_id[node_id].dependencies)
    while stack:
        current = stack.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        stack.extend(by_id[current].dependencies)
    return seen


CUSTOM_ROLE_PREFIX = "custom:"


def repair_plan(
    nodes: list[PlanNode],
    allowed_tools: frozenset[str],
    custom_names: frozenset[str] = frozenset(),
    user_text: str = "",
) -> tuple[list[PlanNode], list[str]]:
    """Repair ``nodes`` in place of a copy. Returns ``(nodes, repairs)`` (repairs for logging).

    ``custom_names`` are the user's enabled custom agents; a role ``custom:<name>`` is kept only
    for those (any other custom role becomes ``executor``).
    """
    repairs: list[str] = []
    nodes = [
        PlanNode(
            id=n.id,
            role=n.role,
            description=n.description,
            dependencies=list(n.dependencies),
            input_data=dict(n.input_data),
            requires_approval=n.requires_approval,
            tool=n.tool,
            tool_args=dict(n.tool_args),
            critical=n.critical,
        )
        for n in nodes
    ]

    # 1. Dependencies: drop self edges and ids that are not in the plan.
    ids = {n.id for n in nodes}
    for node in nodes:
        kept = [d for d in node.dependencies if d in ids and d != node.id]
        if kept != node.dependencies:
            dropped = sorted(set(node.dependencies) - set(kept))
            repairs.append(f"{node.id}: dropped invalid dependencies {dropped}")
            node.dependencies = kept

    # 2. Roles.
    for node in nodes:
        role = (node.role or "").strip().lower()
        role = _LEGACY_ROLE_MAP.get(role, role)
        if role.startswith(CUSTOM_ROLE_PREFIX) and role[len(CUSTOM_ROLE_PREFIX) :] in custom_names:
            node.role = role
            continue
        if role in ("builder", "operator"):
            # Unified agents (ADR-0023) resolve to one concrete member here, so the rest of the
            # pipeline (stored roles, worker, healing) only ever sees the member roles.
            member = resolve_member(role, node.description, node.input_data)
            repairs.append(f"{node.id}: unified agent '{role}' -> {member}")
            role = member
        if role in ("document", "file_edit") and not node.input_data.get("path"):
            # A multi-file website is ONE project step (a zip, or a folder when one is connected),
            # whichever name the planner used for the Builder (ADR-0023).
            request = node.input_data.get("requirements")
            wording = f"{node.description} {user_text} {(request or {}).get('user_request', '')}"
            if role == "document" and needs_project(wording):
                repairs.append(f"{node.id}: multi-file website request -> project")
                role = "project"
        if role not in PLANNER_ROLES:
            repairs.append(f"{node.id}: unknown agent '{node.role}' -> executor")
            role = "executor"
        node.role = role

    # 3. Steps that only save/export a file: remove (rewire) or turn into a document step.
    by_id = {n.id: n for n in nodes}
    removed: set[str] = set()
    for node in list(nodes):
        if node.role == "email" or node.requires_approval or node.role == "document":
            continue
        if not is_save_step(node.description, node.tool, SAVE_TOOL_NAMES):
            continue
        has_document_before = any(
            by_id[a].role == "document" and a not in removed for a in _ancestors(node.id, by_id)
        )
        if has_document_before:
            removed.add(node.id)
            repairs.append(
                f"{node.id}: removed redundant save step (the document agent stores the artifact)"
            )
        else:
            node.role = "document"
            node.tool, node.tool_args = None, {}
            repairs.append(f"{node.id}: save step without a document step before it -> document")
    if removed:
        for node in nodes:
            new_deps: list[str] = []
            for dep in node.dependencies:
                replacement = _resolve_removed(dep, by_id, removed)
                for item in replacement:
                    if item != node.id and item not in new_deps:
                        new_deps.append(item)
            node.dependencies = new_deps
        nodes = [n for n in nodes if n.id not in removed]

    # 4. pm only briefs calendar / Notion tasks; content planning goes to the executor.
    for node in nodes:
        if node.role == "pm" and not pm_topic(node.description):
            repairs.append(f"{node.id}: pm used for content work -> executor")
            node.role = "executor"

    # 4b. A file-edit or project step runs its own handler (ADR-0022). A raw tool name the planner
    # copied from the tool list (file_edit_as_new_version ...) would win over that handler and
    # run with empty arguments, so it is dropped.
    for node in nodes:
        if node.role in FILE_STEP_ROLES and node.tool:
            repairs.append(f"{node.id}: tool '{node.tool}' dropped ({node.role} step runs itself)")
            node.tool, node.tool_args = None, {}

    # 5. Tools: only tools that really exist; an invented one is dropped (the step then runs
    # without a tool, which is what the user asked for in plain words).
    for node in nodes:
        if node.tool and node.tool not in allowed_tools:
            repairs.append(f"{node.id}: unknown tool '{node.tool}' dropped")
            node.tool, node.tool_args = None, {}

    # 5b. A web search without a query searches the step's own instruction (the planner meant
    # that); the worker applies the same rule at run time for plans persisted earlier.
    for node in nodes:
        if node.tool == "web_search" and not str(node.tool_args.get("query") or "").strip():
            query = " ".join(node.description.split())[:MAX_DERIVED_QUERY_CHARS]
            if query:
                node.tool_args = {**node.tool_args, "query": query}
                repairs.append(f"{node.id}: web_search had no query -> used the step instruction")

    # 5c. A github_* repository tool without a usable repo takes the repository named in the
    # user's request (a github.com URL or owner/name), the same rule the worker applies.
    for node in nodes:
        if (
            node.tool
            and node.tool.startswith("github_")
            and node.tool not in ("github_list_repositories", "github_create_pull_request")
        ):
            repo = derive_repo_arg(node.tool_args, node.description, user_text)
            if repo and node.tool_args.get("repo") != repo:
                node.tool_args = {**node.tool_args, "repo": repo}
                repairs.append(f"{node.id}: {node.tool} had no usable repo -> {repo}")

    # 6. Critical flags (a deliverable or a side effect is critical; others may be skipped).
    dependents: set[str] = {d for n in nodes for d in n.dependencies}
    for node in nodes:
        if (
            node.role == "email"
            or node.requires_approval
            or node.input_data.get("requires_approval")
        ):
            node.critical = True
        elif (
            node.role == "research" and node.id in dependents and node.tool in (None, "web_search")
        ):
            # Web research feeds a later step. The research step itself does not sink the task
            # (whatever the planner said): the dependent step is told it failed (missing
            # dependency / sources_unavailable) and decides: it fails with a plain message unless
            # the user said to build "from general knowledge" (``research_policy``). A research
            # step that is itself the deliverable (a leaf) keeps its flag.
            node.critical = False
        elif node.critical is None:
            leaf = node.id not in dependents
            node.critical = leaf or node.role in _ALWAYS_CRITICAL
    for message in repairs:
        logger.info("plan_repaired %s", message)
    return nodes, repairs


def derive_repo_arg(args: dict[str, Any], *texts: str) -> str | None:
    """A normalised ``owner/name`` for a github_* tool: the given ``repo`` when it parses, else the
    first github.com URL in ``texts`` (step text, the user's request). None when there is none."""
    from app.mcp.connectors.github import GitHubRepoError, find_repo_urls, parse_repo_ref

    given = str(args.get("repo") or "").strip()
    if given:
        try:
            return parse_repo_ref(given).full_name
        except GitHubRepoError:
            pass
    for text in texts:
        urls = find_repo_urls(text or "")
        if urls:
            return urls[0].full_name
    return None


def _resolve_removed(
    dep: str, by_id: dict[str, PlanNode], removed: set[str], seen: frozenset[str] = frozenset()
) -> list[str]:
    """Replace a removed dependency by what it depended on (transitively)."""
    if dep not in removed:
        return [dep]
    if dep in seen:
        return []  # a cycle among removed steps: nothing to rewire to
    out: list[str] = []
    for upstream in by_id[dep].dependencies:
        for item in _resolve_removed(upstream, by_id, removed, seen | {dep}):
            if item not in out:
                out.append(item)
    return out


def drop_invented_edit_targets(nodes: list[Any], input_files: list[str]) -> list[str]:
    """Attached uploads are the edit target: remove a path the planner invented.

    ``nodes`` are persisted ``SubTask`` nodes (``input_data.executor`` set). Returns notes.
    """
    notes: list[str] = []
    if not input_files:
        return notes
    for node in nodes:
        data = dict(node.input_data or {})
        if data.get("executor") != "file_edit":
            continue
        removed = [k for k in ("path", "target_path") if data.pop(k, None)]
        if removed:
            node.input_data = data
            notes.append(f"{node.id}: planner path dropped, the attached file is the target")
    return notes


# An edit request (the file is changed, not written anew): mirrors the chat router's wording.
_FILE_EDIT_WORDS = re.compile(
    r"\b(?:edit|change|update|fix|correct|rewrite|translate|improve|revise)\b|"
    r"\bkar (?:do|de|dein|diya|dijiye)\b|\bkaro\b|\bbadal\w*\b|\bsudhar\w*\b|\btheek\b|\bhata\w*\b",
    re.IGNORECASE,
)


def attached_source_repairs(
    nodes: list[Any], instruction: str, input_files: list[str]
) -> list[Any]:
    """A request about an attached file: the file's own text is the source, not a web search.

    ``nodes`` are persisted ``SubTask`` nodes; returns the nodes to store (a new list when a step
    was removed). Research steps are never critical here, so a failed search cannot fail the task
    or block the step that builds from the file. A research step is removed (its dependents are
    rewired) when the request asks for no web facts. A request left with no deliverable step gets
    one: a ``file`` edit step for an edit wording, otherwise a ``document`` step.
    """
    from app.agents.state import SubTask  # local: the agents package imports this module
    from app.tasks.dispatch import needs_web_facts  # local: the task layer imports this module

    if not input_files:
        return nodes
    for node in nodes:
        if _role_of(node) == "research":
            node.input_data = {**(node.input_data or {}), "critical": False}
    if needs_web_facts(instruction):
        kept = nodes
    else:
        removed = {
            n.id for n in nodes if _role_of(n) == "research" and n.tool in (None, "web_search")
        }
        by_id = {n.id: n for n in nodes}
        for node in nodes:
            if node.id in removed:
                continue
            rewired: list[str] = []
            for dep in node.dependencies:
                for item in _resolve_removed(dep, by_id, removed):
                    if item != node.id and item not in rewired:
                        rewired.append(item)
            node.dependencies = rewired
        kept = [n for n in nodes if n.id not in removed]
        if removed:
            logger.info("plan_repaired attached file: removed research steps %s", sorted(removed))
    if not any(_role_of(n) != "research" for n in kept):
        taken = {str(n.id) for n in kept}
        root_id = next(f"step_{k}" for k in range(1, 10_000) if f"step_{k}" not in taken)
        if _FILE_EDIT_WORDS.search(instruction):
            root = SubTask(
                id=root_id,
                assigned_agent="file",
                description=instruction,
                dependencies=[],
                input_data={"executor": "file_edit", "critical": True},
            )
        else:
            root = SubTask(
                id=root_id,
                assigned_agent="document",
                description=instruction,
                dependencies=[],
                input_data={"critical": True},
            )
        kept = [*kept, root]
    return kept


def general_request_repairs(nodes: list[Any], instruction: str) -> list[Any]:
    """A request that claims no web facts gets no research step.

    The planner sometimes adds one anyway (for notes, a tutorial or any general topic). Such a
    step is removed and its dependents are rewired to what it depended on, as in
    ``attached_source_repairs``; a plan left with no deliverable step gets a ``document`` step that
    builds from general knowledge. A request that names current or sourced facts
    (``needs_web_facts``) keeps its research step unchanged.
    """
    from app.agents.state import SubTask  # local: the agents package imports this module
    from app.tasks.dispatch import needs_web_facts  # local: the task layer imports this module

    if not nodes or needs_web_facts(instruction):
        return nodes
    removed = {n.id for n in nodes if _role_of(n) == "research" and n.tool in (None, "web_search")}
    if not removed:
        return nodes
    by_id = {n.id: n for n in nodes}
    for node in nodes:
        if node.id in removed:
            continue
        rewired: list[str] = []
        for dep in node.dependencies:
            for item in _resolve_removed(dep, by_id, removed):
                if item != node.id and item not in rewired:
                    rewired.append(item)
        node.dependencies = rewired
    kept = [n for n in nodes if n.id not in removed]
    logger.info("plan_repaired general request: removed research steps %s", sorted(removed))
    if not any(_role_of(n) != "research" for n in kept):
        taken = {str(n.id) for n in kept}
        root_id = next(f"step_{k}" for k in range(1, 10_000) if f"step_{k}" not in taken)
        kept = [
            *kept,
            SubTask(
                id=root_id,
                assigned_agent="document",
                description=instruction,
                dependencies=[],
                input_data={"critical": True},
            ),
        ]
    return kept


_NOTION_WRITE_TOOLS = frozenset({"notion_create_page", "notion_append_blocks", "notion_add_row"})


def _builds_file(node: Any) -> bool:
    """A step that produces a file or writes a Notion page (never a research or answer step)."""
    role = _role_of(node)
    data = node.input_data or {}
    if role == "document":
        return True
    if role == "file" and str(data.get("executor") or "") == "project":
        return True
    return str(node.tool or "") in _NOTION_WRITE_TOOLS


def chat_only_repairs(nodes: list[Any], instruction: str, *, attached: bool = False) -> list[Any]:
    """Owner rule: no file unless the user's own words name one.

    When ``requested_deliverable`` says ``chat``, every builder (document or project) step and every
    Notion-write step is removed and its dependents are rewired, so the plan is research or answer
    steps only. A plan left empty becomes one research step that answers the request. A request
    with an attached file keeps its edit steps (editing is the request).
    """
    from app.agents.state import SubTask  # local: the agents package imports this module
    from app.services.deliverable_intent import CHAT, requested_deliverable

    if not nodes or attached or requested_deliverable(instruction) != CHAT:
        return nodes
    removed = {n.id for n in nodes if _builds_file(n)}
    if not removed:
        return nodes
    by_id = {n.id: n for n in nodes}
    for node in nodes:
        if node.id in removed:
            continue
        rewired: list[str] = []
        for dep in node.dependencies:
            for item in _resolve_removed(dep, by_id, removed):
                if item != node.id and item not in rewired:
                    rewired.append(item)
        node.dependencies = rewired
    kept = [n for n in nodes if n.id not in removed]
    logger.info("plan_repaired chat-only request: removed file steps %s", sorted(removed))
    if not kept:
        kept = [
            SubTask(
                id="step_1",
                assigned_agent="research",
                description=instruction,
                dependencies=[],
                input_data={"critical": True},
            )
        ]
    return kept


def _role_of(node: Any) -> str:
    return str(getattr(node.assigned_agent, "value", node.assigned_agent) or "")
