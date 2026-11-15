"""Unified Tool Registry for MAYA AI.

Unifies tools across chat, voice, and background tasks into a single architecture:
- Standard risk levels: SAFE, APPROVAL_REQUIRED, RESTRICTED.
- Workspace tools: read, write, edit with diff, list dir.
- Dynamic planning: todo_manage for interactive task tracking.
- Voice & Chat bridge: shared ecosystem across all interaction modalities.
"""

from __future__ import annotations

from app.tools.base import (
    BaseTool,
    FunctionTool,
    ToolCallable,
    ToolDefinition,
    ToolError,
    ToolExecutionContext,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRisk,
    WorkspaceNotSetError,
    tool,
)
from app.tools.bridge import (
    ChatGatewayToolAdapter,
    VoiceToolAdapter,
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
    TodoItem,
    TodoManageTool,
    TodoStore,
    get_todo_store,
    register_todo_tools,
    todo_manage,
)
from app.tools.workspace import (
    TerminalRunCommandTool,
    WorkspaceDeleteDirTool,
    WorkspaceDeleteFileTool,
    WorkspaceEditFileTool,
    WorkspaceListDirTool,
    WorkspaceReadFileTool,
    WorkspaceRunCommandTool,
    WorkspaceWriteFileTool,
    register_workspace_tools,
    workspace_delete_dir,
    workspace_delete_file,
    workspace_edit_file,
    workspace_list_dir,
    workspace_read_file,
    workspace_run_command,
    workspace_write_file,
)

__all__ = [
    # Base abstractions
    "BaseTool",
    "FunctionTool",
    "ToolCallable",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionContext",
    "ToolNotFoundError",
    "ToolPermissionError",
    "ToolRisk",
    "WorkspaceNotSetError",
    "tool",
    # Registry
    "ToolRegistry",
    "get_global_registry",
    "reset_global_registry",
    # Workspace
    "TerminalRunCommandTool",
    "WorkspaceDeleteDirTool",
    "WorkspaceDeleteFileTool",
    "WorkspaceEditFileTool",
    "WorkspaceListDirTool",
    "WorkspaceReadFileTool",
    "WorkspaceRunCommandTool",
    "WorkspaceWriteFileTool",
    "register_workspace_tools",
    "workspace_delete_dir",
    "workspace_delete_file",
    "workspace_edit_file",
    "workspace_list_dir",
    "workspace_read_file",
    "workspace_run_command",
    "workspace_write_file",
    # Todo
    "TodoItem",
    "TodoManageTool",
    "TodoStore",
    "get_todo_store",
    "register_todo_tools",
    "todo_manage",
    # Bridge
    "ChatGatewayToolAdapter",
    "VoiceToolAdapter",
    "bridge_chat_tools",
    "bridge_voice_tools",
    "create_unified_registry",
]
