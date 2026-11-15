"""Unit tests for tool startup registration (BUG-003)."""

from app.tools.registry import ToolRegistry, get_global_registry, reset_global_registry
from app.tools.startup import register_global_tools


def test_register_global_tools_populates_expected_tools() -> None:
    test_reg = ToolRegistry("test_startup")
    register_global_tools(test_reg)

    tool_names = {t.name for t in test_reg.list_tools()}

    # 1. Workspace tools
    assert "workspace_read_file" in tool_names
    assert "workspace_write_file" in tool_names
    assert "workspace_edit_file" in tool_names
    assert "workspace_list_dir" in tool_names

    # 2. Todo tools
    assert "todo_manage" in tool_names

    # 3. Chat tools
    assert "web_search" in tool_names
    assert "send_email" in tool_names
    assert "create_document" in tool_names

    # 4. Voice tools
    assert "submit_task" in tool_names
    assert "get_task_status" in tool_names


def test_get_global_registry_singleton_identity() -> None:
    reset_global_registry()
    r1 = get_global_registry()
    r2 = get_global_registry()
    assert r1 is r2
    assert r1.name == "global"
    reset_global_registry()
