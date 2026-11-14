"""A planned file edit with attachments: the raw tool name and the invented path are dropped."""

from __future__ import annotations

from typing import Any

from app.agents.plan_repair import PlanNode, drop_invented_edit_targets, repair_plan
from app.agents.state import SubTask
from app.files.edit_step import DocxEditItem, DocxEditPlan, TextEditPlan
from app.mcp.permissions import SAVE_TOOL_NAMES
from app.tasks.executor import execute_subtask_with_agent
from tests.support.files import USER, WORKSPACE, make_docx, make_job
from tests.unit.test_files_extract_edit import FakeGateway


def test_repair_drops_the_raw_tool_name_from_edit_and_project_steps() -> None:
    nodes = [
        PlanNode(
            id="n1",
            role="file_edit",
            description="edit the attached report",
            input_data={"path": "report.docx"},
            tool="file_edit_as_new_version",
        ),
        PlanNode(id="n2", role="project", description="site", tool="file_write_new"),
    ]
    repaired, notes = repair_plan(
        nodes, frozenset({"file_edit_as_new_version", "file_write_new"}), user_text="edit"
    )
    assert [n.tool for n in repaired] == [None, None]
    assert any("file_edit_as_new_version" in n for n in notes)
    assert SAVE_TOOL_NAMES is not None


def test_an_invented_path_is_removed_when_files_are_attached() -> None:
    node = SubTask(
        id="n1",
        assigned_agent="file",
        description="edit",
        input_data={"executor": "file_edit", "path": "report.docx"},
    )
    drop_invented_edit_targets([node], ["upload-1"])
    assert "path" not in node.input_data
    keep = SubTask(
        id="n2",
        assigned_agent="file",
        description="e",
        input_data={"executor": "file_edit", "path": "a.txt"},
    )
    drop_invented_edit_targets([keep], [])
    assert keep.input_data["path"] == "a.txt"


async def test_docx_and_csv_uploads_are_edited_as_new_versions(file_env: Any) -> None:
    services, folder = file_env
    docx = make_docx(folder.parent / "report.docx", ["Alpha", "Beta"])
    csv_bytes = b"item,qty\na,1\n"
    d = services.uploads.save(WORKSPACE, USER, "report.docx", docx.read_bytes())
    c = services.uploads.save(WORKSPACE, USER, "sales.csv", csv_bytes)
    before = {d.id: docx.read_bytes(), c.id: csv_bytes}
    for record, plan in (
        (d, DocxEditPlan(edits=[DocxEditItem(paragraph_index=1, new_text="Gamma")])),
        (c, TextEditPlan(new_content="item,qty,total\na,1,1\n")),
    ):
        out = await execute_subtask_with_agent(
            make_job(),
            {
                "executor": "file_edit",
                "path": "report.docx",  # invented by the planner
                "input_files": [record.id],
                "description": "edit it",
            },
            gateway=FakeGateway(plan),  # type: ignore[arg-type]
        )
        assert out["result"]["new_version"] == record.name
        assert out["result"]["diff"]
        assert out["proof"]["status"] == "verified"
    assert not list(folder.glob("*.bak"))
    assert (folder / "report.docx").is_file() and (folder / "sales.csv").is_file()
    assert (
        services.uploads.path_of(services.uploads.get(WORKSPACE, d.id)).read_bytes() == before[d.id]
    )
    assert (
        services.uploads.path_of(services.uploads.get(WORKSPACE, c.id)).read_bytes() == before[c.id]
    )
