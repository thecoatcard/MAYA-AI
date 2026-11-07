"""Integration tests for all 5 Specialist AI Agents (Phase 09)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agents.schemas import AgentExecutionStatus, SpecialistAgentRole
from app.agents.specialized.coding import CodingAgent
from app.agents.specialized.document import DocumentAgent
from app.agents.specialized.email import EmailAgent
from app.agents.specialized.pm import PMAgent
from app.agents.specialized.research import ResearchAgent
from app.mcp.connectors.brave import BraveSearchResult
from app.mcp.sanitizer import SanitizationResult
from app.sandbox.runner import SandboxRunner
from app.tasks.queue import SubtaskJob

# ============================================================================
# 1. ResearchAgent Tests
# ============================================================================


@pytest.mark.asyncio
async def test_research_agent_workflow() -> None:
    brave_connector = MagicMock()
    brave_connector.search = AsyncMock(
        return_value=BraveSearchResult(
            citations=[
                {
                    "title": "PostgreSQL 17 Release Notes",
                    "url": "https://postgresql.org/docs/17",
                    "snippet": "New JSON_TABLE features and performance optimizations.",
                }
            ],
            sanitized_result=SanitizationResult(
                clean_text="PostgreSQL 17 Release Notes",
                wrapped_output=(
                    "<<<UNTRUSTED_EXTERNAL_INPUT>>>PostgreSQL 17 Release Notes"
                    "<<<END_UNTRUSTED_EXTERNAL_INPUT>>>"
                ),
                has_injection_suspicions=False,
                suspicions=(),
            ),
        )
    )

    agent = ResearchAgent(brave_connector=brave_connector)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )

    result = await agent.execute_subtask(
        job=job,
        context={"query": "PostgreSQL 17 features"},
    )

    assert result.agent_role == SpecialistAgentRole.RESEARCH
    assert result.status == AgentExecutionStatus.SUCCESS
    assert len(result.citations) == 1
    assert result.citations[0]["url"] == "https://postgresql.org/docs/17"
    assert result.verification_proof["citations_count"] == 1


# ============================================================================
# 2. CodingAgent Tests (Workflow 3: Bug fix -> Sandbox verification -> PR)
# ============================================================================


@pytest.mark.asyncio
async def test_coding_agent_workflow_3(tmp_path: Path) -> None:
    sandbox = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "coding_ws"
    workspace.mkdir()

    # Step 1: Initial buggy code
    code_file = workspace / "math_lib.py"
    code_file.write_text("def add(a, b):\n    return a - b  # Bug!\n", encoding="utf-8")

    test_file = workspace / "test_math.py"
    test_file.write_text(
        "import math_lib\n\ndef test_add():\n    assert math_lib.add(2, 2) == 4\n",
        encoding="utf-8",
    )

    github_connector = MagicMock()
    github_connector.create_pull_request = AsyncMock(
        return_value={
            "number": 42,
            "html_url": "https://github.com/maya-ai/maya/pull/42",
        }
    )

    agent = CodingAgent(sandbox_runner=sandbox, github_connector=github_connector)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="coding",
    )

    # Execute workflow without approval token: produces fix, tests exit code 0, requests PR approval
    result = await agent.execute_subtask(
        job=job,
        context={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_math.py"],
            "patch_file": "math_lib.py",
            "patch_content": "def add(a, b):\n    return a + b\n",
            "has_valid_approval_token": False,
        },
    )

    assert result.agent_role == SpecialistAgentRole.CODING
    assert result.status == AgentExecutionStatus.PARTIAL
    assert result.requires_human_approval is True
    assert result.verification_proof["initial_test_passed"] is False
    assert result.verification_proof["retest_passed"] is True

    # Now execute with valid approval token -> opens PR!
    result_with_token = await agent.execute_subtask(
        job=job,
        context={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_math.py"],
            "patch_file": "math_lib.py",
            "patch_content": "def add(a, b):\n    return a + b\n",
            "has_valid_approval_token": True,
        },
    )

    assert result_with_token.status == AgentExecutionStatus.SUCCESS
    assert (
        result_with_token.verification_proof["pr_url"] == "https://github.com/maya-ai/maya/pull/42"
    )


# ============================================================================
# 3. DocumentAgent Tests
# ============================================================================


@pytest.mark.asyncio
async def test_document_agent_workflow(tmp_path: Path) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    workspace_id = uuid4()
    job = SubtaskJob(
        workspace_id=workspace_id,
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )

    result = await agent.execute_subtask(
        job=job,
        context={
            "filename": "quarterly_analysis.md",
            "content": "# Q4 Executive Summary\nRevenue target achieved 105%.",
        },
    )

    assert result.agent_role == SpecialistAgentRole.DOCUMENT
    assert result.status == AgentExecutionStatus.SUCCESS
    assert len(result.artifact_paths) == 1
    assert "file_checksum" in result.verification_proof
    assert result.verification_proof["size_bytes"] > 0

    # Verify physical file existence in /artifacts/{workspace_id}
    artifact = tmp_path / str(workspace_id) / "quarterly_analysis.md"
    assert artifact.exists()
    assert "Revenue target achieved" in artifact.read_text(encoding="utf-8")


# ============================================================================
# 4. EmailAgent Tests (Autonomous Draft, Gated Send)
# ============================================================================


@pytest.mark.asyncio
async def test_email_agent_workflow() -> None:
    gmail_service = MagicMock()
    gmail_service.create_draft = AsyncMock(return_value={"id": "draft-777"})
    gmail_service.send_email = AsyncMock(return_value={"id": "msg-888"})

    agent = EmailAgent(gmail_service=gmail_service)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="email",
    )

    # Step 1: Draft autonomously without approval token
    draft_result = await agent.execute_subtask(
        job=job,
        context={
            "to": "client@acme.com",
            "subject": "Proposal Update",
            "body": "Please find the revised terms attached.",
            "has_valid_approval_token": False,
        },
    )

    assert draft_result.agent_role == SpecialistAgentRole.EMAIL
    assert draft_result.status == AgentExecutionStatus.PARTIAL
    assert draft_result.requires_human_approval is True
    assert draft_result.verification_proof["draft_id"] == "draft-777"
    assert gmail_service.create_draft.call_count == 1
    assert gmail_service.send_email.call_count == 0

    # Step 2: Send with approval token
    send_result = await agent.execute_subtask(
        job=job,
        context={
            "to": "client@acme.com",
            "subject": "Proposal Update",
            "body": "Please find the revised terms attached.",
            "has_valid_approval_token": True,
        },
    )

    assert send_result.status == AgentExecutionStatus.SUCCESS
    assert send_result.verification_proof["message_id"] == "msg-888"
    assert gmail_service.send_email.call_count == 1


# ============================================================================
# 5. PMAgent Tests (Workflow 1: Daily Briefing)
# ============================================================================


@pytest.mark.asyncio
async def test_pm_agent_workflow_1() -> None:
    calendar_service = MagicMock()
    calendar_service.list_events = AsyncMock(
        return_value=[
            {
                "id": "ev-1",
                "summary": "Engineering Sync",
                "start": {"dateTime": "2026-11-07T09:00:00Z"},
            }
        ]
    )

    notion_connector = MagicMock()
    notion_connector.query_database = AsyncMock(
        return_value=[
            {
                "id": "task-1",
                "properties": {"Name": {"title": [{"plain_text": "Phase 08 Integration"}]}},
            }
        ]
    )

    agent = PMAgent(calendar_service=calendar_service, notion_connector=notion_connector)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="pm",
    )

    result = await agent.execute_subtask(
        job=job,
        context={"date": "2026-11-07"},
    )

    assert result.agent_role == SpecialistAgentRole.PM
    assert result.status == AgentExecutionStatus.SUCCESS
    assert "Engineering Sync" in result.summary
    assert "Phase 08 Integration" in result.summary
    assert result.verification_proof["events_reviewed"] == 1
    assert result.verification_proof["tasks_reviewed"] == 1


@pytest.mark.asyncio
async def test_coding_agent_fails_when_retest_fails(tmp_path: Path) -> None:
    """Principle 2 / BUG-01: CodingAgent must fail if retest does not pass."""
    sandbox = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "repo_failing_patch"
    workspace.mkdir()

    (workspace / "test_math.py").write_text(
        "def test_broken():\n    assert 1 == 2\n", encoding="utf-8"
    )

    github_connector = MagicMock()
    agent = CodingAgent(sandbox_runner=sandbox, github_connector=github_connector)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="coding",
    )

    result = await agent.execute_subtask(
        job=job,
        context={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_math.py"],
            "patch_file": "noop.py",
            "patch_content": "# Does not fix test_broken\n",
        },
    )

    assert result.status == AgentExecutionStatus.FAILED
    assert result.requires_human_approval is False
    assert result.verification_proof["retest_passed"] is False


@pytest.mark.asyncio
async def test_coding_agent_blocks_path_traversal(tmp_path: Path) -> None:
    """BUG-02: CodingAgent must reject patch files escaping repository root."""
    sandbox = SandboxRunner(base_workspace_dir=tmp_path)
    workspace = tmp_path / "repo_traversal"
    workspace.mkdir()

    (workspace / "test_dummy.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    agent = CodingAgent(sandbox_runner=sandbox)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="coding",
    )

    result = await agent.execute_subtask(
        job=job,
        context={
            "repo_dir": str(workspace),
            "test_command": ["python", "-m", "pytest", "test_dummy.py"],
            "patch_file": "../../escaped.txt",
            "patch_content": "malicious payload",
        },
    )

    assert result.status == AgentExecutionStatus.FAILED
    assert result.verification_proof["error"] == "path_traversal"


@pytest.mark.asyncio
async def test_email_agent_reuses_existing_draft(tmp_path: Path) -> None:
    """BUG-20: EmailAgent must not create duplicate draft on resumed approval."""
    gmail_service = MagicMock()
    gmail_service.create_draft = AsyncMock(return_value={"id": "draft-new"})
    gmail_service.send_email = AsyncMock(return_value={"id": "msg-123"})

    agent = EmailAgent(gmail_service=gmail_service)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="email",
    )

    result = await agent.execute_subtask(
        job=job,
        context={
            "to": "boss@example.com",
            "subject": "Q3 Report",
            "body": "Here is the report.",
            "draft_id": "draft-existing-123",
            "has_valid_approval_token": True,
        },
    )

    assert result.status == AgentExecutionStatus.SUCCESS
    gmail_service.create_draft.assert_not_called()
    gmail_service.send_email.assert_called_once()


@pytest.mark.asyncio
async def test_pm_agent_handles_notion_outage_gracefully() -> None:
    """BUG-21: PMAgent must report partial failure notice instead of crashing."""
    calendar_service = MagicMock()
    calendar_service.list_events = AsyncMock(return_value=[{"summary": "Standup"}])

    notion_connector = MagicMock()
    notion_connector.query_database = AsyncMock(
        side_effect=RuntimeError("Notion 503 Service Unavailable")
    )

    agent = PMAgent(calendar_service=calendar_service, notion_connector=notion_connector)
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="pm",
    )

    result = await agent.execute_subtask(job=job, context={"date": "2026-11-07"})

    assert result.status == AgentExecutionStatus.SUCCESS
    assert "Standup" in result.summary
    assert "Notion tasks unavailable" in result.summary
