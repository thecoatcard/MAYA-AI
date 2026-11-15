"""Background worker runner executing subtask jobs conforming to Decision D8."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.plan_repair import derive_repo_arg
from app.agents.schemas import AgentExecutionStatus
from app.agents.unsourced import UNSOURCED_WARNING, UNSOURCED_WARNING_PREFIX
from app.db.models import FileArtifact, PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.repositories.approvals import ApprovalRepository
from app.decisions.approval_check import check_payload
from app.decisions.required_details_check import rank_notion_pages
from app.decisions.step_review import KEY as DECISION_REVIEW_KEY
from app.decisions.step_review import review_step
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.mcp.approvals import compute_payload_hash
from app.mcp.connectors.google_search import parse_sources
from app.mcp.notion_auto_approve import MANUAL, NotionAutoApprover
from app.mcp.permissions import (
    SAVE_TOOL_NAMES,
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.scope import tool_call_scope
from app.mcp.tokens import (
    ApprovalTokenExpired,
    claim_approval_atomically,
    complete_execution_atomically,
    fail_execution_atomically,
    start_execution_atomically,
    verify_approval_token,
)
from app.services.artifact_service import db_file_type
from app.services.audit_service import AuditWriteError, record_audit
from app.services.progress import tool_step
from app.tasks import healing, input_cards
from app.tasks.events import TaskEventType
from app.tasks.needs_input import (
    DEFAULT_PARENT_KEY,
    KIND_NOTION_PARENT,
    MAX_OPTIONS,
    NEEDS_INPUT_KEY,
    Candidate,
    NeedsInput,
    candidates_from_search,
    card_questions,
    parse_default,
)
from app.tasks.needs_input import Pause as NeedsPause
from app.tasks.outbox import write_outbox_event
from app.tasks.pipeline import STAGE_OUTLINE, light_output, retitles, stage_of
from app.tasks.progress_steps import emit_task_step, step_label, subtask_step_id
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.rate_limit import is_rate_limited

logger = logging.getLogger(__name__)

DEFAULT_SUBTASK_TIMEOUT_SECONDS = 300
MAX_SUBTASK_RETRIES = 3
# Building an agent's structured input is one model call; bound it separately from the agent run
# so a slow model produces a clear "preparing input" timeout instead of eating the full budget.
# 300 s = two agent attempts (MAYA_AGENT_REQUEST_TIMEOUT_SECONDS, 150 s each), so one hung
# attempt plus a failover still fits.
CONTEXT_TIMEOUT_SECONDS = 300
# Budget of a long-document outline or chapter step (two 150 s calls plus failover headroom).
LONG_STEP_TIMEOUT_SECONDS = 600

CODING_DISABLED_MESSAGE = (
    "The coding agent is turned off in this deployment, so no code was run. Its sandbox is not "
    "yet container-isolated and model-directed commands are never run on the host. Ask for the "
    "change as a written plan or patch instead."
)
# Keys an agent's approval context must never write back into the subtask input.
_RESERVED_INPUT_KEYS = frozenset(
    {
        "tool",
        "tool_name",
        "tool_args",
        "args",
        "idempotency_key",
        "diff",
        "payload_diff",
        "approval_token",
        "has_valid_approval_token",
        "requires_approval",
    }
)


_TIER_TO_AUDIT_LEVEL = {
    "READ": "READ",
    "WRITE": "WRITE",
    "APPROVAL_REQUIRED": "SENSITIVE",
    "RESTRICTED": "RESTRICTED",
}


async def _audit(
    session: AsyncSession,
    job: SubtaskJob,
    *,
    action: str | None,
    status: str,
    subtask: Subtask | None = None,
    **fields: Any,
) -> None:
    """Best-effort audit row for worker lifecycle events (never breaks task execution)."""
    try:
        await record_audit(
            session,
            workspace_id=job.workspace_id,
            user_ref=str(job.user_id),
            status=status,
            action=action,
            task_id=job.task_id,
            subtask_id=job.subtask_id,
            **fields,
        )
    except AuditWriteError:
        logger.error("worker_audit_write_failed action=%s subtask=%s", action, job.subtask_id)
    except Exception:
        logger.error("worker_audit_unexpected_failure action=%s", action, exc_info=True)


async def _audit_tool(
    session: AsyncSession,
    job: SubtaskJob,
    subtask: Subtask,
    registry: ToolPermissionRegistry,
    *,
    status: str,
    duration_ms: int | None,
    reason: str | None = None,
    server_name: str | None = None,
) -> None:
    """Audit a tool execution (only subtasks that name a tool)."""
    data = subtask.input_data or {}
    tool = data.get("tool") or data.get("tool_name")
    if not tool:
        return
    level: str | None = None
    if registry.is_registered(str(tool)):
        level = _TIER_TO_AUDIT_LEVEL.get(registry.classify(str(tool)).value)
    await _audit(
        session,
        job,
        action=None,
        status=status,
        server_name=str(server_name or data.get("server_name") or "maya"),
        tool_name=str(tool),
        permission_level=level,
        duration_ms=duration_ms,
        output={"error": reason} if reason else None,
    )


def resolve_subtask_uuid(task_id: UUID, key: str) -> UUID:
    """Deterministic subtask id (imported lazily: projection_service imports the agents package,
    which imports this package)."""
    from app.services.projection_service import resolve_subtask_uuid as _resolve

    return _resolve(task_id, key)


async def _register_artifacts(
    session: AsyncSession, job: SubtaskJob, user_id: UUID, proof_data: Any
) -> list[str]:
    """Insert the FileArtifact rows of a verified result; returns warnings for any that failed.

    Every produced type is mapped to the database's allowed set at this single point (the real
    extension stays in ``file_name``). Each row runs in a SAVEPOINT, so a failing row is rolled
    back alone and the surrounding transaction (outbox event, status) stays usable.
    """
    if not (
        isinstance(proof_data, dict) and "storage_uri" in proof_data and "sha256" in proof_data
    ):
        return []
    entries: list[dict[str, Any]] = [dict(proof_data)]
    # A multi-file result (a written project) registers every file (ADR-0022).
    entries.extend(
        dict(e) for e in list(proof_data.get("extra_artifacts") or [])[:300] if isinstance(e, dict)
    )
    warnings: list[str] = []
    for index, entry in enumerate(entries):
        name = str(entry.get("file_name") or ("output_artifact" if index == 0 else "file"))
        try:
            async with session.begin_nested():
                session.add(
                    FileArtifact(
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        user_id=user_id,
                        file_name=name[:255],
                        file_type=db_file_type(entry.get("file_type"), name),
                        storage_uri=str(entry["storage_uri"])[:512],
                        size_bytes=int(entry.get("size_bytes", 0)),
                        sha256=str(entry["sha256"]),
                        verification_status=(
                            str(entry.get("status", "verified")) if index == 0 else "verified"
                        ),
                        verification_report=entry.get("report") if index == 0 else None,
                    )
                )
                await session.flush()
        except Exception as art_err:
            logger.warning("Failed to persist file artifact record %s: %s", name, art_err)
            warnings.append(
                f"'{name}' was produced but could not be listed as a downloadable artifact "
                f"({type(art_err).__name__})."
            )
    return warnings[:5]


class SubtaskExecutionError(Exception):
    """Raised when subtask execution fails unrecoverably."""


class SubtaskTimeoutError(SubtaskExecutionError):
    """Raised when subtask execution exceeds bounded timeout."""


class NonRetryableSubtaskError(SubtaskExecutionError):
    """Raised when retrying cannot help (for example no executor is configured)."""


MAX_DERIVED_QUERY_CHARS = 300


class MissingToolArgumentsError(NonRetryableSubtaskError):
    """A tool step lacks a required argument that cannot be derived (recoverable by healing)."""


class ToolNotProvidedError(NonRetryableSubtaskError):
    """The tool is classified but no tool server provides it (a recoverable planning error)."""


RETRY_BACKOFF_BASE_SECONDS = 2.0
RETRY_BACKOFF_MAX_SECONDS = 30.0
# A provider quota/rate limit is shared by every task, so a retry waits longer (the key pool is
# cooling) and the final message tells the user what to do instead of "no results".
RATE_LIMIT_BACKOFF_BASE_SECONDS = 20.0
RATE_LIMIT_BACKOFF_MAX_SECONDS = 60.0
RATE_LIMITED_MESSAGE = (
    "Gemini is overloaded right now. MAYA retried this step with a pause on other models and "
    "keys; please try again in a minute."
)
RATE_LIMITED_PROOF_ERROR = "rate_limited"


def _overloaded_reason(exc: BaseException) -> str | None:
    """The plain 'Gemini is overloaded' text when the gateway exhausted every model and key."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, GatewayFallbackExhaustedError) and cur.overloaded:
            return cur.user_message
        cur = cur.__cause__ or cur.__context__
    return None


def _as_result_dict(value: Any) -> dict[str, Any]:
    """Coerce a handler result to the JSON object the ``output_result`` column stores."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"summary": str(value)}


def _approval_binding(input_data: dict[str, Any], tool_name: Any) -> tuple[str, dict[str, Any]]:
    """Tool name and args an approval is bound to (``approval_tool`` is set by the agent path)."""
    tool = str(tool_name or input_data.get("approval_tool") or "subtask_action")
    args = (
        input_data.get("tool_args")
        or input_data.get("args")
        or input_data.get("approval_args")
        or {}
    )
    return tool, dict(args)


def _proof_file_names(proof: Any) -> list[str]:
    """File names a step's proof reports (for the review input); empty when there are none."""
    if not isinstance(proof, dict):
        return []
    names = [str(proof["file_name"])] if proof.get("file_name") else []
    names.extend(
        str(e["file_name"])
        for e in list(proof.get("extra_artifacts") or [])[:20]
        if isinstance(e, dict) and e.get("file_name")
    )
    return names


def _as_proof_dict(value: Any) -> dict[str, Any]:
    """Coerce a handler proof to an object; a missing proof is reported as unverified."""
    if isinstance(value, dict) and value:
        return value
    return {"status": "unverified", "reason": "handler returned no verification proof"}


def _says_sources_unavailable(subtask: Any) -> bool:
    """True when a finished step reports that no web sources could be retrieved."""
    proof = getattr(subtask, "verification_proof", None)
    result = getattr(subtask, "output_result", None)
    return bool(
        (isinstance(proof, dict) and proof.get("sources_unavailable"))
        or (isinstance(result, dict) and result.get("sources_unavailable"))
    )


def _attached_file_is_the_source(subtasks: list[Any]) -> bool:
    """True when the user's attached file is the material and no web facts were asked for: then
    a failed research step makes no unsourced claim (the file is stamped the same way)."""
    from app.tasks.dispatch import needs_web_facts  # local: dispatch imports this module

    has_files = any((getattr(st, "input_data", None) or {}).get("input_files") for st in subtasks)
    return has_files and not any(
        needs_web_facts(str(getattr(st, "description", "") or "")) for st in subtasks
    )


def _claims_web_facts(subtasks: list[Any]) -> bool:
    """True when a step or the user's own words ask for current or citable web facts."""
    from app.agents.requirements import REQUIREMENTS_KEY  # local: keeps the worker import light
    from app.tasks.dispatch import needs_web_facts  # local: dispatch imports this module

    for st in subtasks:
        req = (getattr(st, "input_data", None) or {}).get(REQUIREMENTS_KEY)
        user_request = str(req.get("user_request") or "") if isinstance(req, dict) else ""
        if needs_web_facts(str(getattr(st, "description", "") or "")) or needs_web_facts(
            user_request
        ):
            return True
    return False


def failed_step_report(subtasks: list[Any]) -> list[dict[str, Any]]:
    """The steps that failed inside a task that still finished: ``step``, plain ``error`` and
    ``deliverable_incomplete`` (a failed web search leaves a file without sources, a failed
    chapter or other step leaves the file without that part)."""
    report = [
        {
            "step": (st.description or "")[:80],
            "error": " ".join(str(st.error_message or "failed").split())[:200],
            "deliverable_incomplete": getattr(st.assigned_agent, "value", st.assigned_agent)
            != "research",
        }
        for st in subtasks
        if getattr(st.status, "value", st.status) == "failed"
    ]
    # ADR-0029: a step the decision-model review flagged finished, but its result may be
    # incomplete; it is listed so the task shows as finished with problems, never cleanly done.
    # Calibrated: only a strong, corroborated flag recorded in "enforce" mode counts. Everything
    # else is an advisory note (see ``step_review_notes``) and never changes the task status.
    for st in subtasks:
        review = (getattr(st, "output_result", None) or {}).get(DECISION_REVIEW_KEY)
        if isinstance(review, dict) and review.get("flagged") and review.get("mode") == "enforce":
            report.append(
                {
                    "step": (st.description or "")[:80],
                    "error": "Result may be incomplete: "
                    + "; ".join(str(r) for r in review.get("reasons") or [])[:160],
                    "deliverable_incomplete": True,
                    # The step itself completed: clients must not label it "Failed".
                    "step_completed": True,
                }
            )
    return report


def step_review_notes(subtasks: list[Any]) -> list[str]:
    """``Note: ...`` lines for reviews that did not change the status (advisory mode, or a model
    opinion without deterministic corroboration). Shown with the result, never as a failure."""
    notes: list[str] = []
    for st in subtasks:
        review = (getattr(st, "output_result", None) or {}).get(DECISION_REVIEW_KEY)
        if not isinstance(review, dict) or not review.get("reasons"):
            continue
        if review.get("flagged") and review.get("mode") == "enforce":
            continue
        reasons = "; ".join(str(r) for r in review.get("reasons") or [])[:160]
        step = (st.description or "")[:60]
        notes.append(f"Note: {step}: {reasons} (automatic check, unconfirmed)")
    return notes


def _unsourced_warnings(subtasks: list[Any]) -> list[str]:
    """Task warnings for a deliverable written without web sources (deduplicated). A request that
    claims no web facts gets no such warning: its content is general knowledge, not web claims."""
    out: list[str] = []
    skipped_research = any(
        getattr(st.assigned_agent, "value", st.assigned_agent) == "research"
        and getattr(st.status, "value", st.status) == "failed"
        for st in subtasks
    )
    unsourced = skipped_research or any(_says_sources_unavailable(st) for st in subtasks)
    if unsourced and _claims_web_facts(subtasks) and not _attached_file_is_the_source(subtasks):
        out.append(UNSOURCED_WARNING)
    for st in subtasks:
        report = (getattr(st, "verification_proof", None) or {}).get("report")
        for item in (report.get("warnings") if isinstance(report, dict) else None) or []:
            if str(item).startswith(UNSOURCED_WARNING_PREFIX) and str(item) not in out:
                out.append(str(item))
    return out


def _proof_says_rate_limited(proof: Any) -> bool:
    """An agent reports a provider quota failure with ``error: rate_limited`` in its proof."""
    return isinstance(proof, dict) and proof.get("error") == RATE_LIMITED_PROOF_ERROR


def _is_repo_read_tool(tool_name: str) -> bool:
    """A read-only github_* repository tool (never the approval-gated pull request one)."""
    return tool_name.startswith("github_") and tool_name not in (
        "github_create_pull_request",
        "github_list_repositories",
    )


class SubtaskWorker:
    """Async worker processing individual subtask jobs.

    Core Decision D8 rules:
    - Exactly 1 subtask per queued job message.
    - Workers never sleep or block waiting for human approval; upon hitting
      awaiting_approval, state is persisted and the worker exits immediately.
    - Job execution bounded strictly by timeout.
    """

    def __init__(
        self,
        queue: SubtaskQueue | None = None,
        default_timeout: int = DEFAULT_SUBTASK_TIMEOUT_SECONDS,
        permission_registry: ToolPermissionRegistry | None = None,
        jwt_signing_key: str | bytes | None = None,
        fleet_registry: Any | None = None,
        tool_invoker: Any | None = None,
        fleet_support: Any | None = None,
        auto_approver: NotionAutoApprover | None = None,
    ) -> None:
        self.queue = queue or SubtaskQueue()
        # Optional, default OFF per user: records the approval of a few additive Notion writes
        # when the owner enabled that in Settings. The token is the same one a click produces.
        self.auto_approver = auto_approver
        self.default_timeout = default_timeout
        self.permission_registry = permission_registry or ToolPermissionRegistry()
        # No fallback key: an unset key means approval tokens cannot be verified, and the
        # worker then leaves the subtask awaiting approval (fail closed).
        self.jwt_signing_key = jwt_signing_key or None
        self.fleet_registry = fleet_registry
        # ``tool_invoker`` (an McpGateway) runs explicit tool steps; ``fleet_support``
        # (tasks.dispatch.FleetSupport) builds agent context and proof. Both optional.
        self.tool_invoker = tool_invoker
        self._paused_for_input: set[UUID] = set()
        self.fleet_support = fleet_support

    async def _step_label(self, session: AsyncSession, job: SubtaskJob) -> str:
        """The step's label on the task card: the plan's human label (with the chapter title
        once the outline has run), else the agent label. Never raises."""
        try:
            row = (
                await session.execute(
                    select(Subtask.input_data).where(
                        Subtask.id == job.subtask_id, Subtask.workspace_id == job.workspace_id
                    )
                )
            ).first()
            data = row[0] if row is not None and isinstance(row[0], dict) else {}
            title = ""
            pipeline = data.get("pipeline")
            if isinstance(pipeline, dict) and pipeline.get("index"):
                outline_row = (
                    await session.execute(
                        select(Subtask.output_result).where(
                            Subtask.id == resolve_subtask_uuid(job.task_id, "outline"),
                            Subtask.workspace_id == job.workspace_id,
                        )
                    )
                ).first()
                outline = outline_row[0] if outline_row is not None else None
                chapters = (outline or {}).get("outline", {}).get("chapters") or []
                for chapter in chapters:
                    if isinstance(chapter, dict) and chapter.get("index") == pipeline["index"]:
                        title = str(chapter.get("title") or "")
            return step_label(data, job.assigned_agent, title)
        except Exception as error:  # noqa: BLE001 - a label never fails a step
            logger.debug("step_label_failed error=%s", type(error).__name__)
            return step_label(None, job.assigned_agent)

    async def execute_job(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        agent_handler: (
            Callable[[SubtaskJob, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        deferred_jobs: list[SubtaskJob] | None = None,
    ) -> SubtaskStatus:
        """Run one subtask and show its progress step: running, then its outcome.

        The step is additive: it is queued in the same transaction and never changes the
        status or error the subtask reports.
        """
        step: dict[str, Any] = dict(
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            subtask_id=job.subtask_id,
            step_id=subtask_step_id(job.subtask_id),
            kind="step",
            label=await self._step_label(session, job),
        )
        if job.queued_step:
            # The task waited for a free worker (see TaskSubmissionService): it has one now.
            job.queued_step = False
            await emit_task_step(
                session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                subtask_id=None,
                step_id=f"queued-{job.task_id}",
                kind="step",
                label="Queued",
                state="done",
                detail="A worker is free; the task is starting.",
            )
        await emit_task_step(session, state="running", **step)
        try:
            status = await self._execute_job(session, job, agent_handler, deferred_jobs)
        except Exception:
            # The unit of work is lost with the exception (the runtime never commits it). Roll the
            # session back now so it is not left in a failed state; the runtime's recovery then
            # retries the step or fails it plainly, and writes the step's outcome in a fresh one.
            with contextlib.suppress(Exception):
                await session.rollback()
            raise
        if status == SubtaskStatus.COMPLETED:
            await emit_task_step(session, state="done", **step)
        elif status == SubtaskStatus.FAILED:
            await emit_task_step(
                session,
                state="failed",
                detail="The step did not finish. The task shows the reason.",
                **step,
            )
        elif status == SubtaskStatus.AWAITING_APPROVAL:
            waiting = (
                "Waiting for your answer on the card in the chat."
                if job.subtask_id in getattr(self, "_paused_for_input", ())
                else "Waiting for your approval."
            )
            await emit_task_step(session, state="skipped", detail=waiting, **step)
        return status

    async def _execute_job(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        agent_handler: (
            Callable[[SubtaskJob, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
        deferred_jobs: list[SubtaskJob] | None = None,
    ) -> SubtaskStatus:
        """Execute a single subtask job under strict tenant isolation.

        Jobs that become runnable (unblocked dependents, retries) are appended to
        ``deferred_jobs`` when given, so the caller can enqueue them only after the surrounding
        transaction commits. Without it they are enqueued immediately (legacy behaviour).
        """
        # 1. Load subtask and verify tenant boundary
        stmt = (
            select(Subtask)
            .where(
                Subtask.id == job.subtask_id,
                Subtask.workspace_id == job.workspace_id,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        subtask = result.scalars().first()
        if subtask is None:
            logger.error(
                "Subtask %s not found in workspace %s",
                job.subtask_id,
                job.workspace_id,
            )
            return SubtaskStatus.FAILED

        if subtask.status in (SubtaskStatus.COMPLETED, SubtaskStatus.FAILED):
            logger.info(
                "Subtask %s already %s; skipping duplicate job",
                job.subtask_id,
                subtask.status.value,
            )
            return subtask.status

        # 2. Verify dependencies are satisfied
        dep_outputs: dict[str, Any] = {}
        missing_dependencies: list[str] = []
        sources_unavailable = False
        if subtask.dependencies:
            dep_stmt = select(Subtask).where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            dep_res = await session.execute(dep_stmt)
            all_subtasks = {str(s.id): s for s in dep_res.scalars().all()}
            for dep_id in subtask.dependencies:
                dep_sub = all_subtasks.get(str(dep_id))
                if dep_sub is None:
                    # Defensive fallback: resolve planner string ID to deterministic UUID
                    resolved_dep_id = str(resolve_subtask_uuid(job.task_id, str(dep_id)))
                    dep_sub = all_subtasks.get(resolved_dep_id)

                if dep_sub is None or not self._satisfied(dep_sub):
                    logger.warning(
                        "Subtask %s dependencies not satisfied (dep %s status: %s)",
                        job.subtask_id,
                        dep_id,
                        getattr(dep_sub, "status", "missing"),
                    )
                    return SubtaskStatus.PENDING
                if dep_sub.status == SubtaskStatus.FAILED:
                    # A non-critical predecessor failed: run with its input marked missing.
                    dep_outputs[str(dep_id)] = {
                        "missing": True,
                        "reason": dep_sub.error_message or "the step failed",
                    }
                    missing_dependencies.append(str(dep_id))
                    if self._role(dep_sub) == "research":
                        sources_unavailable = True  # skipped research: nothing was retrieved
                elif dep_sub.output_result is not None:
                    dep_outputs[str(dep_id)] = dep_sub.output_result
                if _says_sources_unavailable(dep_sub):
                    # A research step could not retrieve web sources (search quota): the steps
                    # that write the deliverable must not invent figures.
                    sources_unavailable = True

        # 3. Check for human approval requirement (Decision D8, Decision D4, Decision D5)
        # Workers NEVER sleep or block on human approval; exit immediately!
        tool_name = subtask.input_data.get("tool") or subtask.input_data.get("tool_name")
        if subtask.input_data.get("executor") in ("file_edit", "project"):
            # A file-edit / project step runs its own handler; a raw tool name the planner copied
            # (file_edit_as_new_version with empty args) must never win over it.
            tool_name = None
        explicit_approval = bool(
            subtask.input_data.get("requires_approval", False)
            or subtask.input_data.get("approval_required", False)
        )
        tool_approval = False
        if tool_name and self._runs_research_pipeline(
            job, self.permission_registry.canonical_name(str(tool_name))
        ):
            # A research node that names web_search runs the grounded research pipeline (plan,
            # search, cited synthesis) instead of one bare search that returns no sources.
            tool_name = None
        if tool_name:
            # A known synonym (save_file ...) is classified as the tool it stands for; the
            # tier is the target's own and unknown names stay denied.
            tool_name = self.permission_registry.canonical_name(str(tool_name))
            if not self.permission_registry.is_registered(str(tool_name)):
                # Deny by default: an unclassified tool is refused, never offered for approval.
                # It is a recoverable planning error, so try the step without the tool first.
                healed = await self._try_heal(
                    session,
                    job,
                    subtask,
                    code=healing.CODE_UNCLASSIFIED_TOOL,
                    reason=f"tool '{tool_name}' is not classified",
                    dep_outputs=dep_outputs,
                    deferred_jobs=deferred_jobs,
                )
                if healed is not None:
                    return healed
                return await self._deny(
                    session,
                    job,
                    subtask,
                    tool=str(tool_name),
                    code="unclassified_tool",
                    message=(
                        f"Tool '{tool_name}' is not classified and is denied by default. "
                        "Plan the step without a tool or use a registered tool."
                    ),
                    deferred_jobs=deferred_jobs,
                )
            tier = self.permission_registry.classify(str(tool_name))
            if tier == ToolTier.RESTRICTED:
                return await self._deny(
                    session,
                    job,
                    subtask,
                    tool=str(tool_name),
                    code="restricted_tool",
                    message=(
                        f"Tool '{tool_name}' is RESTRICTED and unavailable: step-up "
                        "authentication is not enrolled"
                    ),
                )
            tool_approval = tier == ToolTier.APPROVAL_REQUIRED

        requires_approval = explicit_approval or tool_approval

        # Fetch parent task to get user context
        task_stmt = select(Task).where(
            Task.id == job.task_id,
            Task.workspace_id == job.workspace_id,
        )
        t_res = await session.execute(task_stmt)
        task = t_res.scalars().first()
        if task is not None and task.status == "cancelled":
            logger.info("Task %s was cancelled; skipping subtask %s", job.task_id, job.subtask_id)
            return subtask.status

        # Fail up front, with an actionable message, when the agent cannot run at all (missing
        # credential, coding disabled): never ask a human to approve an action that cannot happen.
        fleet_agent = None
        if (
            not tool_name
            and self.fleet_registry
            and self.fleet_registry.has_agent(job.assigned_agent)
        ):
            fleet_agent = self.fleet_registry.get_agent(job.assigned_agent)
        unavailable: str | None = None
        if fleet_agent is not None:
            per_user = getattr(fleet_agent, "availability_error_for", None)
            if callable(per_user):
                # Credentials are per user (Settings > Connections): ask about THIS job's owner.
                reason = await per_user(job)
            else:
                reason = getattr(fleet_agent, "availability_error", lambda: None)()
            unavailable = reason if isinstance(reason, str) and reason else None
        if unavailable is not None:
            healed = await self._try_heal(
                session,
                job,
                subtask,
                code=healing.CODE_AGENT_UNAVAILABLE,
                reason=unavailable,
                dep_outputs=dep_outputs,
                task=task,
                deferred_jobs=deferred_jobs,
            )
            if healed is not None:
                return healed
            return await self._deny(
                session,
                job,
                subtask,
                tool=job.assigned_agent,
                code="agent_unavailable",
                message=unavailable,
                deferred_jobs=deferred_jobs,
            )

        token_consumed = False
        claimed_approval_id: UUID | None = None
        worker_id = f"worker-{os.getpid()}-{uuid4().hex[:8]}"
        approval_token = subtask.input_data.get("approval_token")

        if requires_approval and approval_token and task is not None:
            expected_tool, expected_args = _approval_binding(subtask.input_data, tool_name)
            idempotency_key = str(subtask.input_data.get("idempotency_key") or job.subtask_id)
            expected_hash = compute_payload_hash(
                tool=expected_tool,
                args=expected_args,
                workspace_id=job.workspace_id,
                user_id=task.user_id,
                idempotency_key=idempotency_key,
            )

            try:
                conn = await session.connection()
                signing_key = self.jwt_signing_key
                if signing_key is None:
                    raise SubtaskExecutionError(
                        "JWT signing key is not configured for the subtask worker"
                    )

                claims = verify_approval_token(
                    str(approval_token),
                    signing_key,
                    expected_workspace_id=job.workspace_id,
                    expected_user_id=task.user_id,
                    expected_tool_name=expected_tool,
                    expected_payload_hash=expected_hash,
                )

                # Atomic CAS single-use claim in PostgreSQL
                # Prevents multiple workers from executing the same operation
                claimed = await claim_approval_atomically(
                    conn,
                    request_id=claims.request_id,
                    workspace_id=job.workspace_id,
                    payload_hash=expected_hash,
                    worker_id=worker_id,
                )
                if claimed:
                    await start_execution_atomically(
                        conn,
                        request_id=claims.request_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                    )
                    token_consumed = True
                    claimed_approval_id = claims.request_id
                    logger.info(
                        "Approval token claimed and execution started for subtask %s by %s",
                        job.subtask_id,
                        worker_id,
                    )
                else:
                    logger.warning(
                        "Approval claim refused for subtask %s (already claimed or revoked)",
                        job.subtask_id,
                    )
                    token_consumed = False
            except ApprovalTokenExpired:
                # The user approved, but the single-use token's short window ran out before this
                # step ran (a restart or a long queue). The token can never be used again and
                # the old row would stay "approved" forever while the card still said "waiting".
                # Close that row as expired so the step asks again with a fresh request; nothing
                # is executed on the stale one.
                token_consumed = False
                await self._expire_stale_approval(session, job, subtask, expected_hash)
            except Exception as err:
                logger.warning(
                    "Approval token verification failed for subtask %s: %s",
                    job.subtask_id,
                    err,
                )
                token_consumed = False

        if requires_approval and not token_consumed:
            if str(tool_name or "") == "file_replace_original" and (
                "approval_tool" not in (subtask.input_data or {})
            ):
                # The paths come from the edit step; bind the approval to exactly that pair and
                # show the diff (ADR-0022). A missing or unsafe pair fails before anyone is asked.
                from app.files.replace_flow import prepare_replace_original

                try:
                    replace_prep = await prepare_replace_original(
                        job.workspace_id,
                        job.user_id,
                        job.task_id,
                        dict(subtask.input_data or {}),
                        dep_outputs,
                    )
                except Exception as replace_err:
                    return await self._record_failure(
                        session,
                        job,
                        subtask,
                        reason=str(replace_err) or replace_err.__class__.__name__,
                        error_code=replace_err.__class__.__name__,
                        claimed_approval_id=None,
                        worker_id=worker_id,
                        retryable=False,
                        deferred_jobs=deferred_jobs,
                    )
                subtask.input_data = {
                    **(subtask.input_data or {}),
                    **replace_prep,
                    "approval_tool": "file_replace_original",
                    "approval_args": replace_prep["tool_args"],
                }
            if str(tool_name or "") == "notion_create_page_from_job" and (
                "approval_tool" not in (subtask.input_data or {})
            ):
                # A learning book (ADR-0031): bind the approval to the exact Markdown of this job
                # (its SHA-256 is inside the arguments) and show its outline and counts.
                try:
                    book_prep = await self._prepare_notion_book(session, job, subtask)
                except NeedsInput as need:
                    return await self._pause_for_input(session, job, subtask, task, need)
                except Exception as book_err:
                    return await self._record_failure(
                        session,
                        job,
                        subtask,
                        reason=str(book_err) or book_err.__class__.__name__,
                        error_code=book_err.__class__.__name__,
                        claimed_approval_id=None,
                        worker_id=worker_id,
                        retryable=False,
                        deferred_jobs=deferred_jobs,
                    )
                subtask.input_data = {
                    **(subtask.input_data or {}),
                    "tool_args": book_prep["tool_args"],
                    "approval_tool": "notion_create_page_from_job",
                    "approval_args": book_prep["tool_args"],
                    "payload_diff": book_prep["payload_diff"],
                    "book_summary": book_prep["summary"],
                }
            # Build the final content of the action first so the approval shows (and binds)
            # exactly what will happen, and a missing recipient fails before anyone is asked.
            prepare = getattr(self.fleet_support, "prepare_approval", None)
            if (
                fleet_agent is not None
                and callable(prepare)
                and "approval_tool" not in (subtask.input_data or {})
            ):
                prep_input = dict(subtask.input_data or {})
                prep_input.setdefault("description", subtask.description)
                if dep_outputs:
                    prep_input["dependency_outputs"] = dep_outputs
                prep_budget = min(
                    CONTEXT_TIMEOUT_SECONDS, job.timeout_seconds or self.default_timeout
                )
                try:
                    prepared = await asyncio.wait_for(prepare(job, prep_input), timeout=prep_budget)
                except TimeoutError:
                    return await self._record_failure(
                        session,
                        job,
                        subtask,
                        reason=(
                            f"Preparing the '{job.assigned_agent}' action timed out after "
                            f"{prep_budget:g}s (the model did not answer in time)."
                        ),
                        error_code="ExecutionTimeoutError",
                        claimed_approval_id=None,
                        worker_id=worker_id,
                        retryable=True,
                        deferred_jobs=deferred_jobs,
                    )
                except Exception as prep_err:
                    logger.warning("approval_preparation_failed subtask=%s", job.subtask_id)
                    return await self._record_failure(
                        session,
                        job,
                        subtask,
                        reason=str(prep_err) or prep_err.__class__.__name__,
                        error_code=prep_err.__class__.__name__,
                        claimed_approval_id=None,
                        worker_id=worker_id,
                        retryable=not isinstance(prep_err, NonRetryableSubtaskError),
                        deferred_jobs=deferred_jobs,
                    )
                if prepared:
                    subtask.input_data = {
                        **(subtask.input_data or {}),
                        **prepared["context"],
                        "approval_tool": prepared["tool"],
                        "approval_args": prepared["tool_args"],
                        "payload_diff": prepared.get("diff"),
                    }
            # Ensure approval request row is persisted in approval_requests table
            auto_token: str | None = None
            if task is not None:
                try:
                    conn = await session.connection()
                    repo = ApprovalRepository(conn, job.workspace_id, task.user_id)
                    expected_tool, expected_args = _approval_binding(subtask.input_data, tool_name)
                    idempotency_key = str(
                        subtask.input_data.get("idempotency_key") or job.subtask_id
                    )
                    expected_hash = compute_payload_hash(
                        tool=expected_tool,
                        args=expected_args,
                        workspace_id=job.workspace_id,
                        user_id=task.user_id,
                        idempotency_key=idempotency_key,
                    )
                    check_stmt = text(
                        "SELECT id FROM approval_requests "
                        "WHERE workspace_id = :workspace_id AND subtask_id = :subtask_id "
                        "  AND status IN ('pending', 'approved') LIMIT 1"
                    )
                    existing = await conn.execute(
                        check_stmt,
                        {"workspace_id": job.workspace_id, "subtask_id": job.subtask_id},
                    )
                    first_row = existing.first()
                    if inspect.isawaitable(first_row):
                        first_row = await first_row  # type: ignore[attr-defined]
                    if first_row is None:
                        auto_plan = MANUAL
                        # A scheduled (unattended) run never auto-approves, whatever the setting:
                        # nobody is there to have chosen it for this payload (ADR-0033).
                        unattended = str(getattr(task, "idempotency_key", "") or "").startswith(
                            "sched-"
                        )
                        if self.auto_approver is not None and tool_name and not unattended:
                            auto_plan = await self.auto_approver.plan(
                                session,
                                workspace_id=job.workspace_id,
                                user_id=task.user_id,
                                tool=expected_tool,
                                args=expected_args,
                            )
                        # ADR-0029: a flag only ADDS a warning to the card; a strong flag also
                        # refuses auto-approve. No answer changes nothing.
                        guard = await check_payload(
                            str(task.user_instruction), expected_tool, dict(expected_args)
                        )
                        if guard is not None and guard.strong:
                            auto_plan = MANUAL
                        description = subtask.description or f"Execution of {expected_tool}"
                        if guard is not None and guard.flagged:
                            description = description + chr(10) * 2 + guard.note()
                        if auto_plan.info:
                            description = description + chr(10) * 2 + auto_plan.info
                        created = await repo.create_request(
                            task_id=job.task_id,
                            subtask_id=job.subtask_id,
                            tool_name=expected_tool,
                            tool_args=expected_args,
                            payload_hash=expected_hash,
                            tier="APPROVAL_REQUIRED",
                            action_description=(
                                f"{description}\n\n{auto_plan.note}"
                                if auto_plan.note
                                else description
                            ),
                            payload_diff=subtask.input_data.get("diff")
                            or subtask.input_data.get("payload_diff"),
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                            idempotency_key=idempotency_key,
                        )
                        if self.auto_approver is not None and auto_plan.allowed:
                            auto_token = await self.auto_approver.approve(
                                session,
                                repo,
                                created,
                                auto_plan,
                                workspace_id=job.workspace_id,
                                user_id=task.user_id,
                            )
                        elif self.auto_approver is not None and auto_plan.note:
                            await self.auto_approver.record_declined(
                                session,
                                created,
                                workspace_id=job.workspace_id,
                                user_id=task.user_id,
                            )
                except Exception as app_err:
                    logger.warning("Failed to record approval request row: %s", app_err)

            if auto_token is not None and task is not None:
                # The owner's setting approved this exact payload (see notion_auto_approve): the
                # step resumes with the signed token exactly as after a click, and the worker
                # verifies, claims and runs it on the next pass.
                subtask.input_data = {**(subtask.input_data or {}), "approval_token": auto_token}
                subtask.status = SubtaskStatus.PENDING
                resume = SubtaskJob(
                    workspace_id=job.workspace_id,
                    user_id=task.user_id,
                    task_id=job.task_id,
                    subtask_id=job.subtask_id,
                    assigned_agent=job.assigned_agent,
                )
                if deferred_jobs is not None:
                    deferred_jobs.append(resume)  # enqueued by the runtime after the commit
                else:
                    await self.queue.enqueue(resume)
                await session.flush()
                return SubtaskStatus.PENDING
            subtask.status = SubtaskStatus.AWAITING_APPROVAL
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.TASK_PAUSED_APPROVAL,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "assigned_agent": job.assigned_agent,
                    "tool_name": tool_name,
                    "reason": "Human approval required before executing mutating side effect",
                },
            )
            # Update parent task state to paused / awaiting_approval
            if task:
                task.status = "awaiting_approval"
                task.plan_status = PlanStatus.PAUSED

            await _audit(
                session,
                job,
                action="task.paused",
                status="pending",
                output={"reason": "approval_required", "tool": str(tool_name or "")},
            )
            await session.flush()
            logger.info(
                "Subtask %s requires approval; transitioned to awaiting_approval "
                "and worker exited cleanly.",
                job.subtask_id,
            )
            return SubtaskStatus.AWAITING_APPROVAL

        # 4. Transition to in_progress
        subtask.status = SubtaskStatus.IN_PROGRESS
        if task is not None and task.status == "awaiting_approval":
            task.status = "executing"
            task.plan_status = PlanStatus.EXECUTING
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.IN_PROGRESS.value,
                "assigned_agent": job.assigned_agent,
            },
        )
        await _audit(session, job, action="task.started", status="success")
        await session.flush()
        # Make "in progress" (and any approval claim) durable and visible to the outbox relay
        # before the potentially long handler call; failures below are recorded in a new
        # transaction.
        await session.commit()

        # 5. Execute assigned agent handler within bounded timeout
        timeout: float = job.timeout_seconds or self.default_timeout
        agent_budget = getattr(fleet_agent, "execution_timeout_seconds", None)
        if isinstance(agent_budget, int | float) and 0 < agent_budget < timeout:
            timeout = agent_budget  # per-agent budget: a hung provider fails fast and clearly
        agent_input = dict(subtask.input_data or {})
        agent_input.setdefault("description", subtask.description)
        # Only a token this worker verified and claimed may authorise a side effect; a value
        # smuggled in through planner-written input_data is overwritten, never trusted.
        agent_input["has_valid_approval_token"] = token_consumed
        if agent_input.get("input_files"):
            # Attached files reach every step as untrusted dependency data (ADR-0022).
            from app.files.attachments import attachment_dependency_outputs

            attached = await attachment_dependency_outputs(
                job.workspace_id, [str(x) for x in agent_input["input_files"]]
            )
            if attached:
                dep_outputs = {**dep_outputs, **attached}
        if dep_outputs:
            agent_input["dependency_outputs"] = dep_outputs
        if missing_dependencies:
            agent_input["missing_dependencies"] = missing_dependencies
        if sources_unavailable:
            agent_input["sources_unavailable"] = True
        # The signed approval JWT is a credential: never hand it to handlers or LLM prompts.
        agent_input.pop("approval_token", None)
        # An agent may size its own budget from the input (a long document needs 60 s plus 6 s
        # per page, up to 10 minutes); that is the only way a budget can exceed the default.
        budget_for = getattr(fleet_agent, "execution_timeout_for", None)
        if callable(budget_for):
            scaled = budget_for(agent_input)
            if isinstance(scaled, int | float) and scaled > 0:
                timeout = float(scaled)
        if (subtask.input_data or {}).get("executor") in ("outline", "chapter"):
            # A chapter or outline is a structured model call (plus one repair turn) with a
            # 150 s attempt cap: one slow attempt, its repair turn and a failover must fit.
            timeout = max(timeout, LONG_STEP_TIMEOUT_SECONDS)

        started_at = time.monotonic()
        stage = "running"
        stage_timeout = float(timeout)
        try:
            handler_output: dict[str, Any] = {}
            if tool_name and self.tool_invoker is not None:
                handler_output = await self._tool_with_step(
                    session, job, subtask, str(tool_name), token_consumed, timeout
                )
            elif self.fleet_registry and self.fleet_registry.has_agent(job.assigned_agent):
                agent = self.fleet_registry.get_agent(job.assigned_agent)
                assert agent is not None
                if self.fleet_support is not None:
                    stage, stage_timeout = "preparing", min(CONTEXT_TIMEOUT_SECONDS, timeout)
                    agent_input = await asyncio.wait_for(
                        self.fleet_support.build_context(job, agent_input),
                        timeout=stage_timeout,
                    )
                    stage, stage_timeout = "running", float(timeout)
                agent_res = await asyncio.wait_for(
                    agent.execute_subtask(job, agent_input),
                    timeout=timeout,
                )
                if (
                    agent_res.requires_human_approval
                    and agent_res.status == AgentExecutionStatus.PARTIAL
                ):
                    subtask.status = SubtaskStatus.AWAITING_APPROVAL
                    subtask.output_result = _as_result_dict(agent_res.summary)
                    subtask.verification_proof = _as_proof_dict(agent_res.verification_proof)
                    if task is not None:
                        try:
                            conn = await session.connection()
                            repo = ApprovalRepository(conn, job.workspace_id, task.user_id)
                            act_ctx = agent_res.approval_context or {}
                            act_tool = str(
                                act_ctx.get("tool")
                                or act_ctx.get("tool_name")
                                or f"{job.assigned_agent}_action"
                            )
                            act_args = act_ctx.get("tool_args") or act_ctx.get("args") or {}
                            act_idempotency = str(
                                act_ctx.get("idempotency_key") or f"{job.subtask_id}-agent-approval"
                            )
                            act_hash = compute_payload_hash(
                                tool=act_tool,
                                args=act_args,
                                workspace_id=job.workspace_id,
                                user_id=task.user_id,
                                idempotency_key=act_idempotency,
                            )
                            # Persist what was approved so the resumed run is gated on the same
                            # binding and acts on the same content (not a regenerated one).
                            subtask.input_data = {
                                **(subtask.input_data or {}),
                                **{
                                    k: v
                                    for k, v in act_ctx.items()
                                    if k not in _RESERVED_INPUT_KEYS
                                },
                                "requires_approval": True,
                                "approval_tool": act_tool,
                                "approval_args": act_args,
                                "idempotency_key": act_idempotency,
                            }
                            check_stmt = text(
                                "SELECT id FROM approval_requests "
                                "WHERE workspace_id = :workspace_id AND subtask_id = :subtask_id "
                                "  AND status IN ('pending', 'approved') LIMIT 1"
                            )
                            res = await conn.execute(
                                check_stmt,
                                {"workspace_id": job.workspace_id, "subtask_id": job.subtask_id},
                            )
                            first_res = res.first()
                            if inspect.isawaitable(first_res):
                                first_res = await first_res  # type: ignore[attr-defined]
                            if first_res is None:
                                await repo.create_request(
                                    task_id=job.task_id,
                                    subtask_id=job.subtask_id,
                                    tool_name=act_tool,
                                    tool_args=act_args,
                                    payload_hash=act_hash,
                                    tier="APPROVAL_REQUIRED",
                                    action_description=agent_res.summary,
                                    payload_diff=act_ctx.get("diff") or act_ctx.get("payload_diff"),
                                    expires_at=datetime.now(UTC) + timedelta(days=7),
                                    idempotency_key=act_idempotency,
                                )
                        except Exception as app_err:
                            logger.warning(
                                "Failed to record dynamic approval request row: %s", app_err
                            )

                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.TASK_PAUSED_APPROVAL,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "assigned_agent": job.assigned_agent,
                            "reason": agent_res.summary,
                            "approval_context": agent_res.approval_context,
                        },
                    )
                    if task:
                        task.status = "awaiting_approval"
                        task.plan_status = PlanStatus.PAUSED
                    await _audit(
                        session,
                        job,
                        action="task.paused",
                        status="pending",
                        output={"reason": "approval_required"},
                    )
                    await session.flush()
                    return SubtaskStatus.AWAITING_APPROVAL

                elif agent_res.status == AgentExecutionStatus.FAILED and (
                    _proof_says_rate_limited(agent_res.verification_proof)
                ):
                    return await self._record_failure(
                        session,
                        job,
                        subtask,
                        reason=RATE_LIMITED_MESSAGE,
                        error_code="RateLimited",
                        claimed_approval_id=claimed_approval_id,
                        worker_id=worker_id,
                        retryable=not token_consumed,
                        deferred_jobs=deferred_jobs,
                        started_at=started_at,
                        rate_limited=True,
                    )
                elif agent_res.status in (
                    AgentExecutionStatus.FAILED,
                    AgentExecutionStatus.BLOCKED,
                ):
                    proof_error = (agent_res.verification_proof or {}).get("error")
                    if (
                        agent_res.status == AgentExecutionStatus.FAILED
                        and not token_consumed
                        and proof_error in ("all_sources_failed", "no_sources_connected")
                    ):
                        healed = await self._try_heal(
                            session,
                            job,
                            subtask,
                            code=healing.CODE_ALL_SOURCES_FAILED,
                            reason=str(agent_res.summary),
                            dep_outputs=dep_outputs,
                            task=task,
                            deferred_jobs=deferred_jobs,
                        )
                        if healed is not None:
                            return healed
                    subtask.status = SubtaskStatus.FAILED
                    subtask.error_message = str(agent_res.summary)
                    subtask.output_result = _as_result_dict(agent_res.summary)
                    subtask.verification_proof = _as_proof_dict(agent_res.verification_proof)
                    if not token_consumed and not self._is_critical(subtask):
                        return await self._finish_tolerated_failure(
                            session, job, subtask, str(agent_res.summary), deferred_jobs
                        )
                    if task and task.status != "cancelled":
                        self._mark_task_failed(task, subtask, str(agent_res.summary))
                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "status": SubtaskStatus.FAILED.value,
                            "error": str(agent_res.summary),
                        },
                    )
                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.TASK_FAILED,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "error": str(agent_res.summary),
                        },
                    )
                    await _audit(
                        session,
                        job,
                        action="task.failed",
                        status="failed",
                        output={"error": str(agent_res.summary)},
                    )
                    await session.flush()
                    return SubtaskStatus.FAILED

                if self.fleet_support is not None:
                    handler_output = self.fleet_support.build_result(job, agent_res)
                else:
                    handler_output = {
                        "result": agent_res.summary,
                        "proof": agent_res.verification_proof,
                    }
            elif agent_handler is not None:
                # Fallback only for roles without a specialist agent (for example browser/file).
                handler_output = await asyncio.wait_for(
                    agent_handler(job, agent_input),
                    timeout=timeout,
                )
            else:
                # No success without proof: never fabricate a result when nothing can execute.
                raise NonRetryableSubtaskError(
                    f"No executor is configured for agent '{job.assigned_agent}'"
                )

            subtask.output_result = _as_result_dict(handler_output.get("result", handler_output))
            subtask.verification_proof = _as_proof_dict(handler_output.get("proof"))
            trail = list((subtask.input_data or {}).get("healing") or [])
            if trail:
                # Say honestly that this step did not run the way it was planned.
                subtask.output_result = {
                    **(subtask.output_result or {}),
                    "recovered": healing.recovered_text(trail),
                    "healing": trail,
                }
                subtask.verification_proof = {
                    **(subtask.verification_proof or {}),
                    "healing": [
                        *trail,
                        *((subtask.verification_proof or {}).get("healing") or []),
                    ],
                }
                subtask.error_message = None
            # ADR-0029: advisory review of the result. It can only ADD a "may be incomplete" mark;
            # no answer (error, timeout, off) changes nothing.
            review = await review_step(
                str(task.user_instruction) if task is not None else "",
                str(subtask.description or ""),
                subtask.output_result,
                files=_proof_file_names(handler_output.get("proof")),
                # Research found little or nothing (this step or an upstream one said "no web
                # sources"): an honest short result is complete, not "incomplete".
                thin_input=sources_unavailable or _says_sources_unavailable(subtask),
                proof=_as_proof_dict(handler_output.get("proof")),
            )
            # Any model objection is recorded (advisory note); only a corroborated one in
            # "enforce" mode changes the task status (failed_step_report).
            if review is not None and review["reasons"]:
                subtask.output_result = {
                    **(subtask.output_result or {}),
                    DECISION_REVIEW_KEY: review,
                }
            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.now(UTC)
            await self._sync_task_spend(session, job)
            await _audit_tool(
                session,
                job,
                subtask,
                self.permission_registry,
                status="success",
                duration_ms=int((time.monotonic() - started_at) * 1000),
                server_name=self._server_name_for(
                    str((subtask.input_data or {}).get("tool") or "")
                ),
            )

            # Persist FileArtifact if generated and verified. A registration problem never fails an
            # otherwise good task nor poisons the session: each row is its own savepoint.
            artifact_warnings = await _register_artifacts(
                session, job, task.user_id if task else job.user_id, handler_output.get("proof")
            )
            if artifact_warnings:
                subtask.output_result = {
                    **(subtask.output_result or {}),
                    "warnings": [
                        *list((subtask.output_result or {}).get("warnings") or []),
                        *artifact_warnings,
                    ],
                }

            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "status": SubtaskStatus.COMPLETED.value,
                    # The step's title as the task card shows it (clients name the step with it).
                    "description": str(subtask.description or "")[:160],
                    # A chapter's full draft is large: it stays in the step's own row only.
                    "output": light_output(subtask.output_result),
                },
            )

            await self._advance_task(
                session,
                job,
                subtask,
                deferred_jobs,
                node_status=SubtaskStatus.COMPLETED.value,
                node_output=light_output(subtask.output_result),
            )

            if claimed_approval_id is not None:
                try:
                    conn = await session.connection()
                    await complete_execution_atomically(
                        conn,
                        request_id=claimed_approval_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                        output=handler_output,
                    )
                except Exception as rec_err:
                    logger.warning("Failed to record completed execution: %s", rec_err)

            await session.flush()
            return SubtaskStatus.COMPLETED

        except TimeoutError:
            logger.error("Subtask %s exceeded timeout of %ss", job.subtask_id, stage_timeout)
            if stage == "preparing":
                timeout_reason = (
                    f"Preparing the input for the '{job.assigned_agent}' agent timed out after "
                    f"{stage_timeout:g}s (the model did not answer in time)."
                )
            else:
                timeout_reason = (
                    f"The '{job.assigned_agent}' agent timed out after "
                    f"{stage_timeout:g}s without finishing."
                )
            if token_consumed:
                # The approved action may have gone through; a blind retry could repeat it.
                timeout_reason += (
                    " The approved action may or may not have completed: check before retrying."
                )
            return await self._record_failure(
                session,
                job,
                subtask,
                reason=timeout_reason,
                error_code="ExecutionTimeoutError",
                claimed_approval_id=claimed_approval_id,
                worker_id=worker_id,
                retryable=not token_consumed,
                deferred_jobs=deferred_jobs,
                started_at=started_at,
            )

        except Exception as exc:
            logger.error("Subtask %s failed: %s", job.subtask_id, exc, exc_info=True)
            if isinstance(exc, MissingToolArgumentsError):
                healed = await self._try_heal(
                    session,
                    job,
                    subtask,
                    code=healing.CODE_MISSING_ARGUMENTS,
                    reason=str(exc),
                    dep_outputs=dep_outputs,
                    task=task,
                    token_consumed=token_consumed,
                    deferred_jobs=deferred_jobs,
                )
                if healed is not None:
                    return healed
            if isinstance(exc, ToolNotProvidedError):
                healed = await self._try_heal(
                    session,
                    job,
                    subtask,
                    code=healing.CODE_TOOL_UNAVAILABLE,
                    reason=str(exc),
                    dep_outputs=dep_outputs,
                    task=task,
                    token_consumed=token_consumed,
                    deferred_jobs=deferred_jobs,
                )
                if healed is not None:
                    return healed
            limited = is_rate_limited(exc)
            return await self._record_failure(
                session,
                job,
                subtask,
                reason=_overloaded_reason(exc)
                or (RATE_LIMITED_MESSAGE if limited else str(exc) or exc.__class__.__name__),
                error_code="RateLimited" if limited else exc.__class__.__name__,
                claimed_approval_id=claimed_approval_id,
                worker_id=worker_id,
                retryable=not isinstance(exc, NonRetryableSubtaskError)
                and not (limited and token_consumed),
                deferred_jobs=deferred_jobs,
                started_at=started_at,
                rate_limited=limited,
            )

    # ------------------------------------------------------------------ self-healing

    async def _sync_task_spend(self, session: AsyncSession, job: SubtaskJob) -> None:
        """Copy the task's recorded model spend (budget tracker) into ``tasks.spent_usd``.

        Best effort and monotonic: the counter only ever goes up, an unpriced task keeps its
        value (unknown cost is never written as zero) and any tracker failure is logged, never
        raised into the step.
        """
        gateway = getattr(self.fleet_support, "gateway", None)
        tracker = getattr(gateway, "budget_tracker", None)
        if tracker is None:
            return
        try:
            usage = await tracker.get_usage(job.workspace_id, job.task_id)
            cost = Decimal(usage["cost_usd"]).quantize(Decimal("0.0001"))
            if cost <= 0:
                return
            task = (
                (
                    await session.execute(
                        select(Task).where(
                            Task.id == job.task_id, Task.workspace_id == job.workspace_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if task is not None and cost > (task.spent_usd or Decimal("0")):
                task.spent_usd = cost
        except Exception as err:  # noqa: BLE001
            logger.warning("task_spend_sync_failed error=%s", type(err).__name__)

    def _runs_research_pipeline(self, job: SubtaskJob, tool_name: str) -> bool:
        """True for a research step whose planner hint is the ``web_search`` tool.

        Every research step is ONE Gemini google_search grounded call made by the research agent
        (search alias, fallback alias, one retry on another alias, citations kept), whatever tool
        hint the planner attached: the bare tool returns one answer without sources and needs a
        separately wired search backend. The tool path is used only when no research agent is
        registered at all.
        """
        if tool_name != "web_search" or str(job.assigned_agent).lower() != "research":
            return False
        return bool(self.fleet_registry and self.fleet_registry.has_agent("research"))

    @staticmethod
    def _role(subtask: Subtask) -> str:
        return str(getattr(subtask.assigned_agent, "value", subtask.assigned_agent))

    def _is_critical(self, subtask: Subtask) -> bool:
        """A failure of a critical step fails the task; side-effect steps are always critical."""
        proof = subtask.verification_proof
        if isinstance(proof, dict) and proof.get("recovered_failure") is True:
            # Written only by ``fail_step_plainly`` (never by a planner): the deliverable already
            # exists, so the task ends "completed with errors" instead of failing or wedging.
            return False
        data = subtask.input_data or {}
        gated = bool(data.get("requires_approval") or data.get("approval_required"))
        tool = data.get("tool") or data.get("tool_name")
        if tool:
            canonical = self.permission_registry.canonical_name(str(tool))
            if (
                self.permission_registry.is_registered(canonical)
                and self.permission_registry.classify(canonical) >= ToolTier.APPROVAL_REQUIRED
            ):
                gated = True
        return healing.is_critical(data, self._role(subtask), gated)

    def _satisfied(self, subtask: Subtask) -> bool:
        """Done for the purpose of unblocking dependents: completed, or failed but non-critical."""
        return subtask.status == SubtaskStatus.COMPLETED or (
            subtask.status == SubtaskStatus.FAILED and not self._is_critical(subtask)
        )

    @staticmethod
    def _mark_task_failed(task: Task, subtask: Subtask, reason: str) -> None:
        """Fail the task and keep the FIRST real cause (later failures do not overwrite it)."""
        task.status = "failed"
        task.plan_status = PlanStatus.FAILED
        context = dict(task.shared_context or {})
        if "failure" not in context:
            context["failure"] = {
                "subtask_id": str(subtask.id),
                "step": (subtask.description or "")[:200],
                "message": reason[:1000],
            }
            task.shared_context = context

    async def fail_step_plainly(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        *,
        reason: str,
        deferred_jobs: list[SubtaskJob] | None,
    ) -> SubtaskStatus:
        """End a step the worker could not run or finish, with a plain reason (never a wedge).

        The Notion book step keeps the finished book: the step fails with "Could not prepare the
        Notion page: <reason>" and the task ends "completed with errors". Any other step fails the
        way a handler failure does (its task fails with the reason, or a non-critical step is
        skipped). ``reason`` must hold no values or secrets (callers pass a class name)."""
        short = " ".join(reason.split())[:160] or "an internal error"
        tool = str((subtask.input_data or {}).get("tool") or "")
        if tool == "notion_create_page_from_job":
            subtask.verification_proof = {
                **(subtask.verification_proof or {}),
                "recovered_failure": True,
            }
            return await self._finish_tolerated_failure(
                session,
                job,
                subtask,
                f"Could not prepare the Notion page: {short}",
                deferred_jobs,
            )
        return await self._record_failure(
            session,
            job,
            subtask,
            reason=f"Could not finish this step: {short}",
            error_code="StepRecoveryError",
            claimed_approval_id=None,
            worker_id="recovery",
            retryable=False,
            deferred_jobs=deferred_jobs,
        )

    async def _finish_tolerated_failure(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        reason: str,
        deferred_jobs: list[SubtaskJob] | None,
    ) -> SubtaskStatus:
        """A non-critical step failed for good: the task continues, with a warning."""
        subtask.status = SubtaskStatus.FAILED
        subtask.error_message = reason
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.FAILED.value,
                "error": reason,
                "description": str(subtask.description or "")[:160],
                "tolerated": True,
            },
        )
        await _audit(
            session,
            job,
            action="task.step_skipped",
            status="failed",
            output={"error": reason, "tolerated": True},
        )
        await session.flush()
        await self._advance_task(
            session,
            job,
            subtask,
            deferred_jobs,
            node_status=SubtaskStatus.FAILED.value,
            node_output=None,
        )
        return SubtaskStatus.FAILED

    async def _try_heal(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        *,
        code: str,
        reason: str,
        dep_outputs: dict[str, Any],
        deferred_jobs: list[SubtaskJob] | None,
        task: Task | None = None,
        token_consumed: bool = False,
    ) -> SubtaskStatus | None:
        """Recover from a recoverable failure by taking the next route, or return ``None``.

        Never heals around an approval gate or an approved action, a RESTRICTED or
        approval-tier tool, a cancelled task or a step that already used its healing hops. The
        route and the reason are appended to ``input_data.healing`` (bounded), written to an
        event, and shown on the finished step.
        """
        data = dict(subtask.input_data or {})
        if token_consumed or data.get("requires_approval") or data.get("approval_required"):
            return None
        tool_raw = data.get("tool") or data.get("tool_name")
        tool = str(tool_raw) if tool_raw else None
        if tool:
            canonical = self.permission_registry.canonical_name(tool)
            if (
                self.permission_registry.is_registered(canonical)
                and self.permission_registry.classify(canonical) >= ToolTier.APPROVAL_REQUIRED
            ):
                return None
        if task is None:
            task = (
                (
                    await session.execute(
                        select(Task).where(
                            Task.id == job.task_id, Task.workspace_id == job.workspace_id
                        )
                    )
                )
                .scalars()
                .first()
            )
        if task is not None and task.status == "cancelled":
            return None
        trail = list(data.get("healing") or [])
        role = self._role(subtask)
        if (
            tool
            and role in ("coding", "email")
            and code
            in (
                healing.CODE_UNCLASSIFIED_TOOL,
                healing.CODE_TOOL_UNAVAILABLE,
            )
        ):
            return None  # a tool step of a side-effect role is never silently rewritten
        decision = healing.decide(
            code=code,
            role=role,
            description=subtask.description or "",
            tool=tool,
            dep_outputs=dep_outputs,
            save_tools=SAVE_TOOL_NAMES,
            hops=len(trail),
        )
        if decision is None:
            return None

        if tool:
            await _audit(
                session,
                job,
                action=None,
                status="denied",
                server_name=self._server_name_for(tool) or "maya",
                tool_name=tool,
                output={"error": code, "healed": decision.action},
            )
        if decision.action == "skip":
            source = tool or "this step"
            item = healing.entry(len(trail) + 1, source, "skipped", decision.reason)
        elif decision.action == "rerun_without_tool":
            item = healing.entry(len(trail) + 1, tool or "tool", "no tool", decision.reason)
        else:
            item = healing.entry(len(trail) + 1, role, "executor", decision.reason)
        trail.append(item)
        note = healing.recovered_text(trail)
        new_data = {k: v for k, v in data.items() if k not in ("tool", "tool_name", "tool_args")}
        new_data["healing"] = trail
        logger.info(
            "subtask_healed subtask=%s code=%s action=%s", job.subtask_id, code, decision.action
        )

        if decision.action == "skip":
            subtask.input_data = new_data
            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.now(UTC)
            subtask.error_message = None
            subtask.output_result = {
                "skipped": True,
                "reason": decision.reason,
                "recovered": note,
                "healing": trail,
            }
            subtask.verification_proof = {
                "status": "skipped",
                "kind": "healing_skip",
                "predecessor": decision.predecessor,
                "healing": trail,
            }
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "status": SubtaskStatus.COMPLETED.value,
                    "skipped": True,
                    "healing": item,
                    "recovered": note,
                },
            )
            await _audit(
                session, job, action="task.step_healed", status="success", output={"healing": item}
            )
            await session.flush()
            await self._advance_task(
                session,
                job,
                subtask,
                deferred_jobs,
                node_status=SubtaskStatus.COMPLETED.value,
                node_output=subtask.output_result,
            )
            return SubtaskStatus.COMPLETED

        assigned = job.assigned_agent
        if decision.action == "reroute_executor":
            new_data["executor"] = "text"
            subtask.assigned_agent = SubtaskAgent.FILE
            assigned = SubtaskAgent.FILE.value
        subtask.input_data = new_data
        subtask.status = SubtaskStatus.PENDING
        subtask.error_message = f"Recovering: {reason}"[:1000]
        retry_job = SubtaskJob(
            workspace_id=job.workspace_id,
            user_id=job.user_id,
            task_id=job.task_id,
            subtask_id=job.subtask_id,
            assigned_agent=assigned,
            timeout_seconds=job.timeout_seconds,
        )
        if deferred_jobs is not None:
            deferred_jobs.append(retry_job)
        else:
            await self.queue.enqueue(retry_job)
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.PENDING.value,
                "healing": item,
                "recovered": note,
            },
        )
        await _audit(
            session, job, action="task.step_healed", status="success", output={"healing": item}
        )
        await session.flush()
        return SubtaskStatus.PENDING

    async def _advance_task(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        deferred_jobs: list[SubtaskJob] | None,
        *,
        node_status: str,
        node_output: dict[str, Any] | None,
    ) -> None:
        """After a step reached a final state: complete the task or enqueue what is unblocked."""
        # Sibling subtasks of this task may finish at the same moment on other workers, each
        # in its own transaction. Serialise the "is the task done / what is unblocked" step
        # on the task row, then read every sibling fresh, so the last finisher sees the whole
        # picture (otherwise two parallel finishers could each miss the other and the task
        # would never complete, or a join step would never be queued).
        # ``key_share=True`` makes this ``FOR NO KEY UPDATE``. A plain ``FOR UPDATE`` deadlocks
        # here: by this point every finisher already holds a ``FOR KEY SHARE`` lock on the
        # task row (the foreign keys of the outbox/audit rows it just inserted), and two
        # ``FOR UPDATE`` requests each wait for the other's key-share lock. ``FOR NO KEY
        # UPDATE`` still serialises finishers but is compatible with key-share locks.
        task_stmt = (
            select(Task)
            .where(
                Task.id == job.task_id,
                Task.workspace_id == job.workspace_id,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        t_res = await session.execute(task_stmt)
        task = t_res.scalars().first()
        all_stmt = (
            select(Subtask)
            .where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        all_res = await session.execute(all_stmt)
        task_subtasks = list(all_res.scalars().all())
        all_done = all(self._satisfied(st) for st in task_subtasks)
        if node_status == SubtaskStatus.COMPLETED.value and stage_of(subtask.input_data) == (
            STAGE_OUTLINE
        ):
            self._retitle_chapters(task, task_subtasks, node_output)
        if task and task.active_plan:
            updated_plan = []
            for node in task.active_plan:
                node_id = str(node.get("id"))
                st_uuid = resolve_subtask_uuid(task.id, node_id)
                if st_uuid == job.subtask_id or node_id == str(job.subtask_id):
                    node_copy = dict(node)
                    node_copy["status"] = node_status
                    node_copy["output_result"] = node_output
                    updated_plan.append(node_copy)
                else:
                    updated_plan.append(node)
            task.active_plan = updated_plan

        if all_done:
            if task and task.status != "cancelled":
                task.status = "completed"
                task.plan_status = PlanStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
                # A step that failed but was not critical, and any recovery that happened, are
                # reported with the result instead of being hidden (status stays "completed":
                # a new status would need a database migration).
                failed_steps = failed_step_report(task_subtasks)
                warnings = [
                    f"Step '{(st.description or '')[:80]}' failed and was skipped: "
                    f"{st.error_message}"
                    for st in task_subtasks
                    if st.status == SubtaskStatus.FAILED
                ]
                # A file that finished but missed the length the user asked for is reported
                # with the result as a NOTE ("You asked for 50 pages; the file has 38"). It is
                # never a failed step: the file was delivered (count policy).
                warnings.extend(
                    str(report["acceptance"].get("note") or report["acceptance"]["message"])
                    for st in task_subtasks
                    if isinstance(report := (st.verification_proof or {}).get("report"), dict)
                    and isinstance(report.get("acceptance"), dict)
                    and report["acceptance"].get("message")
                )
                warnings.extend(_unsourced_warnings(task_subtasks))
                warnings.extend(step_review_notes(task_subtasks))
                recovered = [
                    {
                        "subtask_id": str(st.id),
                        "step": (st.description or "")[:80],
                        "recovered": healing.recovered_text(list(st.input_data["healing"])),
                    }
                    for st in task_subtasks
                    if (st.input_data or {}).get("healing")
                ]
                completed_payload: dict[str, Any] = {
                    "task_id": str(job.task_id),
                    "status": "completed",
                }
                if failed_steps:
                    # "Completed with errors": status stays "completed" (a new status would need a
                    # migration); the flag and the steps travel with the result and the event.
                    completed_payload["partial"] = True
                    completed_payload["failed_steps"] = failed_steps
                    context = dict(task.shared_context or {})
                    context["partial"] = True
                    context["failed_steps"] = failed_steps
                    task.shared_context = context
                if warnings or recovered:
                    context = dict(task.shared_context or {})
                    if warnings:
                        context["warnings"] = warnings
                        completed_payload["warnings"] = warnings
                    if recovered:
                        context["healing"] = recovered
                        completed_payload["healing"] = recovered
                    task.shared_context = context
                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_COMPLETED,
                    payload=completed_payload,
                )
                await _audit(session, job, action="task.completed", status="success")
        else:
            # Progress DAG: Find newly unblocked pending subtasks and enqueue them
            completed_ids = {str(st.id) for st in task_subtasks if self._satisfied(st)}
            pending_subtasks = [st for st in task_subtasks if st.status == SubtaskStatus.PENDING]
            for pst in pending_subtasks:
                deps = pst.dependencies or []
                deps_satisfied = True
                for d in deps:
                    d_str = str(d)
                    if d_str not in completed_ids:
                        resolved_d = str(resolve_subtask_uuid(job.task_id, d_str))
                        if resolved_d not in completed_ids:
                            deps_satisfied = False
                            break
                if deps_satisfied:
                    agent_name = (
                        pst.assigned_agent.value
                        if hasattr(pst.assigned_agent, "value")
                        else str(pst.assigned_agent)
                    )
                    next_job = SubtaskJob(
                        workspace_id=job.workspace_id,
                        user_id=job.user_id,
                        task_id=job.task_id,
                        subtask_id=pst.id,
                        assigned_agent=agent_name,
                    )
                    if deferred_jobs is not None:
                        deferred_jobs.append(next_job)
                    else:
                        await self.queue.enqueue(next_job)
                    logger.info("Enqueued unblocked subtask %s for task %s", pst.id, job.task_id)

    @staticmethod
    def _retitle_chapters(
        task: Task | None, task_subtasks: list[Subtask], outline_output: dict[str, Any] | None
    ) -> None:
        """Give the chapter steps their real titles ("Chapter 3: OOP basics") once the outline
        exists, in the step rows and in the stored plan. A chapter with no entry in the outline
        (it came back shorter than planned) is marked as not needed and writes nothing."""
        outline = (outline_output or {}).get("outline")
        chapters = outline.get("chapters") if isinstance(outline, dict) else None
        if not isinstance(chapters, list):
            return
        steps: list[tuple[str, int]] = []
        by_id: dict[str, Subtask] = {}
        for st in task_subtasks:
            pipeline = (st.input_data or {}).get("pipeline")
            if (
                st.status == SubtaskStatus.PENDING
                and isinstance(pipeline, dict)
                and pipeline.get("stage") == "chapter"
            ):
                steps.append((str(st.id), int(pipeline.get("index") or 0)))
                by_id[str(st.id)] = st
        titles = retitles(steps, chapters)
        for step_id, (title, _used) in titles.items():
            by_id[step_id].description = title
        if task is not None and task.active_plan:
            plan = []
            for node in task.active_plan:
                st_id = str(resolve_subtask_uuid(task.id, str(node.get("id"))))
                if st_id in titles:
                    node = {**node, "description": titles[st_id][0]}
                plan.append(node)
            task.active_plan = plan

    def _server_name_for(self, tool_name: str) -> str | None:
        lookup = getattr(self.tool_invoker, "server_name_for", None)
        return lookup(tool_name) if callable(lookup) else None

    async def _deny(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        *,
        tool: str,
        code: str,
        message: str,
        deferred_jobs: list[SubtaskJob] | None = None,
    ) -> SubtaskStatus:
        """Refuse a step: fail the subtask (and the task, unless the step is non-critical)."""
        subtask.status = SubtaskStatus.FAILED
        subtask.error_message = message
        tolerated = code != healing.CODE_RESTRICTED_TOOL and not self._is_critical(subtask)
        task = (
            (
                await session.execute(
                    select(Task).where(
                        Task.id == job.task_id, Task.workspace_id == job.workspace_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if task is not None and task.status != "cancelled" and not tolerated:
            self._mark_task_failed(task, subtask, message)
        events: list[tuple[TaskEventType, dict[str, Any]]] = [
            (
                TaskEventType.SUBTASK_STATUS_CHANGED,
                {"status": SubtaskStatus.FAILED.value, "error": message, "tolerated": tolerated},
            )
        ]
        if not tolerated:
            events.append((TaskEventType.TASK_FAILED, {"error": code, "message": message}))
        for event_type, payload in events:
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=event_type,
                payload={"task_id": str(job.task_id), "subtask_id": str(job.subtask_id), **payload},
            )
        level = None
        if self.permission_registry.is_registered(tool):
            level = _TIER_TO_AUDIT_LEVEL.get(self.permission_registry.classify(tool).value)
        await _audit(
            session,
            job,
            action=None,
            status="denied",
            server_name=self._server_name_for(tool) or "maya",
            tool_name=tool,
            permission_level=level,
            output={"error": code},
        )
        await session.flush()
        logger.warning("Subtask %s tool '%s' denied: %s", job.subtask_id, tool, code)
        if tolerated and task is not None and task.status != "cancelled":
            await self._advance_task(
                session,
                job,
                subtask,
                deferred_jobs,
                node_status=SubtaskStatus.FAILED.value,
                node_output=None,
            )
        return SubtaskStatus.FAILED

    def _repair_tool_args(
        self, subtask: Subtask, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Fill or explain a missing required argument before the permission gate rejects it.

        ``web_search`` without a query searches the step's own instruction (trimmed): the node's
        instruction is what the planner meant. Any other missing argument cannot be derived, so
        the step raises :class:`MissingToolArgumentsError`, which self-healing turns into an
        agent-only run when the agent can do the instruction. This is validation only: tiers,
        approvals and unknown-tool denial are untouched.
        """
        lookup = getattr(self.tool_invoker, "required_args", None)
        required = lookup(tool_name) if callable(lookup) else frozenset()
        missing = sorted(
            name
            for name in required
            if args.get(name) is None or (isinstance(args[name], str) and not args[name].strip())
        )
        if not missing:
            return args
        if tool_name == "web_search" and missing == ["query"]:
            query = " ".join(str(subtask.description or "").split())[:MAX_DERIVED_QUERY_CHARS]
            if query:
                data = dict(subtask.input_data or {})
                trail = list(data.get("healing") or [])
                trail.append(
                    healing.entry(
                        len(trail) + 1,
                        "web_search",
                        "arguments repaired",
                        "no query was given; searched the step's instruction",
                    )
                )
                data["healing"] = trail
                subtask.input_data = data
                return {**args, "query": query}
        if _is_repo_read_tool(tool_name) and "repo" in missing:
            req = (subtask.input_data or {}).get("requirements")
            request_text = str(req.get("user_request") or "") if isinstance(req, dict) else ""
            repo = derive_repo_arg(args, str(subtask.description or ""), request_text)
            if repo:
                return {**args, "repo": repo}
        words = ", ".join(f"'{name}'" for name in missing)
        raise MissingToolArgumentsError(
            f"The step needs a value for {words} to use '{tool_name}', but none was given and "
            "it cannot be worked out from the request. Say it in your request (for example the "
            "repository name or the search words) and try again."
        )

    async def _pause_for_input(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        task: Task | None,
        need: NeedsInput,
    ) -> SubtaskStatus:
        """A step needs a detail: show the ask card (real options fetched now) in the task's chat
        and pause the step. Nothing failed and the finished work is kept; the answer resumes
        this step (``TaskSubmissionService.resume_with_input``)."""
        from app.schemas.chat import InputRequestedEvent
        from app.services import ask_user

        data = dict(subtask.input_data or {})
        if not need.options and need.kind == KIND_NOTION_PARENT:
            topic = str((data.get("pipeline") or {}).get("request") or "")
            need.options = (await self._notion_candidates(session, job, topic))[:MAX_OPTIONS]
        questions, mapping = card_questions(need)
        clamped = ask_user.clamp_questions(questions)
        ask_id = f"ask_{uuid4().hex[:16]}"
        pause = NeedsPause(need.kind, ask_id, mapping, need.offer_default)
        data[NEEDS_INPUT_KEY] = pause.as_dict()
        subtask.input_data = data
        subtask.status = SubtaskStatus.AWAITING_APPROVAL
        self._paused_for_input.add(job.subtask_id)
        if task is not None:
            # ``tasks.status`` is CHECK-constrained (migration 0002) and has no "awaiting_input"
            # value: writing one raised an IntegrityError and wedged the task. A paused step that
            # waits for the user is "awaiting_approval"; the subtask's ``needs_input`` marker and
            # the card in the chat tell the two apart.
            task.status = "awaiting_approval"
            task.plan_status = PlanStatus.PAUSED
            if clamped:
                card = InputRequestedEvent(ask_id=ask_id, questions=clamped, source="task")
                stored = ask_user.stored_request(card, "task-input", None)
                stored["task_input"] = {
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "kind": need.kind,
                }
                await input_cards.post_card(
                    session,
                    workspace_id=job.workspace_id,
                    user_id=task.user_id,
                    chat_session_id=task.session_id,
                    task_id=job.task_id,
                    ask_id=ask_id,
                    question=need.question,
                    stored=stored,
                )
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": "needs_input",
                "reason": need.question,
            },
        )
        await session.flush()
        return SubtaskStatus.AWAITING_APPROVAL

    async def _notion_candidates(
        self, session: AsyncSession, job: SubtaskJob, topic: str
    ) -> list[Candidate]:
        """Pages the integration can see right now (read-only ``notion_search``), the page that
        fits the topic first when the decision model is sure. Never raises."""
        invoker = self.tool_invoker
        if invoker is None:
            return []
        try:
            with tool_call_scope(job.workspace_id, job.task_id, job.user_id):
                result = await asyncio.wait_for(
                    invoker.invoke(
                        "notion_search",
                        {"query": "", "scope": "workspace", "limit": 10},
                        session=session,
                        workspace_id=job.workspace_id,
                    ),
                    timeout=10,
                )
            found = candidates_from_search(result.clean_text)
            order = await rank_notion_pages(topic, [c.label for c in found])
            rank = {title: index for index, title in enumerate(order)}
            return sorted(found, key=lambda c: rank.get(c.label, len(rank)))
        except Exception as error:  # noqa: BLE001 - the card then takes a pasted link
            logger.warning("needs_input_options_failed error=%s", type(error).__name__)
            return []

    async def _with_title_parent(
        self, session: AsyncSession, job: SubtaskJob, subtask: Subtask
    ) -> dict[str, Any]:
        """The step's input with the page the user ALREADY chose by title (gate card answer)
        resolved to its id: only an exact, case-insensitive title match that is unique counts.
        No match, several equal titles or no search means the step asks (with real options)."""
        data = dict(subtask.input_data or {})
        hints = dict(data.get("notion_book") or {})
        title = " ".join(str(hints.get("parent_title") or "").split())
        if str(hints.get("parent_page_id") or "").strip() or not title:
            return data
        invoker = self.tool_invoker
        if invoker is None:
            return data
        try:
            with tool_call_scope(job.workspace_id, job.task_id, job.user_id):
                result = await asyncio.wait_for(
                    invoker.invoke(
                        "notion_search",
                        {"query": title[:100], "scope": "workspace", "limit": 20},
                        session=session,
                        workspace_id=job.workspace_id,
                    ),
                    timeout=10,
                )
            found = candidates_from_search(result.clean_text, limit=50)
        except Exception as error:  # noqa: BLE001 - the step asks instead
            logger.warning("notion_parent_title_lookup_failed error=%s", type(error).__name__)
            return data
        exact = [c for c in found if c.label.casefold() == title.casefold()]
        if len({c.value for c in exact}) != 1:
            return data
        hints.update(parent_page_id=exact[0].value, parent_label=exact[0].label)
        data["notion_book"] = hints
        return data

    @staticmethod
    async def _with_default_parent(
        session: AsyncSession, job: SubtaskJob, subtask: Subtask
    ) -> dict[str, Any]:
        """The step's input with the user's CONFIRMED default Notion parent filled in when the
        step has none. A model guess is never used; no setting means the step will ask."""
        data = dict(subtask.input_data or {})
        hints = dict(data.get("notion_book") or {})
        if str(hints.get("parent_page_id") or "").strip():
            return data
        try:
            from app.db.repositories.user_settings import _READ

            stored = (
                await session.execute(
                    _READ,
                    {"ws": job.workspace_id, "user": job.user_id, "key": DEFAULT_PARENT_KEY},
                )
            ).scalar_one_or_none()
            default = parse_default(stored if isinstance(stored, str) else None)
        except Exception as error:  # noqa: BLE001 - no default means the step asks
            logger.debug("default_parent_read_failed error=%s", type(error).__name__)
            return data
        if default is None:
            return data
        hints.update(parent_page_id=default[0], parent_label=default[1], used_default=True)
        data["notion_book"] = hints
        return data

    async def _prepare_notion_book(
        self, session: AsyncSession, job: SubtaskJob, subtask: Subtask
    ) -> dict[str, Any]:
        """The approval content of a learning-book Notion page (see ``app.mcp.notion_book``)."""
        from app.mcp.notion_book import prepare_book_write  # local: keeps the worker import light

        subtask.input_data = await SubtaskWorker._with_title_parent(self, session, job, subtask)
        subtask.input_data = await SubtaskWorker._with_default_parent(session, job, subtask)
        rows = (
            (
                await session.execute(
                    select(FileArtifact)
                    .where(
                        FileArtifact.workspace_id == job.workspace_id,
                        FileArtifact.task_id == job.task_id,
                        FileArtifact.file_type == "md",
                    )
                    .order_by(FileArtifact.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        root = getattr(self.fleet_support, "artifacts_dir", None) or "storage/artifacts"
        return await prepare_book_write(
            artifacts_root=Path(root),
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            input_data=dict(subtask.input_data or {}),
            artifact_rows=list(rows),
        )

    async def _tool_with_step(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        tool_name: str,
        approved: bool,
        timeout: float,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """``_run_tool_step`` wrapped in a nested progress step (running, then done or failed)."""
        kind, label = tool_step(tool_name)
        step_id = f"tool-{uuid4().hex}"
        scope: dict[str, Any] = dict(
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            subtask_id=job.subtask_id,
            step_id=step_id,
            kind=kind,
            label=label,
            parent_id=subtask_step_id(job.subtask_id),
        )
        await emit_task_step(session, state="running", **scope)
        try:
            output = await self._run_tool_step(session, job, subtask, tool_name, approved, timeout)
        except Exception:
            await emit_task_step(session, state="failed", **scope)
            raise
        failed = isinstance(output, dict) and str(output.get("status", "")).lower() in (
            "error",
            "failed",
        )
        await emit_task_step(session, state="failed" if failed else "done", **scope)
        return output

    async def _run_tool_step(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        tool_name: str,
        approved: bool,
        timeout: float,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Run one explicit tool step through the MCP gateway (permission gate inside)."""
        # Imported here: the gateway module imports the DB models and transports.
        from app.mcp.gateway import ToolUnavailableError

        assert self.tool_invoker is not None
        args = dict((subtask.input_data or {}).get("tool_args") or {})
        args = self._repair_tool_args(subtask, tool_name, args)
        if _is_repo_read_tool(tool_name) and args.get("repo") is not None:
            # A repo the planner wrote badly (a bare name, a pasted sentence) is re-derived from
            # the user's request; a good one is only normalised.
            req = (subtask.input_data or {}).get("requirements")
            request_text = str(req.get("user_request") or "") if isinstance(req, dict) else ""
            fixed = derive_repo_arg(args, str(subtask.description or ""), request_text)
            if fixed:
                args = {**args, "repo": fixed}
        started = time.monotonic()
        has_tool = getattr(self.tool_invoker, "has_tool", None)
        if callable(has_tool) and not has_tool(tool_name):
            loader = getattr(self.tool_invoker, "load_workspace_servers", None)
            if callable(loader):
                await loader(session, job.workspace_id)
            if not has_tool(tool_name):
                raise ToolNotProvidedError(
                    f"No tool server provides '{tool_name}' in this deployment."
                )
        try:
            with tool_call_scope(job.workspace_id, job.task_id, job.user_id):
                result = await asyncio.wait_for(
                    self.tool_invoker.invoke(
                        tool_name,
                        args,
                        has_valid_approval_token=approved,
                        session=session,
                        workspace_id=job.workspace_id,
                    ),
                    timeout=timeout,
                )
        except PermissionDeniedError as err:
            await _audit(
                session,
                job,
                action=None,
                status="denied",
                server_name=self._server_name_for(tool_name) or "maya",
                tool_name=tool_name,
                duration_ms=int((time.monotonic() - started) * 1000),
                output={"error": "permission_denied"},
            )
            raise NonRetryableSubtaskError(f"Tool '{tool_name}' denied: {err}") from err
        except ToolUnavailableError as err:
            if str(err).startswith("No tool server provides"):
                raise ToolNotProvidedError(str(err)) from err
            raise NonRetryableSubtaskError(str(err)) from err
        output_text = result.clean_text
        citations = parse_sources(output_text) if tool_name == "web_search" else []
        return {
            "result": {
                "tool": tool_name,
                "output": output_text,
                "untrusted_content": True,
                "has_injection_suspicions": result.has_injection_suspicions,
                **({"citations": citations} if citations else {}),
            },
            "proof": {
                "kind": "mcp_tool_call",
                "status": "unverified",
                "tool": tool_name,
                "approved": approved,
                "output_sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            },
        }

    async def _expire_stale_approval(
        self, session: AsyncSession, job: SubtaskJob, subtask: Subtask, payload_hash: str
    ) -> None:
        """Mark this step's approved-but-unclaimed request expired (its token ran out).

        The next request needs its own idempotency key (one request per key and workspace), and
        the key is part of the payload hash, so the new request is bound afresh. The dead token
        is dropped from the step.
        """
        try:
            conn = await session.connection()
            await conn.execute(
                text(
                    "UPDATE approval_requests SET status = 'expired' "
                    "WHERE workspace_id = :workspace_id AND subtask_id = :subtask_id "
                    "  AND status = 'approved' AND payload_hash = :payload_hash"
                ),
                {
                    "workspace_id": job.workspace_id,
                    "subtask_id": job.subtask_id,
                    "payload_hash": payload_hash,
                },
            )
            data = dict(subtask.input_data or {})
            attempt = int(data.get("approval_attempt") or 0) + 1
            data.pop("approval_token", None)
            subtask.input_data = {
                **data,
                "approval_attempt": attempt,
                "idempotency_key": f"{job.subtask_id}:{attempt}",
            }
            logger.warning(
                "Approval token expired before subtask %s ran; asking for a fresh approval",
                job.subtask_id,
            )
        except Exception:
            logger.warning(
                "Could not expire the stale approval of %s", job.subtask_id, exc_info=True
            )

    async def _record_failure(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        subtask: Subtask,
        *,
        reason: str,
        error_code: str,
        claimed_approval_id: UUID | None,
        worker_id: str,
        retryable: bool,
        deferred_jobs: list[SubtaskJob] | None,
        started_at: float | None = None,
        rate_limited: bool = False,
    ) -> SubtaskStatus:
        """Persist a handler failure: bounded retry with backoff, else fail subtask and task.

        A subtask is never left ``in_progress``: it goes back to ``pending`` (and a delayed job
        is queued) or to ``failed`` with a real ``error_message``.
        """
        if claimed_approval_id is not None:
            try:
                conn = await session.connection()
                await fail_execution_atomically(
                    conn,
                    request_id=claimed_approval_id,
                    workspace_id=job.workspace_id,
                    worker_id=worker_id,
                    reason=reason,
                )
            except Exception as rec_err:
                logger.warning("Failed to record failed execution: %s", rec_err)

        subtask.retry_count += 1
        await self._sync_task_spend(session, job)
        await _audit_tool(
            session,
            job,
            subtask,
            self.permission_registry,
            status="timeout" if error_code == "ExecutionTimeoutError" else "failed",
            duration_ms=(
                int((time.monotonic() - started_at) * 1000) if started_at is not None else None
            ),
            reason=reason,
            server_name=self._server_name_for(str((subtask.input_data or {}).get("tool") or "")),
        )
        if retryable and subtask.retry_count < MAX_SUBTASK_RETRIES:
            subtask.status = SubtaskStatus.PENDING
            subtask.error_message = f"Attempt {subtask.retry_count} failed: {reason}"
            if rate_limited:
                delay = min(
                    RATE_LIMIT_BACKOFF_BASE_SECONDS * subtask.retry_count,
                    RATE_LIMIT_BACKOFF_MAX_SECONDS,
                )
            else:
                delay = min(
                    RETRY_BACKOFF_BASE_SECONDS**subtask.retry_count, RETRY_BACKOFF_MAX_SECONDS
                )
            retry_job = SubtaskJob(
                workspace_id=job.workspace_id,
                user_id=job.user_id,
                task_id=job.task_id,
                subtask_id=job.subtask_id,
                assigned_agent=job.assigned_agent,
                timeout_seconds=job.timeout_seconds,
                retry_delay_seconds=delay,
            )
            if deferred_jobs is not None:
                deferred_jobs.append(retry_job)
            else:
                await self.queue.enqueue(retry_job)
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "status": SubtaskStatus.PENDING.value,
                    "retry_count": subtask.retry_count,
                    "error": reason,
                },
            )
            await session.flush()
            return subtask.status

        subtask.status = SubtaskStatus.FAILED
        subtask.error_message = reason
        if claimed_approval_id is None and not self._is_critical(subtask):
            return await self._finish_tolerated_failure(
                session, job, subtask, reason, deferred_jobs
            )
        task_stmt = select(Task).where(
            Task.id == job.task_id,
            Task.workspace_id == job.workspace_id,
        )
        t_res = await session.execute(task_stmt)
        task = t_res.scalars().first()
        if task and task.status != "cancelled":
            self._mark_task_failed(task, subtask, reason)
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.FAILED.value,
                "error": reason,
            },
        )
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.TASK_FAILED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "error": error_code,
                "message": reason,
            },
        )
        await _audit(session, job, action="task.failed", status="failed", output={"error": reason})
        await session.flush()
        return subtask.status
