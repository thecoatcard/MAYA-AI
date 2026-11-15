"""Preparing the approval for ``file_replace_original`` (ADR-0022).

The plan cannot know the paths (the new version is created by an earlier edit step), so the worker
asks this module, just before it asks the human: it takes the paths from the edit step's output
(or from the step's own tool arguments), checks both are real files inside the connected folder,
and builds the diff the approval card shows. The approval binds the exact ``{path, new_version}``
pair, so the approved action can only be that replacement.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.automation.file_edit import EditError, describe_change
from app.files import services as file_services
from app.files.access import FileAccessError, context_from
from app.files.extract import kind_for


def _paths_from(input_data: dict[str, Any], dep_outputs: dict[str, Any]) -> tuple[str, str]:
    args = input_data.get("tool_args") or {}
    path, new_version = str(args.get("path") or ""), str(args.get("new_version") or "")
    if path and new_version:
        return path, new_version
    for output in reversed(list((dep_outputs or {}).values())):
        if isinstance(output, dict) and output.get("original") and output.get("new_version"):
            return str(output["original"]), str(output["new_version"])
    raise FileAccessError(
        "There is no edited version to replace the original with. Edit the file first."
    )


async def prepare_replace_original(
    workspace_id: UUID,
    user_id: UUID,
    task_id: UUID | None,
    input_data: dict[str, Any],
    dep_outputs: dict[str, Any],
) -> dict[str, Any]:
    """``{"tool_args": {...}, "diff": str, "payload_diff": str}`` for the approval, or raises
    :class:`FileAccessError` / :class:`EditError` with a plain message."""
    import asyncio

    ctx = context_from(file_services.current(), workspace_id, user_id, task_id)
    path, new_version = _paths_from(input_data, dep_outputs)
    root = await ctx.root()
    target = ctx.resolve(root, path)
    replacement = ctx.resolve(root, new_version)
    if not await asyncio.to_thread(target.is_file):
        raise FileAccessError("The original file no longer exists in the connected folder.")
    if not await asyncio.to_thread(replacement.is_file):
        raise FileAccessError("The replacement version no longer exists in the connected folder.")
    if target == replacement:
        raise EditError("The replacement version and the original are the same file.")
    diff = await asyncio.to_thread(describe_change, target, replacement, kind_for(target.name))
    header = (
        f"Update '{ctx.relative(root, target)}' in place with "
        f"'{ctx.relative(root, replacement)}'. "
        "A backup of the previous version is kept in .maya-backups.\n\n"
    )
    text = (header + diff)[:20_000]
    return {
        "tool_args": {
            "path": ctx.relative(root, target),
            "new_version": ctx.relative(root, replacement),
        },
        "diff": text,
        "payload_diff": text,
    }
