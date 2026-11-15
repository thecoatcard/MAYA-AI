"""End-to-End tests for Specialist Agent Workflows (1 and 3) via SubtaskWorker (Phase 09)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentFleetRegistry
from app.agents.specialized.coding import CodingAgent
from app.agents.specialized.pm import PMAgent
from app.db.models import Subtask, SubtaskStatus, Task
from app.sandbox.runner import SandboxRunner
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker
from tests.support.approval import TEST_JWT_KEY, approve_paused_subtask


@pytest.mark.asyncio
async def test_workflow_1_daily_briefing_e2e() -> None:
    # 1. Setup mock services
    calendar_service = MagicMock()
    calendar_service.list_events = AsyncMock(
        return_value=[
            {
                "id": "cal-101",
                "summary": "Leadership Standup",
                "start": {"dateTime": "2026-11-07T09:00:00Z"},
            }
        ]
    )
    notion_connector = MagicMock()
    notion_connector.query_database = AsyncMock(
        return_value=[
            {
                "id": "page-202",
                "properties": {"Name": {"title": [{"plain_text": "Phase 09 Fleet Deployment"}]}},
            }
        ]
    )

    pm_agent = PMAgent(
        calendar_service=calendar_service,
        notion_connector=notion_connector,
        notion_database_id="tasks-db",
    )

    fleet = AgentFleetRegistry()
    fleet.register_agent("pm", pm_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)

    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    # 2. Mock database session with mock task and subtask
    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Synthesize Daily Briefing",
        assigned_agent="pm",
        status=SubtaskStatus.PENDING,
        input_data={"date": "2026-11-07"},
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Daily Ops Plan",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        # If querying for subtask
        if "FROM subtasks" in str(stmt) or "subtasks.id" in str(stmt):
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )

    final_status = await worker.execute_job(session=session, job=job)

    assert final_status == SubtaskStatus.COMPLETED
    assert subtask.status == SubtaskStatus.COMPLETED
    assert "Leadership Standup" in str(subtask.output_result)
    assert "Phase 09 Fleet Deployment" in str(subtask.output_result)
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["events_reviewed"] == 1
    assert subtask.verification_proof["tasks_reviewed"] == 1


@pytest.mark.asyncio
async def test_workflow_3_coding_bugfix_to_pr_e2e(tmp_path: Path) -> None:
    sandbox = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "repo_ws"
    workspace.mkdir()

    # Create failing test & bug
    (workspace / "service.py").write_text("def process():\n    return 'bad'\n", encoding="utf-8")
    (workspace / "test_service.py").write_text(
        "import service\n\ndef test_process():\n    assert service.process() == 'good'\n",
        encoding="utf-8",
    )

    github_connector = MagicMock()
    github_connector.create_pull_request = AsyncMock(
        return_value={"number": 88, "html_url": "https://github.com/maya-ai/maya/pull/88"}
    )

    coding_agent = CodingAgent(
        sandbox_runner=sandbox,
        github_connector=github_connector,
    )

    fleet = AgentFleetRegistry()
    fleet.register_agent("coding", coding_agent)

    worker = SubtaskWorker(fleet_registry=fleet, jwt_signing_key=TEST_JWT_KEY)

    workspace_id = uuid4()
    task_id = uuid4()
    subtask_id = uuid4()

    subtask = Subtask(
        id=subtask_id,
        task_id=task_id,
        workspace_id=workspace_id,
        description="Fix process return value",
        assigned_agent="coding",
        status=SubtaskStatus.PENDING,
        input_data={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_service.py"],
            "patch_file": "service.py",
            "patch_content": "def process():\n    return 'good'\n",
            "has_valid_approval_token": False,
        },
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        title="Fix service bug",
        status="in_progress",
    )

    session = AsyncMock(spec=AsyncSession)

    def execute_side_effect(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        if "FROM subtasks" in str(stmt) or "subtasks.id" in str(stmt):
            mock_result.scalars.return_value.first.return_value = subtask
            mock_result.scalars.return_value.all.return_value = [subtask]
        else:
            mock_result.scalars.return_value.first.return_value = task
            mock_result.scalars.return_value.all.return_value = [task]
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.flush = AsyncMock()

    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=task.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="coding",
    )

    # 1. Run without token: should transition to AWAITING_APPROVAL cleanly (Decision D8)
    status_1 = await worker.execute_job(session=session, job=job)
    assert status_1 == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.status == SubtaskStatus.AWAITING_APPROVAL
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["retest_passed"] is True

    # 2. Run with valid approval token -> opens PR and COMPLETED
    # A real signed approval token is verified and claimed; a bare input flag no longer works.
    approve_paused_subtask(session, subtask, task)
    status_2 = await worker.execute_job(session=session, job=job)
    assert status_2 == SubtaskStatus.COMPLETED
    assert str(subtask.status) == str(SubtaskStatus.COMPLETED)
    assert subtask.verification_proof is not None
    assert subtask.verification_proof["pr_url"] == "https://github.com/maya-ai/maya/pull/88"
