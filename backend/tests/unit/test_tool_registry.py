"""Comprehensive unit tests for the Unified Tool Registry.

Tests:
1. ToolRisk classification and BaseTool / FunctionTool abstraction
2. ToolRegistry registration, query, LLM format exports, and permission enforcement
3. Workspace tools (read, write, edit with diff, list) and safe path containment
4. Dynamic planning todo tool (create, update, mark_done, reorder, view, clear)
5. Voice and chat bridge adapters and unified registry factory
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.tools.base import (
    BaseTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRisk,
    tool,
)
from app.tools.bridge import (
    bridge_chat_tools,
    bridge_voice_tools,
    create_unified_registry,
)
from app.tools.registry import (
    ToolRegistry,
    get_global_registry,
    reset_global_registry,
)
from app.tools.todo import (
    todo_manage,
)
from app.tools.workspace import (
    workspace_edit_file,
    workspace_list_dir,
    workspace_read_file,
    workspace_write_file,
)
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

# ===========================================================================
# 1. Base Abstractions and Risk Tagging
# ===========================================================================


class DummyEchoTool(BaseTool):
    name = "dummy_echo"
    description = "Echoes back input arguments."
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    risk = ToolRisk.SAFE
    category = "test"
    tags = ["test", "dummy"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        return {"echo": kwargs.get("message"), "user_id": str(context.user_id)}


class RestrictedNukeTool(BaseTool):
    name = "restricted_nuke"
    description = "A strictly forbidden dangerous tool."
    parameters = {"type": "object", "properties": {}}
    risk = ToolRisk.RESTRICTED
    category = "test"

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        return {"nuked": True}


@pytest.mark.asyncio
async def test_base_tool_execution_and_definition() -> None:
    context = ToolExecutionContext(user_id=uuid.uuid4())
    echo = DummyEchoTool()

    assert echo.risk == ToolRisk.SAFE
    res = await echo.execute(context, message="hello")
    assert res["echo"] == "hello"
    assert res["user_id"] == str(context.user_id)

    defn = echo.definition
    assert isinstance(defn, ToolDefinition)
    assert defn.name == "dummy_echo"
    assert defn.risk == ToolRisk.SAFE


@pytest.mark.asyncio
async def test_function_tool_and_decorator() -> None:
    @tool(
        name="add_numbers",
        description="Adds two integers.",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        risk=ToolRisk.SAFE,
        category="math",
    )
    def add(a: int, b: int) -> int:
        return a + b

    context = ToolExecutionContext()
    result = await add.execute(context, a=10, b=25)
    assert result == 35
    assert add.risk == ToolRisk.SAFE
    assert add.category == "math"


# ===========================================================================
# 2. ToolRegistry & LLM Formats
# ===========================================================================


@pytest.mark.asyncio
async def test_registry_registration_and_execution() -> None:
    reg = ToolRegistry("test_registry")
    echo_tool = DummyEchoTool()
    restricted_tool = RestrictedNukeTool()

    reg.register(echo_tool)
    reg.register(restricted_tool)

    assert len(reg) == 2
    assert "dummy_echo" in reg
    assert "restricted_nuke" in reg
    assert reg.get("dummy_echo") is echo_tool

    # Safe execution succeeds
    ctx = ToolExecutionContext(user_id=uuid.uuid4())
    res = await reg.execute("dummy_echo", ctx, message="ping")
    assert res["echo"] == "ping"

    # Restricted execution raises ToolPermissionError
    with pytest.raises(ToolPermissionError) as exc_info:
        await reg.execute("restricted_nuke", ctx)
    assert "RESTRICTED and cannot be executed" in str(exc_info.value)

    # Missing tool raises ToolNotFoundError
    with pytest.raises(ToolNotFoundError):
        await reg.execute("non_existent_tool", ctx)


def test_registry_filtering_and_formats() -> None:
    reg = ToolRegistry("test_formats")
    echo = DummyEchoTool()
    nuke = RestrictedNukeTool()
    reg.register(echo)
    reg.register(nuke)

    # List filtering
    safe_tools = reg.list_tools(risk=ToolRisk.SAFE)
    assert len(safe_tools) == 1
    assert safe_tools[0].name == "dummy_echo"

    restricted_tools = reg.list_tools(risk=ToolRisk.RESTRICTED)
    assert len(restricted_tools) == 1
    assert restricted_tools[0].name == "restricted_nuke"

    # Gemini format
    gemini_decls = reg.to_gemini_format(risk=ToolRisk.SAFE)
    assert len(gemini_decls) == 1
    assert gemini_decls[0]["name"] == "dummy_echo"
    assert "parameters" in gemini_decls[0]

    # Claude format
    claude_decls = reg.to_claude_format(risk=ToolRisk.SAFE)
    assert len(claude_decls) == 1
    assert claude_decls[0]["name"] == "dummy_echo"
    assert "input_schema" in claude_decls[0]

    # OpenAI format
    openai_decls = reg.to_openai_format(risk=ToolRisk.SAFE)
    assert len(openai_decls) == 1
    assert openai_decls[0]["type"] == "function"
    assert openai_decls[0]["function"]["name"] == "dummy_echo"


def test_global_registry_singleton() -> None:
    reset_global_registry()
    r1 = get_global_registry()
    r2 = get_global_registry()
    assert r1 is r2
    reset_global_registry()
    r3 = get_global_registry()
    assert r3 is not r1


# ===========================================================================
# 3. Workspace Tools & Path Containment
# ===========================================================================


@pytest.mark.asyncio
async def test_workspace_write_and_read(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path)

    # 1. Write file
    write_res = await workspace_write_file(
        "docs/notes.txt", "line 1\nline 2\nline 3\nline 4\n", context=ctx
    )
    assert write_res["ok"] is True
    assert write_res["bytes_written"] > 0
    assert (tmp_path / "docs" / "notes.txt").exists()

    # 2. Prevent accidental overwrite
    dupe_res = await workspace_write_file(
        "docs/notes.txt", "replacement", overwrite=False, context=ctx
    )
    assert dupe_res["ok"] is False
    assert "already exists" in dupe_res["error"]

    # 3. Allow explicit overwrite
    overwrite_res = await workspace_write_file(
        "docs/notes.txt", "line 1\nline 2\nline 3\nline 4\n", overwrite=True, context=ctx
    )
    assert overwrite_res["ok"] is True

    # 4. Read file with pagination
    read_all = await workspace_read_file("docs/notes.txt", context=ctx)
    assert read_all["ok"] is True
    assert read_all["total_lines"] == 4
    assert read_all["lines_returned"] == 4
    assert read_all["truncated"] is False

    # Read slice with offset and limit
    read_slice = await workspace_read_file("docs/notes.txt", offset=2, limit=2, context=ctx)
    assert read_slice["ok"] is True
    assert read_slice["start_line"] == 2
    assert read_slice["lines_returned"] == 2
    assert read_slice["content"] == "line 2\nline 3\n"
    assert read_slice["truncated"] is True


@pytest.mark.asyncio
async def test_workspace_edit_with_unified_diff(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path)
    file_path = "src/config.py"
    initial_content = "DEBUG = True\nPORT = 8000\nHOST = 'localhost'\n"

    await workspace_write_file(file_path, initial_content, context=ctx)

    # Perform edit
    edit_res = await workspace_edit_file(
        file_path,
        target="DEBUG = True",
        replacement="DEBUG = False",
        context=ctx,
    )
    assert edit_res["ok"] is True
    assert edit_res["occurrences"] == 1
    assert "--- a/src/config.py" in edit_res["diff"]
    assert "+++ b/src/config.py" in edit_res["diff"]
    assert "-DEBUG = True" in edit_res["diff"]
    assert "+DEBUG = False" in edit_res["diff"]

    # Verify content changed on disk
    updated_content = (tmp_path / "src" / "config.py").read_text(encoding="utf-8")
    assert "DEBUG = False" in updated_content

    # Target not found returns error
    fail_edit = await workspace_edit_file(
        file_path,
        target="NON_EXISTENT_KEY = 123",
        replacement="CHANGED",
        context=ctx,
    )
    assert fail_edit["ok"] is False
    assert "Target string not found" in fail_edit["error"]


@pytest.mark.asyncio
async def test_workspace_delete_file_and_directory(tmp_path: Path) -> None:
    from app.tools.workspace import workspace_delete_dir, workspace_delete_file

    ctx = ToolExecutionContext(workspace_root=tmp_path)
    file_path = "subfolder/test_doc.txt"
    await workspace_write_file(file_path, "temporary text", context=ctx)
    assert (tmp_path / "subfolder" / "test_doc.txt").exists()

    # Delete file
    del_file_res = await workspace_delete_file(file_path, context=ctx)
    assert del_file_res["ok"] is True
    assert not (tmp_path / "subfolder" / "test_doc.txt").exists()

    # Create another file inside subfolder and delete directory recursively
    await workspace_write_file("subfolder/another.txt", "data", context=ctx)
    del_dir_res = await workspace_delete_dir("subfolder", recursive=True, context=ctx)
    assert del_dir_res["ok"] is True
    assert not (tmp_path / "subfolder").exists()

    # Security check: cannot delete root
    del_root_res = await workspace_delete_dir(".", recursive=True, context=ctx)
    assert del_root_res["ok"] is False
    assert any(
        term in del_root_res["error"].lower() for term in ("cannot delete", "root", "empty", "part")
    )


@pytest.mark.asyncio
async def test_workspace_list_dir(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path)

    # Setup directories and files
    await workspace_write_file("root.txt", "root", context=ctx)
    await workspace_write_file("sub/file1.txt", "f1", context=ctx)
    await workspace_write_file("sub/nested/file2.txt", "f2", context=ctx)

    # Non-recursive listing
    list_root = await workspace_list_dir(".", recursive=False, context=ctx)
    assert list_root["ok"] is True
    names = [e["name"] for e in list_root["entries"]]
    assert "root.txt" in names
    assert "sub" in names

    # Recursive listing
    list_rec = await workspace_list_dir(".", recursive=True, context=ctx)
    assert list_rec["ok"] is True
    paths = [e["path"] for e in list_rec["entries"]]
    assert "root.txt" in paths
    assert "sub/file1.txt" in paths
    assert "sub/nested/file2.txt" in paths


@pytest.mark.asyncio
async def test_workspace_path_containment_security(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path)

    # Relative path escaping root must be rejected
    escape_read = await workspace_read_file("../../outside.txt", context=ctx)
    assert escape_read["ok"] is False
    assert "error" in escape_read

    escape_write = await workspace_write_file("../dangerous.txt", "bad", context=ctx)
    assert escape_write["ok"] is False
    assert "error" in escape_write

    # Without workspace root context, must fail cleanly
    no_ctx = ToolExecutionContext(workspace_root=None)
    no_root_res = await workspace_read_file("test.txt", context=no_ctx)
    assert no_root_res["ok"] is False
    assert "workspace root" in no_root_res["error"].lower()


# ===========================================================================
# 4. Dynamic Planning Todo Tool
# ===========================================================================


@pytest.mark.asyncio
async def test_todo_planning_lifecycle() -> None:
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ctx = ToolExecutionContext(session_id=session_id)

    # 1. Create tasks
    create_res = await todo_manage(
        action="create",
        tasks=[
            "Design architecture",
            {"id": "task_custom", "title": "Implement models", "priority": "high"},
            "Write comprehensive tests",
        ],
        note="Initial plan",
        context=ctx,
    )
    assert create_res["ok"] is True
    assert create_res["created_count"] == 3
    assert create_res["counts"]["total"] == 3
    assert create_res["counts"]["pending"] == 3
    assert create_res["note"] == "Initial plan"

    # 2. View tasks
    view_res = await todo_manage(action="view", context=ctx)
    assert view_res["ok"] is True
    assert len(view_res["tasks"]) == 3
    task_ids = [t["id"] for t in view_res["tasks"]]
    assert "task_1" in task_ids
    assert "task_custom" in task_ids

    # 3. Update task
    update_res = await todo_manage(
        action="update",
        tasks=[{"id": "task_custom", "status": "in_progress", "note": "Drafting schemas"}],
        context=ctx,
    )
    assert update_res["ok"] is True
    assert update_res["counts"]["in_progress"] == 1

    # 4. Mark done
    done_res = await todo_manage(
        action="mark_done",
        tasks=["task_1", "task_custom"],
        context=ctx,
    )
    assert done_res["ok"] is True
    assert done_res["counts"]["done"] == 2
    assert done_res["counts"]["pending"] == 1

    # 5. Reorder tasks
    reorder_res = await todo_manage(
        action="reorder",
        tasks=["task_custom", "task_1"],
        context=ctx,
    )
    assert reorder_res["ok"] is True
    assert [t["id"] for t in reorder_res["tasks"][:2]] == ["task_custom", "task_1"]

    # 6. Clear tasks
    clear_res = await todo_manage(action="clear", context=ctx)
    assert clear_res["ok"] is True
    assert clear_res["cleared_tasks"] == 3

    post_clear = await todo_manage(action="view", context=ctx)
    assert post_clear["counts"]["total"] == 0


# ===========================================================================
# 5. Voice and Chat Bridge
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_tool_bridge() -> None:
    reg = ToolRegistry("voice_test")
    voice_reg = VoiceToolRegistry()

    # Wire a dummy task submitter
    async def fake_submitter(
        instruction: str, deliverable: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        return {"ok": True, "task_id": "task_12345", "status": "queued"}

    voice_reg.set_task_submitter(fake_submitter)

    bridged = bridge_voice_tools(reg, voice_registry=voice_reg)
    assert len(bridged) >= 2

    # Check risk tagging
    submit_tool = reg.get("submit_task")
    assert submit_tool is not None
    assert submit_tool.risk == ToolRisk.APPROVAL_REQUIRED
    assert submit_tool.category == "voice"

    status_tool = reg.get("get_task_status")
    assert status_tool is not None
    assert status_tool.risk == ToolRisk.SAFE

    # Execute bridged tool through unified registry
    ctx = ToolExecutionContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
    exec_res = await reg.execute(
        "submit_task",
        ctx,
        instruction="Complete financial analysis report",
        deliverable="pdf",
    )
    assert exec_res["ok"] is True
    assert exec_res["task_id"] == "task_12345"


def test_chat_tool_bridge() -> None:
    reg = ToolRegistry("chat_test")
    bridged = bridge_chat_tools(reg)

    assert len(bridged) > 5

    # Verify risk mappings
    web_search = reg.get("web_search")
    assert web_search is not None
    assert web_search.risk == ToolRisk.SAFE
    assert web_search.category == "chat"

    send_email = reg.get("send_email")
    assert send_email is not None
    assert send_email.risk == ToolRisk.APPROVAL_REQUIRED

    create_file = reg.get("create_file_inline")
    assert create_file is not None
    assert create_file.risk == ToolRisk.APPROVAL_REQUIRED


def test_create_unified_registry() -> None:
    unified = create_unified_registry()

    # Contains workspace tools
    assert "workspace_read_file" in unified
    assert "workspace_write_file" in unified
    assert "workspace_edit_file" in unified
    assert "workspace_list_dir" in unified
    assert "workspace_delete_file" in unified
    assert "workspace_delete_dir" in unified

    # Contains todo tool
    assert "todo_manage" in unified

    # Contains voice tools
    assert "submit_task" in unified
    assert "get_task_status" in unified

    # Contains chat tools
    assert "web_search" in unified
    assert "send_email" in unified

    # Check risk distribution
    safe_tools = unified.list_tools(risk=ToolRisk.SAFE)
    approval_tools = unified.list_tools(risk=ToolRisk.APPROVAL_REQUIRED)

    assert len(safe_tools) > 0
    assert len(approval_tools) > 0

    # Ensure all tools serialize properly to all 3 LLM formats
    gemini_specs = unified.to_gemini_format()
    claude_specs = unified.to_claude_format()
    openai_specs = unified.to_openai_format()

    assert len(gemini_specs) == len(unified)
    assert len(claude_specs) == len(unified)
    assert len(openai_specs) == len(unified)
