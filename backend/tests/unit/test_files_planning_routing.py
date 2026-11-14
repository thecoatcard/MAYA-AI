"""Planner roles for file steps, task input context, and where finished documents go."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.agents.capabilities import Capabilities
from app.agents.planner import SYSTEM_PLANNING_PROMPT, SubTaskPlanItem, build_subtasks
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.document import DocumentAgent
from app.agents.state import SubTask
from app.mcp.permissions import ToolPermissionRegistry
from app.services.task_submission import _attach_file_context, _attachments_exist
from tests.support.files import OTHER_WORKSPACE, USER, WORKSPACE, make_job

SHEETS = [{"title": "S1", "headers": ["a"], "rows": [["1"]]}]


def _xlsx_context(name: str = "budget.xlsx") -> dict[str, Any]:
    return {"filename": name, "file_type": "xlsx", "sheets": SHEETS}


# ------------------------------------------------------------------------------ planner


def test_file_roles_are_stored_with_the_file_role_and_an_executor_marker() -> None:
    items = [
        SubTaskPlanItem(id="s1", assigned_agent="file_edit", description="edit a.docx"),
        SubTaskPlanItem(id="s2", assigned_agent="project", description="build a site"),
        SubTaskPlanItem(id="s3", assigned_agent="executor", description="outline"),
    ]
    by_id = {n.id: n for n in build_subtasks(items)}
    assert (
        by_id["s1"].assigned_agent == "file" and by_id["s1"].input_data["executor"] == "file_edit"
    )
    assert by_id["s2"].assigned_agent == "file" and by_id["s2"].input_data["executor"] == "project"
    assert by_id["s3"].input_data["executor"] == "text"
    assert by_id["s1"].input_data["critical"] and by_id["s2"].input_data["critical"]


def test_the_planner_prompt_and_catalogue_describe_the_file_steps() -> None:
    assert '"file_edit"' in SYSTEM_PLANNING_PROMPT and '"project"' in SYSTEM_PLANNING_PROMPT
    assert "file_replace_original" in SYSTEM_PLANNING_PROMPT
    catalogue = Capabilities().prompt_block()
    assert "input_files" in catalogue and "MAYA's storage" in catalogue


def test_replace_original_is_an_approval_required_tool_the_planner_may_name() -> None:
    from app.agents.planner import PLANNER_TOOL_NAMES

    assert "file_replace_original" in PLANNER_TOOL_NAMES
    registry = ToolPermissionRegistry()
    assert registry.classify("file_replace_original").name == "APPROVAL_REQUIRED"


# ------------------------------------------------------------------------------ submission


def _node() -> SubTask:
    return SubTask(
        id="s1", assigned_agent="document", description="d", dependencies=[], input_data={}
    )


async def test_input_files_and_output_mode_reach_every_step(file_env: Any) -> None:
    nodes = [_node(), _node()]
    await _attach_file_context(nodes, WORKSPACE, USER, ["abc"])
    for node in nodes:
        assert node.input_data["input_files"] == ["abc"]
        assert node.input_data["output_dir_mode"] == "folder"


async def test_output_mode_is_artifacts_without_a_folder(file_env_no_folder: Any) -> None:
    nodes = [_node()]
    await _attach_file_context(nodes, WORKSPACE, USER, [])
    assert nodes[0].input_data["output_dir_mode"] == "artifacts"
    assert "input_files" not in nodes[0].input_data


async def test_unknown_attachment_ids_are_detected(file_env: Any) -> None:
    services, _folder = file_env
    mine = services.uploads.save(WORKSPACE, USER, "a.txt", b"x").id
    assert await _attachments_exist(WORKSPACE, [mine])
    assert not await _attachments_exist(WORKSPACE, [mine, "nope"])
    assert not await _attachments_exist(OTHER_WORKSPACE, [mine])


async def test_task_submission_refuses_an_unknown_attachment(file_env: Any) -> None:
    from app.services.task_submission import TaskRefusal, TaskSubmissionService

    service = TaskSubmissionService(engine=None, gateway=None, queue=None)  # type: ignore[arg-type]
    result = await service.submit(
        workspace_id=WORKSPACE,
        user_id=USER,
        idempotency_key="k",
        instruction="summarise the attached file",
        title=None,
        session_id=None,
        input_files=["no-such-upload"],
    )
    assert isinstance(result, TaskRefusal) and result.code == "attachment_not_found"


# ------------------------------------------------------------------------------ routing


async def test_document_is_saved_into_the_connected_folder(file_env: Any, tmp_path: Path) -> None:
    services, folder = file_env
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    job = make_job()
    result = await agent.execute_subtask(job, _xlsx_context())
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    saved = Path(result.artifact_paths[0])
    assert saved == folder / "budget.xlsx" and saved.is_file()  # noqa: ASYNC240
    assert "connected folder" in result.summary
    # The artifacts-store copy stays, so download keeps working.
    assert (services.artifacts_dir / str(job.workspace_id) / "budget.xlsx").is_file()


async def test_a_second_document_never_overwrites_the_first(file_env: Any) -> None:
    services, folder = file_env
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    (folder / "budget.xlsx").write_bytes(b"the user's own file")
    result = await agent.execute_subtask(make_job(), _xlsx_context())
    assert Path(result.artifact_paths[0]) == folder / "budget (2).xlsx"
    assert (folder / "budget.xlsx").read_bytes() == b"the user's own file"


async def test_document_without_a_folder_stays_in_the_artifacts_store(
    file_env_no_folder: Any,
) -> None:
    services = file_env_no_folder
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    result = await agent.execute_subtask(make_job(), _xlsx_context())
    saved = Path(result.artifact_paths[0])
    assert services.artifacts_dir in saved.parents
    assert "connected folder" not in result.summary


async def test_document_with_a_missing_folder_falls_back_and_says_so(file_env: Any) -> None:
    services, folder = file_env
    folder.rmdir()
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    result = await agent.execute_subtask(make_job(), _xlsx_context())
    assert result.status == AgentExecutionStatus.SUCCESS
    assert Path(result.artifact_paths[0]).is_file()  # noqa: ASYNC240
    assert "missing" in result.summary and "storage" in result.summary


async def test_a_task_started_by_voice_routes_the_same_way(file_env: Any) -> None:
    """Voice plans and dispatches elsewhere, but the agents resolve the folder from the caller's
    workspace and user at run time, so a voice-started task lands in the folder too."""
    services, folder = file_env
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    voice_job = make_job(uuid4())  # no input_data marker: nothing about the origin is needed
    result = await agent.execute_subtask(voice_job, _xlsx_context("voice-sheet.xlsx"))
    assert Path(result.artifact_paths[0]) == folder / "voice-sheet.xlsx"


async def test_pdf_cannot_be_edited_but_can_be_recreated_from_its_text(file_env: Any) -> None:
    from app.automation.file_edit import not_editable_message

    message = not_editable_message("pdf", "a.pdf")
    assert "pdf" in message.lower() and "new" in message.lower()


@pytest.mark.parametrize("bad", ["../escape.xlsx", "..\\escape.xlsx", "C:\\escape.xlsx"])
async def test_a_traversal_filename_never_reaches_the_folder(file_env: Any, bad: str) -> None:
    services, folder = file_env
    agent = DocumentAgent(artifacts_base_dir=services.artifacts_dir)
    result = await agent.execute_subtask(make_job(), _xlsx_context(bad))
    assert result.status == AgentExecutionStatus.FAILED or Path(  # noqa: ASYNC240
        result.artifact_paths[0]
    ).resolve().is_relative_to(services.artifacts_dir.parent)
    assert not list(folder.parent.glob("escape*"))
