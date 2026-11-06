"""Central LangGraph Orchestrator for multi-agent workflows."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.planner import DAGPlanner, PlannerError, PlanValidationError, validate_dag
from app.agents.state import AgentExecutionState, SubTask
from app.gateway.model_gateway import ModelGateway

logger = logging.getLogger(__name__)

# Roles or actions that require human authorization before execution
APPROVAL_REQUIRED_ROLES = frozenset({"email"})


# ---------------------------------------------------------------------------
# Orchestrator Tools
# ---------------------------------------------------------------------------


async def create_plan_dag(
    instruction: str,
    planner: DAGPlanner | None = None,
    gateway: ModelGateway | None = None,
    workspace_id: UUID | None = None,
    task_id: UUID | None = None,
) -> tuple[str, list[SubTask]]:
    """Tool: Decompose user instruction into intent and validated subtask DAG."""
    p = planner or DAGPlanner(gateway=gateway)
    return await p.plan(
        instruction,
        gateway=gateway,
        workspace_id=workspace_id,
        task_id=task_id,
    )


def dispatch_subtask(subtask: SubTask) -> SubTask:
    """Tool: Transition a pending subtask to in_progress."""
    return subtask.model_copy(update={"status": "in_progress"})


def request_human_approval(
    subtask: SubTask,
    action_description: str | None = None,
    permission_level: str = "APPROVAL_REQUIRED",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tool: Request human approval for a sensitive subtask action.

    Initiates and records the approval request only; NEVER resolves or approves it.
    """
    desc = action_description or f"Execution of subtask '{subtask.id}' ({subtask.description})"
    return {
        "subtask_id": subtask.id,
        "assigned_agent": subtask.assigned_agent,
        "action_description": desc,
        "permission_level": permission_level,
        "payload": payload or dict(subtask.input_data),
        "status": "pending",
    }


def synthesize_final_report(state: AgentExecutionState) -> dict[str, Any]:
    """Tool: Build final synthesis output from completed subtasks."""
    completed = [st for st in state.active_plan if st.status == "completed"]
    failed = [st for st in state.active_plan if st.status == "failed"]

    results_summary: list[dict[str, Any]] = []
    for st in completed:
        results_summary.append(
            {
                "subtask_id": st.id,
                "agent": st.assigned_agent,
                "description": st.description,
                "output": st.output_result or {},
            }
        )

    total = len(state.active_plan)
    all_successful = (
        total > 0 and state.plan_status != "failed" and len(failed) == 0 and len(completed) == total
    )
    if state.plan_status == "failed" and "planning_error" in state.shared_context:
        summary_text = "Planning failed; no valid plan was produced."
    elif total == 0:
        summary_text = "No subtasks were available to execute."
    elif all_successful:
        summary_text = f"Completed {len(completed)}/{total} subtasks successfully."
    else:
        summary_text = f"Execution concluded with {len(failed)} failure(s) out of {total} subtasks."

    return {
        "session_id": state.session_id,
        "workspace_id": state.workspace_id,
        "user_instruction": state.user_instruction,
        "summary": summary_text,
        "all_successful": all_successful,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "total_subtasks": len(state.active_plan),
        "results": results_summary,
        "failed_subtasks": [st.id for st in failed],
    }


def _completed_dependency_ids(state: AgentExecutionState) -> set[str]:
    """Require both the completion record and current subtask status to confirm success."""
    completed_status_ids = {st.id for st in state.active_plan if st.status == "completed"}
    return set(state.completed_task_ids) & completed_status_ids


# ---------------------------------------------------------------------------
# Graph Node Definitions
# ---------------------------------------------------------------------------


class OrchestratorNodes:
    """Encapsulates node execution methods with optional planner and gateway."""

    def __init__(
        self,
        planner: DAGPlanner | None = None,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.planner = planner or DAGPlanner(gateway=gateway)
        self.gateway = gateway

    async def plan_node(self, state: AgentExecutionState) -> dict[str, Any]:
        """Node 'plan': decomposes instruction into DAG subtasks if not already planned."""
        if state.active_plan:
            # Plan already provided (e.g. pre-constructed or continuing)
            try:
                sorted_plan = validate_dag(state.active_plan)
                return {
                    "active_plan": sorted_plan,
                    "plan_status": "executing",
                    "execution_trace": [
                        {"step": "plan", "subtasks": len(sorted_plan), "status": "executing"}
                    ],
                }
            except PlannerError as err:
                return {
                    "plan_status": "failed",
                    "shared_context": {**state.shared_context, "planning_error": str(err)},
                    "execution_trace": [{"step": "plan", "error": str(err), "status": "failed"}],
                }

        try:
            ws_id = UUID(state.workspace_id) if state.workspace_id else None
            intent, subtasks = await create_plan_dag(
                state.user_instruction,
                planner=self.planner,
                gateway=self.gateway,
                workspace_id=ws_id,
            )
            if not subtasks:
                raise PlanValidationError("Plan must contain at least one subtask")
            return {
                "active_plan": subtasks,
                "plan_status": "executing",
                "shared_context": {**state.shared_context, "classified_intent": intent},
                "execution_trace": [
                    {
                        "step": "plan",
                        "intent": intent,
                        "subtask_count": len(subtasks),
                        "status": "executing",
                    }
                ],
            }
        except Exception as err:
            logger.exception("orchestrator_planning_failed")
            return {
                "plan_status": "failed",
                "shared_context": {**state.shared_context, "planning_error": str(err)},
                "execution_trace": [{"step": "plan", "error": str(err), "status": "failed"}],
            }

    async def dispatch_node(self, state: AgentExecutionState) -> dict[str, Any]:
        """Node 'dispatch': inspects dependencies and transitions ready subtasks."""
        completed_ids = _completed_dependency_ids(state)

        updated_plan: list[SubTask] = []
        pending_approval_req: dict[str, Any] | None = None
        new_trace: list[dict[str, Any]] = []
        dispatched_any = False

        for st in state.active_plan:
            if st.status == "pending" and not dispatched_any:
                deps_met = all(dep in completed_ids for dep in st.dependencies)
                if deps_met:
                    # Check if subtask requires human approval
                    requires_approval = st.assigned_agent in APPROVAL_REQUIRED_ROLES or bool(
                        st.input_data.get("requires_approval")
                    )
                    if requires_approval:
                        pending_approval_req = request_human_approval(st)
                        updated_plan.append(st.model_copy(update={"status": "awaiting_approval"}))
                        new_trace.append({"step": "await_approval", "subtask_id": st.id})
                        dispatched_any = True
                        continue

                    # Otherwise dispatch to in_progress
                    updated_plan.append(dispatch_subtask(st))
                    new_trace.append({"step": "dispatch", "subtask_id": st.id})
                    dispatched_any = True
                    continue

            updated_plan.append(st)

        updates: dict[str, Any] = {
            "active_plan": updated_plan,
            "execution_trace": new_trace,
        }
        if pending_approval_req is not None:
            updates["pending_approval"] = pending_approval_req
            updates["plan_status"] = "paused"

        return updates

    async def await_approval_node(self, state: AgentExecutionState) -> dict[str, Any]:
        """Node 'await_approval': pauses workflow awaiting human approval (never resolves)."""
        return {
            "plan_status": "paused",
            "execution_trace": [
                {"step": "await_approval_paused", "pending": state.pending_approval}
            ],
        }

    async def synthesize_node(self, state: AgentExecutionState) -> dict[str, Any]:
        """Node 'synthesize': aggregates all subtask outputs into final synthesized report."""
        report = synthesize_final_report(state)
        new_status = "completed" if report["all_successful"] else "failed"
        return {
            "plan_status": new_status,
            "shared_context": {**state.shared_context, "synthesis": report},
            "execution_trace": [{"step": "synthesize", "status": new_status, "report": report}],
        }


# ---------------------------------------------------------------------------
# Routing Logic
# ---------------------------------------------------------------------------


def route_after_plan(state: AgentExecutionState) -> str:
    """Determine routing destination after planning node."""
    if state.plan_status == "failed":
        return "synthesize"
    if state.pending_approval is not None or state.plan_status == "paused":
        return "await_approval"
    if any(st.status == "awaiting_approval" for st in state.active_plan):
        return "await_approval"
    return "dispatch"


def route_after_dispatch(state: AgentExecutionState) -> str:
    """Determine routing destination after dispatch node."""
    if state.pending_approval is not None or state.plan_status == "paused":
        return "await_approval"
    if any(st.status == "awaiting_approval" for st in state.active_plan):
        return "await_approval"

    pending = [st for st in state.active_plan if st.status == "pending"]
    in_prog = [st for st in state.active_plan if st.status == "in_progress"]
    completed = [st for st in state.active_plan if st.status == "completed"]
    failed = [st for st in state.active_plan if st.status == "failed"]

    # All subtasks finished (either completed or failed)
    if len(completed) + len(failed) == len(state.active_plan) and len(state.active_plan) > 0:
        return "synthesize"

    # Tasks are currently running in workers (Phase 06 decoupled execution)
    if in_prog and not pending:
        return END

    # Additional runnable pending tasks can be dispatched
    completed_ids = _completed_dependency_ids(state)
    runnable_pending = any(all(dep in completed_ids for dep in st.dependencies) for st in pending)
    if runnable_pending:
        return "dispatch"

    if not in_prog and not runnable_pending:
        # Deadlock or plan finished
        return "synthesize"

    return END


# ---------------------------------------------------------------------------
# Graph Compilation
# ---------------------------------------------------------------------------


def create_orchestrator_graph(
    planner: DAGPlanner | None = None,
    gateway: ModelGateway | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    interrupt_before_approval: bool = True,
) -> CompiledStateGraph[AgentExecutionState]:
    """Build and compile the authoritative MAYA AI Orchestrator StateGraph."""
    nodes = OrchestratorNodes(planner=planner, gateway=gateway)

    workflow = StateGraph(AgentExecutionState)

    workflow.add_node("plan", nodes.plan_node)
    workflow.add_node("dispatch", nodes.dispatch_node)
    workflow.add_node("await_approval", nodes.await_approval_node)
    workflow.add_node("synthesize", nodes.synthesize_node)

    workflow.add_edge(START, "plan")
    workflow.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "dispatch": "dispatch",
            "await_approval": "await_approval",
            "synthesize": "synthesize",
        },
    )
    workflow.add_conditional_edges(
        "dispatch",
        route_after_dispatch,
        {
            "dispatch": "dispatch",
            "await_approval": "await_approval",
            "synthesize": "synthesize",
            END: END,
        },
    )
    workflow.add_edge("await_approval", END)
    workflow.add_edge("synthesize", END)

    interrupt_nodes: list[str] = ["await_approval"] if interrupt_before_approval else []

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes or None,
    )
