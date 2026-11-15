"""Unit tests for workspace and terminal command execution tools."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.tools.base import ToolExecutionContext, ToolRisk
from app.tools.registry import ToolRegistry
from app.tools.workspace import (
    TerminalRunCommandTool,
    WorkspaceRunCommandTool,
    register_workspace_tools,
    workspace_run_command,
)


@pytest.mark.asyncio
async def test_workspace_run_command_success(tmp_path: Path) -> None:
    """Test successful command execution inside the workspace directory."""
    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "print(\'hello from workspace\')"'
    res = await workspace_run_command(cmd, context=ctx)

    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert "hello from workspace" in res["stdout"]
    assert res["stderr"] == ""
    assert res["command"] == cmd
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_workspace_run_command_executes_in_workspace_cwd(tmp_path: Path) -> None:
    """Verify that commands execute with cwd set to the active workspace root."""
    marker = tmp_path / "marker.txt"
    marker.write_text("maya-workspace-marker", encoding="utf-8")

    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "import pathlib; print(pathlib.Path(\'marker.txt\').read_text())"'
    res = await workspace_run_command(cmd, context=ctx)

    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert "maya-workspace-marker" in res["stdout"]


@pytest.mark.asyncio
async def test_workspace_run_command_nonzero_exit_code(tmp_path: Path) -> None:
    """Verify non-zero exit codes and stderr capture."""
    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "import sys; sys.stderr.write(\'fatal err\'); sys.exit(42)"'
    res = await workspace_run_command(cmd, context=ctx)

    assert res["ok"] is False
    assert res["exit_code"] == 42
    assert "fatal err" in res["stderr"]
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_workspace_run_command_empty_command_rejection(tmp_path: Path) -> None:
    """Verify empty or whitespace-only commands are rejected."""
    ctx = ToolExecutionContext(workspace_root=tmp_path)

    for empty_cmd in ["", "   ", "\t\n"]:
        res = await workspace_run_command(empty_cmd, context=ctx)
        assert res["ok"] is False
        assert res["exit_code"] != 0
        assert "empty" in res["stderr"].lower()
        assert res["truncated"] is False


@pytest.mark.asyncio
async def test_workspace_run_command_missing_workspace_root(tmp_path: Path) -> None:
    """Verify missing or invalid workspace root contexts are rejected."""
    # 1. No context
    res1 = await workspace_run_command("echo hello", context=None)
    assert res1["ok"] is False
    assert res1["exit_code"] != 0
    assert "workspace root" in res1["stderr"].lower()

    # 2. Context with None workspace root
    ctx_none = ToolExecutionContext(workspace_root=None)
    res2 = await workspace_run_command("echo hello", context=ctx_none)
    assert res2["ok"] is False
    assert res2["exit_code"] != 0
    assert "workspace root" in res2["stderr"].lower()

    # 3. Context pointing to non-existent directory
    missing_dir = tmp_path / "does_not_exist"
    ctx_missing = ToolExecutionContext(workspace_root=missing_dir)
    res3 = await workspace_run_command("echo hello", context=ctx_missing)
    assert res3["ok"] is False
    assert res3["exit_code"] != 0
    assert "not a valid directory" in res3["stderr"].lower()


@pytest.mark.asyncio
async def test_workspace_run_command_timeout(tmp_path: Path) -> None:
    """Verify that long-running commands are terminated on timeout."""
    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "import time; time.sleep(10)"'
    res = await workspace_run_command(cmd, timeout_seconds=1, context=ctx)

    assert res["ok"] is False
    assert res["exit_code"] == 124
    assert "timed out" in res["stderr"].lower()
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_workspace_run_command_truncation(tmp_path: Path) -> None:
    """Verify that command output exceeding 100 KB is truncated."""
    ctx = ToolExecutionContext(workspace_root=tmp_path)
    # Output 150 KB of 'A's
    cmd = f'"{sys.executable}" -c "print(\'A\' * 153600)"'
    res = await workspace_run_command(cmd, context=ctx)

    assert res["ok"] is True
    assert res["truncated"] is True
    assert len(res["stdout"]) == 100 * 1024


@pytest.mark.asyncio
async def test_workspace_run_command_tool_execute(tmp_path: Path) -> None:
    """Test Tool wrapper execution and schema attributes."""
    tool = WorkspaceRunCommandTool()
    assert tool.name == "workspace_run_command"
    assert tool.risk == ToolRisk.APPROVAL_REQUIRED
    assert tool.category == "workspace"
    assert "command" in tool.parameters["properties"]

    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "print(\'via tool\')"'
    res = await tool.execute(ctx, command=cmd)

    assert res["ok"] is True
    assert "via tool" in res["stdout"]


@pytest.mark.asyncio
async def test_terminal_run_command_alias_tool_execute(tmp_path: Path) -> None:
    """Test TerminalRunCommandTool alias execution and schema attributes."""
    tool = TerminalRunCommandTool()
    assert tool.name == "terminal_run_command"
    assert tool.risk == ToolRisk.APPROVAL_REQUIRED
    assert tool.category == "workspace"

    ctx = ToolExecutionContext(workspace_root=tmp_path)
    cmd = f'"{sys.executable}" -c "print(\'via alias\')"'
    res = await tool.execute(ctx, command=cmd)

    assert res["ok"] is True
    assert "via alias" in res["stdout"]


def test_register_workspace_tools_includes_run_and_terminal() -> None:
    """Verify both run tools are registered into ToolRegistry."""
    registry = ToolRegistry("test_workspace")
    register_workspace_tools(registry)

    assert "workspace_run_command" in registry
    assert "terminal_run_command" in registry

    run_tool = registry.get("workspace_run_command")
    alias_tool = registry.get("terminal_run_command")

    assert run_tool is not None
    assert alias_tool is not None
    assert run_tool.risk == ToolRisk.APPROVAL_REQUIRED
    assert alias_tool.risk == ToolRisk.APPROVAL_REQUIRED
