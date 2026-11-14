"""Wiring of deep research into the app: availability, the production searcher, and the run of one
task step (used by the research agent when a step carries ``deep_research`` input)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.agents.schemas import AgentExecutionStatus, SpecialistAgentResult, SpecialistAgentRole
from app.research.fetcher import ResearchFetcher
from app.research.intent import requested_sources
from app.research.limits import ResearchLimits
from app.research.pipeline import DeepResearch
from app.research.progress import NullProgress, ProgressSink, TaskProgress
from app.search.models import SearchHit, SearchRequest
from app.search.names import engine_label

logger = logging.getLogger(__name__)

INPUT_KEY = "deep_research"


def search_ready(web_search: Any) -> bool:
    """True when a real (non-Gemini) search provider is configured on the search service."""
    chain = getattr(web_search, "chain", None)
    if chain is None:
        return False
    try:
        return bool(chain.active_names(without="gemini"))
    except Exception:  # noqa: BLE001
        return False


def unavailable_reason(settings: Any, web_search: Any, gateway: Any) -> str | None:
    """Why deep research cannot start (a plain reason), or ``None``. Fails closed."""
    if not getattr(settings, "deep_research_enabled", False):
        return "deep research is switched off on this server"
    if gateway is None or getattr(gateway, "client", None) is None:
        return "no model is available"
    if not search_ready(web_search):
        return "web search is not available"
    return None


def status(settings: Any, web_search: Any, gateway: Any) -> dict[str, Any]:
    """The ``deep_research`` info for a status route: whether it can run, and its caps."""
    reason = unavailable_reason(settings, web_search, gateway)
    return {
        "enabled": bool(settings.deep_research_enabled),
        "available": reason is None,
        "unavailable_reason": reason,
        **ResearchLimits.from_settings(settings).as_status(),
    }


class ChainSearcher:
    """The search chain as a ``Searcher`` (Gemini grounding is skipped: its links are redirects)."""

    def __init__(self, chain: Any, *, user_key: str, workspace_id: Any) -> None:
        self._chain = chain
        self._user_key = user_key
        self._workspace_id = workspace_id
        self.engines_used: Counter[str] = Counter()  # display name -> searches it answered

    def parallelism(self) -> int:
        """Queries to run at once: fewer while engines are cooling down (the chain also limits the
        provider calls of the whole process)."""
        try:
            return 1 if self._chain.cooling_count() > 0 else 3
        except Exception:  # noqa: BLE001 - a chain without health information: the default
            return 3

    async def __call__(self, query: str) -> tuple[list[SearchHit], bool]:
        outcome = await self._chain.run(
            SearchRequest(query, max_results=10),
            user_key=self._user_key,
            workspace_id=self._workspace_id,
            skip=frozenset({"gemini"}),
        )
        if outcome.hits:
            engine = getattr(outcome, "engine", None) or getattr(outcome, "provider", None)
            self.engines_used[engine_label(engine)] += 1
        return list(outcome.hits), bool(outcome.limited)


class DeepResearchRuntime:
    def __init__(
        self,
        *,
        engine: Any,
        gateway: Any,
        web_search: Any,
        limits: ResearchLimits,
        enabled: bool = True,
        fetcher: ResearchFetcher | None = None,
    ) -> None:
        self._engine = engine
        self._gateway = gateway
        self._web_search = web_search
        self._limits = limits
        self._enabled = enabled
        self._fetcher = fetcher

    def execution_timeout(self) -> float:
        """The step budget: the time cap plus a grace period for the last model call."""
        return float(self._limits.time_cap_seconds) + 120.0

    async def run(self, job: Any, ctx: dict[str, Any]) -> SpecialistAgentResult:
        spec = ctx.get(INPUT_KEY)
        spec = spec if isinstance(spec, dict) else {}
        request = str(spec.get("request") or ctx.get("description") or "").strip()
        if not self._enabled or not search_ready(self._web_search) or not request:
            return _failed("Deep research is not available right now: nothing was searched.")
        progress: ProgressSink = NullProgress()
        if self._engine is not None:
            progress = TaskProgress(
                self._engine,
                workspace_id=job.workspace_id,
                user_id=job.user_id,
                task_id=job.task_id,
                subtask_id=job.subtask_id,
            )
        requested = spec.get("sources")
        requested = int(requested) if isinstance(requested, int) else requested_sources(request)
        pipeline = DeepResearch(
            gateway=self._gateway,
            search=ChainSearcher(
                self._web_search.chain,
                user_key=f"deep:{job.task_id}",
                workspace_id=job.workspace_id,
            ),
            fetcher=self._fetcher or ResearchFetcher(),
            limits=self._limits,
            progress=progress,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
        )
        result = await pipeline.run(request, requested_sources=requested)
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.SUCCESS if result.ok else AgentExecutionStatus.FAILED,
            summary=result.summary,
            verification_proof=result.proof,
            citations=result.citations,
        )


def _failed(message: str) -> SpecialistAgentResult:
    return SpecialistAgentResult(
        agent_role=SpecialistAgentRole.RESEARCH,
        status=AgentExecutionStatus.FAILED,
        summary=message,
        verification_proof={"search_engine": "deep_research", "error": "unavailable"},
    )
