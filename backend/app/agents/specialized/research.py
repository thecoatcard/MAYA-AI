"""Research Specialist Agent: web search as ONE Gemini Google Search grounded call.

The agent (kept under the role name ``research`` for the fleet and the database constraint) makes a
single ``generate_text(enable_google_search=True)`` call on the ``search`` alias (then the search
fallback alias, see ``app.agents.model_policy.search_call_kwargs``). The model's answer is cleaned
against Gemini's own grounding metadata: a marker or URL that is not in the metadata is stripped,
and the numbered ``Sources`` list is appended by code, never written by the model.

When the search alias and its fallback are exhausted (quota) the step is ``degraded``, never
failed: it completes with an explicit result, no citations and ``sources_unavailable``, so the
task goes on and says honestly that it was written without web sources.

Which backend a research step uses is ``MAYA_RESEARCH_BACKEND``: ``native`` (default: MAYA's own
search + page reading, no Gemini grounding quota involved), ``grounding`` (Gemini first) or ``auto``
(native, then grounding only when native found nothing). Quota exhaustion of grounding starts a
30-minute cooldown shared by every caller (``app.agents.grounding_cooldown``); during it grounding
is skipped and the native stack answers at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.agents import grounding_cooldown
from app.agents.base import BaseSpecialistAgent
from app.agents.model_policy import alternate_search_call_kwargs, search_call_kwargs
from app.agents.prompts import research as prompts
from app.agents.requirements import requirements_block
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.agents.unsourced import NO_SOURCES_TEXT
from app.core.provider_errors import failure_cause, provider_error_info
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.google_search import (
    GoogleSearchConnector,
    GoogleSearchError,
    GoogleSearchResult,
)
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.mcp.sanitizer import sanitize_tool_output
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)

SEARCH_ENGINE = "google_search"
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_PLAN_TIMEOUT_SECONDS = 90.0
DEFAULT_SEARCH_TIMEOUT_SECONDS = 120.0
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 180.0
_MAX_PRIOR_CHARS = 4000
_MAX_FINDING_CHARS = 12000

_URL = re.compile(r"https?://[^\s<>\")\]]+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_MARKER = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_TRAILING = ".,;:!?"


def _normalise_url(url: str) -> str:
    return url.strip().rstrip(_TRAILING).rstrip("/")


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """Grounding sources without repeated URLs, in first-seen order."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for src in sources:
        key = _normalise_url(src["url"])
        if key not in seen:
            seen.add(key)
            out.append(src)
    return out


def render_citations(
    report: str, sources: list[dict[str, str]]
) -> tuple[str, list[dict[str, str]], int]:
    """Keep only citations that exist in ``sources``; renumber them in order of first use.

    ``sources`` is the 1-based list of grounding sources offered to the model. Returns the cleaned
    report text, the cited sources (renumbered 1..n in the same order) and how many unverifiable
    markers or URLs were removed. Any URL in the text that is not a grounding URL is dropped
    (markdown links keep their label), so a fabricated source can never reach the result.
    """
    allowed = {_normalise_url(s["url"]) for s in sources}
    removed = 0

    def _link(match: re.Match[str]) -> str:
        nonlocal removed
        if _normalise_url(match.group(2)) in allowed:
            return match.group(0)
        removed += 1
        return match.group(1)

    def _bare(match: re.Match[str]) -> str:
        nonlocal removed
        raw = match.group(0)
        if _normalise_url(raw) in allowed:
            return raw
        removed += 1
        return ""

    text = _MD_LINK.sub(_link, report)
    text = _URL.sub(_bare, text)

    order: dict[int, int] = {}

    def _marker(match: re.Match[str]) -> str:
        nonlocal removed
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        kept: list[int] = []
        for number in numbers:
            if 1 <= number <= len(sources):
                kept.append(order.setdefault(number, len(order) + 1))
            else:
                removed += 1
        return "".join(f"[{n}]" for n in kept)

    text = _MARKER.sub(_marker, text)
    cited = [sources[original - 1] for original in sorted(order, key=order.__getitem__)]
    return text, cited, removed


class ResearchAgent(BaseSpecialistAgent):
    """Specialist agent for web discovery, source verification, and cited reports."""

    role = SpecialistAgentRole.RESEARCH
    permission_ceiling = ToolTier.READ
    assigned_tools = frozenset({"web_search", "google_search", "fetch_web_page", "file_read"})

    def execution_timeout_for(self, ctx: dict[str, Any]) -> float | None:
        """Plan + search + synthesis run one after the other, each with its own cap (390 s).
        A deep-research step (ADR-0032) has a longer budget: the run's time cap plus grace."""
        if self.deep_research is not None and isinstance(ctx.get("deep_research"), dict):
            return float(self.deep_research.execution_timeout())
        return (
            self.plan_timeout_seconds + self.search_timeout_seconds + self.synthesis_timeout_seconds
        ) + 30.0

    def __init__(
        self,
        google_search_connector: GoogleSearchConnector | None = None,
        model_gateway: Any | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        plan_timeout_seconds: float = DEFAULT_PLAN_TIMEOUT_SECONDS,
        search_timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
        synthesis_timeout_seconds: float = DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
        today: Callable[[], date] | None = None,
        native_search: Any | None = None,
        deep_research: Any | None = None,
        research_backend: str = "native",
        cooldown: grounding_cooldown.GroundingCooldown | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        # native | grounding | auto (MAYA_RESEARCH_BACKEND); only meaningful with ``native_search``.
        self.research_backend = research_backend
        self.cooldown = cooldown or grounding_cooldown.shared
        self.google_search_connector = google_search_connector
        # The native search stack (app.search.report.NativeResearch): tried first for a research
        # step; Gemini grounding below stays the last fallback.
        self.native_search = native_search
        # app.research.runtime.DeepResearchRuntime: runs a step that carries ``deep_research``.
        self.deep_research = deep_research
        self.model_gateway = model_gateway
        self.max_concurrency = max(1, max_concurrency)
        self.plan_timeout_seconds = plan_timeout_seconds
        self.search_timeout_seconds = search_timeout_seconds
        self.synthesis_timeout_seconds = synthesis_timeout_seconds
        self._today = today or (lambda: datetime.now(UTC).date())

    # ------------------------------------------------------------------ entry point

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Search the goal with ONE grounded call and return a cited answer."""
        ctx = context or {}
        if isinstance(ctx.get("deep_research"), dict):
            if self.deep_research is None:
                # Never degrade to a one-source grounded search labelled "Deep research".
                return SpecialistAgentResult(
                    agent_role=SpecialistAgentRole.RESEARCH,
                    status=AgentExecutionStatus.FAILED,
                    summary="Deep research is not available right now: nothing was searched.",
                    verification_proof={
                        "search_engine": "deep_research",
                        "error": "deep_research_unavailable",
                    },
                )
            result: SpecialistAgentResult = await self.deep_research.run(job, ctx)
            return result
        goal = str(
            ctx.get("query")
            or ctx.get("instruction")
            or ctx.get("description")
            or ctx.get("user_instruction")
            or ""
        ).strip()
        if not goal:
            goal = f"Technical research for {job.assigned_agent} subtask"
        logger.info(
            "research_agent_executing query=%s task_id=%s subtask_id=%s",
            goal[:60],
            job.task_id,
            job.subtask_id,
        )
        if self.model_gateway is not None:
            return await self._run_single_call(job, goal, ctx)
        return await self._run_single_query(goal)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _degraded(*, search_engine: str = SEARCH_ENGINE, **extra: Any) -> SpecialistAgentResult:
        """Search was unavailable (quota) after the search alias and its fallback: not a failure.

        The step completes (status PARTIAL without an approval request, which the worker stores as
        a completed step) with an explicit result, no citations and the flags ``degraded`` and
        ``sources_unavailable`` in its proof; later document steps read the flag.
        """
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.PARTIAL,
            summary=NO_SOURCES_TEXT,
            verification_proof={
                "search_engine": search_engine,
                "degraded": True,
                "sources_unavailable": True,
                "error": "search_unavailable",
                "citations_count": 0,
                "sources": [],
                **extra,
            },
            citations=[],
        )

    @staticmethod
    def _failed(error: str, **extra: Any) -> SpecialistAgentResult:
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.FAILED,
            summary="Research failed: no search backend returned results.",
            verification_proof={"search_engine": SEARCH_ENGINE, "error": error, **extra},
        )

    async def _run_single_query(self, goal: str) -> SpecialistAgentResult:
        """No model gateway: one grounded query through the connector."""
        if self.google_search_connector is None:
            return self._failed("no_results")
        try:
            res: GoogleSearchResult = await self.google_search_connector.search(query=goal)
        except GoogleSearchError as err:
            logger.warning("google_search_failed reason=%s status=%s", err.reason, err.status)
            if err.search_unavailable:
                return self._degraded()
            return self._failed("no_results")
        text = res.sanitized_result.clean_text
        if not text.strip():
            return self._failed("no_results")
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.SUCCESS,
            summary=text,
            verification_proof={
                "search_engine": SEARCH_ENGINE,
                "citations_count": len(res.citations),
                "sources": [c.get("url", "") for c in res.citations if c.get("url")],
                "has_injection_suspicions": res.sanitized_result.has_injection_suspicions,
            },
            citations=res.citations,
        )

    async def _run_single_call(
        self, job: SubtaskJob, goal: str, ctx: dict[str, Any]
    ) -> SpecialistAgentResult:
        return await self.grounded_answer(
            goal, workspace_id=job.workspace_id, task_id=job.task_id, ctx=ctx
        )

    async def grounded_answer(
        self,
        goal: str,
        *,
        workspace_id: UUID | None,
        task_id: UUID | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """The research step's search, by ``research_backend``:

        * ``native`` (default): MAYA's own search + page reading only. Gemini grounding is never
          called; when native finds nothing the step is ``degraded`` (honest, no invented source).
        * ``auto``: native first; grounding only when native had no result and its quota bucket is
          not cooling down.
        * ``grounding``: grounding first (unless its bucket is cooling down), native as fallback.

        Without a native stack (no providers) grounding is the only backend there is."""
        if self.native_search is None:
            return await self.gemini_grounded_answer(
                goal, workspace_id=workspace_id, task_id=task_id, ctx=ctx
            )
        backend = self.research_backend
        if backend == "grounding" and not self.cooldown.active():
            grounded = await self.gemini_grounded_answer(
                goal, workspace_id=workspace_id, task_id=task_id, ctx=ctx
            )
            if grounded.status is AgentExecutionStatus.SUCCESS:
                return grounded
        native: SpecialistAgentResult | None = await self.native_search.research(
            goal, workspace_id=workspace_id, task_id=task_id, ctx=ctx or {}
        )
        if native is not None:
            return native
        if backend == "auto" and not self.cooldown.active():
            return await self.gemini_grounded_answer(
                goal, workspace_id=workspace_id, task_id=task_id, ctx=ctx
            )
        logger.warning("research_native_no_result backend=%s", backend)
        return self._degraded(
            search_engine="native_search",
            as_of=self._today().isoformat(),
            cause="no_results",
        )

    async def gemini_grounded_answer(
        self,
        goal: str,
        *,
        workspace_id: UUID | None,
        task_id: UUID | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """The one grounded search call and its cited report. Shared by the planner step and the
        chat ``web_search`` tool. Quota ends as ``degraded``; no source is ever invented."""
        ctx = ctx or {}
        today = self._today().isoformat()
        if self.cooldown.active():
            # Quota was exhausted a moment ago: no call, no key failover; callers use native.
            logger.info("research_grounding_cooldown remaining=%d", int(self.cooldown.remaining()))
            return self._degraded(as_of=today, cause="rate_limited")
        prior = ctx.get("dependency_outputs")
        prior_text = json.dumps(prior, default=str)[:_MAX_PRIOR_CHARS] if prior else ""
        requirements = ctx.get("requirements")
        style = requirements.get("style") if isinstance(requirements, dict) else None
        language = str((style or {}).get("language") or "the language of the task")
        user = prompts.SEARCH_USER_PROMPT.format(
            goal=goal,
            requirements=requirements_block(ctx),
            prior_outputs=prior_text or "(none)",
        )
        last_error: BaseException | None = None
        resp: Any = None
        # Research is ONE grounded call. When it errors or returns nothing it is retried ONCE on
        # a different model alias (the gateway has already failed over keys and the search
        # fallback alias inside each call); then the step is given up honestly.
        for attempt, call_kwargs in enumerate(
            (search_call_kwargs("research"), alternate_search_call_kwargs("research"))
        ):
            try:
                resp = await asyncio.wait_for(
                    self.model_gateway.generate_text(  # type: ignore[union-attr]
                        **call_kwargs,
                        contents=[user],
                        system_instruction=prompts.SEARCH_SYSTEM_PROMPT.format(
                            today=today, language=language
                        ),
                        temperature=0.2,
                        enable_google_search=True,
                        workspace_id=workspace_id,
                        task_id=task_id,
                    ),
                    timeout=self.search_timeout_seconds,
                )
            except TimeoutError as err:
                last_error = err
                break
            except Exception as err:
                last_error = err
                error_class, status = provider_error_info(err)
                # Class and HTTP status only: never the prompt, the key or provider text.
                logger.warning(
                    "research_search_failed attempt=%d alias=%s error_class=%s status=%s",
                    attempt + 1,
                    call_kwargs["alias"],
                    error_class,
                    status if status is not None else "none",
                )
                if failure_cause(err) == "rate_limited":
                    # Quota: the gateway already tried every key. Do not try another alias (it
                    # shares the quota) and keep the whole bucket quiet for 30 minutes.
                    self.cooldown.trip()
                    break
                continue
            last_error = None
            if (resp.text or "").strip():
                break
            logger.info("research_search_empty attempt=%d", attempt + 1)
        if last_error is not None:
            exhausted = isinstance(last_error, GatewayFallbackExhaustedError)
            cause = failure_cause(last_error)
            if cause == "rate_limited" or (exhausted and cause == "unknown"):
                logger.warning("research_search_unavailable cause=%s", cause)
                return self._degraded(as_of=today, cause="rate_limited")
            error_class, status = provider_error_info(last_error)
            return self._failed(
                "timeout" if isinstance(last_error, TimeoutError) else "error",
                error_class=error_class,
                http_status=status,
                cause=cause,
            )
        text = (resp.text or "").strip() if resp is not None else ""
        sources = _dedupe_sources(
            [
                {"title": c.get("title") or c["url"], "url": c["url"], "snippet": ""}
                for c in (getattr(resp, "citations", None) or [])
                if c.get("url")
            ]
        )
        if not text:
            return self._failed("empty", as_of=today, cause="no_sources")
        if not sources:
            return self._failed("no_grounding", as_of=today, cause="no_sources")
        sanitized = sanitize_tool_output(text[:_MAX_FINDING_CHARS], tool_name="google_search")
        report, cited, removed = render_citations(sanitized.clean_text, sources)
        if not cited:
            cited = list(sources)
            report += (
                "\n\n_No claim was individually attributed; all retrieved sources are listed._"
            )
        report += "\n\n## Sources\n" + "\n".join(
            f"{i}. {s['title']} - {s['url']}" for i, s in enumerate(cited, start=1)
        )
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=report,
            verification_proof={
                "search_engine": SEARCH_ENGINE,
                "citations_count": len(cited),
                "sources": [c["url"] for c in cited],
                "stripped_unverified_citations": removed,
                "has_injection_suspicions": sanitized.has_injection_suspicions,
                "as_of": today,
            },
            citations=cited,
        )
