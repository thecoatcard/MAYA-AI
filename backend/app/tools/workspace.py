"""Cowork and Antigravity-style Workspace Tools.

Provides sandboxed file and directory operations strictly restricted to the active
workspace directory using app.files.safepath:
- workspace_read_file: Read file lines with pagination (offset, limit) - SAFE
- workspace_write_file: Write file with overwrite protection - APPROVAL_REQUIRED
- workspace_edit_file: Replace target string generating unified diff - APPROVAL_REQUIRED
- workspace_list_dir: List files and directories (optionally recursive) - SAFE
- workspace_delete_file: Delete a file inside workspace - APPROVAL_REQUIRED
- workspace_delete_dir: Delete a directory inside workspace - APPROVAL_REQUIRED
- workspace_run_command: Execute shell command inside workspace - APPROVAL_REQUIRED
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import shutil
import sys
from pathlib import Path
from typing import Any

from app.files.safepath import SafePathError, resolve_inside
from app.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRisk,
    WorkspaceNotSetError,
)
from app.tools.registry import ToolRegistry


def _get_workspace_root(context: ToolExecutionContext | None) -> Path:
    """Extract and validate the active workspace root directory from context."""
    if context is None or context.workspace_root is None:
        raise WorkspaceNotSetError("Active workspace root is not configured in execution context.")
    root = Path(context.workspace_root).resolve()
    if not root.is_dir():
        raise WorkspaceNotSetError(f"Active workspace root '{root}' is not a valid directory.")
    return root


def _resolve_workspace_path(
    context: ToolExecutionContext | None, path: str, *, allow_root: bool = False
) -> Path:
    """Resolve a relative workspace path safely, ensuring it cannot escape the workspace root."""
    root = _get_workspace_root(context)
    normalized = path.strip()
    if not normalized:
        if allow_root:
            normalized = "."
        else:
            raise SafePathError("Path cannot be empty.")
    return resolve_inside(root, normalized, allow_root=allow_root)


async def workspace_read_file(
    path: str,
    offset: int = 1,
    limit: int | None = None,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Read contents of a file within the workspace with line offset and limit pagination."""
    try:
        resolved = _resolve_workspace_path(context, path, allow_root=False)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if not resolved.exists():
        return {"ok": False, "error": f"File '{path}' does not exist.", "path": path}
    if not resolved.is_file():
        return {"ok": False, "error": f"Path '{path}' is not a file.", "path": path}

    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as err:
        return {"ok": False, "error": f"Failed to read file '{path}': {err}", "path": path}

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)

    start = max(1, offset)
    start_idx = start - 1

    if start_idx >= total_lines:
        slice_lines: list[str] = []
    elif limit is not None and limit >= 0:
        slice_lines = lines[start_idx : start_idx + limit]
    else:
        slice_lines = lines[start_idx:]

    content = "".join(slice_lines)
    lines_returned = len(slice_lines)
    truncated = (start_idx + lines_returned) < total_lines

    return {
        "ok": True,
        "path": path,
        "content": content,
        "start_line": start,
        "lines_returned": lines_returned,
        "total_lines": total_lines,
        "truncated": truncated,
    }


async def workspace_write_file(
    path: str,
    content: str,
    overwrite: bool = False,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Write text content to a relative file path inside the workspace."""
    try:
        resolved = _resolve_workspace_path(context, path, allow_root=False)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if resolved.exists():
        if resolved.is_dir():
            return {"ok": False, "error": f"Path '{path}' is a directory.", "path": path}
        if not overwrite:
            return {
                "ok": False,
                "error": f"File '{path}' already exists and overwrite is False.",
                "path": path,
            }

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except Exception as err:
        return {"ok": False, "error": f"Failed to write file '{path}': {err}", "path": path}

    return {
        "ok": True,
        "path": path,
        "bytes_written": len(content.encode("utf-8")),
        "overwritten": resolved.exists() and overwrite,
    }


async def workspace_edit_file(
    path: str,
    target: str,
    replacement: str,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Replace target text in a file inside the workspace, generating a unified diff."""
    try:
        resolved = _resolve_workspace_path(context, path, allow_root=False)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if not resolved.exists():
        return {"ok": False, "error": f"File '{path}' does not exist.", "path": path}
    if not resolved.is_file():
        return {"ok": False, "error": f"Path '{path}' is not a file.", "path": path}

    try:
        original = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as err:
        return {"ok": False, "error": f"Failed to read file '{path}': {err}", "path": path}

    if target not in original:
        return {
            "ok": False,
            "error": f"Target string not found in '{path}'.",
            "path": path,
        }

    occurrences = original.count(target)
    modified = original.replace(target, replacement)

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    diff_text = "".join(diff_lines)

    try:
        resolved.write_text(modified, encoding="utf-8")
    except Exception as err:
        return {"ok": False, "error": f"Failed to save edit to '{path}': {err}", "path": path}

    return {
        "ok": True,
        "path": path,
        "diff": diff_text,
        "occurrences": occurrences,
    }


async def workspace_list_dir(
    path: str = ".",
    recursive: bool = False,
    max_depth: int = 3,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """List directory contents within the workspace root."""
    try:
        root = _get_workspace_root(context)
        resolved = _resolve_workspace_path(context, path, allow_root=True)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if not resolved.exists():
        return {"ok": False, "error": f"Directory '{path}' does not exist.", "path": path}
    if not resolved.is_dir():
        return {"ok": False, "error": f"Path '{path}' is not a directory.", "path": path}

    entries: list[dict[str, Any]] = []

    def scan(current_dir: Path, current_depth: int) -> None:
        try:
            items = sorted(current_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception:
            return

        for item in items:
            # Skip hidden control directories like .git
            if item.name.startswith(".git"):
                continue

            rel_to_root = item.relative_to(root).as_posix()
            is_directory = item.is_dir()
            size = None
            if not is_directory:
                try:
                    size = item.stat().st_size
                except Exception:
                    size = None

            entries.append(
                {
                    "name": item.name,
                    "path": rel_to_root,
                    "type": "directory" if is_directory else "file",
                    "size": size,
                }
            )

            if recursive and is_directory and current_depth < max_depth:
                scan(item, current_depth + 1)

    scan(resolved, 1)

    return {
        "ok": True,
        "path": path,
        "entries": entries,
        "total_entries": len(entries),
    }


class WorkspaceReadFileTool(BaseTool):
    """Tool to read a file within the workspace with line-level pagination."""

    name = "workspace_read_file"
    description = (
        "Read file contents within the active workspace. Supports line offset (1-indexed) "
        "and line limit for pagination."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file inside the workspace.",
            },
            "offset": {
                "type": "integer",
                "description": "1-indexed line number to start reading from. Default 1.",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Omit to read until the end.",
            },
        },
        "required": ["path"],
    }
    risk = ToolRisk.SAFE
    category = "workspace"
    tags = ["file", "workspace", "read"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        path = kwargs["path"]
        offset = kwargs.get("offset", 1)
        limit = kwargs.get("limit")
        return await workspace_read_file(path, offset=offset, limit=limit, context=context)


class WorkspaceWriteFileTool(BaseTool):
    """Tool to create or overwrite a file within the workspace."""

    name = "workspace_write_file"
    description = (
        "Write text content to a relative path in the workspace. Fails if the file exists "
        "unless overwrite is True."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to write."},
            "content": {"type": "string", "description": "Full text content to write."},
            "overwrite": {
                "type": "boolean",
                "description": "Whether to overwrite if file already exists. Default False.",
                "default": False,
            },
        },
        "required": ["path", "content"],
    }
    risk = ToolRisk.APPROVAL_REQUIRED
    category = "workspace"
    tags = ["file", "workspace", "write"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        path = kwargs["path"]
        content = kwargs["content"]
        overwrite = kwargs.get("overwrite", False)
        return await workspace_write_file(
            path, content=content, overwrite=overwrite, context=context
        )


class WorkspaceEditFileTool(BaseTool):
    """Tool to perform exact search-and-replace in a file, generating a unified diff."""

    name = "workspace_edit_file"
    description = (
        "Replace an exact target string with a replacement string in a workspace file. "
        "Generates a unified diff before applying the change."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file to edit."},
            "target": {"type": "string", "description": "The exact character sequence to replace."},
            "replacement": {"type": "string", "description": "The replacement string."},
        },
        "required": ["path", "target", "replacement"],
    }
    risk = ToolRisk.APPROVAL_REQUIRED
    category = "workspace"
    tags = ["file", "workspace", "edit", "diff"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        path = kwargs["path"]
        target = kwargs["target"]
        replacement = kwargs["replacement"]
        return await workspace_edit_file(
            path, target=target, replacement=replacement, context=context
        )


async def workspace_delete_file(
    path: str,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Delete a file within the workspace. APPROVAL_REQUIRED."""
    try:
        resolved = _resolve_workspace_path(context, path, allow_root=False)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if not resolved.exists():
        return {"ok": False, "error": f"File '{path}' does not exist.", "path": path}
    if not resolved.is_file():
        return {
            "ok": False,
            "error": f"Path '{path}' is a directory, not a file. Use workspace_delete_dir.",
            "path": path,
        }

    try:
        resolved.unlink()
        return {"ok": True, "path": path, "deleted": True}
    except Exception as err:
        return {"ok": False, "error": f"Failed to delete file: {err}", "path": path}


async def workspace_delete_dir(
    path: str,
    recursive: bool = False,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Delete a directory within the workspace. APPROVAL_REQUIRED."""
    try:
        resolved = _resolve_workspace_path(context, path, allow_root=False)
    except (SafePathError, WorkspaceNotSetError) as err:
        return {"ok": False, "error": str(err), "path": path}

    if not resolved.exists():
        return {"ok": False, "error": f"Directory '{path}' does not exist.", "path": path}
    if not resolved.is_dir():
        return {
            "ok": False,
            "error": f"Path '{path}' is a file, not a directory. Use workspace_delete_file.",
            "path": path,
        }

    # Safety check: do not delete the root directory itself
    root = _get_workspace_root(context)
    if resolved.resolve() == root.resolve():
        return {
            "ok": False,
            "error": "Cannot delete the entire workspace root folder.",
            "path": path,
        }

    try:
        if recursive:
            shutil.rmtree(resolved)
        else:
            resolved.rmdir()
        return {"ok": True, "path": path, "deleted": True, "recursive": recursive}
    except OSError as err:
        return {
            "ok": False,
            "error": f"Directory not empty. Pass recursive=True to delete contents: {err}",
            "path": path,
        }
    except Exception as err:
        return {"ok": False, "error": f"Failed to delete directory: {err}", "path": path}


class WorkspaceListDirTool(BaseTool):
    """Tool to list directory entries inside the workspace."""

    name = "workspace_list_dir"
    description = "List files and directories within a workspace directory (optionally recursive)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative directory path. Default '.' for workspace root.",
                "default": ".",
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to scan subdirectories recursively. Default False.",
                "default": False,
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum directory depth when recursive is True. Default 3.",
                "default": 3,
            },
        },
    }
    risk = ToolRisk.SAFE
    category = "workspace"
    tags = ["file", "workspace", "list"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        path = kwargs.get("path", ".")
        recursive = kwargs.get("recursive", False)
        max_depth = kwargs.get("max_depth", 3)
        return await workspace_list_dir(
            path, recursive=recursive, max_depth=max_depth, context=context
        )


class WorkspaceDeleteFileTool(BaseTool):
    """Tool to delete a file within the workspace."""

    name = "workspace_delete_file"
    description = "Delete a specific file inside the workspace directory. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path inside the workspace to delete.",
            },
        },
        "required": ["path"],
    }
    risk = ToolRisk.APPROVAL_REQUIRED
    category = "workspace"
    tags = ["file", "workspace", "delete"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        return await workspace_delete_file(kwargs.get("path", ""), context=context)


class WorkspaceDeleteDirTool(BaseTool):
    """Tool to delete a directory within the workspace."""

    name = "workspace_delete_dir"
    description = (
        "Delete a directory inside the workspace directory (optionally recursive). "
        "Requires confirmation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative directory path inside the workspace to delete.",
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to delete subdirectories and files recursively.",
                "default": False,
            },
        },
        "required": ["path"],
    }
    risk = ToolRisk.APPROVAL_REQUIRED
    category = "workspace"
    tags = ["file", "workspace", "delete", "dir"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        return await workspace_delete_dir(
            kwargs.get("path", ""),
            recursive=kwargs.get("recursive", False),
            context=context,
        )


MAX_COMMAND_OUTPUT_BYTES: int = 100 * 1024


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate process and all descendant processes cleanly."""
    if process.returncode is not None:
        return
    pid = process.pid
    if sys.platform == "win32":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except Exception:
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()
    else:
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()
    with contextlib.suppress(Exception):
        await process.wait()


async def _execute_command_async(
    command: str,
    root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Execute shell command asynchronously using asyncio.create_subprocess_shell."""
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as err:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Failed to spawn command: {err}",
            "command": command,
            "truncated": False,
        }

    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _kill_process_tree(process)
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout_seconds} seconds.",
            "command": command,
            "truncated": False,
        }

    out_bytes = raw_stdout or b""
    err_bytes = raw_stderr or b""

    truncated = False
    if len(out_bytes) > MAX_COMMAND_OUTPUT_BYTES:
        out_bytes = out_bytes[:MAX_COMMAND_OUTPUT_BYTES]
        truncated = True
    if len(err_bytes) > MAX_COMMAND_OUTPUT_BYTES:
        err_bytes = err_bytes[:MAX_COMMAND_OUTPUT_BYTES]
        truncated = True

    stdout_str = out_bytes.decode("utf-8", errors="replace")
    stderr_str = err_bytes.decode("utf-8", errors="replace")
    exit_code = process.returncode if process.returncode is not None else 0
    ok = exit_code == 0

    return {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": stdout_str,
        "stderr": stderr_str,
        "command": command,
        "truncated": truncated,
    }


def _run_in_proactor_thread(
    command: str,
    root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Helper to run async execution in a ProactorEventLoop on Windows."""
    loop = asyncio.ProactorEventLoop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_execute_command_async(command, root, timeout_seconds))
    finally:
        loop.close()


async def workspace_run_command(
    command: str,
    timeout_seconds: int = 60,
    *,
    context: ToolExecutionContext | None = None,
) -> dict[str, Any]:
    """Execute a shell command inside the workspace directory. APPROVAL_REQUIRED."""
    if not command or not command.strip():
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": "Command cannot be empty.",
            "command": command,
            "truncated": False,
        }

    try:
        root = _get_workspace_root(context)
    except WorkspaceNotSetError as err:
        return {
            "ok": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(err),
            "command": command,
            "truncated": False,
        }

    effective_timeout = 60 if (timeout_seconds is None or timeout_seconds <= 0) else timeout_seconds

    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        if not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ())):
            return await asyncio.to_thread(
                _run_in_proactor_thread, command, root, float(effective_timeout)
            )

    return await _execute_command_async(command, root, float(effective_timeout))


class WorkspaceRunCommandTool(BaseTool):
    """Tool to execute a shell command inside the active workspace directory."""

    name = "workspace_run_command"
    description = (
        "Execute a shell command inside the active workspace directory. "
        "Requires confirmation before execution."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute inside the workspace directory.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum execution time in seconds. Defaults to 60.",
                "default": 60,
            },
        },
        "required": ["command"],
    }
    risk = ToolRisk.APPROVAL_REQUIRED
    category = "workspace"
    tags = ["terminal", "command", "workspace", "shell", "exec"]

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        command = kwargs.get("command", "")
        timeout_seconds = kwargs.get("timeout_seconds", 60)
        return await workspace_run_command(
            command,
            timeout_seconds=timeout_seconds,
            context=context,
        )


class TerminalRunCommandTool(WorkspaceRunCommandTool):
    """Alias for WorkspaceRunCommandTool under the terminal_run_command name."""

    name = "terminal_run_command"
    description = (
        "Execute a shell command inside the active workspace directory (terminal alias). "
        "Requires confirmation before execution."
    )
    tags = ["terminal", "command", "workspace", "shell", "alias"]


def register_workspace_tools(registry: ToolRegistry) -> None:
    """Register all workspace tools into the given ToolRegistry."""
    registry.register(WorkspaceReadFileTool())
    registry.register(WorkspaceWriteFileTool())
    registry.register(WorkspaceEditFileTool())
    registry.register(WorkspaceListDirTool())
    registry.register(WorkspaceDeleteFileTool())
    registry.register(WorkspaceDeleteDirTool())
    registry.register(WorkspaceRunCommandTool())
    registry.register(TerminalRunCommandTool())
