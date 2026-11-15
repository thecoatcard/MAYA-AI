"""Unit tests for SubtaskWorker tool permission gating and tokens (Decisions D4, D5, D8)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import mint_approval_token
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker


@pytest.mark.asyncio
async def test_worker_gates_approval_required_tool_without_token() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.EMAIL,
        description="Send notification email",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={"tool": "gmail_send", "args": {"to": "ops@example.com", "subject": "Alert"}},
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Notify team",
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
        assigned_agent="email",
    )

    status = await worker.execute_job(session=session, job=job)

    assert status == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.status == SubtaskStatus.AWAITING_APPROVAL
    assert task.status == "awaiting_approval"
    assert task.plan_status == PlanStatus.PAUSED


@pytest.mark.asyncio
async def test_worker_denies_unregistered_tool_by_default() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.CODING,
        description="Run unknown script",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={"tool": "dangerous_unknown_tool", "args": {}},
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Execute unknown command",
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

    # Deny by default: an unclassified tool is refused outright (not offered for approval).
    assert status == SubtaskStatus.FAILED
    assert subtask.status == SubtaskStatus.FAILED
    assert "denied by default" in (subtask.error_message or "")
    assert task.status == "failed"


async def _ok_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    assert "approval_token" not in input_data  # the JWT must never reach handlers
    return {"result": {"ok": True}, "proof": {"status": "verified"}}


@pytest.mark.asyncio
async def test_worker_executes_when_valid_token_presented_and_consumed() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()
    req_id = uuid4()
    jwt_key = "a" * 32

    tool_name = "git_push"
    args = {"branch": "main", "remote": "origin"}
    idempotency_key = str(st_id)

    payload_hash = compute_payload_hash(
        tool=tool_name,
        args=args,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=idempotency_key,
    )

    token, _ = mint_approval_token(
        jwt_key,
        user_id=u_id,
        workspace_id=ws_id,
        request_id=req_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
    )

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.CODING,
        description="Push to git repository",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={
            "tool": tool_name,
            "args": args,
            "idempotency_key": idempotency_key,
            "approval_token": token,
        },
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Deploy",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    # Mock connection that simulates atomic CAS returning row (consumed = True)
    mock_conn = MagicMock()
    cas_res = MagicMock()
    cas_res.first.return_value = (req_id,)
    mock_conn.execute = AsyncMock(return_value=cas_res)
    mock_conn.info = {}
    session.connection = AsyncMock(return_value=mock_conn)

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

    worker = SubtaskWorker(jwt_signing_key=jwt_key)
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="coding",
    )

    status = await worker.execute_job(session=session, job=job, agent_handler=_ok_handler)

    # Valid token was verified and consumed: execution completed!
    assert status == SubtaskStatus.COMPLETED
    assert subtask.status == SubtaskStatus.COMPLETED
    assert mock_conn.execute.called


@pytest.mark.asyncio
async def test_worker_rejects_replayed_or_already_consumed_token() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()
    req_id = uuid4()
    jwt_key = "a" * 32

    tool_name = "git_push"
    args = {"branch": "main"}
    idempotency_key = str(st_id)

    payload_hash = compute_payload_hash(
        tool=tool_name,
        args=args,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=idempotency_key,
    )

    token, _ = mint_approval_token(
        jwt_key,
        user_id=u_id,
        workspace_id=ws_id,
        request_id=req_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
    )

    subtask = Subtask(
        id=st_id,
        task_id=t_id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.CODING,
        description="Push to git repository",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={
            "tool": tool_name,
            "args": args,
            "idempotency_key": idempotency_key,
            "approval_token": token,
        },
    )
    task = Task(
        id=t_id,
        workspace_id=ws_id,
        user_id=u_id,
        session_id=str(t_id),
        user_instruction="Deploy",
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()
    session.add = MagicMock()

    # Replay simulation: PostgreSQL CAS returns None (already consumed or expired)
    mock_conn = MagicMock()
    cas_res = MagicMock()
    cas_res.first.return_value = None
    mock_conn.execute = AsyncMock(return_value=cas_res)
    mock_conn.info = {}
    session.connection = AsyncMock(return_value=mock_conn)

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

    worker = SubtaskWorker(jwt_signing_key=jwt_key)
    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="coding",
    )

    status = await worker.execute_job(session=session, job=job)

    # Replay was prevented: worker did not execute and transitioned to awaiting_approval
    assert status == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.status == SubtaskStatus.AWAITING_APPROVAL
    assert task.status == "awaiting_approval"
