"""Unit tests for SubtaskWorker and Decision D8 execution rules."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker


@pytest.mark.asyncio
async def test_worker_awaits_approval_exits_immediately_decision_d8() -> None:
    """Decision D8: Workers never sleep or block on human approval; exit immediately."""
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.CODING,
        description="Push to production repository",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={"requires_approval": True, "action": "push_main"},
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Deploy to production",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    async def mock_execute(stmt: Any) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        elif "FROM tasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=mock_execute)

    worker = SubtaskWorker()
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="coding",
    )

    status = await worker.execute_job(session=session, job=job)

    # Worker transitioned subtask to awaiting_approval and exited immediately
    assert status == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.status == SubtaskStatus.AWAITING_APPROVAL
    assert task.status == "awaiting_approval"
    assert task.plan_status == PlanStatus.PAUSED


@pytest.mark.asyncio
async def test_worker_executes_subtask_successfully() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.RESEARCH,
        description="Fetch competitors data",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={"query": "AI agents"},
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Analyze competitors",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    async def mock_execute(stmt: Any) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        elif "FROM tasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=mock_execute)

    async def mock_agent_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": {"competitors": ["Alpha", "Beta"]},
            "proof": {"verified": True, "exit_code": 0},
        }

    worker = SubtaskWorker()
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="research",
    )

    status = await worker.execute_job(
        session=session,
        job=job,
        agent_handler=mock_agent_handler,
    )

    assert status == SubtaskStatus.COMPLETED
    assert subtask.status == SubtaskStatus.COMPLETED
    assert subtask.output_result == {"competitors": ["Alpha", "Beta"]}
    assert subtask.verification_proof == {"verified": True, "exit_code": 0}
    # Task marked completed since all subtasks are done
    assert task.status == "completed"
    assert task.plan_status == PlanStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_times_out_and_fails_after_retries() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.BROWSER,
        description="Scrape slow web page",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={},
        retry_count=2,  # Already retried twice
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Scrape page",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    async def mock_execute(stmt: Any) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM subtasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        elif "FROM tasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=mock_execute)

    async def hanging_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(1.0)
        return {}

    worker = SubtaskWorker(default_timeout=1)
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="browser",
        timeout_seconds=0,  # 0 or small timeout triggers immediate TimeoutError
    )

    status = await worker.execute_job(
        session=session,
        job=job,
        agent_handler=hanging_handler,
    )

    assert status == SubtaskStatus.FAILED
    assert subtask.status == SubtaskStatus.FAILED
    assert subtask.error_message is not None and "timed out" in subtask.error_message
    assert task.status == "failed"
    assert task.plan_status == PlanStatus.FAILED


@pytest.mark.asyncio
async def test_worker_resolves_string_dependency_ids() -> None:
    """Verifies that planner string dependency IDs (e.g. 'step_1') resolve to UUIDs."""
    from app.services.projection_service import resolve_subtask_uuid

    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()

    dep_uuid = resolve_subtask_uuid(t_id, "step_1")
    dep_subtask = Subtask(
        id=dep_uuid,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.RESEARCH,
        description="First step",
        dependencies=[],
        status=SubtaskStatus.COMPLETED,
        input_data={},
    )

    st_id = uuid4()
    # Subtask specifies dependency as string 'step_1'
    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.CODING,
        description="Second step dependent on step_1",
        dependencies=["step_1"],
        status=SubtaskStatus.PENDING,
        input_data={},
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Multi-step task",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    async def mock_execute(stmt: Any) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if f"subtasks.id = '{st_id}'" in stmt_str or "subtasks.id =" in stmt_str:
            mock_result.scalars.return_value.first.return_value = subtask
        elif "FROM subtasks" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [dep_subtask, subtask]
        elif "FROM tasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=mock_execute)

    async def mock_agent_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
        return {"result": "ok", "proof": {"done": True}}

    worker = SubtaskWorker()
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="coding",
    )

    status = await worker.execute_job(
        session=session,
        job=job,
        agent_handler=mock_agent_handler,
    )

    # Dependency satisfied via string resolution; execution succeeded
    assert status == SubtaskStatus.COMPLETED
    assert subtask.status == SubtaskStatus.COMPLETED
